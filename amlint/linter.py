#!/usr/bin/env python3
"""
amlint - semantic linter for Prometheus Alertmanager configs.

amtool check-config validates syntax. amlint validates SEMANTICS:
whether alerts will actually be delivered, whether inhibition rules
fire correctly, whether routing branches are reachable.

Usage:
    amlint check alertmanager.yml
    amlint check alertmanager.yml --format json
"""

import glob as _glob
import os
import re
import warnings
from typing import Dict, Generator, List, Optional, Set, Tuple

ERROR = "error"      # config is broken: alerts will be lost
WARN = "warn"        # almost certainly not what you intended
INFO = "info"        # suspicious, worth a look

# (label, operator, value), operator is one of = != =~ !~
Matcher = Tuple[str, str, str]


class Finding:
    __slots__ = ("level", "code", "msg", "where", "line", "col")

    def __init__(self, level: str, code: str, msg: str, where: str = "") -> None:
        self.level = level
        self.code = code
        self.msg = msg
        self.where = where
        # 1-based position of `where` in the source file, filled in by the CLI
        self.line: Optional[int] = None
        self.col: Optional[int] = None


def _dict_items(value) -> List[Tuple[int, dict]]:
    """(index, item) for the mapping entries of a YAML list; anything else is skipped."""
    if not isinstance(value, list):
        return []
    return [(i, v) for i, v in enumerate(value) if isinstance(v, dict)]


def _root_route(cfg: dict) -> Optional[dict]:
    route = cfg.get("route")
    return route if isinstance(route, dict) else None


def _receivers(cfg: dict) -> List[dict]:
    return [r for _, r in _dict_items(cfg.get("receivers"))]


def _defined_receivers(cfg: dict) -> Set[str]:
    return {r.get("name") for r in _receivers(cfg) if r.get("name")}


def _walk_routes(node: dict, path: str = "route") -> Generator[Tuple[dict, str], None, None]:
    """Yields (route_node, path) for the root and all nested routes."""
    yield node, path
    for i, child in _dict_items(node.get("routes")):
        yield from _walk_routes(child, f"{path}.routes[{i}]")


def route_cycle(route) -> Optional[Tuple[str, str]]:
    """(path, ancestor) when the route at `path` is the route at `ancestor` again, which
    contains it: a YAML alias of a route used inside that route. yaml.safe_load builds a
    recursive dict for it, and every walk over the tree would recurse forever, so the CLI
    rejects it before linting (Alertmanager can't load it either). A route reused in
    several places without nesting in itself is fine, and is walked once here."""
    if not isinstance(route, dict):
        return None
    walking: Dict[int, str] = {}  # id -> path of the routes from the root down to here
    done: Set[int] = set()

    def visit(node: dict, path: str) -> Optional[Tuple[str, str]]:
        walking[id(node)] = path
        for i, child in _dict_items(node.get("routes")):
            cpath = f"{path}.routes[{i}]"
            if id(child) in walking:
                return cpath, walking[id(child)]
            if id(child) not in done:
                found = visit(child, cpath)
                if found:
                    return found
        del walking[id(node)]
        done.add(id(node))
        return None

    return visit(route, "route")


# Matchers: match / match_re maps and matchers: lists all normalized to Matcher tuples,
# so checks compare `match: {sev: crit}` and `matchers: [sev="crit"]` as the same thing.
_MATCHER_RE = re.compile(r'^\s*("(?:[^"\\]|\\.)*"|[^=!~"\s]+)\s*(=~|!~|!=|=)\s*(.*?)\s*$', re.S)


def _scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _unquote(raw: str) -> str:
    """Strip quotes the way Alertmanager does: \\n, \\" and \\\\ are escapes, any other
    backslash is kept literally (so regex escapes like \\. survive)."""
    if not (len(raw) >= 2 and raw[0] == raw[-1] == '"'):
        return raw
    out, escaped = [], False
    for ch in raw[1:-1]:
        if escaped:
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(ch, "\\" + ch))
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def _split_matchers(line: str) -> List[str]:
    """One matchers: entry may hold several comma-separated matchers, optionally in {}."""
    line = line.strip()
    if line.startswith("{") and line.endswith("}"):
        line = line[1:-1]
    parts, buf, in_quotes, escaped = [], [], False, False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _parse_matchers(items) -> List[Matcher]:
    out: List[Matcher] = []
    if not isinstance(items, list):
        return out
    for item in items:
        for part in _split_matchers(_scalar(item)):
            hit = _MATCHER_RE.match(part)
            if hit:
                out.append((_unquote(hit.group(1)), hit.group(2), _unquote(hit.group(3))))
            else:
                # Unparseable, but still a matcher: keep it so the route isn't a catch-all.
                out.append((part, "?", ""))
    return out


def _matchers(node: dict, prefix: str = "") -> List[Matcher]:
    """All matchers of a route (prefix "") or of one side of an inhibit rule
    (prefix "source_" / "target_"), from match, match_re and matchers alike."""
    out: List[Matcher] = []
    for key, op in (("match", "="), ("match_re", "=~")):
        value = node.get(prefix + key)
        if isinstance(value, dict):
            out.extend((str(k), op, _scalar(v)) for k, v in value.items())
    out.extend(_parse_matchers(node.get(prefix + "matchers")))
    return out


