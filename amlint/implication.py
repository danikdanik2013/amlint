"""
Matcher implication, for shadowed-route: does every alert that matches one set of route
matchers also match another?

Each matcher allows a set of values of its label: `="v"` allows v, `!="v"` everything but
v, `=~"r"` the strings r matches in full and `!~"r"` the rest; a missing label is the
empty string, as in Alertmanager. Matchers combine with AND, so matchers L imply matchers
E when, for each label E constrains, the values L allows are all values E allows.

Per label that is a question about regular languages, decided on automata: a literal value
becomes a chain of characters, a regex an NFA built from Python's parse of the same RE2-exact
pattern that `amlint route` and `amlint test` evaluate (routing.compile_regex). L implies E
when no string is accepted by all of L's automata and rejected by E's, which a walk of their
product decides. Only "proven" counts: a regex construct this module does not model (case
folding, word boundaries, anchors anywhere but at the very start or end), a regex amlint
cannot evaluate, or an automaton too large to walk makes the answer False.
"""

import re
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

from .linter import Matcher, _is_catch_all
from .routing import RoutingError, _matcher_ok, _regex_examples, _sre_parse, compile_regex

_MAX_CHAR = 0x10FFFF
_MAX_STATES = 2000   # NFA states per matcher: .{0,1000} is past it
_MAX_PRODUCT = 5000  # product states walked before giving up

# Sorted, disjoint, inclusive code point ranges.
Charset = Tuple[Tuple[int, int], ...]
_ANY: Charset = ((0, _MAX_CHAR),)
_NOT_NEWLINE: Charset = ((0, 9), (11, _MAX_CHAR))


class _Unsupported(Exception):
    """A regex the automata do not model: nothing is proven about it."""


