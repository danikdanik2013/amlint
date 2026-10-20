"""Rewrites for findings amlint can fix in the text itself, for `amlint fix` and the
editor's quick fixes.

A fix is a list of text edits. It is used only when the config it makes reads back the
same to Alertmanager: the same matchers on every route and inhibit rule, and everything
else equal (see _same). Anything this module can't rewrite safely, such as a mapping in
flow style or one built with a merge key, is left for a person.
"""

import copy
import re
from typing import List, Optional, Tuple

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .formats import FormatError, Loaded, load_input
from .linter import Finding, _matcher_nodes, _matchers, legacy_matchers

Edit = Tuple[int, int, str]  # replace text[start:end] with the string

FIXABLE = ("deprecated-match", "deprecated-mute-time-intervals")

# The formats whose config is a YAML mapping in the text itself, which edits can reach.
_FORMATS = ("alertmanager", "helm")
_MERGE_TAG = "tag:yaml.org,2002:merge"
_NULL_TAG = "tag:yaml.org,2002:null"
_STEP = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


# Reading the text

def _config_node(text: str, fmt: str) -> Optional[MappingNode]:
    """The mapping that holds the config, in a one-document input."""
    if fmt not in _FORMATS:
        return None
    try:
        nodes = [n for n in yaml.compose_all(text, Loader=yaml.SafeLoader) if n.tag != _NULL_TAG]
    except yaml.YAMLError:
        return None
    if len(nodes) != 1:
        return None
    node: Optional[Node] = nodes[0]
    if fmt == "helm":
        node = _value(_value(node, "alertmanager"), "config")
    return node if isinstance(node, MappingNode) else None


def _index(node: MappingNode, key: str) -> Optional[int]:
    """Position of `key` among the pairs of a mapping. None if it is missing, written more
    than once, or the mapping has a merge key that could supply it."""
    found = [i for i, (k, _) in enumerate(node.value)
             if isinstance(k, ScalarNode) and k.value == key]
    if len(found) != 1 or any(k.tag == _MERGE_TAG for k, _ in node.value):
        return None
    return found[0]


def _value(node: Optional[Node], key: str) -> Optional[Node]:
    if not isinstance(node, MappingNode):
        return None
    i = _index(node, key)
    return None if i is None else node.value[i][1]


def _node_at(root: Node, path: str) -> Optional[Node]:
    """The node at a path such as `route.routes[1]` or `inhibit_rules[0]`."""
    node: Optional[Node] = root
    for name, index in _STEP.findall(path):
        if name:
            node = _value(node, name)
        elif isinstance(node, SequenceNode) and int(index) < len(node.value):
            node = node.value[int(index)]
        else:
            return None
    return node


def _dict_at(cfg: dict, path: str) -> Optional[dict]:
    node = cfg
    for name, index in _STEP.findall(path):
        if name and isinstance(node, dict):
            node = node.get(name)
        elif not name and isinstance(node, list) and int(index) < len(node):
            node = node[int(index)]
        else:
            return None
    return node if isinstance(node, dict) else None


def _tail(node: Node) -> Node:
    """The node written last in the text of `node`."""
    while isinstance(node, (MappingNode, SequenceNode)) and not node.flow_style and node.value:
        last = node.value[-1]
        node = last[1] if isinstance(node, MappingNode) else last
    return node


def _end(text: str, node: Node) -> int:
    """Just after the last character of `node`, before any comment on that line."""
    tail = _tail(node)
    end = tail.end_mark.index
    # an empty value (`match:` and nothing) is marked where the next token starts
    empty = isinstance(tail, ScalarNode) and tail.value == "" and tail.style is None
    low = 0 if empty else tail.start_mark.index
    while end > low and text[end - 1] in " \t\r\n":
        end -= 1
    return end


def _line_start(text: str, i: int) -> int:
    return text.rfind("\n", 0, i) + 1