def _equality(matchers: List[Matcher]) -> Dict[str, str]:
    return {label: value for label, op, value in matchers if op == "="}


# CHECK 1: route references a receiver that doesn't exist
def check_undefined_receivers(cfg):
    out = []
    defined = _defined_receivers(cfg)
    route = _root_route(cfg)
    if not route:
        out.append(Finding(ERROR, "no-root-route", "No root 'route' defined.", "route"))
        return out
    for node, path in _walk_routes(route):
        rcv = node.get("receiver")
        if rcv and rcv not in defined:
            out.append(Finding(
                ERROR, "undefined-receiver",
                f"Route references receiver '{rcv}' which is not defined in receivers. "
                f"Alerts matched here will be dropped.",
                path,
            ))
    return out


# CHECK 2: receiver is defined but never used in any route
def check_unused_receivers(cfg):
    out = []
    defined = _defined_receivers(cfg)
    used = set()
    route = _root_route(cfg)
    if route:
        for node, _ in _walk_routes(route):
            if node.get("receiver"):
                used.add(node["receiver"])
    for name in sorted(defined - used, key=str):
        out.append(Finding(
            INFO, "unused-receiver",
            f"Receiver '{name}' is defined but not referenced by any route.",
            f"receivers[{name}]",
        ))
    return out


# CHECK 3: inhibition rule that can never fire
def check_dead_inhibitions(cfg):
    """
    An inhibition rule silences target alerts when a source alert fires,
    but only when labels in 'equal' match. Missing 'equal' means the rule
    will silence across unrelated alerts — almost never intentional.

    A rule with target matchers but no source matchers is the exception: every
    alert counts as its source, so it mutes its targets whenever anything else
    fires. That is a deliberate "always mute" idiom (kube-prometheus uses it for
    InfoInhibitor), not a forgotten 'equal'.
    """
    out = []
    for i, rule in _dict_items(cfg.get("inhibit_rules")):
        where = f"inhibit_rules[{i}]"
        src = _matchers(rule, "source_")
        tgt = _matchers(rule, "target_")
        equal = rule.get("equal", []) or []

        if not equal and src:
            out.append(Finding(
                WARN, "inhibit-no-equal",
                "Inhibition rule has no 'equal' field: it will silence alerts across "
                "unrelated firing sources (no shared label binding them). Almost always a mistake.",
                where,
            ))

        src_eq, tgt_eq = _equality(src), _equality(tgt)
        for key in sorted(set(src_eq) & set(tgt_eq)):
            if src_eq[key] == tgt_eq[key]:
                out.append(Finding(
                    INFO, "inhibit-same-match",
                    f"Source and target matchers both have '{key}={src_eq[key]}'. "
                    f"Check that the rule is not silencing its own source alert.",
                    where,
                ))
    return out


# CHECK 4: regex matchers that Alertmanager (Go RE2) will reject
_GO_FLAGS_RE = re.compile(r"\(\?([a-zA-Z-]*)([:)])")
_REPEAT_RE = re.compile(r"\{(\d+)(?:,(\d*))?\}")
_MAX_REPEAT = 1000  # RE2 rejects larger repeat counts

# Exact equivalents of RE2's ASCII-only Perl classes and POSIX classes, for matching.
_PERL_CLASSES = {"d": "0-9", "w": "0-9A-Za-z_", "s": "\\t\\n\\f\\r "}
_POSIX_CLASSES = {
    "alnum": "0-9A-Za-z", "alpha": "A-Za-z", "ascii": "\\x00-\\x7f", "blank": "\\t ",
    "cntrl": "\\x00-\\x1f\\x7f", "digit": "0-9", "graph": "!-~", "lower": "a-z",
    "print": " -~", "punct": "!-/:-@\\[-`{-~", "space": "\\t\\n\\v\\f\\r ", "upper": "A-Z",
    "word": "0-9A-Za-z_", "xdigit": "0-9A-Fa-f",
}


def _python_flags(go_flags: str) -> str:
    """Go inline flags (imsU, optionally with -) as Python ones. U only swaps greedy
    and lazy, which never changes whether a full-string match exists, so it is dropped."""
    on, _, off = go_flags.replace("U", "").partition("-")
    return on + ("-" + off if off else "")


