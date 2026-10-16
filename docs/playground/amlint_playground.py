"""Glue between the playground page and amlint, run by Pyodide in a Web Worker.

check, route and diff call amlint's own CLI with --format json, so the page shows what
`amlint check|route|diff` print. Only the route tree, which the CLI prints as text, is
built here, from the config as amlint.formats reads it (a plain alertmanager.yml, a
Kubernetes Secret, Helm values, a Mimir config or AlertmanagerConfig resources). Every
function takes strings and returns a JSON string.
"""

import contextlib
import io
import json
import os
import re
import sys

os.environ["COLUMNS"] = "1000"  # rich wraps stderr messages at the console width

from amlint import cli  # noqa: E402
from amlint.explains import EXPLAINS  # noqa: E402
from amlint.formats import FormatError, load_input  # noqa: E402
from amlint.rules import RulesError, load_rules, rule_files  # noqa: E402

_DIR = "/tmp/amlint-playground"  # Pyodide's in-memory file system, never uploaded
_STDIN = {"(-)": "(config)", "<stdin>": "config"}  # how the CLI names stdin in messages
_ROUTE_KEYS = ("group_by", "group_wait", "group_interval", "repeat_interval",
               "mute_time_intervals", "active_time_intervals")


def _write(name: str, text: str) -> str:
    os.makedirs(_DIR, exist_ok=True)
    path = os.path.join(_DIR, name)
    with open(path, "w") as f:
        f.write(text)
    return path


def _run(argv, stdin: str = "", names=None) -> dict:
    """Run the amlint CLI: {"exit": code, "data": parsed JSON or None, "stderr": text}.
    `names` maps temporary file paths to the names shown on the page."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.stdin = saved
    text = out.getvalue()
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    stderr = err.getvalue().strip()
    for path, name in (names or {}).items():
        stderr = stderr.replace(path, name)
    return {"exit": code or 0, "data": data, "stderr": stderr}


def info() -> str:
    explains = {code: {k: e.get(k, "") for k in ("level", "summary", "why", "bad", "good")}
                for code, e in EXPLAINS.items()}
    return json.dumps({
        "version": cli._VERSION,
        "python": sys.version.split()[0],
        "explains": explains,
    })


def _rules_status(rules: str):
    """(path to pass to --rules or None, status shown in the Rules tab)."""
    if not rules.strip():
        return None, None
    path = _write("rules.yml", rules)
    try:
        alerts, notes = load_rules(rule_files([path]))
    except RulesError as e:
        return None, {"error": str(e).replace(path, "rules"), "alerts": 0, "notes": []}
    notes = [n.replace(path, "rules") for n in notes]
    return (path if alerts else None), {"error": None, "alerts": len(alerts), "notes": notes}


def check(config: str, rules: str = "") -> str:
    """`amlint check --format json` on the config, read from stdin as the CLI does for
    `-`, so template-file-missing (which needs the files next to the config) is skipped."""
    rules_path, status = _rules_status(rules)
    argv = ["check", "--format", "json"]
    names = dict(_STDIN)
    if rules_path:
        argv += ["--rules", rules_path]
        names[rules_path] = "rules"
    result = _run(argv + ["-"], stdin=config, names=names)
    result["rules"] = status
    return json.dumps(result)


_NEXT_LABEL = re.compile(r"\s+[A-Za-z_][A-Za-z0-9_]*\s*=")


def _commas(text: str) -> str:
    """Labels as typed on the page (`a=b c=d` as on the command line, `a=b, c="d e"`,
    `{a="b"}`, or one per line) with commas between them, as amlint parses them."""
    out, in_quotes, escaped = [], False, False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes and (ch == "\n" or _NEXT_LABEL.match(text, i)):
            ch = ","
        out.append(ch)
    return "".join(out)


def _parse_labels(text: str):
    text = _commas(text)
    return cli._parse_label_set(text) if text.strip(" ,") else {}


def route(config: str, labels: str, firing: str = "") -> str:
    """`amlint route --format json`, plus --firing for each non-empty line of `firing`."""
    parsed = _parse_labels(labels)
    if parsed is None:
        return json.dumps({"exit": 2, "data": None,
                           "stderr": "Labels must be label=value pairs, e.g. "
                                     "alertname=NodeDown, severity=critical"})
    argv = ["route", "--format", "json"]
    for line in firing.splitlines():
        if line.strip():
            argv += ["--firing", _commas(line.strip())]
    argv += ["--", "-"] + [f"{k}={v}" for k, v in parsed.items()]
    return json.dumps(_run(argv, stdin=config, names=_STDIN))


def diff(old: str, new: str) -> str:
    """`amlint diff --format json`. Template files aren't in the browser, so
    template-file-missing is ignored on both sides."""
    old_path, new_path = _write("before.yml", old), _write("after.yml", new)
    return json.dumps(_run(
        ["diff", "--format", "json", "--ignore", "template-file-missing", old_path, new_path],
        names={old_path: "before", new_path: "after"},
    ))


def _matchers(node: dict) -> list:
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


def tree(config: str) -> str:
    """The route tree with each route's line, matchers, receiver and own settings, or
    {"tree": null} when the config has no route mapping to draw, with "error" when amlint
    can't read it (worded as `amlint check` words it). The config is read as the CLI reads
    it, so the lines are in the pasted text even when the route is merged from several
    resources or held in a Secret's string."""
    try:
        loaded = load_input(config)
    except FormatError as e:
        return json.dumps({"tree": None, "error": f"{e.title} (config): {e}"})
    root = loaded.cfg.get("route") if isinstance(loaded.cfg, dict) else None
    if not isinstance(root, dict):
        return json.dumps({"tree": None})

    def walk(node: dict, path: str, inherited):
        line, col = loaded.locate(path)
        receiver = node.get("receiver")
        children = node.get("routes") if isinstance(node.get("routes"), list) else []
        return {
            "path": path,
            "line": line,
            "column": col,
            "matchers": _matchers(node),
            "receiver": None if receiver is None else str(receiver),
            "inherited_receiver": inherited,
            "continue": node.get("continue") is True,
            "settings": {k: node[k] for k in _ROUTE_KEYS if k in node},
            "children": [
                walk(child, f"{path}.routes[{i}]",
                     inherited if receiver is None else str(receiver))
                for i, child in enumerate(children) if isinstance(child, dict)
            ],
        }

    return json.dumps({"tree": walk(root, "route", None)}, default=str)
