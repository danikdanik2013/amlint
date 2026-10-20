"""
`amlint lsp`: a Language Server Protocol server over stdio, so editors show amlint's
findings as you type.

- Diagnostics: every finding in the open buffer (not the file on disk), at the line and
  column `amlint check` prints, with the check code linked to its docs. The config is read
  in any format `amlint check` reads (amlint.formats). YAML files that hold no Alertmanager
  config get no diagnostics: a plain document needs `route:` or `receivers:` at the top.
- Hover on a route: its matchers, receiver, grouping and timings with where each comes
  from, and an alert that reaches it, from the routing simulation behind `amlint route`.
- Quick fixes: disable a finding with an `# amlint: disable=<code>` comment on its line, or
  `# amlint: disable-file=<code>` at the top of the file.

Standard library only: the base protocol (JSON-RPC 2.0 messages framed by Content-Length
headers) is implemented here, and documents are synced in full.
"""

import difflib
import json
import os
import queue
import re
import sys
import threading
import traceback
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import yaml

from .config import ConfigError, load_project_config, tests_for_config
from .explains import EXPLAINS, docs_url
from .formats import FormatError, Loaded, apply_locations, load_input
from .linter import ERROR, INFO, WARN, _dict_items, _matchers, _scalar, _walk_routes, lint
from .routing import GROUP_BY_ALL, RouteOpts, Router, RoutingError, probe_label_sets
from .rules import CONFIG_SOURCE, RulesError, load_rules, project_rule_patterns, rule_files
from .source import _COMMENT_RE, _DIRECTIVE_RE

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("amlint")
except Exception:
    _VERSION = "unknown"  # running from a source checkout that isn't pip-installed

_SEVERITY = {ERROR: 1, WARN: 2, INFO: 3}
_HINT = 4
_FULL_SYNC = 1

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603
_SERVER_NOT_INITIALIZED = -32002
_REQUEST_FAILED = -32803

# Text that holds an Alertmanager config, for when amlint can't read it: then the error is
# worth showing, while a broken docker-compose.yml is not amlint's business.
_LOOKS_LIKE_CONFIG = re.compile(
    r"^(?:route|receivers|alertmanager)[ \t]*:"
    r"|^[ \t]*[\"']?(?:alertmanager_config|alertmanager\.ya?ml(?:\.gz)?)[\"']?[ \t]*:"
    r"|^kind:[ \t]*[\"']?AlertmanagerConfig\b",
    re.M,
)
_ROUTE_KEY = re.compile(r"[\"']?route[\"']?[ \t]*:")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_ROUTE_SETTINGS = (
    "receiver", "group_by", "group_wait", "group_interval", "repeat_interval",
    "mute_time_intervals", "active_time_intervals",
)


# ── Base protocol ─────────────────────────────────────────────────────

def read_message(stream) -> Optional[dict]:
    """The next message on a binary stream, or None at the end of the input. Raises
    ValueError for a message whose body isn't JSON."""
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            if length is not None:
                break
            continue
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    body = stream.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream, message: dict) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    stream.flush()


class _BadMessage:
    def __init__(self, why: str) -> None:
        self.why = why


def serve(stdin=None, stdout=None) -> int:
    """Run the server until the client sends `exit` or closes the input. Returns the exit
    code: 0 after a `shutdown` request, 1 otherwise, as the protocol asks."""
    if stdout is None:
        stdout = sys.stdout.buffer
        sys.stdout = sys.stderr  # a stray print must not corrupt the protocol stream
    stdin = stdin if stdin is not None else sys.stdin.buffer
    inbox: "queue.Queue" = queue.Queue()

    def reader() -> None:
        while True:
            try:
                message = read_message(stdin)
            except ValueError as e:
                inbox.put(_BadMessage(str(e)))
                continue
            inbox.put(message)
            if message is None:
                return

    threading.Thread(target=reader, name="amlint-lsp-reader", daemon=True).start()
    server = Server(stdout)
    while True:
        try:
            # Lint once the client has nothing more to say, so a burst of edits is linted once.
            message = inbox.get(block=not server.pending)
        except queue.Empty:
            server.publish_pending()
            continue
        if message is None:
            return 0 if server.shut_down else 1
        code = server.dispatch(message)
        if code is not None:
            return code


def main() -> None:
    """`amlint lsp`: serve on stdin/stdout, then end the process. It ends without the usual
    interpreter shutdown, which would stall on the reader thread still blocked on stdin."""
    code = serve()
    sys.stderr.flush()
    os._exit(code)


# ── Positions ─────────────────────────────────────────────────────────

def _lines(text: str) -> List[str]:
    return _LINE_BREAK.split(text)


def _uri_path(uri: str) -> Optional[str]:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = url2pathname(unquote(parsed.path)) if os.name == "nt" else unquote(parsed.path)
    if parsed.netloc and parsed.netloc != "localhost":
        path = f"//{parsed.netloc}{path}"
    return path


def _content_end(text: str, start: int) -> int:
    """Where the YAML on a line ends: before trailing blanks and a trailing comment."""
    end = len(text.rstrip())
    for hit in re.finditer(r"[ \t]#", text):
        if hit.start() >= start:
            before = text[start:hit.start()]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                return start + len(before.rstrip())
    return end


# ── Names: receivers and time intervals ──────────────────────────────

class _Occurrence:
    """A receiver or time interval name written in the document: where it's defined
    (`- name: x`) or used (`receiver: x`, `mute_time_intervals: [x]`). `start`/`end` are the
    columns of the name itself, inside any quotes."""

    def __init__(self, kind: str, role: str, name: str, line: int, start: int, end: int,
                 quote: str) -> None:
        self.kind, self.role, self.name = kind, role, name
        self.line, self.start, self.end, self.quote = line, start, end, quote


def _in_flow(text: str, col: int) -> bool:
    """Whether `col` of a line is inside a flow collection ([...] or {...}) opened on it."""
    depth, quote = 0, ""
    for ch in text[:col]:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth > 0


def _scalar_at(text: str, col: int) -> Optional[Tuple[int, int, str]]:
    """(start, end, quote) of the scalar that begins at `col`: its text inside any quotes."""
    if col >= len(text):
        return None
    quote = text[col]
    if quote in "\"'":
        i = col + 1
        while i < len(text):
            if quote == '"' and text[i] == "\\":
                i += 2
                continue
            if text[i] == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2  # '' is a quote inside a single-quoted string
                    continue
                return col + 1, i, quote
            i += 1
        return None
    stops = ",]}" if _in_flow(text, col) else ""
    end = col
    while end < len(text) and text[end] not in stops:
        if text[end] == "#" and end > col and text[end - 1] in " \t":
            break
        end += 1
    end = col + len(text[col:end].rstrip())
    return (col, end, "") if end > col else None


def _value_after_key(text: str, col: int) -> Optional[Tuple[int, int, str]]:
    """The scalar value of the `key: value` whose key starts at `col`."""
    colon = text.find(":", col)
    if colon < 0:
        return None
    start = colon + 1
    while start < len(text) and text[start] in " \t":
        start += 1
    return _scalar_at(text, start)


