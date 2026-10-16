"""amlint's inhibition simulation against a running Alertmanager: POST the alerts, then
read each one's status.inhibitedBy (and receivers) from GET /api/v2/alerts."""

from typing import List, Optional

from conftest import INHIBIT_EXAMPLES, PHASES, SEED
from harness import (
    Alertmanager,
    binary_version,
    dump,
    format_labels,
    version_tuple,
    write_config,
)
from hypothesis import assume, event, given, seed, settings
from reduce import reduce
from strategies import inhibition_scenarios

from amlint.cli import load
from amlint.routing import Inhibitor, Router, RoutingError, _matches


def older_inhibitor_bug(version: str, inhibitor: Inhibitor, rule_path: str, alert,
                        firing) -> bool:
    """Alertmanager before 0.34.1 keeps one source alert per rule and `equal:` values, and
    misses the inhibition when that one also matches the target side (so it may not
    inhibit) even though another firing alert would: the result depends on the order the
    alerts arrive. Fixed upstream in 0.34.1 ("inhibit: Fix several issues related to
    inhibitions that caused alerts to be improperly un-muted", #5449, #5542, #5559).
    amlint follows 0.34.1. True if that explains Alertmanager not inhibiting `alert`."""
    if not version_tuple(version) or version_tuple(version) >= (0, 34, 1):
        return False
    rule = next(r for r in inhibitor._rules if r.path == rule_path)
    return any(
        _matches(rule.source, src) and _matches(rule.target, src)
        and all(src.get(n, "") == alert.get(n, "") for n in rule.equal)
        for src in firing
    )


def disagreement(binary: str, workdir: str, cfg: dict, alerts: List[dict],
                 name: str = "inhibition.yml") -> Optional[List[str]]:
    """Report lines for the first alert amlint and Alertmanager treat differently, or None.
    Raises RoutingError if amlint cannot simulate the config."""
    path = write_config(cfg, workdir, name)
    loaded = load(path)
    router = Router(loaded.get("route"))
    inhibitor = Inhibitor(loaded.get("inhibit_rules"))
    with Alertmanager(binary, path) as am:
        am.post_alerts(alerts)
        got = am.settled_alerts(len(alerts))
    by_labels = {frozenset(a["labels"].items()): a for a in got}
    labels_of = {a["fingerprint"]: a["labels"] for a in got}

    for alert in alerts:
        status = by_labels[frozenset(alert.items())]
        sources = [labels_of[fp] for fp in status["status"]["inhibitedBy"] or []]
        want = inhibitor.inhibited_by(alert, alerts)
        head = [f"alert:   {format_labels(alert)}"]
        if want and not sources and older_inhibitor_bug(
                binary_version(binary), inhibitor, want.rule, alert, alerts):
            event("Alertmanager < 0.34.1 misses an inhibition (upstream bug, fixed in 0.34.1)")
        elif bool(want) != bool(sources):
            return ["amlint and Alertmanager disagree on inhibition", *head,
                    "alertmanager: " + (f"inhibited by {format_labels(sources[0])}"
                                        if sources else "not inhibited"),
                    "amlint:       " + (f"inhibited by {format_labels(want.source)} ({want.rule})"
                                        if want else "not inhibited")]
        for source in sources:
            if inhibitor.inhibited_by(alert, [source]) is None:
                return ["Alertmanager inhibits by a source amlint does not accept", *head,
                        f"source:  {format_labels(source)}"]
        receivers = ", ".join(r["name"] for r in status["receivers"])
        expected = ", ".join(str(m.receiver) for m in router.match(alert))
        if receivers != expected:
            return ["amlint and Alertmanager route an alert differently", *head,
                    f"alertmanager: {receivers}", f"amlint:       {expected}"]
        if want:
            event("an alert is inhibited")
    return None


@seed(SEED)
@settings(settings.get_profile("differential"), max_examples=INHIBIT_EXAMPLES, phases=PHASES)
@given(scenario=inhibition_scenarios())
def test_inhibition_matches_alertmanager(alertmanager_binary, workdir, scenario):
    cfg, alerts = scenario
    try:
        problem = disagreement(alertmanager_binary, workdir, cfg, alerts)
    except RoutingError:
        assume(False)  # amlint cannot simulate it: the routing test covers that
    if problem is None:
        return

    def fails(case) -> bool:  # an Alertmanager per try, so this takes a while
        try:
            alerts = case["alerts"]
            if not alerts or not all(alerts) or len({frozenset(a.items()) for a in alerts}) < len(
                    alerts):
                return False
            found = disagreement(alertmanager_binary, workdir, case["config"], alerts,
                                 "reduce.yml")
        except Exception:
            return False
        return found is not None and found[0] == problem[0]

    case = reduce({"config": cfg, "alerts": alerts}, fails)
    problem = disagreement(alertmanager_binary, workdir, case["config"], case["alerts"],
                           "reduced.yml") or problem
    raise AssertionError("\n".join([
        f"{problem[0]} (Alertmanager {binary_version(alertmanager_binary)})", "",
        *problem[1:],
        "firing:  " + "\n         ".join(format_labels(a) for a in case["alerts"]), "",
        "config.yml (reduced):", dump(case["config"]),
    ]))
