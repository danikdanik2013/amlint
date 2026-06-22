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

import re


ERROR = "error"      # config is broken: alerts will be lost
WARN = "warn"        # almost certainly not what you intended
INFO = "info"        # suspicious, worth a look


class Finding:
    __slots__ = ("level", "code", "msg", "where")

    def __init__(self, level, code, msg, where=""):
        self.level = level
        self.code = code
        self.msg = msg
        self.where = where


def _defined_receivers(cfg):
    return {r.get("name") for r in cfg.get("receivers", []) if r.get("name")}


def _walk_routes(node, path="route"):
    """Yields (route_node, path) for the root and all nested routes."""
    yield node, path
    for i, child in enumerate(node.get("routes", []) or []):
        yield from _walk_routes(child, f"{path}.routes[{i}]")


# CHECK 1: route references a receiver that doesn't exist
def check_undefined_receivers(cfg):
    out = []
    defined = _defined_receivers(cfg)
    route = cfg.get("route")
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
    route = cfg.get("route")
    if route:
        for node, _ in _walk_routes(route):
            if node.get("receiver"):
                used.add(node["receiver"])
    for name in defined - used:
        out.append(Finding(
            INFO, "unused-receiver",
            f"Receiver '{name}' is defined but not referenced by any route.",
            "receivers",
        ))
    return out


# CHECK 3: inhibition rule that can never fire
def check_dead_inhibitions(cfg):
    """
    An inhibition rule silences target alerts when a source alert fires,
    but only when labels in 'equal' match. Missing 'equal' means the rule
    will silence across unrelated alerts — almost never intentional.
    """
    out = []
    for i, rule in enumerate(cfg.get("inhibit_rules", []) or []):
        where = f"inhibit_rules[{i}]"
        src = {**(rule.get("source_match") or {}), **(rule.get("source_matchers_map") or {})}
        tgt = {**(rule.get("target_match") or {}), **(rule.get("target_matchers_map") or {})}
        equal = rule.get("equal", []) or []

        if not equal and (src or tgt):
            out.append(Finding(
                WARN, "inhibit-no-equal",
                "Inhibition rule has no 'equal' field: it will silence alerts across "
                "unrelated firing sources (no shared label binding them). Almost always a mistake.",
                where,
            ))

        for key in set(src) & set(tgt):
            if src[key] == tgt[key]:
                out.append(Finding(
                    INFO, "inhibit-same-match",
                    f"source_match and target_match both have '{key}={src[key]}'. "
                    f"Check that the rule is not silencing its own source alert.",
                    where,
                ))
    return out


# CHECK 4: match_re / matchers: patterns that fail to compile
_MATCHER_RE = re.compile(r'^([^=!~]+)(=~|!~)(.+)$')


def _regex_patterns(node):
    """Yield (label, pattern) for all regex matchers in a route node."""
    for label, pattern in (node.get("match_re") or {}).items():
        yield label, pattern
    for m in node.get("matchers") or []:
        hit = _MATCHER_RE.match(str(m))
        if hit:
            yield hit.group(1).strip(), hit.group(3)