def _go_regex_to_python(pattern: str, exact: bool = False) -> Tuple[str, str]:
    """
    Translate a Go RE2 pattern into Python `re` syntax.

    Returns (python_pattern, unsupported). `unsupported` names the first construct
    Python accepts but RE2 rejects (lookarounds, backreferences, ...), or "" if none.

    By default the result is only meant to be compiled: Go-only syntax (\\pL, \\z, \\x{..},
    \\Q..\\E, [:alpha:], (?<name>), (?U)) is swapped for a Python stand-in and flags are
    dropped. With exact=True the result matches what Go matches: flags are kept as scoped
    groups, \\d \\w \\s and POSIX classes stay ASCII-only as in RE2, and constructs with
    no exact Python equivalent (\\pL, [:^alpha:]) are reported in `unsupported`.
    """
    out: List[str] = []
    i, n = 0, len(pattern)
    in_class = False
    after_quantifier = False
    # Python only allows flags at the very start, Go anywhere: a mid-pattern (?i) becomes
    # (?i: ... ) closed at the end of its group, one entry per open scope per group level.
    flag_scopes: List[List[str]] = [[]]
    while i < n:
        ch = pattern[i]
        nxt = pattern[i + 1:i + 2]
        quantifier = False

        if ch == "\\" and nxt:
            if nxt in "pP":
                if exact:
                    return "", "Unicode character classes (\\p{...})"
                end = pattern.find("}", i) + 1 if pattern[i + 2:i + 3] == "{" else i + 3
                out.append("\\w")
                i = end if end > 0 else n
            elif nxt == "Q":
                end = pattern.find("\\E", i + 2)
                stop = end if end >= 0 else n
                out.append(re.escape(pattern[i + 2:stop]))
                i = stop + 2 if end >= 0 else n
            elif nxt == "x" and pattern[i + 2:i + 3] == "{":
                end = pattern.find("}", i)
                digits = pattern[i + 3:end] if end > 0 else ""
                if not re.fullmatch(r"[0-9a-fA-F]{1,6}", digits) or int(digits, 16) > 0x10FFFF:
                    return "", "an invalid \\x{...} escape"
                out.append("\\U%08x" % int(digits, 16))
                i = end + 1
            elif nxt in "123456789":
                octal = re.match(r"[0-7]{1,3}", pattern[i + 1:])
                if nxt in "89" or not octal or len(octal.group(0)) < 2:
                    return "", "backreferences (\\%s)" % nxt
                out.append("\\x%02x" % (int(octal.group(0), 8) & 0xFF))
                i += 1 + len(octal.group(0))
            elif nxt in "ZuUN":
                return "", "the \\%s escape" % nxt
            elif exact and nxt.lower() in _PERL_CLASSES:
                chars = _PERL_CLASSES[nxt.lower()]
                if not in_class:
                    out.append(("[^%s]" if nxt.isupper() else "[%s]") % chars)
                elif nxt.islower():
                    out.append(chars)
                else:
                    return "", "negated classes (\\%s) inside [...]" % nxt
                i += 2
            else:
                out.append("\\Z" if nxt == "z" else "\\" + nxt)
                i += 2
            after_quantifier = False
            continue

        if in_class:
            if ch == "[" and nxt == ":":
                end = pattern.find(":]", i + 2)
                if end >= 0:
                    if exact:
                        name = pattern[i + 2:end]
                        if name not in _POSIX_CLASSES:
                            return "", "the [:%s:] class" % name
                        out.append(_POSIX_CLASSES[name])
                    else:
                        out.append("\\w")
                    i = end + 2
                    continue
            if ch == "]":
                in_class = False
            out.append(ch)
            i += 1
            continue

        if ch == "[":
            j = i + 1
            if pattern[j:j + 1] == "^":
                j += 1
            if pattern[j:j + 1] == "]":
                j += 1
            in_class = True
            out.append(pattern[i:j])
            i = j
            after_quantifier = False
            continue

        if ch == "(" and nxt == "?":
            ext = pattern[i + 2:i + 4]
            if ext[:1] in ("=", "!") or ext in ("<=", "<!"):
                return "", "lookahead/lookbehind assertions"
            if ext[:1] == ">":
                return "", "atomic groups (?>...)"
            if ext[:1] == "(":
                return "", "conditional groups (?(...)...)"
            if ext[:1] == "#":
                return "", "inline comments (?#...)"
            if ext in ("P=", "P>"):
                return "", "named backreferences (?P=name)"
            if ext[:1] == "<" or ext == "P<":
                # named group: Go accepts (?<name>) and (?P<name>), Python only the latter
                out.append("(?P<")
                flag_scopes.append([])
                i += 3 if ext[:1] == "<" else 4
                continue
            flags = _GO_FLAGS_RE.match(pattern, i)
            if flags:
                bad = sorted(set(flags.group(1)) - set("imsU-"))
                if bad:
                    return "", "the (?%s) flag" % "".join(bad)
                py_flags = _python_flags(flags.group(1)) if exact else ""
                if flags.group(2) == ":":
                    out.append("(?%s:" % py_flags)
                    flag_scopes.append([])
                elif py_flags:
                    out.append("(?%s:" % py_flags)
                    flag_scopes[-1].append("(?%s:" % py_flags)
                # Without exact, flags are dropped: they never change whether a pattern
                # compiles (Python lacks U and rejects global flags mid-pattern, Go allows both).
                i = flags.end()
                continue

        if ch == "(":
            flag_scopes.append([])
        elif ch == ")" and len(flag_scopes) > 1:
            out.append(")" * len(flag_scopes.pop()))
        elif ch == "|":
            # a flag scope must not swallow the alternation: a(?i)b|c is a(?i:b)|(?i:c)
            out.append(")" * len(flag_scopes[-1]) + "|" + "".join(flag_scopes[-1]))
            i += 1
            after_quantifier = False
            continue
        elif ch in "*+?":
            if ch == "+" and after_quantifier:
                return "", "possessive quantifiers (*+, ++, ?+)"
            # `?` right after a quantifier makes it lazy, which ends the quantifier
            quantifier = not (ch == "?" and after_quantifier)
        elif ch == "{":
            rep = _REPEAT_RE.match(pattern, i)
            if rep:
                if any(int(g) > _MAX_REPEAT for g in rep.groups() if g):
                    return "", f"a repeat count above {_MAX_REPEAT}"
                out.append(rep.group(0))
                i = rep.end()
                after_quantifier = True
                continue
            if exact:
                # Go reads a { that starts no valid repeat as a literal; Python may not ({,3})
                out.append("\\{")
                i += 1
                continue

        out.append(ch)
        after_quantifier = quantifier
        i += 1

    out.append(")" * len(flag_scopes[0]))
    return "".join(out), ""


