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

from .config import load_project_config
from .explains import EXPLAINS, docs_url
from .formats import FormatError, Loaded, apply_locations, load_input
from .linter import ERROR, INFO, WARN, _walk_routes, lint
from .routing import GROUP_BY_ALL, RouteOpts, Router, RoutingError, probe_label_sets
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
        self._requests = {
            "initialize": self._initialize,
            "shutdown": self._shutdown,
            "textDocument/hover": self._hover,
            "textDocument/codeAction": self._code_action,
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

    def _project(self, doc: _Document) -> dict:
        """The .amlint.yml (or pyproject.toml) of the workspace folder holding the document,
        else of the document's own directory."""
        folder = None
        if doc.path:
            path = os.path.abspath(doc.path)
            inside = [f for f in self.folders
                      if path.startswith(os.path.join(os.path.abspath(f), ""))]
            folder = max(inside, key=len) if inside else os.path.dirname(path)
        try:
            proj = load_project_config(folder)
            return proj if isinstance(proj, dict) else {}
        except Exception as e:
            self.log(f"amlint: can't read the project config in {folder or os.getcwd()}: {e}", 2)
            return {}

    def analyze(self, doc: _Document) -> _Analysis:
        if doc.analysis is None:
            analysis = _Analysis(doc.text)
            try:
                self._lint(doc, analysis)
            except Exception:
                self.log(f"amlint: linting {doc.uri} failed:\n{traceback.format_exc()}")
                analysis.diagnostics, analysis.loaded = [], None
            doc.analysis = analysis
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

        proj = self._project(doc)
        severity = proj.get("severity")
        basedir = os.path.dirname(doc.path) if doc.path and loaded.templates is None else None
        findings = lint(loaded.cfg, ignore=set(proj.get("ignore") or []),
                        severity=severity if isinstance(severity, dict) else None,
                        basedir=basedir, template_files=loaded.templates)
        for f in apply_locations(findings, loaded):
            diags.append({
                "range": self._line_range(lines, f.line - 1, f.col - 1),
                "severity": _SEVERITY[f.level],
                "code": f.code,
                "codeDescription": {"href": docs_url(f.code)},
                "source": "amlint",
                "message": f.msg,
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
    def _action(title: str, uri: str, diag: dict, edit: dict) -> dict:
        return {"title": title, "kind": "quickfix", "diagnostics": [diag],
                "edit": {"changes": {uri: [edit]}}}

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