def _line_end(text: str, i: int) -> int:
    """Just after the line break that ends the line holding `i`."""
    j = text.find("\n", i)
    return len(text) if j < 0 else j + 1


def _starts_line(text: str, node: Node) -> bool:
    i = node.start_mark.index
    return not text[_line_start(text, i):i].strip()


# Writing it

def _item(entry: str, flow: bool) -> str:
    """A matchers: entry as a YAML list item, quoted only where it has to be."""
    try:
        plain = yaml.safe_load(f"[{entry}]" if flow else f"- {entry}") == [entry]
    except yaml.YAMLError:
        plain = False
    return entry if plain else "'" + entry.replace("'", "''") + "'"


def _comments(text: str, mapping: MappingNode, old: List[int]) -> Optional[List[str]]:
    """The comment at the end of each entry's line in the block mappings at positions `old`
    (as "  # ..." or ""), in the order legacy_matchers writes the entries."""
    out: List[str] = []
    for i in old:
        value = mapping.value[i][1]
        if not isinstance(value, MappingNode):
            continue
        for _, v in value.value:
            if value.flow_style:
                out.append("")
                continue
            end = _end(text, v)
            rest = text[end:_line_end(text, end)].strip()
            out.append(f"  {rest}" if rest.startswith("#") else "")
    return out


def _delete(text: str, mapping: MappingNode, drop: List[int]) -> Optional[List[Edit]]:
    """Edits that remove the pairs at positions `drop` of a block mapping."""
    edits: List[Edit] = []
    runs: List[List[int]] = []
    for i in sorted(drop):
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    for run in runs:
        first, last = mapping.value[run[0]][0], mapping.value[run[-1]][1]
        if _starts_line(text, first):
            start = _line_start(text, first.start_mark.index)
            edits.append((start, _line_end(text, _end(text, last)), ""))
        elif run[-1] + 1 < len(mapping.value):
            # the first key of a list item, after "- ": the next key moves up to its place
            edits.append((first.start_mark.index,
                          mapping.value[run[-1] + 1][0].start_mark.index, ""))
        else:
            return None
    return edits


def _matcher_edits(text: str, mapping: MappingNode, prefix: str,
                   entries: List[str]) -> Optional[List[Edit]]:
    """Edits that replace match: and match_re: (with `prefix`) by the matchers: `entries`."""
    if mapping.flow_style:
        return None
    old = [i for i, (k, _) in enumerate(mapping.value)
           if isinstance(k, ScalarNode) and k.value in (prefix + "match", prefix + "match_re")]
    if not old or any(_index(mapping, mapping.value[i][0].value) is None for i in old):
        return None
    new_key = prefix + "matchers"
    notes = _comments(text, mapping, old)
    if len(notes) != len(entries):
        notes = [""] * len(entries)
    has = [i for i, (k, _) in enumerate(mapping.value)
           if isinstance(k, ScalarNode) and k.value == new_key]
    edits: List[Edit] = []
    if has:
        if _index(mapping, new_key) is None:
            return None
        seq = mapping.value[has[0]][1]
        if not isinstance(seq, SequenceNode) or not seq.value or \
                not all(isinstance(it, ScalarNode) for it in seq.value):
            return None
        if seq.flow_style:
            items = [text[it.start_mark.index:it.end_mark.index] for it in seq.value]
            items += [_item(e, flow=True) for e in entries]
            edits.append((seq.start_mark.index, seq.end_mark.index, "[" + ", ".join(items) + "]"))
        else:
            if text[seq.start_mark.index:seq.start_mark.index + 1] != "-":
                return None
            at = _line_end(text, _end(text, seq))
            dash = " " * seq.start_mark.column
            lines = "".join(f"{dash}- {_item(e, flow=False)}{note}\n"
                            for e, note in zip(entries, notes))
            edits.append((at, at, lines if text[at - 1:at] == "\n" else "\n" + lines.rstrip()))
        drop = old
    else:
        key, value = mapping.value[old[0]]
        if isinstance(value, MappingNode) and value.flow_style:
            block = f"{new_key}: [" + ", ".join(_item(e, flow=True) for e in entries) + "]"
        else:
            indent = " " * (key.start_mark.column + 2)
            block = f"{new_key}:" + "".join(f"\n{indent}- {_item(e, flow=False)}{note}"
                                             for e, note in zip(entries, notes))
        if not entries:
            block = f"{new_key}: []"
        end = _end(text, value)
        if isinstance(value, MappingNode) and not value.flow_style:
            end = len(text[:_line_end(text, end)].rstrip("\n"))  # its comment moved up there
        edits.append((key.start_mark.index, end, block))
        drop = old[1:]
    deleted = _delete(text, mapping, drop) if drop else []
    return None if deleted is None else edits + deleted