def _regex_problem(pattern: str) -> Optional[str]:
    py_pattern, unsupported = _go_regex_to_python(pattern)
    if unsupported:
        return f"uses {unsupported}, which Go RE2 (Alertmanager's regex engine) does not support"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            re.compile(py_pattern)
    except re.error as e:
        return f"does not compile: {e.msg}"
    return None


def _regex_patterns(node: dict, prefix: str = ""):
    """Yield (label, pattern) for all regex matchers (=~ and !~) of a route or rule side."""
    for label, op, value in _matchers(node, prefix):
        if op in ("=~", "!~"):
            yield label, value


def check_bad_regex(cfg):
    out = []
    targets = []
    route = _root_route(cfg)
    if route:
        targets += [(node, path, "") for node, path in _walk_routes(route)]
    for i, rule in _dict_items(cfg.get("inhibit_rules")):
        for prefix in ("source_", "target_"):
            targets.append((rule, f"inhibit_rules[{i}]", prefix))
    for node, path, prefix in targets:
        for label, pattern in _regex_patterns(node, prefix):
            problem = _regex_problem(pattern)
            if problem:
                out.append(Finding(
                    ERROR, "bad-regex",
                    f"Regex for '{label}' {problem}. Alertmanager will reject this config.",
                    path,
                ))
    return out


# CHECK 5: unreachable sibling routes behind a catch-all
def check_unreachable_routes(cfg):
    """
    Alertmanager evaluates sibling routes in order. If a route matches
    and continue is not true, subsequent siblings won't receive the alert.
    A catch-all route (no matchers) with continue:false makes all following
    siblings unreachable.
    """
    out = []
    route = _root_route(cfg)
    if not route:
        return out

    def scan_siblings(node, parent_path):
        siblings = _dict_items(node.get("routes"))
        for pos, (i, child) in enumerate(siblings):
            path = f"{parent_path}.routes[{i}]"
            is_catch_all = not _matchers(child)
            cont = child.get("continue", False)
            remaining = len(siblings) - pos - 1
            if is_catch_all and not cont and remaining:
                out.append(Finding(
                    WARN, "unreachable-route",
                    f"Catch-all route (no matchers) with continue:false will intercept all alerts "
                    f"— {remaining} subsequent sibling(s) are unreachable.",
                    path,
                ))
            scan_siblings(child, path)

    scan_siblings(route, "route")
    return out


# CHECK 6: group_by with '...' mixed with explicit labels
def check_groupby(cfg):
    out = []
    route = _root_route(cfg)
    if not route:
        return out
    for node, path in _walk_routes(route):
        gb = node.get("group_by") or []
        if isinstance(gb, list) and "..." in gb and len(gb) > 1:
            out.append(Finding(
                WARN, "groupby-ellipsis",
                "group_by contains '...' alongside other labels — '...' already groups by all "
                "labels, making the others redundant. Remove either '...' or the explicit labels.",
                path,
            ))
    return out


# Prometheus duration syntax (model.ParseDuration): units in descending order, each once.
_DURATION_RE = re.compile(
    r'^(?:(\d+)y)?(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+)ms)?$'
)
_DURATION_UNITS = (365 * 86400, 604800, 86400, 3600, 60, 1, 0.001)


def _parse_duration(s):
    """Seconds for a Prometheus duration string, or None if unset or malformed."""
    if s is None:
        return None
    s = str(s).strip()
    if s == "0":
        return 0
    hit = _DURATION_RE.match(s)
    if not s or not hit:
        return None
    return sum(int(n) * unit for n, unit in zip(hit.groups(), _DURATION_UNITS) if n)


# Timing values a route inherits from its parent, and Alertmanager's defaults at the root.
_TIMING_DEFAULTS = {"group_wait": "30s", "group_interval": "5m", "repeat_interval": "4h"}


def _walk_timing(route: dict):
    """
    Yields (path, effective) for every route, where effective maps each timing key
    to (raw value, seconds, origin): origin is the path of the route that set it,
    or None for Alertmanager's default. Unset values are inherited from the parent.
    """
    def walk(node, path, inherited):
        effective = dict(inherited)
        for key in _TIMING_DEFAULTS:
            seconds = _parse_duration(node.get(key))
            if seconds is not None:
                effective[key] = (node[key], seconds, path)
        yield path, effective
        for i, child in _dict_items(node.get("routes")):
            yield from walk(child, f"{path}.routes[{i}]", effective)

    defaults = {k: (v, _parse_duration(v), None) for k, v in _TIMING_DEFAULTS.items()}
    yield from walk(route, "route", defaults)