def check_bad_regex(cfg):
    out = []
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        for label, pattern in _regex_patterns(node):
            try:
                re.compile(pattern)
            except re.error as e:
                out.append(Finding(
                    ERROR, "bad-regex",
                    f"Regex for '{label}' does not compile: {e}",
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
    route = cfg.get("route")
    if not route:
        return out

    def scan_siblings(routes, parent_path):
        for i, child in enumerate(routes or []):
            path = f"{parent_path}.routes[{i}]"
            has_matcher = bool(
                child.get("match") or child.get("match_re") or child.get("matchers")
            )
            is_catch_all = not has_matcher
            cont = child.get("continue", False)
            if is_catch_all and not cont and i < len(routes) - 1:
                out.append(Finding(
                    WARN, "unreachable-route",
                    f"Catch-all route (no matchers) with continue:false will intercept all alerts "
                    f"— {len(routes) - i - 1} subsequent sibling(s) are unreachable.",
                    path,
                ))
            scan_siblings(child.get("routes"), path)

    scan_siblings(route.get("routes"), "route")
    return out


# CHECK 6: group_by with '...' mixed with explicit labels
def check_groupby(cfg):
    out = []
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        gb = node.get("group_by") or []
        if "..." in gb and len(gb) > 1:
            out.append(Finding(
                WARN, "groupby-ellipsis",
                "group_by contains '...' alongside other labels — '...' already groups by all "
                "labels, making the others redundant. Remove either '...' or the explicit labels.",
                path,
            ))
    return out


_DURATION_UNITS = {'ms': 0.001, 's': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
_DURATION_RE = re.compile(r'(\d+)(ms|[smhdw])')

def _parse_duration(s):
    if not s:
        return None
    total = sum(int(n) * _DURATION_UNITS[u] for n, u in _DURATION_RE.findall(str(s)))
    return total or None


# CHECK 7: duplicate receiver names
def check_duplicate_receivers(cfg):
    out = []
    seen = set()
    for r in cfg.get("receivers", []) or []:
        name = r.get("name")
        if not name:
            continue
        if name in seen:
            out.append(Finding(
                ERROR, "duplicate-receiver",
                f"Receiver '{name}' is defined more than once. "
                f"The second definition will be silently ignored.",
                "receivers",
            ))
        seen.add(name)
    return out


# CHECK 8: receiver with no integration configured
_INTEGRATION_KEYS = {
    "email_configs", "pagerduty_configs", "slack_configs", "opsgenie_configs",
    "victorops_configs", "webhook_configs", "wechat_configs", "sns_configs",
    "msteams_configs", "telegram_configs", "discord_configs",
}

def check_empty_receivers(cfg):
    out = []
    route = cfg.get("route")
    default_rcv = route.get("receiver") if route else None
    for r in cfg.get("receivers", []) or []:
        name = r.get("name")
        if not name:
            continue
        if not any(k in r for k in _INTEGRATION_KEYS):
            level = INFO if name == default_rcv else WARN
            out.append(Finding(
                level, "empty-receiver",
                f"Receiver '{name}' has no integration configured "
                f"(no slack_configs, pagerduty_configs, webhook_configs, etc.). "
                f"Alerts sent here will be silently dropped.",
                "receivers",
            ))
    return out


# CHECK 9: mute_time_intervals / active_time_intervals reference undefined interval
def check_undefined_time_intervals(cfg):
    out = []
    defined = {t.get("name") for t in cfg.get("time_intervals", []) or [] if t.get("name")}
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        for key in ("mute_time_intervals", "active_time_intervals"):
            for name in node.get(key) or []:
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
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        gi = _parse_duration(node.get("group_interval"))
        ri = _parse_duration(node.get("repeat_interval"))
        if gi and ri and ri < gi:
            out.append(Finding(
                WARN, "repeat-before-group",
                f"repeat_interval ({node['repeat_interval']}) is shorter than "
                f"group_interval ({node['group_interval']}). "
                f"Alertmanager will send repeats before the group has a chance to fire.",
                path,
            ))
    return out


# CHECK 11: two inhibition rules that silence each other
def check_circular_inhibition(cfg):
    out = []
    rules = cfg.get("inhibit_rules", []) or []
    for i, a in enumerate(rules):
        for j, b in enumerate(rules):
            if i >= j:
                continue
            src_a = {**(a.get("source_match") or {})}
            tgt_a = {**(a.get("target_match") or {})}
            src_b = {**(b.get("source_match") or {})}
            tgt_b = {**(b.get("target_match") or {})}
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
    route = cfg.get("route")
    if not route:
        return out

    def scan(routes, parent_path):
        if not routes:
            return
        for i, child in enumerate(routes or []):
            path = f"{parent_path}.routes[{i}]"
            if child.get("continue") is True and i == len(routes) - 1:
                out.append(Finding(
                    INFO, "useless-continue",
                    "continue:true on the last sibling route has no effect — "
                    "there are no subsequent routes to continue to.",
                    path,
                ))
            scan(child.get("routes"), path)

    scan(route.get("routes"), "route")
    return out


# CHECK 13: group_wait longer than group_interval
def check_group_wait(cfg):
    out = []
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        gw = _parse_duration(node.get("group_wait"))
        gi = _parse_duration(node.get("group_interval"))
        if gw and gi and gw > gi:
            out.append(Finding(
                WARN, "wait-exceeds-interval",
                f"group_wait ({node['group_wait']}) is longer than "
                f"group_interval ({node['group_interval']}). "
                f"Subsequent alerts will fire before the initial group has a chance to form.",
                path,
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
]


def lint(cfg):
    findings = []
    for check in ALL_CHECKS:
        findings.extend(check(cfg))
    order = {ERROR: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: order[f.level])
    return findings
