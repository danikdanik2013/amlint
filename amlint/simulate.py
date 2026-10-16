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
import warnings
from typing import Dict, List, Tuple

from .linter import _dict_items, _go_regex_to_python, _matchers, _scalar


def _regex_matches(pattern: str, value: str) -> bool:
    """Alertmanager anchors every regex (^(?:pattern)$): the whole value must match."""
    py_pattern, unsupported = _go_regex_to_python(pattern)
    if unsupported:
        return False  # Go RE2 rejects it, so Alertmanager wouldn't load this config
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # The raw pattern first: it keeps flags like (?i) that the translation drops.
        # The translation only stands in for Go-only syntax Python can't compile.
        for candidate in (pattern, py_pattern):
            try:
                return re.fullmatch(candidate, value) is not None
            except re.error:
                continue
    return False


def node_matches(node: dict, labels: Dict[str, str]) -> bool:
    """True if an alert with the given labels satisfies every matcher on this node.

    match / match_re / matchers are normalized by the linter's matcher parser (quoted
    values, several matchers in one string) and ANDed together, matching Alertmanager's
    own behavior. A missing label counts as the empty string.
    """
    for label, op, value in _matchers(node):
        actual = _scalar(labels.get(label))
        if op == "=" and actual != value:
            return False
        if op == "!=" and actual == value:
            return False
        if op == "=~" and not _regex_matches(value, actual):
            return False
        if op == "!~" and _regex_matches(value, actual):
            return False
        if op == "?":
            return False  # unparseable matcher: never let it widen a route to catch-all
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
        for i, child in _dict_items(node.get("routes")):
            if node_matches(child, labels):
                any_child_matched = True
                walk(child, f"{path}.routes[{i}]", effective_receiver)
                if not child.get("continue"):
                    break
        if not any_child_matched and effective_receiver:
            results.append((effective_receiver, path))

    walk(route, "route", None)
    return results