def _describe_timing(value, path: str) -> str:
    raw, _, origin = value
    if origin == path:
        return f"{raw}"
    if origin is None:
        return f"{raw}, Alertmanager default"
    return f"{raw}, inherited from {origin}"


# CHECK 7: duplicate receiver names
def check_duplicate_receivers(cfg):
    out = []
    seen = set()
    for i, r in _dict_items(cfg.get("receivers")):
        name = r.get("name")
        if not name:
            continue
        if name in seen:
            out.append(Finding(
                ERROR, "duplicate-receiver",
                f"Receiver '{name}' is defined more than once. "
                f"The second definition will be silently ignored.",
                f"receivers[{i}]",  # by index: by name would point at the first definition
            ))
        seen.add(name)
    return out


# CHECK 8: receiver with no integration configured
_INTEGRATION_KEYS = {
    "email_configs", "pagerduty_configs", "slack_configs", "opsgenie_configs",
    "victorops_configs", "webhook_configs", "wechat_configs", "sns_configs",
    "msteams_configs", "msteamsv2_configs", "telegram_configs", "discord_configs",
    "pushover_configs", "webex_configs", "jira_configs", "rocketchat_configs",
    "incidentio_configs",
}

# Names that say "drop these on purpose" (kube-prometheus routes Watchdog/InfoInhibitor to "null")
_BLACKHOLE_NAMES = {"null", "blackhole", "devnull", "dev-null", "discard", "drop"}

def check_empty_receivers(cfg):
    out = []
    route = _root_route(cfg)
    default_rcv = route.get("receiver") if route else None
    for r in _receivers(cfg):
        name = r.get("name")
        if not name:
            continue
        # an empty list (`webhook_configs: []`) delivers nothing either
        if not any(r.get(k) for k in _INTEGRATION_KEYS):
            intentional = name == default_rcv or str(name).lower() in _BLACKHOLE_NAMES
            level = INFO if intentional else WARN
            out.append(Finding(
                level, "empty-receiver",
                f"Receiver '{name}' has no integration configured "
                f"(no slack_configs, pagerduty_configs, webhook_configs, etc.). "
                f"Alerts sent here will be silently dropped.",
                f"receivers[{name}]",
            ))
    return out


# CHECK 9: mute_time_intervals / active_time_intervals reference undefined interval
def check_undefined_time_intervals(cfg):
    out = []
    # top-level mute_time_intervals: is the pre-0.24 spelling of time_intervals: and still works
    defined = {
        t.get("name")
        for key in ("time_intervals", "mute_time_intervals")
        for _, t in _dict_items(cfg.get(key))
        if t.get("name")
    }
    route = _root_route(cfg)
    if not route:
        return out
    for node, path in _walk_routes(route):
        for key in ("mute_time_intervals", "active_time_intervals"):
            names = node.get(key)
            for name in names if isinstance(names, list) else []:
                if name not in defined:
                    out.append(Finding(
                        ERROR, "undefined-time-interval",
                        f"{key} references '{name}' which is not defined in time_intervals. "
                        f"Alertmanager will reject this config at runtime.",
                        path,
                    ))
    return out


# CHECK 10: repeat_interval shorter than group_interval
def check_timing(cfg):
    out = []
    route = _root_route(cfg)
    if not route:
        return out
    for path, eff in _walk_timing(route):
        gi, ri = eff["group_interval"], eff["repeat_interval"]
        # report only where one of the two values is set, not on every inheriting child
        if ri[1] < gi[1] and path in (gi[2], ri[2]):
            out.append(Finding(
                WARN, "repeat-before-group",
                f"repeat_interval ({_describe_timing(ri, path)}) is shorter than "
                f"group_interval ({_describe_timing(gi, path)}). "
                f"Alertmanager will send repeats before the group has a chance to fire.",
                path,
            ))
    return out


# CHECK 11: two inhibition rules that silence each other
def check_circular_inhibition(cfg):
    out = []
    rules = [
        (i, _equality(_matchers(r, "source_")), _equality(_matchers(r, "target_")))
        for i, r in _dict_items(cfg.get("inhibit_rules"))
    ]
    for pos, (i, src_a, tgt_a) in enumerate(rules):
        for j, src_b, tgt_b in rules[pos + 1:]:
            a_can_silence_b = any(src_a.get(k) == tgt_b.get(k) for k in set(src_a) & set(tgt_b))
            b_can_silence_a = any(src_b.get(k) == tgt_a.get(k) for k in set(src_b) & set(tgt_a))
            if a_can_silence_b and b_can_silence_a:
                out.append(Finding(
                    WARN, "circular-inhibition",
                    f"inhibit_rules[{i}] and inhibit_rules[{j}] may silence each other — "
                    f"when both conditions fire simultaneously, neither alert will be delivered.",
                    f"inhibit_rules[{i}]",
                ))
    return out


