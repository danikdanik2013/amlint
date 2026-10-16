"""amlint's routing engine against `amtool config routes test`, on generated configs."""

import re
from typing import Tuple

from conftest import EXAMPLES, PHASES, SEED
from harness import AmtoolError, dump, format_labels, write_config
from hypothesis import event, given, seed, settings
from hypothesis import strategies as st
from reduce import reduce
from strategies import config_values, label_sets, route_configs

from amlint.cli import load
from amlint.routing import Router, RoutingError, probe_label_sets

# amlint refuses these regexes instead of guessing; Alertmanager evaluates them.
DECLINED = ("amlint cannot evaluate", "cannot be evaluated")
PROBES = 12  # label sets amlint's own prober adds per config, on top of the random ones

Outcome = Tuple[str, str]  # ("routes", "a, b") | ("rejects", why) | ("declines", why)


def amlint_outcome(path: str, labels) -> Outcome:
    try:
        router = Router(load(path).get("route"))
    except RoutingError as e:
        return ("declines" if any(d in str(e) for d in DECLINED) else "rejects"), str(e)
    return "routes", ", ".join(map(str, (m.receiver for m in router.match(labels))))


def amtool_outcome(amtool, path: str, labels) -> Outcome:
    try:
        return "routes", ", ".join(amtool.receivers(path, labels))
    except AmtoolError as e:
        return "rejects", str(e)


def _why(outcome: Outcome) -> Outcome:
    """The outcome with route paths blanked, to tell one rejection reason from another."""
    return outcome if outcome[0] == "routes" else (outcome[0], re.sub(
        r"route(\.routes\[\d+\])*", "route", outcome[1]))


def agree(mine: Outcome, theirs: Outcome) -> bool:
    if mine[0] == "declines":
        # amlint stops at a regex it can't evaluate and claims nothing about the config;
        # whether Alertmanager accepts that regex is test_validation's business
        return True
    return mine == theirs if mine[0] == theirs[0] == "routes" else mine[0] == theirs[0]


TITLES = {
    ("routes", "routes"): "amlint and amtool route an alert differently",
    ("routes", "rejects"): "amtool rejects a config amlint routes",
    ("rejects", "routes"): "amlint rejects a config amtool loads",
}


def check(amtool, workdir: str, cfg: dict, labels, mine: Outcome, theirs: Outcome) -> None:
    """Fail with a minimal config and label set that still disagree the same way: the
    same kinds of outcome and, where one side rejects the config, for the same reason."""
    if agree(mine, theirs):
        return
    kinds = (mine[0], theirs[0])
    reasons = [_why(o) for o in (mine, theirs) if o[0] != "routes"]

    def fails(case) -> bool:
        if not isinstance(case.get("config"), dict) or not isinstance(case.get("labels"), dict):
            return False
        path = write_config(case["config"], workdir, "reduce.yml")
        a = amlint_outcome(path, case["labels"])
        b = amtool_outcome(amtool, path, case["labels"])
        return ((a[0], b[0]) == kinds and not agree(a, b)
                and [_why(o) for o in (a, b) if o[0] != "routes"] == reasons)

    case = reduce({"config": cfg, "labels": dict(labels)}, fails)
    path = write_config(case["config"], workdir, "reduced.yml")
    labels = case["labels"]
    mine, theirs = amlint_outcome(path, labels), amtool_outcome(amtool, path, labels)
    args = amtool.routes_test_args("config.yml", labels)
    raise AssertionError("\n".join([
        f"{TITLES[kinds]} (Alertmanager {amtool.version})",
        "",
        f"labels:  {format_labels(labels)}",
        f"amtool:  {theirs[1]}",
        f"amlint:  {mine[1]}",
        "repro:   amtool " + " ".join(args[1:]),
        "",
        "config.yml (reduced):",
        dump(case["config"]),
    ]))


@seed(SEED)
@settings(settings.get_profile("differential"), max_examples=EXAMPLES, phases=PHASES)
@given(data=st.data())
def test_routing_matches_amtool(amtool, workdir, data):
    cfg = data.draw(route_configs(), label="config")
    path = write_config(cfg, workdir)
    mine = amlint_outcome(path, {})
    if mine[0] != "routes":
        event(f"amlint {mine[0]} the config")
        check(amtool, workdir, cfg, {}, mine, amtool_outcome(amtool, path, {}))
        return

    router = Router(load(path)["route"])
    sets = data.draw(st.lists(label_sets(config_values(cfg)), min_size=1, max_size=6),
                     label="labels")
    sets += probe_label_sets([router], limit=PROBES)[0]
    sets = list({frozenset(s.items()): s for s in sets}.values())
    try:
        got = amtool.receivers_many(path, sets)
    except AmtoolError as e:
        check(amtool, workdir, cfg, {}, mine, ("rejects", str(e)))
        raise
    for labels, receivers in zip(sets, got):
        want = ", ".join(m.receiver for m in router.match(labels))
        check(amtool, workdir, cfg, labels, ("routes", want), ("routes", ", ".join(receivers)))
    event(f"{len(sets)} label sets compared")


def test_failure_report_is_minimal(amtool, workdir):
    """A disagreement is reduced to the routes and labels that show it. Simulated here by
    a config that amtool rejects (undefined receiver) and amlint's Router routes."""
    cfg = {
        "route": {"receiver": "root", "routes": [
            {"matchers": ['team="a"'], "receiver": "a", "routes": [
                {"matchers": ['sev="crit"'], "receiver": "ghost"},
            ]},
            {"matchers": ['team="b"'], "receiver": "b"},
        ]},
        "receivers": [{"name": "root"}, {"name": "a"}, {"name": "b"}],
    }
    path = write_config(cfg, workdir, "report.yml")
    mine, theirs = amlint_outcome(path, {"team": "a"}), amtool_outcome(amtool, path, {"team": "a"})
    try:
        check(amtool, workdir, cfg, {"team": "a"}, mine, theirs)
    except AssertionError as e:
        report = str(e)
    else:
        raise AssertionError("an undefined receiver should make amtool reject the config")
    assert report.startswith("amtool rejects a config amlint routes")
    assert "labels:  {}" in report
    assert 'undefined receiver \\"ghost\\"' in report or 'undefined receiver "ghost"' in report
    reduced = report.split("config.yml (reduced):")[1]
    assert "receiver: ghost" in reduced and "team" not in reduced