def _charset(ranges: Iterable[Tuple[int, int]]) -> Charset:
    merged: List[Tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return tuple(merged)


def _complement(cs: Charset) -> Charset:
    out, nxt = [], 0
    for lo, hi in cs:
        if lo > nxt:
            out.append((nxt, lo - 1))
        nxt = hi + 1
    if nxt <= _MAX_CHAR:
        out.append((nxt, _MAX_CHAR))
    return tuple(out)


class _NFA:
    """Thompson NFA over code points: state 0 starts, `final` is the only accepting state."""

    def __init__(self) -> None:
        self.eps: List[List[int]] = []
        self.moves: List[List[Tuple[Charset, int]]] = []
        self.final = 0

    def state(self) -> int:
        if len(self.eps) >= _MAX_STATES:
            raise _Unsupported("too many states")
        self.eps.append([])
        self.moves.append([])
        return len(self.eps) - 1

    def closure(self, states: Iterable[int]) -> FrozenSet[int]:
        seen = set(states)
        todo = list(seen)
        while todo:
            for t in self.eps[todo.pop()]:
                if t not in seen:
                    seen.add(t)
                    todo.append(t)
        return frozenset(seen)

    def step(self, states: FrozenSet[int], ch: int) -> FrozenSet[int]:
        return self.closure(
            t for s in states for cs, t in self.moves[s] if any(lo <= ch <= hi for lo, hi in cs)
        )


def _literal_nfa(value: str) -> _NFA:
    nfa = _NFA()
    cur = nfa.state()
    for ch in value:
        nxt = nfa.state()
        nfa.moves[cur].append((((ord(ch), ord(ch)),), nxt))
        cur = nxt
    nfa.final = cur
    return nfa


def _regex_nfa(pattern: str) -> _NFA:
    regex = compile_regex(pattern)  # RoutingError if amlint can't evaluate it
    nfa = _NFA()
    start = nfa.state()
    parsed = _sre_parse.parse(regex.pattern, regex.flags)
    nfa.final = _seq(nfa, parsed, start, regex.flags, lead=True, trail=True)
    return nfa


def _seq(nfa: _NFA, items, cur: int, flags: int, lead: bool, trail: bool) -> int:
    """Add a parsed sequence after state `cur`; returns the state it ends in. `lead` and
    `trail`: the sequence can only start at the start of the string, or must end at its end."""
    last = len(items) - 1
    for i, (op, av) in enumerate(items):
        cur = _item(nfa, op, av, cur, flags, lead and i == 0, trail and i == last)
    return cur


def _item(nfa: _NFA, op, av, cur: int, flags: int, lead: bool, trail: bool) -> int:
    P = _sre_parse
    if op in (P.LITERAL, P.NOT_LITERAL, P.ANY, P.IN):
        if flags & re.IGNORECASE:
            raise _Unsupported("case-insensitive matching")
        nxt = nfa.state()
        nfa.moves[cur].append((_item_charset(op, av, flags), nxt))
        return nxt
    if op == P.AT:
        # Every full match passes ^ / \A at the very start and $ / \Z at the very end;
        # anywhere else an anchor restricts the match, which isn't modelled.
        if (lead and av in (P.AT_BEGINNING, P.AT_BEGINNING_STRING)) or (
            trail and av in (P.AT_END, P.AT_END_STRING)
        ):
            return cur
        raise _Unsupported(f"anchor {av}")
    if op == P.SUBPATTERN:
        _, add_flags, del_flags, sub = av
        return _seq(nfa, sub, cur, (flags | add_flags) & ~del_flags, lead, trail)
    if op == P.BRANCH:
        end = nfa.state()
        for alt in av[1]:
            start = nfa.state()
            nfa.eps[cur].append(start)
            nfa.eps[_seq(nfa, alt, start, flags, lead, trail)].append(end)
        return end
    if op in (P.MAX_REPEAT, P.MIN_REPEAT):  # lazy or greedy: fullmatch accepts the same
        low, high, sub = av
        for _ in range(low):
            cur = _seq(nfa, sub, cur, flags, False, False)
        if high == P.MAXREPEAT:
            loop, end = nfa.state(), nfa.state()
            nfa.eps[cur].append(loop)
            nfa.eps[_seq(nfa, sub, loop, flags, False, False)].append(loop)
            nfa.eps[loop].append(end)
            return end
        for _ in range(high - low):
            end = nfa.state()
            nfa.eps[cur].append(end)
            nfa.eps[_seq(nfa, sub, cur, flags, False, False)].append(end)
            cur = end
        return cur
    raise _Unsupported(str(op))


def _item_charset(op, av, flags: int) -> Charset:
    P = _sre_parse
    if op == P.LITERAL:
        return ((av, av),)
    if op == P.NOT_LITERAL:
        return _complement(((av, av),))
    if op == P.ANY:
        return _ANY if flags & re.DOTALL else _NOT_NEWLINE
    ranges, negate = [], False
    for item_op, item in av:
        if item_op == P.NEGATE:
            negate = True
        elif item_op == P.LITERAL:
            ranges.append((item, item))
        elif item_op == P.RANGE:
            ranges.append(item)
        else:  # CATEGORY: compile_regex spells out RE2's ASCII classes, so not expected
            raise _Unsupported(str(item_op))
    cs = _charset(ranges)
    return _complement(cs) if negate else cs


@lru_cache(maxsize=None)
def _automaton(op: str, value: str) -> Optional[Tuple[_NFA, bool]]:
    """The values a matcher allows, as (NFA, negated): negated means the strings the NFA
    rejects. None if nothing can be proven about the matcher."""
    try:
        if op in ("=", "!="):
            return _literal_nfa(value), op == "!="
        if op in ("=~", "!~"):
            return _regex_nfa(value), op == "!~"
    except (_Unsupported, RoutingError):
        pass
    return None


def _cuts(autos: Sequence[Tuple[_NFA, bool]], sets: Sequence[FrozenSet[int]]) -> List[int]:
    """One character from each run of characters that every automaton treats alike."""
    cuts = {0}
    for (nfa, _), states in zip(autos, sets):
        for s in states:
            for cs, _ in nfa.moves[s]:
                for lo, hi in cs:
                    cuts.add(lo)
                    cuts.add(hi + 1)
    cuts.discard(_MAX_CHAR + 1)
    return sorted(cuts)


def _includes(premises: Sequence[Tuple[_NFA, bool]], conclusion: Tuple[_NFA, bool]) -> bool:
    """True if every string all premises accept is accepted by the conclusion: no reachable
    product state has the premises accepting and the conclusion rejecting."""
    autos = [*premises, conclusion]
    start = tuple(nfa.closure([0]) for nfa, _ in autos)
    seen = {start}
    todo = [start]
    while todo:
        sets = todo.pop()
        accepts = [(nfa.final in s) != negated for (nfa, negated), s in zip(autos, sets)]
        if all(accepts[:-1]) and not accepts[-1]:
            return False
        if any(not s and not negated for (_, negated), s in zip(premises, sets)):
            continue  # a premise rejects every longer string from here
        if not sets[-1] and conclusion[1]:
            continue  # the negated conclusion accepts every longer string from here
        for ch in _cuts(autos, sets):
            nxt = tuple(nfa.step(s, ch) for (nfa, _), s in zip(autos, sets))
            if nxt not in seen:
                if len(seen) >= _MAX_PRODUCT:
                    return False
                seen.add(nxt)
                todo.append(nxt)
    return True


def _allows(op: str, value: str, actual: str) -> bool:
    """Whether a matcher passes this label value, exactly as the Router decides it."""
    if op in ("=~", "!~"):
        try:
            return _matcher_ok(op, compile_regex(value), actual)
        except RoutingError:
            return False
    return op in ("=", "!=") and _matcher_ok(op, value, actual)


def _candidates(premises: FrozenSet[Tuple[str, str]]) -> Iterator[str]:
    """Likely counterexamples: the label missing, and values the premise regexes match."""
    yield ""
    for p_op, v in sorted(premises):
        if p_op == "=~":
            try:
                yield from _regex_examples(compile_regex(v))
            except RoutingError:
                pass


@lru_cache(maxsize=4096)
def _label_implies(premises: FrozenSet[Tuple[str, str]], op: str, value: str) -> bool:
    """True if every value of a label that passes all `premises` (op, value) passes
    `op value` too."""
    pinned = [v for p_op, v in premises if p_op == "="]
    if pinned:  # an = premise allows one value at most, so any one of them settles it
        return any(_allows(op, value, v) for v in pinned)
    if not premises and op in ("=", "!="):
        return False  # any value is allowed, and these each rule out at least one
    # a value that passes the premises but not the conclusion settles it without automata
    for actual in _candidates(premises):
        if all(_allows(p_op, v, actual) for p_op, v in premises) and not _allows(
            op, value, actual
        ):
            return False
    autos = [_automaton(p_op, v) for p_op, v in sorted(premises)]
    conclusion = _automaton(op, value)
    if conclusion is None or any(a is None for a in autos):
        return False
    return _includes(autos, conclusion)


def implies(later: Iterable[Matcher], earlier: Iterable[Matcher]) -> bool:
    """True if every alert that matches all `later` matchers provably matches all `earlier`
    ones. False if it doesn't, or if that can't be proven.

    An `earlier` matcher label=~".*" counts as matching every alert, as in unreachable-route
    (RE2's `.` skips newlines, but label values practically never contain one)."""
    by_label: Dict[str, List[Tuple[str, str]]] = {}
    for label, op, value in later:
        by_label.setdefault(label, []).append((op, value))
    for label, op, value in earlier:
        premises = by_label.get(label, [])
        if (op, value) in premises or _is_catch_all([(label, op, value)]):
            continue
        if op == "?" or not _label_implies(frozenset(premises), op, value):
            return False
    return True