def _unescape(raw: str, quote: str) -> str:
    if quote == "'":
        return raw.replace("''", "'")
    if quote == '"':
        try:
            return json.loads(f'"{raw}"')
        except ValueError:
            return raw
    return raw


_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_./@+-]*\Z")
# YAML 1.1 words that yaml.v2 (Alertmanager) and PyYAML read as booleans or null
_YAML_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}


def _written(name: str, quote: str) -> str:
    """`name` as it should be written where the old name was, keeping its quotes."""
    if quote == "'":
        return name.replace("'", "''")
    if quote == '"':
        return json.dumps(name, ensure_ascii=False)[1:-1]
    if _PLAIN_NAME.match(name) and name.lower() not in _YAML_WORDS:
        return name
    return json.dumps(name, ensure_ascii=False)  # it needs quotes now


class _RequestFailed(Exception):
    """A request that can't be done, with a message for the user (LSP RequestFailed)."""


# ── Completion ────────────────────────────────────────────────────────

# what to complete, from the text left of the cursor; it works on a line still being typed
_INTERVAL_KEYS = {"mute_time_intervals", "active_time_intervals"}
_MATCHER_KEYS = {"matchers", "source_matchers", "target_matchers"}
_LABEL_LIST_KEYS = {"group_by", "equal"}
_MATCH_KEYS = {"match", "match_re", "source_match", "source_match_re", "target_match",
               "target_match_re"}
_RECEIVER_VALUE = re.compile(r"\s*(?:-\s+)?receiver\s*:\s*[\"']?([^\"'\s]*)\Z")
_FLOW_LIST = re.compile(r"\s*(?:-\s+)?([A-Za-z_]+)\s*:\s*\[(.*)\Z")
_BLOCK_ITEM = re.compile(r"(\s*)-\s+(.*)\Z")
_KEY_LINE = re.compile(r"\s*(?:-\s+)?([A-Za-z_]+)\s*:\s*(?:#.*)?\Z")
_MATCH_ENTRY = re.compile(r"(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[\"']?([^\"']*)\Z")
_MATCH_KEY = re.compile(r"(\s*)([A-Za-z_][A-Za-z0-9_]*)\Z")
_MATCHER_VALUE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:=~|!~|!=|=)\s*\"?([^\"]*)\Z")


def _parent_key(lines: List[str], line: int, indent: int) -> Optional[str]:
    """The key of the block holding `line`: the nearest line above with less indentation."""
    for n in range(line - 1, -1, -1):
        text = lines[n]
        body = text.strip()
        if not body or body.startswith("#"):
            continue
        if len(text) - len(text.lstrip(" ")) < indent or (
                len(text) - len(text.lstrip(" ")) == indent and not body.startswith("-")):
            hit = _KEY_LINE.match(text)
            return hit.group(1) if hit else None
    return None


def _item_context(key: str, item: str, col: int):
    """What to complete in one entry of a list under `key`, typed as `item` up to `col`."""
    text = item.lstrip()
    if text[:1] in "\"'":
        text = text[1:]
    if key in _INTERVAL_KEYS:
        return ("interval", col - len(text), None)
    if key in _LABEL_LIST_KEYS:
        return ("label", col - len(text), None)
    if key in _MATCHER_KEYS:
        value = _MATCHER_VALUE.match(text)
        if value:
            return ("value", col - len(value.group(2)), value.group(1))
        if re.fullmatch(r"[A-Za-z0-9_]*", text):
            return ("label", col - len(text), None)
    return None


def _completion_context(lines: List[str], line: int, col: int):
    """(what, start column, label) to complete at the cursor, or None. `what` is receiver,
    interval, label or value (the value of `label`); start is where the typed part begins."""
    prefix = lines[line][:col]
    hit = _RECEIVER_VALUE.match(prefix)
    if hit:
        return ("receiver", col - len(hit.group(1)), None)
    hit = _FLOW_LIST.match(prefix)
    if hit:
        return _item_context(hit.group(1), hit.group(2).rsplit(",", 1)[-1], col)
    hit = _BLOCK_ITEM.match(prefix)
    if hit:
        key = _parent_key(lines, line, len(hit.group(1)))
        return _item_context(key, hit.group(2), col) if key else None
    hit = _MATCH_ENTRY.match(prefix) or _MATCH_KEY.match(prefix)
    if hit and _parent_key(lines, line, len(hit.group(1))) in _MATCH_KEYS:
        if hit.re is _MATCH_KEY:
            return ("label", col - len(hit.group(2)), None)
        return ("value", col - len(hit.group(3)), hit.group(2))
    return None


def _label_values(cfg: dict, alerts) -> Dict[str, Dict[str, str]]:
    """label -> {value: where it comes from}, from the config's matchers and the rules."""
    found: Dict[str, Dict[str, str]] = {}

    def note(label: str, value: Optional[str], source: str) -> None:
        values = found.setdefault(label, {})
        if value is not None:
            values.setdefault(value, source)

    root = cfg.get("route")
    nodes = [(n, "") for n, _ in _walk_routes(root)] if isinstance(root, dict) else []
    for _, rule in _dict_items(cfg.get("inhibit_rules")):
        nodes += [(rule, "source_"), (rule, "target_")]
        for label in rule.get("equal") or []:
            note(str(label), None, "config")
    for node, prefix in nodes:
        for label, op, value in _matchers(node, prefix):
            if op != "?":
                note(label, value if op in ("=", "!=") and value else None, "config")
        if not prefix:
            for label in node.get("group_by") or []:
                if label != "...":
                    note(str(label), None, "config")
    for alert in alerts or []:
        for label, value in alert.labels.items():
            note(label, value, "rules")
        for label in alert.unknown:
            note(label, None, "rules")
    return found


# ── Documents ─────────────────────────────────────────────────────────

class _Probes:
    """Alerts the routing simulation tries (the label sets `amlint diff` probes with), and
    for each route the smallest one that ends there and that goes through it."""

    def __init__(self, router: Router) -> None:
        label_sets, self.truncated = probe_label_sets([router])
        self.count = len(label_sets)
        self.ends: Dict[str, tuple] = {}
        self.reaches: Dict[str, tuple] = {}
        for labels in label_sets:
            matches = router.match(labels)
            key = (len(labels), sorted(labels.items()))
            for m in matches:
                self._keep(self.ends, m.path[-1], key, labels, matches)
                for path in m.path:
                    self._keep(self.reaches, path, key, labels, matches)

    @staticmethod
    def _keep(table: dict, path: str, key, labels, matches) -> None:
        if path not in table or key < table[path][0]:
            table[path] = (key, labels, matches)