def _rename_edits(text: str, root: MappingNode) -> Optional[List[Edit]]:
    i = _index(root, "mute_time_intervals")
    if i is None or any(isinstance(k, ScalarNode) and k.value == "time_intervals"
                        for k, _ in root.value):
        return None
    key = root.value[i][0]
    return [(key.start_mark.index, key.end_mark.index, "time_intervals")]


# Checking it

def _normal(cfg: dict) -> dict:
    """`cfg` with each route's and inhibit rule side's matchers as one sorted list, whatever
    keys they were written with, and the intervals under one key."""
    out = copy.deepcopy(cfg)
    sides = {(id(node), prefix): (node, prefix) for node, _, prefix in _matcher_nodes(out)}
    read = [(node, prefix, sorted(_matchers(node, prefix))) for node, prefix in sides.values()]
    for node, prefix, matchers in read:
        for key in ("match", "match_re", "matchers"):
            node.pop(prefix + key, None)
        node[prefix + " matchers"] = matchers
    if "mute_time_intervals" in out and "time_intervals" not in out:
        out["time_intervals"] = out.pop("mute_time_intervals")
    return out


def _apply(text: str, edits: List[Edit]) -> str:
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def _same(text: str, loaded: Loaded) -> bool:
    try:
        new = load_input(text, loaded.format)
    except FormatError:
        return False
    return _normal(new.cfg) == _normal(loaded.cfg)


def fix_edits(text: str, loaded: Loaded, code: str, where: str) -> Optional[List[Edit]]:
    """Edits to `text` (read as `loaded`) that fix the finding `code` at `where`, or None
    when amlint can't fix it safely."""
    root = _config_node(text, loaded.format)
    if root is None:
        return None
    edits: Optional[List[Edit]] = None
    if code == "deprecated-mute-time-intervals":
        edits = _rename_edits(text, root)
    elif code == "deprecated-match" and "." in where:
        path, key = where.rsplit(".", 1)
        prefix = key[:key.index("match")] if "match" in key else ""
        node, mapping = _dict_at(loaded.cfg, path), _node_at(root, path)
        entries = legacy_matchers(node, prefix) if node is not None else None
        if entries is not None and isinstance(mapping, MappingNode):
            edits = _matcher_edits(text, mapping, prefix, entries)
    if not edits or not _same(_apply(text, edits), loaded):
        return None
    return edits


def fix_text(text: str, loaded: Loaded,
             findings: List[Finding]) -> Tuple[str, List[Finding], List[Finding]]:
    """`text` with every finding in `findings` fixed that can be, one at a time.
    Returns (new text, fixed findings, findings left as they were)."""
    fixed: List[Finding] = []
    left: List[Finding] = []
    for f in findings:
        edits = fix_edits(text, loaded, f.code, f.where) if f.code in FIXABLE else None
        if edits is None:
            left.append(f)
            continue
        text = _apply(text, edits)
        loaded = load_input(text, loaded.format)
        fixed.append(f)
    return text, fixed, left
