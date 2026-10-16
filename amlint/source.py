"""Source locations and inline suppressions for findings.

Checks report where a problem is as a logical path (`route.routes[1]`,
`receivers[pager].webhook_configs[0]`, `inhibit_rules[3]`). This module maps those
paths back to line:column in the YAML text, and reads `# amlint: disable=...` comments.
"""

import bisect
import re
from typing import Dict, List, Optional, Set, Tuple

import yaml
from yaml.constructor import SafeConstructor
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

Position = Tuple[int, int]  # 1-based (line, column)

_MERGE_TAG = "tag:yaml.org,2002:merge"
_STRING_STYLES = ('"', "'", "|", ">")  # the only scalars that can contain " #"


def _pos(node: Node) -> Position:
    return node.start_mark.line + 1, node.start_mark.column + 1


def _mapping_pairs(node: MappingNode) -> List[Tuple[ScalarNode, Node]]:
    """(key, value) node pairs with `<<` merge keys flattened the way safe_load does:
    a mapping's own keys override merged ones, earlier merge sources override later."""
    merged: List[Tuple[ScalarNode, Node]] = []
    own: List[Tuple[ScalarNode, Node]] = []
    for key, value in node.value:
        if key.tag == _MERGE_TAG:
            sources = value.value if isinstance(value, SequenceNode) else [value]
            for src in reversed(sources):
                if isinstance(src, MappingNode):
                    merged.extend(_mapping_pairs(src))
        elif isinstance(key, ScalarNode):
            own.append((key, value))
    return merged + own


def _name(node: Node) -> Optional[str]:
    """An entry's `name:` spelled the way checks spell it, from the loaded value:
    `name: true` is "True" and `name: 0x1F` is "31", as in f"receivers[{name}]"."""
    if isinstance(node, MappingNode):
        for key, value in _mapping_pairs(node):
            if key.value == "name" and isinstance(value, ScalarNode):
                return str(SafeConstructor().construct_object(value))
    return None


class SourceMap:
    """Logical path -> (line, column) for one YAML document.

    Mapping keys resolve to the key (`route` -> the `route:` line), list entries to
    the entry itself (`route.routes[1]` -> its `- ` line). Entries with a `name:` are
    reachable by name too (`receivers[pager]`), as the receiver checks spell them.
    """

    def __init__(self, text: str) -> None:
        self._build(yaml.compose(text, Loader=yaml.SafeLoader))

    @classmethod
    def of_node(cls, root: Optional[Node]) -> "SourceMap":
        """SourceMap of a document that is already composed, such as one document of a
        multi-document stream. Positions stay those of the whole stream."""
        smap = cls.__new__(cls)
        smap._build(root)
        return smap

    def _build(self, root: Optional[Node]) -> None:
        self._paths: Dict[str, Position] = {}
        self._strings: List[Tuple[int, int]] = []
        self._root: Position = _pos(root) if root is not None else (1, 1)
        if root is not None:
            self._index(root, "", "", set())
        self._strings.sort()
        self._string_starts = [start for start, _ in self._strings]

    def _add(self, path: str, named: str, pos: Position) -> None:
        self._paths[path] = pos
        if named != path:
            # a duplicated name keeps its first entry; index paths are never overridden
            self._paths.setdefault(named, pos)

    def _index(self, node: Node, path: str, named: str, seen: Set[int]) -> None:
        # Each node is walked once: an alias reused elsewhere resolves to the alias
        # site's parent instead, and a billion-laughs document stays linear.
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, MappingNode):
            for key, value in _mapping_pairs(node):
                self._index(key, "", "", seen)
                child = f"{path}.{key.value}" if path else key.value
                child_named = f"{named}.{key.value}" if named else key.value
                self._add(child, child_named, _pos(key))
                self._index(value, child, child_named, seen)
        elif isinstance(node, SequenceNode):
            for i, item in enumerate(node.value):
                name = _name(item)
                child = f"{path}[{i}]"
                child_named = f"{named}[{name if name is not None else i}]"
                self._add(child, child_named, _pos(item))
                self._index(item, child, child_named, seen)
        elif isinstance(node, ScalarNode) and node.style in _STRING_STYLES:
            self._strings.append((node.start_mark.index, node.end_mark.index))

    def locate(self, where: str) -> Position:
        """Position of `where`, or of its nearest parent that exists in the file."""
        if where in self._paths:
            return self._paths[where]
        for i in range(len(where) - 1, 0, -1):
            if where[i] in ".[" and where[:i] in self._paths:
                return self._paths[where[:i]]
        return self._root

    def in_string(self, index: int) -> bool:
        """Whether character `index` of the text is inside a quoted or block scalar."""
        i = bisect.bisect_right(self._string_starts, index) - 1
        return i >= 0 and index < self._strings[i][1]


_COMMENT_RE = re.compile(r"(?:^|(?<=\s))#\s*amlint\s*:")
_DIRECTIVE_RE = re.compile(
    r"\s*(disable-file|disable)\s*=\s*([A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*)"
)


class Suppressions:
    """`# amlint: disable=a,b` on a finding's line, or on a comment line just above it;
    `# amlint: disable-file=a,b` in the comment block at the top of the file."""

    def __init__(self) -> None:
        self.file_codes: Set[str] = set()
        self.line_codes: Dict[int, Set[str]] = {}
        self.comments: List[Tuple[int, List[str]]] = []  # (line, codes) of every directive
        self.problems: List[Tuple[int, str]] = []        # (line, why) for unusable comments

    def suppresses(self, code: str, line: Optional[int]) -> bool:
        return code in self.file_codes or code in self.line_codes.get(line, ())


def parse_suppressions(text: str, smap: SourceMap) -> Suppressions:
    sup = Suppressions()
    lines = text.splitlines(keepends=True)
    offsets, total = [], 0
    for line in lines:
        offsets.append(total)
        total += len(line)

    def is_comment_line(n: int) -> bool:
        body = lines[n].lstrip()
        return body.startswith("#") and not smap.in_string(offsets[n] + len(lines[n]) - len(body))

    in_header = True
    for n, line in enumerate(lines):
        body = line.strip()
        if body and not body.startswith(("#", "%", "---")):
            in_header = False
        for hit in _COMMENT_RE.finditer(line):
            if smap.in_string(offsets[n] + hit.start()):
                continue
            lineno = n + 1
            directive = _DIRECTIVE_RE.match(line, hit.end())
            if not directive:
                sup.problems.append((lineno, "unrecognized '# amlint:' comment, expected "
                                             "'disable=<codes>' or 'disable-file=<codes>'"))
                continue
            kind = directive.group(1)
            codes = [c.strip() for c in directive.group(2).split(",")]
            sup.comments.append((lineno, codes))
            if kind == "disable-file":
                if in_header:
                    sup.file_codes.update(codes)
                else:
                    sup.problems.append((lineno, "'disable-file' only works in the comment "
                                                 "block at the top of the file; ignored"))
                continue
            targets = [lineno]
            if is_comment_line(n):
                # a comment on its own line covers the next line of YAML below it
                below = n + 1
                while below < len(lines) and is_comment_line(below):
                    below += 1
                targets.append(below + 1)
            for target in targets:
                sup.line_codes.setdefault(target, set()).update(codes)
    return sup


def apply_source(findings: list, text: str) -> Tuple[list, Suppressions]:
    """Set line/col on every finding; drop those an `# amlint:` comment disables."""
    smap = SourceMap(text)
    for f in findings:
        f.line, f.col = smap.locate(f.where)
    sup = parse_suppressions(text, smap)
    return [f for f in findings if not sup.suppresses(f.code, f.line)], sup
