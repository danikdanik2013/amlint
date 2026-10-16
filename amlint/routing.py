"""
Alertmanager routing, simulated in pure Python for `amlint route`, `amlint test` and the
routing section of `amlint diff`.

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

Inhibition mirrors inhibit/inhibit.go (Inhibitor.Mutes): see Inhibitor below.

tests/differential checks all of this against amtool and a running Alertmanager on
generated configs (scripts/differential.sh); docs/fidelity.md lists the known differences.
"""

import itertools
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Pattern,
    Sequence,
    Set,
    Tuple,
    Union,
)

try:
    import re._parser as _sre_parse  # Python 3.11+
except ImportError:  # pragma: no cover - Python < 3.11
    import sre_parse as _sre_parse

from .linter import (
    _TIMING_DEFAULTS,
    _bad_match_labels,
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

    matchers = _compile_matchers(node, path)
    opts = RouteOpts(receiver=receiver, group_by=group_by, origin=origin, **timings, **intervals)
    children = [
        _build(child, f"{path}.routes[{i}]", opts) for i, child in _dict_items(node.get("routes"))
    ]
    return _Route(path, matchers, bool(node.get("continue")), opts, children)


def _compile_matchers(node: dict, path: str, prefix: str = "") -> List[_Matcher]:
    """Matchers of a route, or of one side of an inhibit rule, with regexes compiled."""
    for key, name, _ in _bad_match_labels(node, prefix):
        raise RoutingError(
            f"{path}: {key} label name {name!r} is invalid, which Alertmanager "
            f"rejects (use {prefix}matchers: ['\"{name}\"=...'] for such names)"
        )
    matchers: List[_Matcher] = []
    for label, op, value in _matchers(node, prefix):
        if op == "?":
            raise RoutingError(f"{path}: cannot parse matcher {label!r}")
        if op in ("=~", "!~"):
            try:
                value = compile_regex(value)
            except RoutingError as e:
                raise RoutingError(f"{path}: {e}") from None
        matchers.append((label, op, value))
    return matchers


def _matcher_ok(op: str, value, actual: str) -> bool:
    if op == "=":
        return actual == value
    if op == "!=":
        return actual != value
    if op == "=~":
        return value.fullmatch(actual) is not None
    return value.fullmatch(actual) is None


def _matches(matchers: List[_Matcher], labels: Mapping[str, str]) -> bool:
    return all(_matcher_ok(op, value, labels.get(label, "")) for label, op, value in matchers)


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


def _matches_partial(matchers: List[_Matcher], labels: Mapping[str, str],
                     unknown: FrozenSet[str]) -> Optional[bool]:
    """_matches when the labels in `unknown` may have any value: False as soon as a matcher
    on a known label fails, None if the rest read an unknown label."""
    undecided = False
    for label, op, value in matchers:
        if label in unknown:
            undecided = True
        elif not _matcher_ok(op, value, labels.get(label, "")):
            return False
    return None if undecided else True


def _match_partial(route: _Route, labels: Mapping[str, str], unknown: FrozenSet[str],
                   trail: Tuple[str, ...]) -> Optional[List[RouteMatch]]:
    """_match, or None when the outcome depends on a label in `unknown`."""
    hit = _matches_partial(route.matchers, labels, unknown)
    if hit is not True:
        return None if hit is None else []
    trail = trail + (route.path,)
    found: List[RouteMatch] = []
    for child in route.routes:
        child_matches = _match_partial(child, labels, unknown, trail)
        if child_matches is None:
            return None
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
        return _match(self._root, _labels(labels), ())

    def paths(self) -> List[str]:
        """Every route's path, depth-first in definition order, the root first."""
        return [route.path for _, route in self._paths()]

    def match_partial(self, labels: Mapping[str, object],
                      unknown: Iterable[str]) -> Optional[List[RouteMatch]]:
        """match() for an alert known only in part: the labels named in `unknown` may have
        any value. None when the routes reached depend on one of them."""
        return _match_partial(self._root, _labels(labels), frozenset(unknown), ())

    def _paths(self) -> Iterator[Tuple[List[_Matcher], _Route]]:
        """Every route, with the matchers an alert has to pass to get there."""
        def walk(route: _Route, above: List[_Matcher]):
            conj = above + route.matchers
            yield conj, route
            for child in route.routes:
                yield from walk(child, conj)
        yield from walk(self._root, [])


def _labels(labels: Mapping[str, object]) -> Dict[str, str]:
    return {str(k): _scalar(v) for k, v in labels.items()}


# ── Route coverage, for `amlint test --coverage` ─────────────────────

@dataclass
class Coverage:
    """Which routes of a tree a set of alerts went through."""
    routes: List[str]  # every route, depth-first in definition order
    covered: Set[str]

    @property
    def uncovered(self) -> List[str]:
        return [p for p in self.routes if p not in self.covered]

    @property
    def percent(self) -> float:
        return 100.0 * len(self.covered) / len(self.routes)


def route_coverage(router: Router, results: Iterable[List[RouteMatch]]) -> Coverage:
    """Coverage of the tree by these Router.match results. A route is covered when an alert
    went through it: every route on a match's path counts, so a parent whose child took the
    alert is covered, and so is every branch of a `continue: true` fan-out."""
    covered = {p for matches in results for m in matches for p in m.path}
    return Coverage(router.paths(), covered)


# ── Inhibition, for `amlint route --firing` and `firing:` in `amlint test` ──
#
# Mirrors inhibit/inhibit.go (Inhibitor.Mutes, InhibitRule.hasEqual). Rules are tried in
# definition order; an alert is inhibited by the first one whose target matchers it matches
# and for which some firing alert
#   - matches the source matchers,
#   - has the alert's value for every `equal` label (a missing label is "", so a label
#     missing on both sides counts as equal), and
#   - if the alert matches the source matchers too, does not match the target matchers
#     itself: alerts on both sides of a rule don't inhibit each other, or themselves.
# A firing alert inhibits even when it is inhibited or silenced itself, as in Alertmanager.

@dataclass
class Inhibition:
    """Why an alert is inhibited: the rule, and the firing alert that matched its source."""
    rule: str  # inhibit_rules[i]
    source: Dict[str, str]


@dataclass
class _InhibitRule:
    path: str
    source: List[_Matcher]
    target: List[_Matcher]
    equal: Tuple[str, ...]


class Inhibitor:
    """A config's inhibit_rules. Raises RoutingError for rules Alertmanager would not load,
    or with a regex amlint cannot evaluate."""

    def __init__(self, rules) -> None:
        self._rules: List[_InhibitRule] = []
        for i, rule in _dict_items(rules):
            path = f"inhibit_rules[{i}]"
            equal = rule.get("equal")
            if equal is not None and not isinstance(equal, list):
                raise RoutingError(f"{path}: equal must be a list of label names")
            self._rules.append(_InhibitRule(
                path,
                _compile_matchers(rule, path, "source_"),
                _compile_matchers(rule, path, "target_"),
                _string_list(equal),
            ))

    def inhibited_by(
        self, labels: Mapping[str, object], firing: Sequence[Mapping[str, object]]
    ) -> Optional[Inhibition]:
        """Why an alert with these labels is inhibited while the `firing` alerts fire, or None
        if it isn't. Checks every rule in order and, per rule, the firing alerts in order."""
        alert = _labels(labels)
        sources = [_labels(a) for a in firing]
        for rule in self._rules:
            if not _matches(rule.target, alert):
                continue
            two_sided = _matches(rule.source, alert)
            for src in sources:
                if not _matches(rule.source, src):
                    continue
                if any(src.get(name, "") != alert.get(name, "") for name in rule.equal):
                    continue
                if two_sided and _matches(rule.target, src):
                    continue
                return Inhibition(rule.path, src)
        return None


# ── Behavioural diff of two route trees, for `amlint diff` ───────────
#
# Probe both trees with the same label sets and report the ones whose outcome changed.
# Candidate values per label come from the matchers of both configs: the value of = and
# !=, a few strings each =~ / !~ regex matches, and the label being absent. Small configs
# get every combination; larger ones, per route, a label set that reaches it and one that
# stops on it (no child matches), then single-label variations of those, spread evenly
# over the routes and capped at MAX_LABEL_SETS.

MAX_LABEL_SETS = 5000
_EXAMPLES_PER_REGEX = 6
_CHAR_POOL = "a0x-_. A"
_SETTINGS = (
    "group_by", "group_wait", "group_interval", "repeat_interval",
    "mute_time_intervals", "active_time_intervals",
)


def _examples(seq, limit: int = _EXAMPLES_PER_REGEX) -> List[str]:
    """Strings a parsed (sre) pattern could match; checked against the regex afterwards."""
    acc = [""]
    for op, av in seq:
        options = _item_examples(op, av, limit)
        if not options:
            return []
        acc = [a + b for a in acc for b in options][:limit]
    return acc


def _item_examples(op, av, limit: int) -> List[str]:
    P = _sre_parse
    if op == P.LITERAL:
        return [chr(av)]
    if op == P.NOT_LITERAL:
        return [c for c in _CHAR_POOL if ord(c) != av][:1]
    if op == P.ANY:
        return ["x"]
    if op == P.AT:
        return [""]
    if op == P.IN:
        chars, negate = [], False
        for item_op, item in av:
            if item_op == P.NEGATE:
                negate = True
            elif item_op == P.LITERAL:
                chars.append(chr(item))
            elif item_op == P.RANGE:
                chars.append(chr(item[0]))
        return [c for c in _CHAR_POOL if c not in chars][:1] if negate else chars[:2]
    if op == P.BRANCH:
        per_branch = [_examples(alt, 2) for alt in av[1]]
        # round-robin, so every alternative of `a|b|c` gets a representative
        return [x for row in itertools.zip_longest(*per_branch) for x in row if x is not None][
            :limit
        ]
    if op == P.SUBPATTERN:
        return _examples(av[-1], limit)
    if op in (P.MAX_REPEAT, P.MIN_REPEAT):
        low, _, sub = av
        inner = _examples(sub, limit)
        if low == 0:
            return ([""] + inner)[:limit]
        return [x * low for x in inner]
    return []


def _regex_examples(regex: Pattern) -> List[str]:
    try:
        parsed = _sre_parse.parse(regex.pattern, regex.flags)
    except Exception:
        return []
    return list(dict.fromkeys(s for s in _examples(parsed) if regex.fullmatch(s)))


def _domains(routers: Sequence[Router]) -> Dict[str, List[Optional[str]]]:
    """Candidate values per label; None (absent) first, since that keeps label sets small."""
    domains: Dict[str, List[Optional[str]]] = {}
    for router in routers:
        for conj, _ in router._paths():
            for label, op, value in conj:
                values = domains.setdefault(label, [None])
                for v in [value] if isinstance(value, str) else _regex_examples(value):
                    # an empty value is the same as the label being absent
                    if v and v not in values:
                        values.append(v)
    return domains


def _reaching(conj: List[_Matcher], domains) -> Optional[Dict[str, str]]:
    """A label set, built from the candidate values, that passes all of these matchers."""
    labels: Dict[str, str] = {}
    for label in dict.fromkeys(m[0] for m in conj):
        for v in domains[label]:
            if all(_matcher_ok(op, value, v or "") for lbl, op, value in conj if lbl == label):
                if v is not None:
                    labels[label] = v
                break
        else:
            return None
    return labels


def _stopping(conj, route: _Route, domains, labels: Dict[str, str]) -> Optional[Dict[str, str]]:
    """A variant of `labels` (which reach `route`) that no child of `route` matches, so the
    alert ends up on `route` itself; None if none of the candidate values gets there."""
    labels = dict(labels)
    for child in route.routes:
        if not _matches(child.matchers, labels):
            continue
        for label, op, value in child.matchers:
            for v in domains[label]:
                trial = {k: x for k, x in labels.items() if k != label}
                if v is not None:
                    trial[label] = v
                if not _matcher_ok(op, value, v or "") and _matches(conj, trial):
                    labels = trial
                    break
            else:
                continue
            break
    if any(_matches(child.matchers, labels) for child in route.routes):
        return None
    return labels


def _variants(labels: Dict[str, str], domains) -> Iterator[Dict[str, str]]:
    for name, values in domains.items():
        for v in values:
            variant = {k: x for k, x in labels.items() if k != name}
            if v is not None:
                variant[name] = v
            yield variant


def probe_label_sets(
    routers: Sequence[Router], limit: int = MAX_LABEL_SETS
) -> Tuple[List[Dict[str, str]], bool]:
    """Label sets that exercise the given route trees; True if `limit` cut the list short."""
    domains = _domains(routers)
    total = 1
    for values in domains.values():
        total *= len(values)
    if total <= limit:
        names = list(domains)
        sets = [
            {k: v for k, v in zip(names, combo) if v is not None}
            for combo in itertools.product(*domains.values())
        ]
        return sorted(sets, key=lambda s: (len(s), sorted(s.items()))), False

    out: List[Dict[str, str]] = []
    seen = set()

    def add(labels: Dict[str, str]) -> bool:
        key = frozenset(labels.items())
        if key not in seen:
            if len(out) >= limit:
                return False
            seen.add(key)
            out.append(labels)
        return True

    bases = []
    for router in routers:
        for conj, route in router._paths():
            labels = _reaching(conj, domains)
            if labels is not None:
                bases.append(labels)
                stop = _stopping(conj, route, domains, labels)
                if stop is not None:
                    bases.append(stop)
    for labels in bases:
        if not add(labels):
            return out, True
    # round-robin over the routes, so a cap cuts every route's variations evenly
    for row in itertools.zip_longest(*(_variants(labels, domains) for labels in bases)):
        for variant in row:
            if variant is not None and not add(variant):
                return out, True
    return out, False


def _outcome(matches: List[RouteMatch]) -> tuple:
    return tuple(sorted(
        (m.receiver or "",) + tuple(getattr(m.opts, key) for key in _SETTINGS) for m in matches
    ))


def _show(value) -> str:
    if isinstance(value, tuple):
        return ", ".join(value) if value else "(none)"
    return value or "(none)"


@dataclass
class RoutingChange:
    """A label set whose routing differs between two configs."""
    labels: Dict[str, str]
    old: List[RouteMatch]
    new: List[RouteMatch]
    similar: int = 0  # further label sets with exactly the same old → new change

    def differences(self) -> List[Dict[str, str]]:
        """What changed: the receivers, and settings of receivers both sides deliver to."""
        old_rcv = sorted(m.receiver or "(none)" for m in self.old)
        new_rcv = sorted(m.receiver or "(none)" for m in self.new)
        out = []
        if Counter(old_rcv) != Counter(new_rcv):
            out.append({"subject": "receivers", "setting": "receivers",
                        "old": ", ".join(old_rcv), "new": ", ".join(new_rcv)})
        old_by = {m.receiver: m for m in reversed(self.old)}
        new_by = {m.receiver: m for m in reversed(self.new)}
        for rcv in sorted(set(old_by) & set(new_by), key=str):
            for key in _SETTINGS:
                a, b = getattr(old_by[rcv].opts, key), getattr(new_by[rcv].opts, key)
                if a != b:
                    out.append({"subject": rcv or "(none)", "setting": key,
                                "old": _show(a), "new": _show(b)})
        if not out:  # e.g. the same receiver twice, once with other settings
            out.append({"subject": "routes", "setting": "routes",
                        "old": ", ".join(m.path[-1] for m in self.old),
                        "new": ", ".join(m.path[-1] for m in self.new)})
        return out


@dataclass
class RoutingDiff:
    checked: int  # label sets probed
    truncated: bool  # MAX_LABEL_SETS was hit, so some combinations were not tried
    changes: List[RoutingChange]


def diff_routing(old: Router, new: Router, limit: int = MAX_LABEL_SETS) -> RoutingDiff:
    """Label sets routed differently by `new` than by `old`. Label sets with the same
    old → new outcome are folded into one change, shown with the smallest label set."""
    label_sets, truncated = probe_label_sets([old, new], limit)
    changes: Dict[tuple, RoutingChange] = {}
    for labels in label_sets:
        before, after = old.match(labels), new.match(labels)
        key = (_outcome(before), _outcome(after))
        if key[0] == key[1]:
            continue
        change = changes.get(key)
        if change is None:
            changes[key] = RoutingChange(labels, before, after)
            continue
        change.similar += 1
        if (len(labels), sorted(labels.items())) < (
            len(change.labels), sorted(change.labels.items())
        ):
            change.labels, change.old, change.new = labels, before, after
    return RoutingDiff(len(label_sets), truncated, list(changes.values()))
