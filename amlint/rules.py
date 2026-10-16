"""
Prometheus alerting rules, to check an Alertmanager config against the alerts that can
actually reach it (`amlint check --rules`).

At runtime an alert's labels are those of the PromQL result series, overridden by the
rule's `labels:` (templates expanded) and then `alertname`, plus Prometheus'
external_labels for labels not yet set. Only the rule's own part is in the rule file, so
the checks here work on that: alertname plus the rule's labels, with templated values
treated as unknown.
"""

import difflib
import glob
import os
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .linter import INFO, WARN, Finding, Matcher, _dict_items, _matchers, _root_route, _walk_routes
from .routing import Router, RoutingError, compile_regex
from .source import _mapping_pairs

_NULL_TAG = "tag:yaml.org,2002:null"
_ALERT_NAME_RE = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")  # Prometheus requires metric-name syntax


class RulesError(ValueError):
    """A --rules pattern matches no file, or a rule file can't be read or parsed."""


@dataclass
class AlertRule:
    """An alerting rule, described by the labels its alerts are sure to carry."""
    name: str
    labels: Dict[str, str]  # alertname, plus the rule's labels: that have a fixed value
    unknown: FrozenSet[str]  # labels: whose value is a template, so only known at runtime
    file: str
    line: int

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"


def rule_files(patterns: List[str]) -> List[str]:
    """Files the --rules globs match, each once; RulesError for a glob that matches none."""
    files: Dict[str, None] = {}
    for pattern in patterns:
        matched = sorted(p for p in glob.glob(pattern, recursive=True) if os.path.isfile(p))
        if not matched and os.path.isdir(pattern):
            example = os.path.join(pattern, "*.yml")
            raise RulesError(f"--rules {pattern!r} is a directory; use a glob like {example!r}")
        if not matched:
            raise RulesError(f"--rules {pattern!r} matches no files")
        files.update(dict.fromkeys(matched))
    return list(files)


def _get(node: Optional[Node], key: str) -> Optional[Node]:
    """Value of `key` in a mapping node; the last one wins, as in yaml.safe_load."""
    found = None
    if isinstance(node, MappingNode):
        for k, v in _mapping_pairs(node):
            if k.value == key:
                found = v
    return found


def _items(node: Optional[Node]) -> List[Node]:
    return node.value if isinstance(node, SequenceNode) else []


def _string(node: Optional[Node]) -> Optional[str]:
    """A scalar the way Prometheus (Go) reads it into a string: its text, "" for null.
    So `code: 0x1F` stays "0x1F" and `on: yes` stays "yes"."""
    if not isinstance(node, ScalarNode):
        return None
    return "" if node.tag == _NULL_TAG else node.value


def _groups(doc: Optional[Node]) -> Iterator[Node]:
    """Rule groups of one YAML document: a Prometheus rule file (groups:), a
    prometheus-operator PrometheusRule (spec.groups:), or a List of PrometheusRules."""
    for item in _items(_get(doc, "items")):
        yield from _groups(item)
    for holder in (doc, _get(doc, "spec")):
        yield from _items(_get(holder, "groups"))


def _alert_rule(node: Node, path: str) -> Optional[AlertRule]:
    name = _string(_get(node, "alert"))
    if not name:
        return None  # a recording rule (record:), or not a rule at all
    labels_node = _get(node, "labels")
    pairs = _mapping_pairs(labels_node) if isinstance(labels_node, MappingNode) else []
    labels: Dict[str, str] = {}
    unknown: Set[str] = set()
    for label, value in {k.value: _string(v) for k, v in pairs}.items():
        if label == "alertname":
            continue  # Prometheus sets alertname to the rule's name, over labels:
        if value is None or "{{" in value:
            unknown.add(label)  # a template; a non-scalar Prometheus would reject anyway
        elif value:
            labels[label] = value  # an empty value removes the label
    return AlertRule(name, {"alertname": name, **labels}, frozenset(unknown),
                     path, node.start_mark.line + 1)


def load_rules(paths: List[str]) -> Tuple[List[AlertRule], List[str]]:
    """The alerting rules in these files (recording rules are skipped), and a note for
    each file that has no rule groups at all."""
    alerts: List[AlertRule] = []
    notes: List[str] = []
    for path in paths:
        try:
            with open(path) as f:
                docs = list(yaml.compose_all(f.read(), Loader=yaml.SafeLoader))
            groups = [group for doc in docs for group in _groups(doc)]
            for group in groups:
                for rule in _items(_get(group, "rules")):
                    alert = _alert_rule(rule, path)
                    if alert:
                        alerts.append(alert)
        except OSError as e:
            raise RulesError(f"cannot read {path}: {e.strerror or e}") from None
        except UnicodeDecodeError:
            raise RulesError(f"cannot read {path}: not a text file") from None
        except yaml.YAMLError as e:
            raise RulesError(f"failed to parse YAML ({path}): {e}") from None
        except RecursionError:
            raise RulesError(f"cannot read {path}: recursive YAML aliases") from None
        if not groups:
            notes.append(f"{path}: no rule groups found (expected groups: or spec.groups:)")
    return alerts, notes


# ── Checks, run by lint() when --rules is given ──────────────────────

