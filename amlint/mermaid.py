"""
Mermaid flowcharts of the route tree: `amlint tree --format mermaid`, and the diagram of
changed routes in `amlint diff --format markdown`.

Everything that comes from a config is written with Mermaid entity codes where it could
change the chart (quotes, #, <, >, &, backticks), so a receiver name can't break the
diagram or put HTML into a rendered PR comment.
"""

import re
from typing import Dict, List, Optional, Tuple

from .linter import ERROR, INFO, WARN, _dict_items, _matchers, _parse_duration

MAX_NODES = 150         # tree --format mermaid
MAX_CHANGED_NODES = 40  # the diagram in a PR comment, which should stay readable
_MAX_LINE = 80          # characters per label line

_ESCAPE = set('"#<>&`')
_ICON = {ERROR: "✖", WARN: "⚠", INFO: "ℹ"}
_ORDER = {ERROR: 0, WARN: 1, INFO: 2}
_STYLES = {
    "error": "fill:#ffebe9,stroke:#cf222e,color:#1f2328",
    "warn": "fill:#fff8c5,stroke:#9a6700,color:#1f2328",
    "added": "fill:#dafbe1,stroke:#1a7f37,color:#1f2328",
    "changed": "fill:#fff8c5,stroke:#9a6700,color:#1f2328",
    "removed": "fill:#ffebe9,stroke:#cf222e,color:#1f2328,stroke-dasharray:5 3",
    "folded": "fill:#f6f8fa,stroke:#d0d7de,color:#57606a,stroke-dasharray:3 3",
}
# Route settings whose change matters to the alerts that reach the route
_SETTINGS = ("receiver", "continue", "group_by", "group_wait", "group_interval",
             "repeat_interval", "mute_time_intervals", "active_time_intervals")


def _escape(value) -> str:
    text = " ".join(str(value).split())
    if len(text) > _MAX_LINE:
        text = text[:_MAX_LINE - 1] + "…"
    out = []
    for ch in text:
        if ord(ch) < 32:
            continue
        out.append(f"#{ord(ch)};" if ch in _ESCAPE else ch)
    return "".join(out)


def _node(node_id: str, lines: List[str]) -> str:
    return f'    {node_id}["{"<br/>".join(_escape(line) for line in lines)}"]'


def _node_id(path: str, prefix: str = "r") -> str:
    """route.routes[2].routes[0] -> r_2_0; Mermaid ids can't contain dots or brackets."""
    return prefix + "".join(f"_{n}" for n in re.findall(r"\[(\d+)\]", path))


def _route_lines(node: dict, path: str) -> List[str]:
    parts = []
    for label, op, value in _matchers(node):
        parts.append(label if op == "?" else f'{label}{op}"{value}"')
    if parts:
        first = "{" + ", ".join(parts) + "}"
    else:
        first = "(root)" if path == "route" else "(catch-all)"
    receiver = node.get("receiver")
    lines = [first, f"→ {receiver}" if receiver else "→ (inherited)"]
    if node.get("continue"):
        lines.append("continue")
    return lines


def _class_lines(classes: Dict[str, List[str]]) -> List[str]:
    out = []
    for name, style in _STYLES.items():
        if classes.get(name):
            out.append(f"    classDef {name} {style}")
            out.append(f"    class {','.join(classes[name])} {name}")
    return out


def _count(node: dict, on_path: frozenset = frozenset()) -> int:
    """Routes below `node`."""
    on_path = on_path | {id(node)}
    return sum(1 + _count(child, on_path) for _, child in _dict_items(node.get("routes"))
               if id(child) not in on_path)


def route_tree(route: dict, findings=()) -> str:
    """The whole route tree, routes with findings marked like `amlint tree` marks them."""
    by_path: Dict[str, list] = {}
    for f in findings:
        by_path.setdefault(f.where, []).append(f)
    lines = ["flowchart LR"]
    classes: Dict[str, List[str]] = {}
    drawn = 0
    left_out = 0

    def visit(node: dict, path: str, parent: Optional[str], on_path: frozenset) -> None:
        nonlocal drawn, left_out
        if drawn >= MAX_NODES:
            left_out += 1 + _count(node, on_path)
            return
        drawn += 1
        node_id = _node_id(path)
        label = _route_lines(node, path)
        fs = by_path.get(path)
        if fs:
            worst = min(fs, key=lambda f: _ORDER[f.level])
            label.append(f"{_ICON[worst.level]} " + ", ".join(sorted({f.code for f in fs})))
            if worst.level in (ERROR, WARN):
                classes.setdefault("error" if worst.level == ERROR else "warn", []).append(
                    node_id)
        lines.append(_node(node_id, label))
        if parent:
            lines.append(f"    {parent} --> {node_id}")
        on_path = on_path | {id(node)}
        for i, child in _dict_items(node.get("routes")):
            if id(child) not in on_path:
                visit(child, f"{path}.routes[{i}]", node_id, on_path)

    visit(route, "route", None, frozenset())
    if left_out:
        lines.append(_node("more", [f"…and {left_out} more routes"]))
        classes.setdefault("folded", []).append("more")
    return "\n".join(lines + _class_lines(classes)) + "\n"


# ── changed routes ──────────────────────────────────────────────────