# CHECK 12: continue:true on the last sibling (no effect)
def check_useless_continue(cfg):
    out = []
    route = _root_route(cfg)
    if not route:
        return out

    def scan(node, parent_path):
        siblings = _dict_items(node.get("routes"))
        for pos, (i, child) in enumerate(siblings):
            path = f"{parent_path}.routes[{i}]"
            if child.get("continue") is True and pos == len(siblings) - 1:
                out.append(Finding(
                    INFO, "useless-continue",
                    "continue:true on the last sibling route has no effect — "
                    "there are no subsequent routes to continue to.",
                    path,
                ))
            scan(child, path)

    scan(route, "route")
    return out


# CHECK 13: group_wait longer than group_interval
def check_group_wait(cfg):
    out = []
    route = _root_route(cfg)
    if not route:
        return out
    for path, eff in _walk_timing(route):
        gw, gi = eff["group_wait"], eff["group_interval"]
        if gw[1] > gi[1] and path in (gw[2], gi[2]):
            out.append(Finding(
                WARN, "wait-exceeds-interval",
                f"group_wait ({_describe_timing(gw, path)}) is longer than "
                f"group_interval ({_describe_timing(gi, path)}). "
                f"Subsequent alerts will fire before the initial group has a chance to form.",
                path,
            ))
    return out


# CHECK 14: route tree deeper than MAX_DEPTH levels
_MAX_ROUTE_DEPTH = 5