def _literal_names(pattern: str) -> Optional[List[str]]:
    """The names a regex spells out literally (`A|B|C`, also as `^(?:A|B)$`), or None."""
    p = pattern[1:] if pattern.startswith("^") else pattern
    p = p[:-1] if p.endswith("$") else p
    for group in ("(?:", "("):
        if p.startswith(group) and p.endswith(")"):
            p = p[len(group):-1]
            break
    names = p.split("|")
    return names if all(_ALERT_NAME_RE.fullmatch(n) for n in names) else None


def _hint(name: str, known: Set[str]) -> Optional[str]:
    by_lower = {k.lower(): k for k in sorted(known)}
    if name.lower() in by_lower:
        return by_lower[name.lower()]
    close = difflib.get_close_matches(name, sorted(known), n=1)
    return close[0] if close else None


def _alertname_findings(matchers: List[Matcher], known: Set[str], subject: str,
                        dead: str, code: str, where: str) -> List[Finding]:
    """Findings for the alertname matchers that name no alert the rules produce.

    `subject` starts the message ("Route", "Inhibit rule source"), `dead` says what an
    equality matcher on an unknown name means for it ("this branch is dead").
    """
    out: List[Finding] = []
    for label, op, value in matchers:
        if label != "alertname" or op == "?":
            continue
        negative = op in ("!=", "!~")
        verb = "excludes" if negative else "matches"
        names = [value] if op in ("=", "!=") else _literal_names(value)
        if names is None:
            try:
                regex = compile_regex(value)
            except RoutingError:
                continue  # bad-regex reports it
            if any(regex.fullmatch(name) for name in known):
                continue
            out.append(_unknown_finding(
                code, where,
                f"{subject} {verb} alertname{op}\"{value}\", which matches none of the "
                f"{len(known)} alert names in the --rules files",
                _hint(value, known), "the matcher excludes nothing" if negative else dead,
            ))
            continue
        unknown = [name for name in names if name not in known]
        partial = len(unknown) < len(names)  # other names of the regex are fine
        for name in unknown:
            context = f" (in alertname{op}\"{value}\")" if op in ("=~", "!~") else ""
            part = "that part of the regex" if partial else "the matcher"
            out.append(_unknown_finding(
                code, where,
                f"{subject} {verb} alertname '{name}'{context}, which no alerting rule in "
                f"the --rules files produces",
                _hint(name, known),
                f"{part} excludes nothing" if negative else (
                    f"{part} never matches" if partial else dead),
            ))
    return out


def _unknown_finding(code: str, where: str, problem: str, hint: Optional[str],
                     consequence: str) -> Finding:
    return Finding(
        WARN, code,
        problem + (f" — did you mean '{hint}'?" if hint else ".")
        + f" Unless such alerts come from another source, {consequence}.",
        where,
    )


def check_route_unknown_alertname(cfg: dict, alerts: List[AlertRule]) -> List[Finding]:
    out: List[Finding] = []
    known = {a.name for a in alerts}
    route = _root_route(cfg)
    if not route:
        return out
    for node, path in _walk_routes(route):
        out.extend(_alertname_findings(_matchers(node), known, "Route", "this branch is dead",
                                       "route-unknown-alertname", path))
    return out


def check_inhibit_unknown_alertname(cfg: dict, alerts: List[AlertRule]) -> List[Finding]:
    out: List[Finding] = []
    known = {a.name for a in alerts}
    for i, rule in _dict_items(cfg.get("inhibit_rules")):
        for side in ("source", "target"):
            out.extend(_alertname_findings(
                _matchers(rule, f"{side}_"), known, f"Inhibit rule {side}",
                "this inhibit rule never mutes anything", "inhibit-unknown-alertname",
                f"inhibit_rules[{i}]",
            ))
    return out


def check_alert_hits_default_receiver(cfg: dict, alerts: List[AlertRule]) -> List[Finding]:
    out: List[Finding] = []
    route = _root_route(cfg)
    if not route or not _dict_items(route.get("routes")):
        return out  # no child routes: the root receiver is the only one, by design
    try:
        router = Router(route)
    except RoutingError:
        return out  # bad-regex / invalid-root-route say why
    # Labels the routes match on that no rule sets come from the PromQL series or
    # external_labels: their value is unknown for every alert, not absent.
    from_rules = {label for a in alerts for label in (*a.labels, *a.unknown)}
    runtime = {
        label for node, _ in _walk_routes(route) for label, _, _ in _matchers(node)
    } - from_rules
    for a in alerts:
        matches = router.match_partial(a.labels, a.unknown | runtime)
        if matches is None or [m.path for m in matches] != [("route",)]:
            continue  # depends on a label amlint can't know, or reaches a child route
        labels = ", ".join(f"{k}={v}" for k, v in a.labels.items())
        out.append(Finding(
            INFO, "alert-hits-default-receiver",
            f"Based on static rule labels ({labels}), alert '{a.name}' from {a.location} "
            f"matches no child route and goes to the root receiver "
            f"'{matches[0].receiver or '(none)'}'. Labels from the PromQL series or "
            f"external_labels may still route it elsewhere at runtime.",
            "route",
        ))
    return out


RULES_CHECKS = [
    check_route_unknown_alertname,
    check_inhibit_unknown_alertname,
    check_alert_hits_default_receiver,
]


def check_rules(cfg: dict, alerts: List[AlertRule]) -> List[Finding]:
    """All checks against the alerting rules; none without any alerting rule to go by."""
    if not alerts:
        return []
    return [f for check in RULES_CHECKS for f in check(cfg, alerts)]