class _Analysis:
    """What amlint makes of one version of a document."""

    def __init__(self, text: str) -> None:
        self.lines = _lines(text)
        self.diagnostics: List[dict] = []
        self.loaded: Optional[Loaded] = None
        self._routes: Optional[List[Tuple[int, int, str, dict]]] = None
        self._router = None
        self._probes: Optional[_Probes] = None
        self._occurrences: Optional[List[_Occurrence]] = None

    def occurrences(self) -> List[_Occurrence]:
        """Every receiver and time interval name written in the document. A name is kept
        only where the text at its place spells the name the config has, so a format that
        renames things (AlertmanagerConfig prefixes receiver names) gets no wrong edits."""
        if self._occurrences is None:
            found: Dict[Tuple[int, int], _Occurrence] = {}
            cfg = self.loaded.cfg if self.loaded else {}

            def add(kind: str, role: str, name, where: str, keyed: bool) -> None:
                if not isinstance(name, str) or not name:
                    return
                line, col = (n - 1 for n in self.loaded.locate(where))
                if not 0 <= line < len(self.lines):
                    return
                text = self.lines[line]
                span = _value_after_key(text, col) if keyed else _scalar_at(text, col)
                if span is None or (line, span[0]) in found:
                    return
                start, end, quote = span
                if _unescape(text[start:end], quote) == name:
                    found[(line, start)] = _Occurrence(kind, role, name, line, start, end, quote)

            for i, receiver in _dict_items(cfg.get("receivers")):
                add("receiver", "definition", receiver.get("name"), f"receivers[{i}].name", True)
            for key in ("time_intervals", "mute_time_intervals"):
                for i, interval in _dict_items(cfg.get(key)):
                    add("time interval", "definition", interval.get("name"),
                        f"{key}[{i}].name", True)
            root = cfg.get("route")
            for node, path in _walk_routes(root) if isinstance(root, dict) else ():
                add("receiver", "reference", node.get("receiver"), f"{path}.receiver", True)
                for key in ("mute_time_intervals", "active_time_intervals"):
                    names = node.get(key)
                    for j, name in enumerate(names if isinstance(names, list) else []):
                        add("time interval", "reference", name, f"{path}.{key}[{j}]", False)
            self._occurrences = [found[k] for k in sorted(found)]
        return self._occurrences

    def occurrence_at(self, line: int, col: int) -> Optional[_Occurrence]:
        for occ in self.occurrences():
            if occ.line == line and occ.start <= col <= occ.end:
                return occ
        return None

    def routes(self) -> List[Tuple[int, int, str, dict]]:
        """(first line, last line, path, node) of each route written in the document, by
        the indentation of the lines below its start."""
        if self._routes is None:
            self._routes = []
            root = self.loaded.cfg.get("route") if self.loaded else None
            seen = set()
            for node, path in _walk_routes(root) if isinstance(root, dict) else ():
                line, col = self.loaded.locate(path)
                line, col = line - 1, col - 1
                if (line, col) in seen or not 0 <= line < len(self.lines):
                    continue  # not in the document, e.g. a route from the chart's defaults
                seen.add((line, col))
                text = self.lines[line]
                item = text[:col].rstrip().endswith("-")
                if not item and not _ROUTE_KEY.match(text, col):
                    continue
                self._routes.append((line, self._block_end(line, col if item else col + 1),
                                     path, node))
        return self._routes

    def _block_end(self, line: int, indent: int) -> int:
        """Last line of the block starting at `line` whose lines are indented `indent` or more."""
        end = line
        for n in range(line + 1, len(self.lines)):
            body = self.lines[n].strip()
            if not body or body.startswith("#"):
                continue
            if len(self.lines[n]) - len(self.lines[n].lstrip(" ")) < indent:
                break
            end = n
        return end

    def item_block(self, where: str, seen: set) -> Optional[Tuple[int, int, int]]:
        """(first line, start column, last line) of a list entry such as `receivers[2]`,
        if it is written in this document; `seen` drops entries that map to a place
        already used (a config that isn't all in the document maps them to one spot)."""
        line, col = self.loaded.locate(where)
        line, col = line - 1, col - 1
        if (line, col) in seen or not 0 <= line < len(self.lines):
            return None
        if not self.lines[line][:col].rstrip().endswith("-"):
            return None  # not a list entry here, e.g. the place of a key above it
        seen.add((line, col))
        return line, col, self._block_end(line, col)

    def route_at(self, line: int) -> Optional[Tuple[str, dict]]:
        """The innermost route whose block holds `line`."""
        found = None
        for first, last, path, node in self.routes():
            if first <= line <= last and (found is None or first >= found[0]):
                found = (first, path, node)
        return found and (found[1], found[2])

    def router(self):
        """The Router of the config, or why the routing can't be simulated."""
        if self._router is None:
            try:
                self._router = Router(self.loaded.cfg.get("route"))
            except RoutingError as e:
                self._router = str(e)
        return self._router

    def probes(self) -> _Probes:
        if self._probes is None:
            self._probes = _Probes(self.router())
        return self._probes


class _Document:
    def __init__(self, uri: str, text: str, version) -> None:
        self.uri = uri
        self.path = _uri_path(uri)
        self.text = text
        self.version = version
        self.analysis: Optional[_Analysis] = None
        self.last_loaded: Optional[Loaded] = None  # of the last version amlint could read


# ── Hover text ────────────────────────────────────────────────────────

def _code(text) -> str:
    text = str(text)
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def _cell(text) -> str:
    """Inline code inside a Markdown table cell."""
    return _code(str(text).replace("|", "\\|"))


def _matcher_texts(node: dict) -> List[str]:
    """A route's matchers as written, the way `amlint tree` shows them."""
    parts = []
    for key, op in (("match", "="), ("match_re", "=~")):
        value = node.get(key)
        if isinstance(value, dict):
            parts += [f"{k}{op}{v}" for k, v in value.items()]
    value = node.get("matchers")
    if isinstance(value, list):
        parts += [str(m) for m in value]
    elif isinstance(value, str):
        parts.append(value)
    return parts


def _trail(path: str) -> List[str]:
    """`route.routes[1].routes[0]` and the routes above it, from the root."""
    return [path[:hit.end()] for hit in re.finditer(r"route(?=\.|$)|\.routes\[\d+\]", path)]


def _setting_rows(opts: RouteOpts, here: str) -> List[Tuple[str, str, str]]:
    """(setting, value, where it comes from), worded as `amlint route` prints them."""
    rows = []
    for key in _ROUTE_SETTINGS:
        value = getattr(opts, key)
        if key.endswith("_time_intervals") and not value:
            continue
        if key == "group_by" and value == (GROUP_BY_ALL,):
            shown = "... (every label)"
        elif key == "group_by":
            shown = ", ".join(value) if value else "(none — one group for all alerts)"
        elif isinstance(value, tuple):
            shown = ", ".join(value)
        else:
            shown = value or "(none)"
        origin = opts.origin.get(key)
        if origin is None:
            source = "Alertmanager default"
        elif origin == here:
            source = "set here"
        else:
            source = f"inherited from {_code(origin)}"
        rows.append((key, shown, source))
    return rows


