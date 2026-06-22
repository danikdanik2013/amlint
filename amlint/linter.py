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
import sys


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


# CHECK 4: match_re patterns that fail to compile
def check_bad_regex(cfg):
    out = []
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        mre = node.get("match_re") or {}
        for label, pattern in mre.items():
            try:
                re.compile(pattern)
            except re.error as e:
                out.append(Finding(
                    ERROR, "bad-regex",
                    f"match_re for '{label}' does not compile: {e}",
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


ALL_CHECKS = [
    check_undefined_receivers,
    check_unused_receivers,
    check_dead_inhibitions,
    check_bad_regex,
    check_unreachable_routes,
    check_groupby,
]


def lint(cfg):
    findings = []
    for check in ALL_CHECKS:
        findings.extend(check(cfg))
    order = {ERROR: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda f: order[f.level])
    return findings