def check_deep_nesting(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    route = _root_route(cfg)
    if not route:
        return out

    def scan(node: dict, path: str, depth: int) -> None:
        if depth > _MAX_ROUTE_DEPTH:
            out.append(Finding(
                INFO, "deep-nesting",
                f"Route tree is {depth} levels deep (>{_MAX_ROUTE_DEPTH}). "
                f"Deeply nested routing is hard to reason about and maintain.",
                path,
            ))
            return
        for i, child in _dict_items(node.get("routes")):
            scan(child, f"{path}.routes[{i}]", depth + 1)

    scan(route, "route", 0)
    return out


# CHECK 15: email_configs without smtp_smarthost
def check_email_smarthost(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    global_smarthost = (cfg.get("global") or {}).get("smtp_smarthost")
    for r in _receivers(cfg):
        for i, ec in _dict_items(r.get("email_configs")):
            if not ec.get("smarthost") and not global_smarthost:
                out.append(Finding(
                    ERROR, "email-no-smarthost",
                    f"email_configs entry in receiver '{r.get('name')}' has no 'smarthost' "
                    f"and global.smtp_smarthost is not set. Emails cannot be sent.",
                    f"receivers[{r.get('name')}].email_configs[{i}]",
                ))
    return out


# CHECK 16: webhook_configs without url or url_file
def check_webhook_no_url(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    for r in _receivers(cfg):
        name = r.get("name")
        for i, wh in _dict_items(r.get("webhook_configs")):
            if not wh.get("url") and not wh.get("url_file"):
                out.append(Finding(
                    ERROR, "webhook-no-url",
                    f"webhook_configs[{i}] in receiver '{name}' has no 'url' or 'url_file'. "
                    f"Alerts sent here will fail to deliver.",
                    f"receivers[{name}].webhook_configs[{i}]",
                ))
    return out


# CHECK 17: pagerduty_configs without routing_key / service_key (or file variants)
def check_pagerduty_no_routing_key(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    for r in _receivers(cfg):
        for i, pd in _dict_items(r.get("pagerduty_configs")):
            has_key = any(pd.get(k) for k in (
                "routing_key", "routing_key_file", "service_key", "service_key_file"
            ))
            if not has_key:
                out.append(Finding(
                    ERROR, "pagerduty-no-routing-key",
                    f"pagerduty_configs[{i}] in receiver '{r.get('name')}' has no 'routing_key' or "
                    f"'routing_key_file'. PagerDuty alerts cannot be sent.",
                    f"receivers[{r.get('name')}].pagerduty_configs[{i}]",
                ))
    return out


# CHECK 18: slack_configs without api_url (and no global.slack_api_url)
def check_slack_no_api_url(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    g = cfg.get("global") or {}
    global_url = g.get("slack_api_url") or g.get("slack_api_url_file")
    for r in _receivers(cfg):
        for i, sl in _dict_items(r.get("slack_configs")):
            if not sl.get("api_url") and not sl.get("api_url_file") and not global_url:
                out.append(Finding(
                    ERROR, "slack-no-api-url",
                    f"slack_configs[{i}] in receiver '{r.get('name')}' has no 'api_url' or "
                    f"'api_url_file', and global.slack_api_url is not set. "
                    f"Slack notifications cannot be sent.",
                    f"receivers[{r.get('name')}].slack_configs[{i}]",
                ))
    return out


# CHECK 19: opsgenie_configs without api_key (no global fallback either)
def check_opsgenie_no_api_key(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    g = cfg.get("global") or {}
    global_key = g.get("opsgenie_api_key") or g.get("opsgenie_api_key_file")
    for r in _receivers(cfg):
        for i, og in _dict_items(r.get("opsgenie_configs")):
            if not og.get("api_key") and not og.get("api_key_file") and not global_key:
                out.append(Finding(
                    ERROR, "opsgenie-no-api-key",
                    f"opsgenie_configs[{i}] in receiver '{r.get('name')}' has no 'api_key' or "
                    f"'api_key_file', and global.opsgenie_api_key is not set. "
                    f"OpsGenie alerts cannot be sent.",
                    f"receivers[{r.get('name')}].opsgenie_configs[{i}]",
                ))
    return out


# CHECK 20: msteams_configs without webhook_url or webhook_url_file
def check_msteams_no_webhook_url(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    for r in _receivers(cfg):
        for i, ms in _dict_items(r.get("msteams_configs")):
            if not ms.get("webhook_url") and not ms.get("webhook_url_file"):
                out.append(Finding(
                    ERROR, "msteams-no-webhook-url",
                    f"msteams_configs[{i}] in receiver '{r.get('name')}' has no 'webhook_url' or "
                    f"'webhook_url_file'. MS Teams notifications cannot be sent.",
                    f"receivers[{r.get('name')}].msteams_configs[{i}]",
                ))
    return out


# CHECK 21: two sibling routes with identical matchers
def check_route_match_collision(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    route = _root_route(cfg)
    if not route:
        return out

    def scan(node, parent_path):
        seen: dict = {}
        for i, child in _dict_items(node.get("routes")):
            path = f"{parent_path}.routes[{i}]"
            # match:, match_re: and matchers: spellings of the same matcher compare equal
            key = frozenset(_matchers(child))
            if key:
                if key in seen:
                    prev = seen[key]
                    out.append(Finding(
                        WARN, "route-match-collision",
                        f"Matchers are identical to {prev} — this route will never "
                        f"receive alerts because the earlier sibling always matches first.",
                        path,
                    ))
                elif not child.get("continue"):
                    # with continue:true the alert still reaches later identical siblings
                    seen[key] = path
            scan(child, path)

    scan(route, "route")
    return out


# CHECK 22: templates: references files that do not exist on disk
def check_template_files(cfg: dict, basedir: str) -> List[Finding]:
    """
    Relative paths are resolved against the config file's directory, as Alertmanager
    does. Absolute paths (e.g. /etc/alertmanager/templates/*.tmpl) point into the
    Alertmanager host or container, not wherever amlint runs, so they aren't checked.
    Alertmanager itself starts fine when a path matches nothing; it just loads no
    templates from it, and notifications that use them fail to render.
    """
    out: List[Finding] = []
    templates = cfg.get("templates")
    for i, tpl in enumerate(templates if isinstance(templates, list) else []):
        if not isinstance(tpl, str) or os.path.isabs(tpl):
            continue
        if _glob.glob(os.path.join(basedir, tpl)):
            continue
        is_glob = any(c in tpl for c in "*?[")
        if is_glob:
            out.append(Finding(
                WARN, "template-file-missing",
                f"templates glob '{tpl}' matches no files (relative to the config file). "
                f"Alertmanager loads no templates from it, so notifications that use "
                f"templates defined there fail to render.",
                f"templates[{i}]",
            ))
        else:
            out.append(Finding(
                ERROR, "template-file-missing",
                f"Template file '{tpl}' does not exist (relative to the config file). "
                f"Alertmanager loads nothing from it, so notifications that use its "
                f"templates fail to render.",
                f"templates[{i}]",
            ))
    return out


# CHECK 23: global.resolve_timeout not set — Alertmanager default (5m) may surprise
def check_global_resolve_timeout(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    if not (cfg.get("global") or {}).get("resolve_timeout"):
        out.append(Finding(
            INFO, "global-resolve-timeout-missing",
            "global.resolve_timeout is not set. Alertmanager defaults to 5m — "
            "set it explicitly so the intent is clear and reviewable.",
            "global",
        ))
    return out


# CHECK 24: telegram_configs without bot_token / bot_token_file
def check_telegram_no_bot_token(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    for r in _receivers(cfg):
        for i, tg in _dict_items(r.get("telegram_configs")):
            if not tg.get("bot_token") and not tg.get("bot_token_file"):
                out.append(Finding(
                    ERROR, "telegram-no-bot-token",
                    f"telegram_configs[{i}] in receiver '{r.get('name')}' has no 'bot_token' or "
                    f"'bot_token_file'. Telegram messages cannot be sent.",
                    f"receivers[{r.get('name')}].telegram_configs[{i}]",
                ))
    return out


# CHECK 25: discord_configs without webhook_url / webhook_url_file
def check_discord_no_webhook_url(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    for r in _receivers(cfg):
        for i, dc in _dict_items(r.get("discord_configs")):
            if not dc.get("webhook_url") and not dc.get("webhook_url_file"):
                out.append(Finding(
                    ERROR, "discord-no-webhook-url",
                    f"discord_configs[{i}] in receiver '{r.get('name')}' has no 'webhook_url' or "
                    f"'webhook_url_file'. Discord notifications cannot be sent.",
                    f"receivers[{r.get('name')}].discord_configs[{i}]",
                ))
    return out


# CHECK 26: victorops_configs without api_key (no global fallback either)
def check_victorops_no_api_key(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    g = cfg.get("global") or {}
    global_key = g.get("victorops_api_key") or g.get("victorops_api_key_file")
    for r in _receivers(cfg):
        for i, vo in _dict_items(r.get("victorops_configs")):
            if not vo.get("api_key") and not vo.get("api_key_file") and not global_key:
                out.append(Finding(
                    ERROR, "victorops-no-api-key",
                    f"victorops_configs[{i}] in receiver '{r.get('name')}' has no 'api_key' or "
                    f"'api_key_file', and global.victorops_api_key is not set. "
                    f"VictorOps alerts cannot be sent.",
                    f"receivers[{r.get('name')}].victorops_configs[{i}]",
                ))
    return out


# CHECK 27: wechat_configs without corp_id (no global fallback either)
def check_wechat_no_corp_id(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    global_corp_id = (cfg.get("global") or {}).get("wechat_api_corp_id")
    for r in _receivers(cfg):
        for i, wc in _dict_items(r.get("wechat_configs")):
            if not wc.get("corp_id") and not global_corp_id:
                out.append(Finding(
                    ERROR, "wechat-no-corp-id",
                    f"wechat_configs[{i}] in receiver '{r.get('name')}' has no 'corp_id', and "
                    f"global.wechat_api_corp_id is not set. WeChat messages cannot be sent.",
                    f"receivers[{r.get('name')}].wechat_configs[{i}]",
                ))
    return out


# CHECK 28: sns_configs missing all of topic_arn / phone_number / target_arn
def check_sns_no_target(cfg: dict) -> List[Finding]:
    """
    SNS requires exactly one destination: a topic ARN, a phone number (SMS),
    or a mobile platform endpoint ARN. None set means AWS rejects the publish call.
    """
    out: List[Finding] = []
    for r in _receivers(cfg):
        for i, sns in _dict_items(r.get("sns_configs")):
            if not any(sns.get(k) for k in ("topic_arn", "phone_number", "target_arn")):
                out.append(Finding(
                    ERROR, "sns-no-target",
                    f"sns_configs[{i}] in receiver '{r.get('name')}' has none of 'topic_arn', "
                    f"'phone_number', or 'target_arn' set. Exactly one is required — "
                    f"AWS will reject the publish call.",
                    f"receivers[{r.get('name')}].sns_configs[{i}]",
                ))
    return out


# CHECK 29: root route breaks a rule Alertmanager enforces when loading the config
def check_root_route(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    route = _root_route(cfg)
    if not route:
        return out
    problems = []
    if not route.get("receiver"):
        problems.append("has no 'receiver' — the root route must name the default receiver")
    if _matchers(route):
        problems.append("has matchers — the root route must match every alert")
    if route.get("continue"):
        problems.append("sets 'continue' — the root route has no siblings to continue to")
    for key in ("mute_time_intervals", "active_time_intervals"):
        if route.get(key):
            problems.append(f"sets '{key}' — only child routes may use time intervals")
    for problem in problems:
        out.append(Finding(
            ERROR, "invalid-root-route",
            f"Root route {problem}. Alertmanager will refuse to load this config.",
            "route",
        ))
    return out


ALL_CHECKS = [
    check_undefined_receivers,
    check_unused_receivers,
    check_dead_inhibitions,
    check_bad_regex,
    check_unreachable_routes,
    check_groupby,
    check_duplicate_receivers,
    check_empty_receivers,
    check_undefined_time_intervals,
    check_timing,
    check_circular_inhibition,
    check_useless_continue,
    check_group_wait,
    check_deep_nesting,
    check_email_smarthost,
    check_webhook_no_url,
    check_pagerduty_no_routing_key,
    check_slack_no_api_url,
    check_opsgenie_no_api_key,
    check_msteams_no_webhook_url,
    check_route_match_collision,
    check_global_resolve_timeout,
    check_telegram_no_bot_token,
    check_discord_no_webhook_url,
    check_victorops_no_api_key,
    check_wechat_no_corp_id,
    check_sns_no_target,
    check_root_route,
]

_VALID_LEVELS = {ERROR, WARN, INFO}


def lint(cfg: dict, ignore=None, severity=None, only=None, basedir=None,
         rules=None) -> List[Finding]:
    """Run every check. `rules`, the alerting rules from amlint.rules.load_rules, adds the
    checks against Prometheus rules."""
    findings: List[Finding] = []
    for check in ALL_CHECKS:
        findings.extend(check(cfg))
    if basedir is not None:
        findings.extend(check_template_files(cfg, basedir))
    if rules is not None:
        from .rules import check_rules  # imported here: rules.py builds on this module
        findings.extend(check_rules(cfg, rules))
    if severity:
        for f in findings:
            if f.code in severity and severity[f.code] in _VALID_LEVELS:
                f.level = severity[f.code]
    order = {ERROR: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: order[f.level])
    if only:
        findings = [f for f in findings if f.code in only]
    if ignore:
        findings = [f for f in findings if f.code not in ignore]
    return findings
