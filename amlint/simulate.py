"""
Route simulation for `amlint test`.

Given a route tree and a set of alert labels, determines which receiver(s)
the alert would be delivered to — replicating Alertmanager's own dispatch
algorithm (dispatch/route.go) in pure Python, no Go binary required.

Semantics (verified empirically against amtool v0.33.1 / amtool config routes test):
  - A route node contributes its own receiver ONLY if none of its children match
    (deepest-match-wins; a matched parent with a matched child does NOT also fire).
  - `continue: true` on a matched child causes evaluation to keep trying that
    child's subsequent siblings too (fan-out to multiple receivers), in
    definition order. Without it, the first matching sibling stops the search
    at that level.
  - `receiver` is inherited from the nearest ancestor that sets one, if a
    matched leaf route doesn't set its own.
"""

import re
from typing import Dict, List, Tuple

_MATCHER_RE = re.compile(
    r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"?(.*?)"?\s*$'
)


def _parse_matcher(s: str):
    """Parse a new-style matcher string like 'team="infra"' or 'severity!~"info.*"'."""
    m = _MATCHER_RE.match(str(s))
    if not m:
        return None
    label, op, value = m.groups()
    return label, op, value


def node_matches(node: dict, labels: Dict[str, str]) -> bool:
    """True if an alert with the given labels satisfies every matcher on this node.

    Combines legacy match/match_re with new-style matchers — all ANDed together,
    matching Alertmanager's own behavior.
    """
    for k, v in (node.get("match") or {}).items():
        if labels.get(k) != v:
            return False
    for k, pattern in (node.get("match_re") or {}).items():
        if not re.search(pattern, labels.get(k, "")):
            return False
    for raw in node.get("matchers") or []:
        parsed = _parse_matcher(raw)
        if not parsed:
            continue
        label, op, value = parsed
        val = labels.get(label, "")
        if op == "=" and val != value:
            return False
        if op == "!=" and val == value:
            return False
        if op == "=~" and not re.search(value, val):
            return False
        if op == "!~" and re.search(value, val):
            return False
    return True


def simulate_route(route: dict, labels: Dict[str, str]) -> List[Tuple[str, str]]:
    """Return [(receiver, route_path), ...] an alert with these labels would reach,
    in the order Alertmanager would deliver them (definition order, honoring
    continue: true fan-out). Root always matches unconditionally.
    """
    results: List[Tuple[str, str]] = []

    def walk(node: dict, path: str, inherited_receiver) -> None:
        effective_receiver = node.get("receiver") or inherited_receiver
        any_child_matched = False
        for i, child in enumerate(node.get("routes") or []):
            if node_matches(child, labels):
                any_child_matched = True
                walk(child, f"{path}.routes[{i}]", effective_receiver)
                if not child.get("continue"):
                    break
        if not any_child_matched and effective_receiver:
            results.append((effective_receiver, path))

    walk(route, "route", None)
    return results
