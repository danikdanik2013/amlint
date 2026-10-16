"""
Alertmanager routing, simulated in pure Python for `amlint route` and `amlint test`.

Mirrors Alertmanager's dispatch/route.go, no Go binary or running Alertmanager needed:

  - NewRoute: every route starts from its parent's settings (Alertmanager's defaults at
    the root) and overrides what it sets itself. receiver, group_by, group_wait,
    group_interval and repeat_interval are inherited; mute_time_intervals and
    active_time_intervals are not. `group_by: []` resets grouping to none,
    `group_by: ['...']` groups by every label.
  - Route.Match: depth-first, children in definition order. A child matches when all its
    matchers match; the first matching child stops the search at that level unless it has
    `continue: true`. A route none of whose children match is itself the match.
  - Matchers: a missing label matches as the empty string, and regexes are fully anchored
    (Alertmanager compiles `^(?:pattern)$`), so `service=~"api"` does not match `api-gw`.
"""

import re
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Mapping, Optional, Pattern, Tuple, Union

from .linter import (
    _TIMING_DEFAULTS,
    _dict_items,
    _go_regex_to_python,
    _matchers,
    _parse_duration,
    _regex_problem,
    _scalar,
)

GROUP_BY_ALL = "..."


class RoutingError(ValueError):
    """The route tree can't be simulated: Alertmanager would refuse to load it, or a
    regex uses Go syntax amlint cannot evaluate."""


@dataclass
class RouteOpts:
    """Effective settings of a route after inheritance, like Alertmanager's RouteOpts."""
    receiver: Optional[str]
    group_by: Tuple[str, ...]  # (GROUP_BY_ALL,) means group by every label
    group_wait: str
    group_interval: str
    repeat_interval: str
    mute_time_intervals: Tuple[str, ...] = ()
    active_time_intervals: Tuple[str, ...] = ()
    # setting -> path of the route that set it; missing means Alertmanager's default
    origin: Dict[str, str] = field(default_factory=dict)


@dataclass
class RouteMatch:
    """One route an alert ends up on: the path walked from the root, and its settings."""
    path: Tuple[str, ...]
    opts: RouteOpts

    @property
    def receiver(self) -> Optional[str]:
        return self.opts.receiver


_Matcher = Tuple[str, str, Union[str, Pattern]]


@dataclass
class _Route:
    path: str
    matchers: List[_Matcher]
    continue_: bool
    opts: RouteOpts
    routes: List["_Route"]


@lru_cache(maxsize=None)
def compile_regex(pattern: str) -> Pattern:
    """Compile an Alertmanager regex for use with fullmatch(), i.e. as ^(?:pattern)$."""
    problem = _regex_problem(pattern)
    if problem:
        raise RoutingError(f"regex {pattern!r} {problem}")
    py_pattern, unsupported = _go_regex_to_python(pattern, exact=True)
    if unsupported:
        raise RoutingError(
            f"regex {pattern!r} uses {unsupported}, which amlint cannot evaluate yet — "
            f"check this route with `amtool config routes test`"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return re.compile(f"(?:{py_pattern})")
    except re.error as e:
        raise RoutingError(f"regex {pattern!r} cannot be evaluated: {e.msg}") from None


def _string_list(value) -> Tuple[str, ...]:
    return tuple(_scalar(v) for v in value) if isinstance(value, list) else ()


def _build(node: dict, path: str, parent: RouteOpts) -> _Route:
    origin = dict(parent.origin)
    receiver = parent.receiver
    if node.get("receiver"):
        receiver = _scalar(node["receiver"])
        origin["receiver"] = path

    group_by = parent.group_by
    if isinstance(node.get("group_by"), list):
        group_by = _string_list(node["group_by"])
        if GROUP_BY_ALL in group_by and len(group_by) > 1:
            raise RoutingError(
                f"{path}: group_by mixes '...' with label names, which Alertmanager rejects"
            )
        origin["group_by"] = path

    timings = {}
    for key in _TIMING_DEFAULTS:
        value = node.get(key)
        if value is None:
            timings[key] = getattr(parent, key)
            continue
        if _parse_duration(value) is None:
            raise RoutingError(f"{path}: {key} {_scalar(value)!r} is not a valid duration")
        timings[key] = _scalar(value)
        origin[key] = path

    # Alertmanager does not inherit time intervals: each route has only its own.
    intervals = {}
    for key in ("mute_time_intervals", "active_time_intervals"):
        intervals[key] = _string_list(node.get(key))
        if intervals[key]:
            origin[key] = path
        else:
            origin.pop(key, None)

    matchers: List[_Matcher] = []
    for label, op, value in _matchers(node):
        if op == "?":
            raise RoutingError(f"{path}: cannot parse matcher {label!r}")
        if op in ("=~", "!~"):
            try:
                value = compile_regex(value)
            except RoutingError as e:
                raise RoutingError(f"{path}: {e}") from None
        matchers.append((label, op, value))

    opts = RouteOpts(receiver=receiver, group_by=group_by, origin=origin, **timings, **intervals)
    children = [
        _build(child, f"{path}.routes[{i}]", opts) for i, child in _dict_items(node.get("routes"))
    ]
    return _Route(path, matchers, bool(node.get("continue")), opts, children)


def _matches(matchers: List[_Matcher], labels: Mapping[str, str]) -> bool:
    for label, op, value in matchers:
        actual = labels.get(label, "")
        if op == "=":
            ok = actual == value
        elif op == "!=":
            ok = actual != value
        elif op == "=~":
            ok = value.fullmatch(actual) is not None
        else:
            ok = value.fullmatch(actual) is None
        if not ok:
            return False
    return True


def _match(route: _Route, labels: Mapping[str, str], trail: Tuple[str, ...]) -> List[RouteMatch]:
    if not _matches(route.matchers, labels):
        return []
    trail = trail + (route.path,)
    found: List[RouteMatch] = []
    for child in route.routes:
        child_matches = _match(child, labels, trail)
        found.extend(child_matches)
        if child_matches and not child.continue_:
            break
    return found or [RouteMatch(trail, route.opts)]


class Router:
    """A parsed route tree. Raises RoutingError for trees Alertmanager would not load."""

    def __init__(self, route: dict) -> None:
        if not isinstance(route, dict):
            raise RoutingError("no root 'route' defined")
        if _matchers(route):
            raise RoutingError(
                "the root route has matchers, which Alertmanager rejects (invalid-root-route)"
            )
        defaults = RouteOpts(receiver=None, group_by=(), **_TIMING_DEFAULTS)
        self._root = _build(route, "route", defaults)

    def match(self, labels: Mapping[str, object]) -> List[RouteMatch]:
        """Routes an alert with these labels reaches, in Alertmanager's order."""
        normalized = {str(k): _scalar(v) for k, v in labels.items()}
        return _match(self._root, normalized, ())