def _labels_text(labels: dict) -> str:
    if not labels:
        return "an alert with no labels"
    return "an alert with " + _code(", ".join(f"{k}={v}" for k, v in labels.items()))


def _route_hover(analysis: _Analysis, path: str, node: dict) -> str:
    out = [f"**Route {_code(path)}** · amlint", ""]
    matchers = _matcher_texts(node)
    if path == "route":
        out.append("The root route: every alert starts here.")
    elif matchers:
        out.append("Matchers: " + ", ".join(_code(m) for m in matchers))
    else:
        out.append("No matchers: it takes every alert that reaches it.")
    if node.get("continue") is True:
        out += ["", "`continue: true`: alerts it takes also go on to the routes after it."]

    router = analysis.router()
    if isinstance(router, str):
        out += ["", f"amlint can't simulate this route tree: {router}"]
        return "\n".join(out)

    out += ["", "| setting | value | from |", "| --- | --- | --- |"]
    for key, shown, source in _setting_rows(router.opts(path), path):
        out.append(f"| {key} | {_cell(shown)} | {source} |")

    trail = _trail(path)
    if len(trail) > 1:
        nodes = {p: n for n, p in _walk_routes(analysis.loaded.cfg["route"])}
        out += ["", "**Alerts that reach it** match every route on the way, unless an earlier "
                    "sibling route without `continue: true` takes them first:"]
        for step in trail[1:]:
            shown = ", ".join(_code(m) for m in _matcher_texts(nodes[step])) or "no matchers"
            out.append(f"- {_code(step)}: {shown}")

    probes = analysis.probes()
    if path in probes.ends:
        _, labels, matches = probes.ends[path]
        receivers = [m.receiver for m in matches if m.receiver]
        to = ", ".join(_code(r) for r in receivers) if receivers else "no receiver"
        out += ["", f"For example, {_labels_text(labels)} ends here and goes to {to}."]
    elif path in probes.reaches:
        _, labels, matches = probes.reaches[path]
        stops = ", ".join(_code(m.path[-1]) for m in matches)
        out += ["", f"Alerts that reach it go on to a route below it. For example, "
                    f"{_labels_text(labels)} ends at {stops}."]
    else:
        limit = " (the most it tries)" if probes.truncated else ""
        out += ["", f"None of the {probes.count} alerts amlint tried{limit} reaches this route, "
                    f"so it may never get any. See `shadowed-route` and `unreachable-route`."]
    return "\n".join(out)


# ── Outline ───────────────────────────────────────────────────────────

# LSP SymbolKind values
_NAMESPACE, _PACKAGE, _CONSTANT, _OBJECT, _EVENT = 3, 4, 14, 19, 24


def _route_name(path: str, node: dict) -> str:
    if path == "route":
        return "route"
    return ", ".join(_matcher_texts(node)) or "(catch-all)"


def _route_detail(node: dict) -> str:
    receiver = node.get("receiver")
    detail = f"→ {receiver}" if isinstance(receiver, str) and receiver else "→ (inherited)"
    return detail + (" · continue" if node.get("continue") is True else "")


def _receiver_detail(receiver: dict) -> str:
    kinds = [k[:-len("_configs")] for k, v in receiver.items()
             if k.endswith("_configs") and v]
    return ", ".join(kinds) if kinds else "no integrations"


def _inhibit_name(rule: dict) -> str:
    def side(prefix: str) -> str:
        parts = [f"{label}{op}{value}" for label, op, value in _matchers(rule, prefix)]
        return ", ".join(parts) or "any alert"

    return f"{side('source_')} → {side('target_')}"


def _interval_detail(interval: dict) -> str:
    ranges = interval.get("time_intervals")
    n = len(ranges) if isinstance(ranges, list) else 0
    return f"{n} time range" + ("" if n == 1 else "s")


def _holds(outer: dict, inner: dict) -> bool:
    """Whether LSP range `outer` contains `inner`."""
    def key(pos: dict) -> tuple:
        return pos["line"], pos["character"]
    return key(outer["start"]) <= key(inner["start"]) and key(inner["end"]) <= key(outer["end"])


def _inhibit_detail(rule: dict) -> str:
    equal = rule.get("equal")
    if isinstance(equal, list) and equal:
        return "equal: " + ", ".join(str(e) for e in equal)
    return "no equal"


# ── The server ────────────────────────────────────────────────────────