class _Change:
    """One route in the merged old/new tree."""

    def __init__(self, kind: str, node: dict, path: str, details=(), children=()):
        self.kind = kind          # same | changed | added | removed
        self.node = node          # the new route, or the old one when removed
        self.path = path          # its path in the new tree, or the old one when removed
        self.details = list(details)
        self.children = list(children)

    def interesting(self) -> bool:
        return self.kind != "same" or any(c.interesting() for c in self.children)


def _setting(value) -> str:
    if value is None:
        return "unset"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "[]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _key(node: dict) -> frozenset:
    return frozenset(_matchers(node))


def _same(key: str, old, new) -> bool:
    """Equal for routing: `continue: false` is the default, group_by order doesn't matter,
    and 5m is 300s."""
    if key == "continue":
        return bool(old) == bool(new)
    if key == "group_by" and isinstance(old, list) and isinstance(new, list):
        return sorted(map(str, old)) == sorted(map(str, new))
    if key in ("group_wait", "group_interval", "repeat_interval"):
        a, b = _parse_duration(old), _parse_duration(new)
        if a is not None and b is not None:
            return a == b
    return old == new


def _drawn(change: "_Change") -> int:
    """How many nodes drawing `change` adds: itself and its interesting routes below."""
    return 1 + sum(_drawn(c) for c in change.children if c.interesting())


def _added(node: dict, path: str, on_path: frozenset) -> _Change:
    on_path = on_path | {id(node)}
    return _Change("added", node, path, children=[
        _added(child, f"{path}.routes[{i}]", on_path)
        for i, child in _dict_items(node.get("routes")) if id(child) not in on_path])


def _compare(old: dict, new: dict, old_path: str, new_path: str, on_path: frozenset) -> _Change:
    """Routes are paired with the sibling that has the same matchers, in order, so a route
    inserted above others doesn't make every route below it look changed."""
    details = [f"{key}: {_setting(old.get(key))} → {_setting(new.get(key))}"
               for key in _SETTINGS if not _same(key, old.get(key), new.get(key))]
    on_path = on_path | {id(old), id(new)}
    unpaired: List[Tuple[int, dict]] = [
        (i, c) for i, c in _dict_items(old.get("routes")) if id(c) not in on_path]
    pairs, added = [], []
    for j, child in _dict_items(new.get("routes")):
        if id(child) in on_path:
            continue
        match = next((o for o in unpaired if _key(o[1]) == _key(child)), None)
        if match:
            unpaired.remove(match)
            pairs.append((match, (j, child)))
        else:
            added.append((j, child))

    children: Dict[int, _Change] = {}
    old_ranks = sorted(i for (i, _), _ in pairs)
    for rank, ((i, ochild), (j, nchild)) in enumerate(pairs):
        change = _compare(ochild, nchild, f"{old_path}.routes[{i}]",
                          f"{new_path}.routes[{j}]", on_path)
        if i != old_ranks[rank]:  # order among siblings matters: the first match wins
            change.details.insert(0, f"position: {i + 1} → {j + 1}")
        if change.details and change.kind == "same":
            change.kind = "changed"
        children[j] = change
    for j, child in added:
        children[j] = _added(child, f"{new_path}.routes[{j}]", on_path)
    ordered = [children[j] for j in sorted(children)]
    for i, child in unpaired:
        ordered.append(_Change("removed", child, f"{old_path}.routes[{i}]"))
    return _Change("changed" if details else "same", new, new_path, details, ordered)


def changed_routes(old_route, new_route) -> Optional[str]:
    """The routes that were added, removed or changed between two route trees, with the
    routes above them for context and their unchanged siblings folded. None when the
    trees route the same way."""
    if not isinstance(new_route, dict):
        return None
    old_route = old_route if isinstance(old_route, dict) else {}
    root = _compare(old_route, new_route, "route", "route", frozenset())
    if not root.interesting():
        return None

    lines = ["flowchart LR"]
    classes: Dict[str, List[str]] = {}
    drawn = 0
    left_out = 0

    def draw(change: _Change, parent: Optional[str]) -> None:
        nonlocal drawn, left_out
        if drawn >= MAX_CHANGED_NODES:
            left_out += _drawn(change)
            return
        drawn += 1
        prefix = "x" if change.kind == "removed" else "r"
        node_id = _node_id(change.path, prefix)
        label = _route_lines(change.node, change.path)
        if change.kind == "removed":
            below = _count(change.node)
            label.append("removed" + (f" with {below} routes below it" if below else ""))
        else:
            label += change.details
        if change.kind != "same":
            classes.setdefault(change.kind, []).append(node_id)
        lines.append(_node(node_id, label))
        if parent:
            arrow = "-.->" if change.kind == "removed" else "-->"
            lines.append(f"    {parent} {arrow} {node_id}")
        shown = [c for c in change.children if c.interesting()]
        quiet = [c for c in change.children if not c.interesting()]
        for child in shown:
            draw(child, node_id)
        if shown and quiet:  # fold the siblings of drawn routes, so it's clear they exist
            folded = f"{node_id}_same"
            noun = "route" if len(quiet) == 1 else "routes"
            lines.append(_node(folded, [f"{len(quiet)} unchanged {noun}"]))
            lines.append(f"    {node_id} -.- {folded}")
            classes.setdefault("folded", []).append(folded)

    draw(root, None)
    if left_out:
        lines.append(_node("more", [f"…and {left_out} more changed routes"]))
        classes.setdefault("folded", []).append("more")
    return "\n".join(lines + _class_lines(classes)) + "\n"