class Server:
    """One client's session. Not thread-safe: serve() feeds it messages one at a time."""

    def __init__(self, out) -> None:
        self._out = out
        self.docs: Dict[str, _Document] = {}
        self.pending: Dict[str, None] = {}  # URIs to lint, in order
        self.initialized = False
        self.shut_down = False
        self.encoding = "utf-16"
        self.folders: List[str] = []
        # (folder, globs) -> (files with their mtime and size, the alerting rules in them)
        self._rules: Dict[tuple, tuple] = {}
        self._tests: Dict[str, tuple] = {}  # test file -> (mtime and size, label sets)
        self._requests = {
            "initialize": self._initialize,
            "shutdown": self._shutdown,
            "textDocument/hover": self._hover,
            "textDocument/codeAction": self._code_action,
            "textDocument/documentSymbol": self._document_symbol,
            "textDocument/definition": self._definition,
            "textDocument/references": self._references,
            "textDocument/prepareRename": self._prepare_rename,
            "textDocument/rename": self._rename,
            "textDocument/completion": self._completion,
            "textDocument/codeLens": self._code_lens,
        }
        self._notifications = {
            "textDocument/didOpen": self._did_open,
            "textDocument/didChange": self._did_change,
            "textDocument/didClose": self._did_close,
            "workspace/didChangeWatchedFiles": self._relint_all,
            "workspace/didChangeWorkspaceFolders": self._did_change_folders,
        }

    # messages

    def _send(self, message: dict) -> None:
        write_message(self._out, dict(message, jsonrpc="2.0"))

    def _error(self, msg_id, code: int, text: str) -> None:
        self._send({"id": msg_id, "error": {"code": code, "message": text}})

    def log(self, text: str, kind: int = 1) -> None:
        self._send({"method": "window/logMessage", "params": {"type": kind, "message": text}})

    def dispatch(self, message) -> Optional[int]:
        """Handle one message; returns the exit code once the client sends `exit`."""
        if isinstance(message, _BadMessage):
            self._error(None, _PARSE_ERROR, f"invalid JSON: {message.why}")
            return None
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and "method" not in message:
                return None  # a response: this server sends no requests
            self._error(None, _INVALID_REQUEST, "not a JSON-RPC request or notification")
            return None
        method, params = message["method"], message.get("params") or {}
        if method == "exit":
            return 0 if self.shut_down else 1
        if "id" not in message:
            handler = self._notifications.get(method)
            if handler is not None and self.initialized:
                try:
                    handler(params)
                except Exception:
                    self.log(f"amlint: {method} failed:\n{traceback.format_exc()}")
            return None
        msg_id = message["id"]
        if not self.initialized and method != "initialize":
            self._error(msg_id, _SERVER_NOT_INITIALIZED, "the server is not initialized yet")
        elif self.shut_down:
            self._error(msg_id, _INVALID_REQUEST, "the server is shut down")
        elif method not in self._requests:
            self._error(msg_id, _METHOD_NOT_FOUND, f"amlint does not handle {method}")
        else:
            try:
                result = self._requests[method](params)
            except _RequestFailed as e:
                self._error(msg_id, _REQUEST_FAILED, str(e))
            except Exception:
                trace = traceback.format_exc()
                self.log(f"amlint: {method} failed:\n{trace}")
                self._error(msg_id, _INTERNAL_ERROR, trace.strip().splitlines()[-1])
            else:
                self._send({"id": msg_id, "result": result})
        return None

    # lifecycle

    def _initialize(self, params: dict) -> dict:
        offered = ((params.get("capabilities") or {}).get("general") or {}).get(
            "positionEncodings") or []
        self.encoding = "utf-32" if "utf-32" in offered else "utf-16"
        self._set_folders(params.get("workspaceFolders") or [])
        if not self.folders:
            root = params.get("rootUri")
            path = _uri_path(root) if root else params.get("rootPath")
            self.folders = [path] if path else []
        self.initialized = True
        return {
            "capabilities": {
                "positionEncoding": self.encoding,
                "textDocumentSync": {"openClose": True, "change": _FULL_SYNC},
                "hoverProvider": True,
                "codeActionProvider": {"codeActionKinds": ["quickfix"]},
                "documentSymbolProvider": True,
                "definitionProvider": True,
                "referencesProvider": True,
                "renameProvider": {"prepareProvider": True},
                "completionProvider": {"triggerCharacters": ['"', "=", "[", ","]},
                "codeLensProvider": {"resolveProvider": False},
                "workspace": {"workspaceFolders": {"supported": True,
                                                   "changeNotifications": True}},
            },
            "serverInfo": {"name": "amlint", "version": _VERSION},
        }

    def _shutdown(self, params) -> None:
        self.shut_down = True
        return None

    def _set_folders(self, folders: list) -> None:
        paths = [_uri_path(f.get("uri", "")) for f in folders if isinstance(f, dict)]
        self.folders = [p for p in paths if p]

    def _did_change_folders(self, params: dict) -> None:
        event = params.get("event") or {}
        removed = {_uri_path(f.get("uri", "")) for f in event.get("removed") or []}
        added = [_uri_path(f.get("uri", "")) for f in event.get("added") or []]
        self.folders = [p for p in self.folders if p not in removed] + [p for p in added if p]
        self._relint_all(params)

    # documents

    def _did_open(self, params: dict) -> None:
        item = params["textDocument"]
        self.docs[item["uri"]] = _Document(item["uri"], item["text"], item.get("version"))
        self.pending[item["uri"]] = None

    def _did_change(self, params: dict) -> None:
        ident = params["textDocument"]
        doc = self.docs.get(ident["uri"])
        if doc is None:
            return
        for change in params.get("contentChanges") or []:
            if "range" in change:  # an incremental change, though the server asks for full ones
                start, end = (self._offset(doc.text, change["range"][k]) for k in ("start", "end"))
                doc.text = doc.text[:start] + change["text"] + doc.text[end:]
            else:
                doc.text = change["text"]
        doc.version = ident.get("version")
        doc.analysis = None
        self.pending[doc.uri] = None

    def _offset(self, text: str, pos: dict) -> int:
        """Index in `text` of an LSP position, for _did_change."""
        starts = [0] + [hit.end() for hit in _LINE_BREAK.finditer(text)]
        if pos["line"] >= len(starts):
            return len(text)
        start = starts[pos["line"]]
        brk = _LINE_BREAK.search(text, start)
        return start + self._col(text[start:brk.start() if brk else len(text)], pos["character"])

    def _did_close(self, params: dict) -> None:
        uri = params["textDocument"]["uri"]
        self.docs.pop(uri, None)
        self.pending.pop(uri, None)
        self._send({"method": "textDocument/publishDiagnostics",
                    "params": {"uri": uri, "diagnostics": []}})

    def _relint_all(self, params) -> None:
        """.amlint.yml or pyproject.toml may have changed."""
        for doc in self.docs.values():
            doc.analysis = None
            self.pending[doc.uri] = None

    def publish_pending(self) -> None:
        while self.pending:
            uri = next(iter(self.pending))
            del self.pending[uri]
            doc = self.docs.get(uri)
            if doc is not None:
                params = {"uri": uri, "diagnostics": self.analyze(doc).diagnostics}
                if doc.version is not None:
                    params["version"] = doc.version
                self._send({"method": "textDocument/publishDiagnostics", "params": params})

    # positions: amlint counts columns in characters; LSP in UTF-16 code units by default

    def _char(self, text: str, col: int) -> int:
        """LSP character offset of character `col` of a line."""
        if self.encoding == "utf-32":
            return col
        return col + sum(1 for ch in text[:col] if ord(ch) > 0xFFFF)

    def _col(self, text: str, char: int) -> int:
        """Character index in a line of an LSP character offset."""
        if self.encoding == "utf-32":
            return min(char, len(text))
        units = 0
        for i, ch in enumerate(text):
            if units >= char:
                return i
            units += 2 if ord(ch) > 0xFFFF else 1
        return len(text)

    def _range(self, lines: List[str], line: int, start: int, end: int) -> dict:
        text = lines[line] if 0 <= line < len(lines) else ""
        return {"start": {"line": line, "character": self._char(text, start)},
                "end": {"line": line, "character": self._char(text, end)}}

    def _line_range(self, lines: List[str], line: int, col: int = 0) -> dict:
        """From `col` (0-based, characters) to the end of the YAML on that line."""
        line = max(0, min(line, len(lines) - 1))
        text = lines[line]
        start = min(col, len(text))
        end = _content_end(text, start)
        if end <= start:
            start, end = 0, len(text.rstrip())
        return self._range(lines, line, start, end)

    def _comment_range(self, lines: List[str], line: int) -> dict:
        """The `# amlint:` comment on a line (0-based)."""
        text = lines[line] if 0 <= line < len(lines) else ""
        hit = re.search(r"#\s*amlint", text)
        return self._range(lines, line, hit.start() if hit else 0, len(text.rstrip()))

    # linting

    def _project(self, doc: _Document) -> Tuple[dict, str]:
        """The .amlint.yml (or pyproject.toml) of the workspace folder holding the document,
        else of the document's own directory; and that directory."""
        folder = os.getcwd()
        if doc.path:
            path = os.path.abspath(doc.path)
            inside = [f for f in self.folders
                      if path.startswith(os.path.join(os.path.abspath(f), ""))]
            folder = max(inside, key=len) if inside else os.path.dirname(path)
        try:
            proj = load_project_config(folder)
            return (proj if isinstance(proj, dict) else {}), folder
        except Exception as e:
            self.log(f"amlint: can't read the project config in {folder}: {e}", 2)
            return {}, folder

    def _alert_rules(self, proj: dict, folder: str):
        """The alerting rules that `rules:` in the project config points at, read again only
        when one of the files changes; or the reason they can't be read."""
        try:
            patterns = project_rule_patterns(proj)
            if not patterns:
                return None, None
            files = rule_files(patterns, base=folder, source=CONFIG_SOURCE)
            stamp = tuple((f, os.stat(f).st_mtime_ns, os.stat(f).st_size) for f in files)
            key = (folder, tuple(patterns))
            cached = self._rules.get(key)
            if cached is None or cached[0] != stamp:
                alerts, notes = load_rules(files)
                for note in notes:
                    self.log(f"amlint: {note}", 2)
                cached = self._rules[key] = (stamp, alerts or None)
            return cached[1], None
        except (RulesError, OSError) as e:
            return None, str(e)

    def analyze(self, doc: _Document) -> _Analysis:
        if doc.analysis is None:
            analysis = _Analysis(doc.text)
            try:
                self._lint(doc, analysis)
            except Exception:
                self.log(f"amlint: linting {doc.uri} failed:\n{traceback.format_exc()}")
                analysis.diagnostics, analysis.loaded = [], None
            doc.analysis = analysis
            if analysis.loaded is not None:
                doc.last_loaded = analysis.loaded
        return doc.analysis

    def _lint(self, doc: _Document, analysis: _Analysis) -> None:
        lines, diags = analysis.lines, analysis.diagnostics
        try:
            loaded = load_input(doc.text)
        except FormatError as e:
            if _LOOKS_LIKE_CONFIG.search(doc.text):
                diags.append(self._format_error(e, doc.text, lines))
            return
        if loaded.format == "alertmanager" and not {"route", "receivers"} & set(loaded.cfg):
            return  # some other YAML file
        analysis.loaded = loaded

        proj, folder = self._project(doc)
        severity = proj.get("severity")
        basedir = os.path.dirname(doc.path) if doc.path and loaded.templates is None else None
        alerts, rules_error = self._alert_rules(proj, folder)
        if rules_error:
            diags.append({"range": self._line_range(lines, 0), "severity": _SEVERITY[WARN],
                          "source": "amlint",
                          "message": f"Can't read the Prometheus rules: {rules_error}. The "
                                     f"checks against them are off."})
        findings = lint(loaded.cfg, ignore=set(proj.get("ignore") or []),
                        severity=severity if isinstance(severity, dict) else None,
                        basedir=basedir, rules=alerts, template_files=loaded.templates)
        for f in apply_locations(findings, loaded):
            diags.append({
                "range": self._line_range(lines, f.line - 1, f.col - 1),
                "severity": _SEVERITY[f.level],
                "code": f.code,
                "codeDescription": {"href": docs_url(f.code)},
                "source": "amlint",
                "message": f.msg,
                "data": {"where": f.where},  # for the quick fixes that rewrite the config
            })
        for line, codes in loaded.comments:
            for code in sorted(set(codes) - set(EXPLAINS)):
                hint = difflib.get_close_matches(code, list(EXPLAINS), n=1)
                diags.append({
                    "range": self._comment_range(lines, line - 1),
                    "severity": _SEVERITY[WARN],
                    "source": "amlint",
                    "message": f"Unknown check code '{code}' in this '# amlint:' comment"
                               + (f". Did you mean '{hint[0]}'?" if hint else "."),
                })
        for line, problem in loaded.problems:
            diags.append({"range": self._comment_range(lines, line - 1),
                          "severity": _SEVERITY[WARN], "source": "amlint", "message": problem})
        for line, note in loaded.notes:
            diags.append({"range": self._line_range(lines, (line or 1) - 1),
                          "severity": _HINT, "source": "amlint", "message": note})

    def _format_error(self, e: FormatError, text: str, lines: List[str]) -> dict:
        """A diagnostic for a config amlint can't read, at the error's position if known."""
        line, col, message = 0, 0, f"{e.title}: {e}"
        if e.title == "Failed to parse YAML":
            try:
                list(yaml.compose_all(text, Loader=yaml.SafeLoader))
            except yaml.YAMLError as err:
                mark = getattr(err, "problem_mark", None) or getattr(err, "context_mark", None)
                if mark is not None:
                    line, col = mark.line, mark.column
                problem, context = getattr(err, "problem", None), getattr(err, "context", None)
                if problem:
                    message = f"{e.title}: {problem}" + (f" ({context})" if context else "")
        if (line, col) == (0, 0):
            spots = re.findall(r"line (\d+), column (\d+)", str(e))
            if spots:
                line, col = int(spots[-1][0]) - 1, int(spots[-1][1]) - 1
            else:
                spot = re.search(r"\(line (\d+)\)", str(e))
                line = int(spot.group(1)) - 1 if spot else 0
        return {"range": self._line_range(lines, line, col), "severity": _SEVERITY[ERROR],
                "source": "amlint", "message": message}

    # requests

    def _hover(self, params: dict) -> Optional[dict]:
        doc = self.docs.get(params["textDocument"]["uri"])
        if doc is None:
            return None
        analysis = self.analyze(doc)
        line = params["position"]["line"]
        if analysis.loaded is None or not 0 <= line < len(analysis.lines):
            return None
        found = analysis.route_at(line)
        if found is None:
            return None
        return {"contents": {"kind": "markdown", "value": _route_hover(analysis, *found)}}

    def _code_action(self, params: dict) -> List[dict]:
        uri = params["textDocument"]["uri"]
        doc = self.docs.get(uri)
        context = params.get("context") or {}
        only = context.get("only")
        if doc is None or only and "quickfix" not in only:
            return []
        lines = _lines(doc.text)
        actions, done = [], set()
        for diag in context.get("diagnostics") or []:
            code = diag.get("code")
            line = (diag.get("range") or {}).get("start", {}).get("line")
            if diag.get("source") != "amlint" or code not in EXPLAINS or line is None:
                continue
            where = (diag.get("data") or {}).get("where")
            if isinstance(where, str) and (code, where) not in done:
                done.add((code, where))
                actions += self._fixes(uri, doc, diag, code, where)
            if (code, line) not in done and 0 <= line < len(lines):
                done.add((code, line))
                edit = self._disable_on_line(lines, line, code)
                if edit is not None:
                    actions.append(self._action(f"Disable {code} on this line", uri, diag, edit))
            if code not in done:
                done.add(code)
                edit = self._disable_in_file(doc.text, lines, code)
                if edit is not None:
                    actions.append(self._action(f"Disable {code} in this file", uri, diag, edit))
        return actions

    @staticmethod
    def _action(title: str, uri: str, diag: dict, edit: dict, preferred: bool = False) -> dict:
        action = {"title": title, "kind": "quickfix", "diagnostics": [diag],
                  "edit": {"changes": {uri: [edit]}}}
        if preferred:
            action["isPreferred"] = True
        return action

    def _fixes(self, uri: str, doc: _Document, diag: dict, code: str, where: str) -> List[dict]:
        """Quick fixes that change the config itself, for a finding at route `where`."""
        analysis = self.analyze(doc)
        if analysis.loaded is None:
            return []
        cfg, lines = analysis.loaded.cfg, analysis.lines
        if code in ("undefined-receiver", "undefined-time-interval"):
            receiver = code == "undefined-receiver"
            keys = ("receivers",) if receiver else ("time_intervals", "mute_time_intervals")
            defined = [str(i["name"]) for key in keys for _, i in _dict_items(cfg.get(key))
                       if i.get("name")]
            spots = ([f"{where}.receiver"] if receiver else
                     [f"{where}.{key}[{j}]"
                      for key in ("mute_time_intervals", "active_time_intervals")
                      for j in range(len(self._route_list(cfg, where, key)))])
            actions = []
            for spot in spots:
                line, col = (n - 1 for n in analysis.loaded.locate(spot))
                occ = next((o for o in analysis.occurrences()
                            if o.role == "reference" and o.line == line
                            and (receiver or o.start in (col, col + 1))  # after a quote
                            and o.name not in defined), None)
                if occ is None:
                    continue
                for n, name in enumerate(difflib.get_close_matches(occ.name, defined, n=3,
                                                                   cutoff=0.5)):
                    edit = {"range": self._range(lines, occ.line, occ.start, occ.end),
                            "newText": _written(name, occ.quote)}
                    actions.append(self._action(f"Change '{occ.name}' to '{name}'", uri, diag,
                                                edit, preferred=n == 0))
            return actions
        if code == "useless-continue":
            line = analysis.loaded.locate(f"{where}.continue")[0] - 1
            text = lines[line] if 0 <= line < len(lines) else ""
            if not re.fullmatch(r"\s*continue\s*:\s*\S+\s*(#.*)?", text):
                return []  # not a line of its own, e.g. `- continue: true` starts the route
            end = {"line": line + 1, "character": 0}
            if line + 1 >= len(lines):  # the last line: take the line break before it instead
                start = {"line": line - 1, "character": self._char(lines[line - 1],
                                                                   len(lines[line - 1]))}
                end = {"line": line, "character": self._char(text, len(text))}
            else:
                start = {"line": line, "character": 0}
            edit = {"range": {"start": start, "end": end}, "newText": ""}
            return [self._action("Remove continue: true (it does nothing here)", uri, diag,
                                 edit, preferred=True)]
        return []

    @staticmethod
    def _route_list(cfg: dict, where: str, key: str) -> list:
        """The `key` list of the route at path `where`, or []."""
        node = next((n for n, p in _walk_routes(cfg.get("route")) if p == where), None) \
            if isinstance(cfg.get("route"), dict) else None
        value = node.get(key) if node else None
        return value if isinstance(value, list) else []

    def _directive(self, text: str, kind: str):
        """The `# amlint: <kind>=...` directive on a line, if any."""
        for hit in _COMMENT_RE.finditer(text):
            directive = _DIRECTIVE_RE.match(text, hit.end())
            if directive and directive.group(1) == kind:
                return directive
        return None

    def _disable_on_line(self, lines: List[str], line: int, code: str) -> Optional[dict]:
        text = lines[line]
        directive = self._directive(text, "disable")
        if directive is not None:
            codes = [c.strip() for c in directive.group(2).split(",")]
            if code in codes:
                return None
            at = directive.end(2)
            return {"range": self._range(lines, line, at, at), "newText": f",{code}"}
        end = len(text.rstrip())
        return {"range": self._range(lines, line, end, len(text)),
                "newText": f"  # amlint: disable={code}"}

    def _disable_in_file(self, text: str, lines: List[str], code: str) -> Optional[dict]:
        for n, line in enumerate(lines):
            body = line.strip()
            if body and not body.startswith(("#", "%", "---")):
                break  # the end of the comment block at the top
            directive = self._directive(line, "disable-file")
            if directive is not None:
                if code in [c.strip() for c in directive.group(2).split(",")]:
                    return None
                at = directive.end(2)
                return {"range": self._range(lines, n, at, at), "newText": f",{code}"}
        newline = "\r\n" if "\r\n" in text else "\n"
        return {"range": self._range(lines, 0, 0, 0),
                "newText": f"# amlint: disable-file={code}{newline}"}

    # names: go to definition, find references, rename

    def _name_at(self, params: dict):
        """(uri, analysis, occurrence) for the name under the cursor, or None."""
        uri = params["textDocument"]["uri"]
        doc = self.docs.get(uri)
        if doc is None:
            return None
        analysis = self.analyze(doc)
        pos = params["position"]
        if analysis.loaded is None or not 0 <= pos["line"] < len(analysis.lines):
            return None
        col = self._col(analysis.lines[pos["line"]], pos["character"])
        occ = analysis.occurrence_at(pos["line"], col)
        return (uri, analysis, occ) if occ is not None else None

    @staticmethod
    def _same(analysis: _Analysis, occ: _Occurrence, role: Optional[str] = None):
        return [o for o in analysis.occurrences()
                if o.kind == occ.kind and o.name == occ.name and role in (None, o.role)]

    def _location(self, uri: str, lines: List[str], occ: _Occurrence) -> dict:
        return {"uri": uri, "range": self._range(lines, occ.line, occ.start, occ.end)}

    def _definition(self, params: dict) -> Optional[List[dict]]:
        found = self._name_at(params)
        if found is None:
            return None
        uri, analysis, occ = found
        defs = self._same(analysis, occ, "definition")
        return [self._location(uri, analysis.lines, o) for o in defs] or None

    def _references(self, params: dict) -> Optional[List[dict]]:
        found = self._name_at(params)
        if found is None:
            return None
        uri, analysis, occ = found
        declarations = (params.get("context") or {}).get("includeDeclaration", True)
        return [self._location(uri, analysis.lines, o) for o in self._same(analysis, occ)
                if declarations or o.role != "definition"]

    def _prepare_rename(self, params: dict) -> Optional[dict]:
        found = self._name_at(params)
        if found is None:
            return None
        _, analysis, occ = found
        return {"range": self._range(analysis.lines, occ.line, occ.start, occ.end),
                "placeholder": occ.name}

    def _rename(self, params: dict) -> Optional[dict]:
        found = self._name_at(params)
        if found is None:
            raise _RequestFailed("Put the cursor on a receiver or time interval name to rename it.")
        uri, analysis, occ = found
        new = params.get("newName")
        if not isinstance(new, str) or not new.strip() or "\n" in new or "\r" in new:
            raise _RequestFailed("The new name must be one line of text.")
        if new == occ.name:
            return {"changes": {uri: []}}
        cfg = analysis.loaded.cfg
        keys = ("receivers",) if occ.kind == "receiver" else ("time_intervals",
                                                              "mute_time_intervals")
        if any(item.get("name") == new for key in keys for _, item in _dict_items(cfg.get(key))):
            raise _RequestFailed(f"There is already a {occ.kind} called {new!r}.")
        edits = [{"range": self._range(analysis.lines, o.line, o.start, o.end),
                  "newText": _written(new, o.quote)} for o in self._same(analysis, occ)]
        return {"changes": {uri: edits}}

    # completion

    def _completion(self, params: dict) -> List[dict]:
        doc = self.docs.get(params["textDocument"]["uri"])
        if doc is None:
            return []
        lines = _lines(doc.text)
        pos = params["position"]
        if not 0 <= pos["line"] < len(lines):
            return []
        text = lines[pos["line"]]
        col = self._col(text, pos["character"])
        context = _completion_context(lines, pos["line"], col)
        if context is None:
            return []
        loaded = self.analyze(doc).loaded or doc.last_loaded
        if loaded is None and not _LOOKS_LIKE_CONFIG.search(doc.text):
            return []
        what, start, label = context
        cfg = loaded.cfg if loaded else {}
        names: Dict[str, str] = {}
        if what == "receiver":
            if loaded is not None and loaded.format == "alertmanagerconfig":
                return []  # the operator renames receivers: config names aren't what to type
            names = {str(r["name"]): "receiver" for _, r in _dict_items(cfg.get("receivers"))
                     if r.get("name")}
        elif what == "interval":
            names = {str(t["name"]): "time interval"
                     for key in ("time_intervals", "mute_time_intervals")
                     for _, t in _dict_items(cfg.get(key)) if t.get("name")}
        else:
            proj, folder = self._project(doc)
            alerts, _ = self._alert_rules(proj, folder)
            values = _label_values(cfg, alerts)
            if what == "label":
                in_config = _label_values(cfg, None)
                names = {k: "label" if k in in_config else "label · from rules" for k in values}
            else:
                typed = text[start:col]  # the matcher being typed is in the config already
                names = {v: f"{label} · {'in the rules' if s == 'rules' else 'in the config'}"
                         for v, s in (values.get(label) or {}).items()
                         if not (s == "config" and v == typed)}
        kind = {"receiver": 18, "interval": 21, "label": 5, "value": 12}[what]
        edit_range = self._range(lines, pos["line"], start, col)
        return [{"label": name, "kind": kind, "detail": detail,
                 "textEdit": {"range": edit_range, "newText": name}}
                for name, detail in sorted(names.items())]

    # test coverage above each route

    def _test_label_sets(self, path: str) -> List[Dict[str, str]]:
        """The labels of each case in an `amlint test` file, read again when it changes.
        Cases amlint test would reject are left out."""
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._tests.get(path)
        if cached is None or cached[0] != stamp:
            with open(path, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
            cases = doc.get("tests") if isinstance(doc, dict) else doc
            if not isinstance(cases, list):
                cases = []
            label_sets = [{str(k): _scalar(v) for k, v in case["labels"].items()}
                          for case in cases
                          if isinstance(case, dict) and isinstance(case.get("labels"), dict)]
            cached = self._tests[path] = (stamp, label_sets)
        return cached[1]

    def _code_lens(self, params: dict) -> List[dict]:
        doc = self.docs.get(params["textDocument"]["uri"])
        if doc is None or not doc.path:
            return []
        analysis = self.analyze(doc)
        if analysis.loaded is None:
            return []
        proj, folder = self._project(doc)
        try:
            files = tests_for_config(proj, doc.path, folder)
            label_sets = [ls for f in files or [] for ls in self._test_label_sets(f)]
        except (ConfigError, OSError, yaml.YAMLError) as e:
            self.log(f"amlint: can't read the tests for {doc.path}: {e}", 2)
            return []
        router = analysis.router()
        if not files or isinstance(router, str):
            return []
        reached: Dict[str, int] = {}
        for labels in label_sets:
            for path in {p for m in router.match(labels) for p in m.path}:
                reached[path] = reached.get(path, 0) + 1
        lenses = []
        for first, _, path, _ in analysis.routes():
            if path == "route":
                continue  # every alert starts at the root
            n = reached.get(path, 0)
            title = (f"{n} of {len(label_sets)} tests reach this route" if n
                     else "no test reaches this route")
            lenses.append({"range": self._range(analysis.lines, first, 0, 0),
                           "command": {"title": title, "command": ""}})
        return lenses

    # outline

    def _symbol(self, lines: List[str], name, detail: str, kind: int,
                first: int, col: int, last: int) -> dict:
        """A DocumentSymbol from (first, col) to the end of line `last`; it's selected by
        what's written on its first line."""
        head, tail = lines[first], lines[last]
        end = _content_end(head, col)
        return {
            "name": str(name) if name not in (None, "") else "(unnamed)",
            "detail": detail, "kind": kind,
            "range": {"start": {"line": first, "character": self._char(head, col)},
                      "end": {"line": last, "character": self._char(tail, len(tail))}},
            "selectionRange": self._range(lines, first, col, end if end > col else len(head)),
        }

    def _document_symbol(self, params: dict) -> List[dict]:
        doc = self.docs.get(params["textDocument"]["uri"])
        if doc is None:
            return []
        analysis = self.analyze(doc)
        if analysis.loaded is None:
            return []
        lines, cfg = analysis.lines, analysis.loaded.cfg
        out: List[dict] = []

        routes: Dict[str, dict] = {}
        for first, last, path, node in analysis.routes():
            col = analysis.loaded.locate(path)[1] - 1
            symbol = self._symbol(lines, _route_name(path, node), _route_detail(node),
                                  _NAMESPACE, first, col, last)
            routes[path] = symbol
            # Under the nearest route above it that also holds it in the text: an
            # AlertmanagerConfig's routes sit elsewhere in the file than the root they join.
            parent = path
            while ".routes[" in parent:
                parent = parent[:parent.rfind(".routes[")]
                if parent in routes and _holds(routes[parent]["range"], symbol["range"]):
                    routes[parent].setdefault("children", []).append(symbol)
                    break
            else:
                out.append(symbol)

        seen: set = set()
        groups = (
            ("receivers", _OBJECT, lambda r: r.get("name"), _receiver_detail),
            ("inhibit_rules", _EVENT, _inhibit_name, _inhibit_detail),
            ("time_intervals", _CONSTANT, lambda t: t.get("name"), _interval_detail),
            ("mute_time_intervals", _CONSTANT, lambda t: t.get("name"), _interval_detail),
        )
        for key, kind, name, detail in groups:
            children = []
            for i, item in _dict_items(cfg.get(key)):
                block = analysis.item_block(f"{key}[{i}]", seen)
                if block is not None:
                    first, col, last = block
                    children.append(self._symbol(lines, name(item), detail(item), kind,
                                                 first, col, last))
            if not children:
                continue
            line, col = (n - 1 for n in analysis.loaded.locate(key))
            start = children[0]["range"]["start"]["line"]
            if not 0 <= line <= start:
                line, col = start, 0
            last = max(c["range"]["end"]["line"] for c in children)
            group = self._symbol(lines, key, str(len(children)), _PACKAGE, line, col, last)
            group["children"] = children
            out.append(group)
        return out
