#!/usr/bin/env python3
"""amlint CLI."""

import argparse
import difflib
import json
import os
import sys
from collections import Counter

try:
    import yaml
except ImportError:
    print("pyyaml is required:  pip install pyyaml", file=sys.stderr)
    sys.exit(2)

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("amlint")
except Exception:
    _VERSION = "unknown"  # running from a source checkout that isn't pip-installed

try:
    import argcomplete
    _ARGCOMPLETE = True
except ImportError:
    _ARGCOMPLETE = False

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .config import load_project_config
from .linter import ERROR, INFO, WARN, _dict_items, _scalar, lint
from .markdown import diff_report
from .routing import GROUP_BY_ALL, Router, RoutingError, diff_routing
from .source import apply_source

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

ICON  = {ERROR: "✖", WARN: "⚠", INFO: "ℹ"}
STYLE = {ERROR: "bold red", WARN: "bold yellow", INFO: "bold cyan"}


def _build_ignore(args_ignore, project_ignore) -> set:
    codes = set(project_ignore or [])
    for val in (args_ignore or []):
        for code in val.split(","):
            if code.strip():
                codes.add(code.strip())
    return codes


def _build_only(args_only) -> set:
    codes: set = set()
    for val in (args_only or []):
        for code in val.split(","):
            if code.strip():
                codes.add(code.strip())
    return codes


def _warn_unknown_codes(codes, source: str) -> None:
    """A typo in --ignore/--only/severity: would otherwise be silently ignored."""
    from .explains import EXPLAINS
    for code in sorted(set(codes) - set(EXPLAINS)):
        hint = difflib.get_close_matches(code, list(EXPLAINS), n=1)
        suffix = f" — did you mean '{hint[0]}'?" if hint else ""
        err_console.print(
            f"[yellow]warning:[/yellow] unknown check code '{escape(code)}' in {source}{suffix}",
            soft_wrap=True,  # one line, so the suggested code can be copied as-is
        )


def _project_settings(args):
    """Merge .amlint.yml / pyproject.toml with CLI flags; warn about unknown codes."""
    proj = load_project_config()
    ignore = _build_ignore(args.ignore, proj.get("ignore"))
    only = _build_only(getattr(args, "only", None))
    severity = proj.get("severity") or {}
    _warn_unknown_codes(ignore, "ignore list")
    _warn_unknown_codes(only, "--only")
    _warn_unknown_codes(severity, "severity config")
    return proj, ignore, only, severity


def _basedir(path):
    return os.path.dirname(os.path.abspath(path)) if path != "-" else None


def _read(path) -> str:
    """Text of a file, or stdin for "-"; exits 2 if the file is missing."""
    if path == "-":
        return sys.stdin.read()
    if not os.path.exists(path):
        err_console.print(f"[red]File not found:[/red] {path}")
        sys.exit(2)
    with open(path) as f:
        return f.read()


def _read_yaml(path, content=None):
    """Parse a YAML file, or stdin for "-"; exits 2 if it is missing or unparsable."""
    if content is None:
        content = _read(path)
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as e:
        err_console.print(f"[red]Failed to parse YAML[/red] ({path}): {e}")
        sys.exit(2)


def load(path, content=None):
    """Load an Alertmanager config; exits 2 unless its top level is a mapping."""
    cfg = _read_yaml(path, content)
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        err_console.print(
            f"[red]Not an Alertmanager config[/red] ({path}): "
            f"top level must be a mapping, got {type(cfg).__name__}"
        )
        sys.exit(2)
    return cfg


def _display(path) -> str:
    return "<stdin>" if path == "-" else path


def _lint_file(path, **kwargs):
    """Load and lint one file. Findings get their line/col, and the ones disabled by
    `# amlint:` comments in the file are dropped. Returns (cfg, findings)."""
    content = _read(path)
    cfg = load(path, content)
    findings, suppressions = apply_source(lint(cfg, basedir=_basedir(path), **kwargs), content)
    where = escape(_display(path))
    for line, codes in suppressions.comments:
        _warn_unknown_codes(codes, f"'# amlint:' comment at {where}:{line}")
    for line, problem in suppressions.problems:
        err_console.print(f"[yellow]warning:[/yellow] {where}:{line}: {escape(problem)}",
                          soft_wrap=True)
    return cfg, findings


def _location(path, f) -> str:
    return f"{_display(path)}:{f.line}:{f.col}"


def _print_findings(findings, path, label=None):
    if label:
        console.print(Rule(f"[bold]{label}[/bold]", style="dim"))

    if not findings:
        console.print("\n  [bold green]✓[/bold green]  No issues found.\n")
        return

    console.print()
    for f in findings:
        header = Text()
        header.append(f"  {ICON[f.level]}  ", style=STYLE[f.level])
        header.append(f.level.upper().ljust(5), style=STYLE[f.level])
        header.append(f"  [{f.code}]", style="dim")
        header.append(f"  ·  {_location(path, f)}")
        if f.where:
            header.append(f"  ·  {f.where}", style="dim")
        console.print(header, soft_wrap=True)  # keep file:line:col in one piece
        console.print(f"     {f.msg}")
        console.print()

    errs  = sum(1 for f in findings if f.level == ERROR)
    warns = sum(1 for f in findings if f.level == WARN)
    infos = sum(1 for f in findings if f.level == INFO)

    console.print(Rule(style="dim"))
    parts = []
    if errs:
        parts.append(f"[red]✖ {errs} error{'s' if errs != 1 else ''}[/red]")
    if warns:
        parts.append(f"[yellow]⚠ {warns} warning{'s' if warns != 1 else ''}[/yellow]")
    if infos:
        parts.append(f"[cyan]ℹ {infos} info[/cyan]")
    console.print("  " + "  ·  ".join(parts))
    console.print()


_SARIF_LEVEL = {ERROR: "error", WARN: "warning", INFO: "note"}
_DOCS_BASE = "https://danikdanik2013.github.io/amlint/checks/"


def _to_sarif(all_findings, version: str) -> dict:
    from .explains import EXPLAINS

    def pascal(code: str) -> str:
        return "".join(w.capitalize() for w in code.split("-"))

    rules = [
        {
            "id": code,
            "name": pascal(code),
            "shortDescription": {"text": e["summary"]},
            "defaultConfiguration": {
                "level": _SARIF_LEVEL.get(e["level"].split()[0], "note")
            },
            "helpUri": f"{_DOCS_BASE}",
        }
        for code, e in EXPLAINS.items()
    ]

    results = []
    for path, findings in all_findings:
        uri = path if path != "-" else "<stdin>"
        for f in findings:
            result: dict = {
                "ruleId": f.code,
                "level": _SARIF_LEVEL.get(f.level, "note"),
                "message": {"text": f.msg},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
                        "region": {"startLine": f.line, "startColumn": f.col},
                    },
                }],
            }
            if f.where:
                result["locations"][0]["logicalLocations"] = [
                    {"name": f.where, "kind": "member"}
                ]
            results.append(result)

    return {
        "$schema": (
            "https://raw.githubusercontent.com/oasis-tcs/sarif-spec"
            "/master/Schemata/sarif-schema-2.1.0.json"
        ),
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "amlint",
                    "version": version,
                    "informationUri": "https://github.com/danikdanik2013/amlint",
                    "rules": rules,
                }
            },
            "results": results,
        }],
    }


_GITHUB_LEVEL = {ERROR: "error", WARN: "warning", INFO: "notice"}


def _github_escape(value: str, prop: bool = False) -> str:
    """Escape a workflow-command message, or (prop=True) a property value."""
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def _github_path(path: str) -> str:
    """Annotations need the path relative to the repository root ($GITHUB_WORKSPACE)."""
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        try:
            rel = os.path.relpath(os.path.abspath(path), workspace)
        except ValueError:  # another drive on Windows
            return path
        if rel != ".." and not rel.startswith(".." + os.sep):
            return rel.replace(os.sep, "/")
    return path


def _print_github(all_findings) -> None:
    """GitHub Actions workflow commands: each finding becomes a PR annotation."""
    for path, findings in all_findings:
        for f in findings:
            props = []
            if path != "-":
                props += [f"file={_github_escape(_github_path(path), prop=True)}",
                          f"line={f.line}", f"col={f.col}"]
            props.append(f"title={_github_escape(f'amlint {f.code}', prop=True)}")
            msg = f"{f.msg} ({f.where})" if f.where else f.msg
            print(f"::{_GITHUB_LEVEL[f.level]} {','.join(props)}::{_github_escape(msg)}")


def _cmd_check(args):
    proj, ignore, only, severity = _project_settings(args)
    strict = args.strict or proj.get("strict", False)

    multi = len(args.files) > 1
    all_findings = []
    for path in args.files:
        _, findings = _lint_file(path, ignore=ignore, severity=severity, only=only or None)
        all_findings.append((path, findings))

    if args.format == "json":
        print(json.dumps(
            [{"file": path, "line": f.line, "column": f.col, "level": f.level,
              "code": f.code, "message": f.msg, "where": f.where}
             for path, findings in all_findings for f in findings],
            ensure_ascii=False, indent=2,
        ))
    elif args.format == "sarif":
        print(json.dumps(_to_sarif(all_findings, _VERSION), ensure_ascii=False, indent=2))
    elif args.format == "github":
        _print_github(all_findings)
    else:
        for path, findings in all_findings:
            _print_findings(findings, path, label=path if multi else None)

    if getattr(args, "exit_zero", False):
        return 0
    has_err  = any(f.level == ERROR for _, fs in all_findings for f in fs)
    has_warn = any(f.level == WARN  for _, fs in all_findings for f in fs)
    return 1 if has_err or (strict and has_warn) else 0


def _format_matchers(node: dict) -> list:
    parts = []
    for k, v in (node.get("match") or {}).items():
        parts.append(f"{k}={v}")
    for k, v in (node.get("match_re") or {}).items():
        parts.append(f"{k}=~{v}")
    for m in node.get("matchers") or []:
        parts.append(str(m))
    return parts


def _cmd_tree(args) -> int:
    _, ignore, _, severity = _project_settings(args)

    cfg, findings = _lint_file(args.file, ignore=ignore, severity=severity)
    route = cfg.get("route")
    if not isinstance(route, dict):
        err_console.print("[red]No root 'route' defined.[/red]")
        return 2

    by_path: dict = {}
    for f in findings:
        by_path.setdefault(f.where, []).append(f)
    level_order = {ERROR: 0, WARN: 1, INFO: 2}
    shown: list = []

    def label_for(node: dict, path: str) -> str:
        parts = _format_matchers(node)
        piece = "{" + ", ".join(parts) + "}" if parts else (
            "(catch-all)" if path != "route" else "(root)"
        )
        rcv = node.get("receiver")
        if rcv:
            piece += f"  →  [bold]{rcv}[/bold]"
        else:
            piece += "  →  [dim italic](inherited)[/dim italic]"
        if node.get("continue"):
            piece += "  [dim]\\[continue][/dim]"

        fs = by_path.get(path, [])
        if fs:
            shown.extend(fs)
            worst = min(fs, key=lambda f: level_order[f.level])
            codes = ", ".join(sorted({f.code for f in fs}))
            style = STYLE[worst.level]
            return f"[{style}]{ICON[worst.level]}[/{style}]  {piece}  [{style}]({codes})[/{style}]"
        return f"   {piece}"

    tree = Tree(label_for(route, "route"), guide_style="dim")

    def add_children(node: dict, path: str, branch: Tree) -> None:
        for i, child in _dict_items(node.get("routes")):
            cpath = f"{path}.routes[{i}]"
            cbranch = branch.add(label_for(child, cpath))
            add_children(child, cpath, cbranch)

    add_children(route, "route", tree)

    console.print()
    console.print(tree)
    console.print()
    if shown:
        noun = "issue" if len(shown) == 1 else "issues"
        console.print(
            f"  [dim]{len(shown)} routing {noun} flagged above — run "
            f"'amlint check {args.file}' for full details.[/dim]\n"
        )
    other = sum(len(v) for p, v in by_path.items()) - len(shown)
    if other:
        noun = "issue" if other == 1 else "issues"
        console.print(
            f"  [dim]{other} more {noun} outside routing (receivers, global, timing) — "
            f"run 'amlint check {args.file}' for full details.[/dim]\n"
        )
    return 0


def _router(cfg: dict):
    """Router for the config's route tree, or None after printing why it can't be built."""
    try:
        return Router(cfg.get("route"))
    except RoutingError as e:
        err_console.print(f"[red]Cannot simulate routing:[/red] {escape(str(e))}")
        return None


def _format_labels(labels: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in labels.items()) if labels else "(no labels)"


def _receivers_of(matches) -> list:
    return [m.receiver for m in matches if m.receiver]


_ROUTE_SETTINGS = (
    "receiver", "group_by", "group_wait", "group_interval", "repeat_interval",
    "mute_time_intervals", "active_time_intervals",
)


def _route_match_json(m) -> dict:
    out = {"path": list(m.path)}
    for key in _ROUTE_SETTINGS:
        value = getattr(m.opts, key)
        out[key] = list(value) if isinstance(value, tuple) else value
    out["set_by"] = dict(m.opts.origin)
    return out


def _cmd_route(args) -> int:
    labels = {}
    for item in args.labels:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            err_console.print(
                f"[red]Invalid label[/red] '{escape(item)}': expected label=value"
            )
            return 2
        labels[name.strip()] = value

    router = _router(load(args.config))
    if router is None:
        return 2
    matches = router.match(labels)

    if args.format == "json":
        print(json.dumps({
            "labels": labels,
            "receivers": _receivers_of(matches),
            "routes": [_route_match_json(m) for m in matches],
        }, ensure_ascii=False, indent=2))
        return 0

    console.print()
    console.print(f"  [bold]Labels[/bold]  {escape(_format_labels(labels))}")
    for n, m in enumerate(matches, 1):
        here = m.path[-1]
        console.print()
        prefix = f"[dim]{n}.[/dim] " if len(matches) > 1 else ""
        console.print("  " + prefix + "[dim] → [/dim]".join(escape(p) for p in m.path))
        grid = Table.grid(padding=(0, 3))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column()
        grid.add_column(style="dim")
        for key in _ROUTE_SETTINGS:
            value = getattr(m.opts, key)
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
            origin = m.opts.origin.get(key)
            if origin is None:
                source = "Alertmanager default"
            elif origin == here:
                source = "set here"
            else:
                source = f"inherited from {origin}"
            style = "bold" if key == "receiver" else ""
            grid.add_row(key, Text(shown, style=style), source)
        console.print(Padding(grid, (0, 0, 0, 4), expand=False))
    console.print()
    console.print(Rule(style="dim"))
    receivers = _receivers_of(matches)
    if receivers:
        console.print(f"  Delivered to: [bold]{escape(', '.join(receivers))}[/bold]\n")
    else:
        console.print("  [yellow]No receiver: the alert would not be delivered.[/yellow]\n")
    return 0


_TEST_ASSERTIONS = ("expect", "receiver", "receivers", "drop")


def _load_test_cases(path):
    """The cases of a tests file: a top-level list, or a mapping with a 'tests:' list."""
    doc = _read_yaml(path)
    cases = doc.get("tests") if isinstance(doc, dict) else doc
    return cases if isinstance(cases, list) else None


def _run_test_case(router, i: int, case) -> dict:
    if not isinstance(case, dict):
        return {"name": f"test #{i + 1}", "labels": {}, "passed": False,
                "error": "test case must be a mapping with labels: and expect:"}
    name = str(case.get("name") or f"test #{i + 1}")
    labels = case.get("labels") or {}
    if not isinstance(labels, dict):
        return {"name": name, "labels": {}, "passed": False,
                "error": "labels: must be a mapping of label names to values"}
    labels = {str(k): _scalar(v) for k, v in labels.items()}
    if sum(k in case for k in _TEST_ASSERTIONS) != 1:
        return {"name": name, "labels": labels, "passed": False,
                "error": "test case must set exactly one of: expect, receiver, receivers, drop"}
    if "drop" in case and case["drop"] is not True:
        return {"name": name, "labels": labels, "passed": False,
                "error": "drop: only accepts true — use expect: to list receivers"}

    matches = router.match(labels)
    actual = _receivers_of(matches)
    ordered = False
    if "expect" in case:
        raw = case["expect"]
        expected = [_scalar(r) for r in raw] if isinstance(raw, list) else (
            [] if raw is None else [_scalar(raw)]
        )
    elif "receivers" in case:
        expected = [_scalar(r) for r in case["receivers"] or []]
        ordered = True
    elif "receiver" in case:
        expected = [_scalar(case["receiver"])]
    else:
        expected = []

    missing = list((Counter(expected) - Counter(actual)).elements())
    unexpected = list((Counter(actual) - Counter(expected)).elements())
    passed = actual == expected if ordered else not missing and not unexpected
    return {
        "name": name, "labels": labels, "passed": passed,
        "expected": ", ".join(expected) if expected else "(no receiver)",
        "actual": ", ".join(actual) if actual else "(no receiver)",
        "missing": missing, "unexpected": unexpected,
        "routes": [m.path[-1] for m in matches],
        "order_only": ordered and not passed and not missing and not unexpected,
    }


def _print_test_result(r: dict) -> None:
    if r["passed"]:
        console.print(f"  [bold green]✓[/bold green]  {escape(r['name'])}")
        return
    console.print(f"  [bold red]✗[/bold red]  {escape(r['name'])}")
    if "error" in r:
        console.print(f"     [red]{escape(r['error'])}[/red]")
    else:
        console.print(f"     [dim]labels:[/dim]    {escape(_format_labels(r['labels']))}")
        console.print(f"     [dim]expected:[/dim]  {escape(r['expected'])}")
        console.print(f"     [dim]actual:[/dim]    {escape(r['actual'])}")
        for rcv in r["missing"]:
            console.print(f"       [red]- {escape(rcv)}[/red]  [dim]expected, not delivered[/dim]")
        for rcv in r["unexpected"]:
            console.print(
                f"       [green]+ {escape(rcv)}[/green]  [dim]delivered, not expected[/dim]"
            )
        if r["order_only"]:
            console.print(
                "       [dim]same receivers, different order (receivers: is "
                "order-sensitive, expect: is not)[/dim]"
            )
        console.print(f"     [dim]routes:[/dim]    {escape(', '.join(r['routes']))}")
    console.print()


def _cmd_test(args) -> int:
    router = _router(load(args.config))
    if router is None:
        return 2

    cases = _load_test_cases(args.tests)
    if cases is None:
        err_console.print(
            f"[red]Invalid test file:[/red] {args.tests} must be a list of test cases, "
            f"or have a top-level 'tests:' list."
        )
        return 2

    results = [_run_test_case(router, i, case) for i, case in enumerate(cases)]

    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        console.print()
        for r in results:
            _print_test_result(r)
        passed_n = sum(1 for r in results if r["passed"])
        failed_n = len(results) - passed_n
        console.print(Rule(style="dim"))
        if failed_n:
            console.print(f"  [red]{failed_n} failed[/red]  ·  [green]{passed_n} passed[/green]\n")
        else:
            console.print(f"  [green]{passed_n} passed[/green]\n")

    return 1 if any(not r["passed"] for r in results) else 0


_INIT_TEMPLATE = """\
global:
  resolve_timeout: 5m

route:
  receiver: default
  group_by: [alertname, cluster]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes: []

receivers:
  - name: default
    webhook_configs:
      - url: http://your-webhook-url/alert

inhibit_rules: []
"""


def _cmd_init() -> int:
    print(_INIT_TEMPLATE, end="")
    return 0


def _routing_diff(old_cfg: dict, new_cfg: dict):
    """(RoutingDiff, None), or (None, why the configs can't be compared)."""
    routers = []
    for side, cfg in (("old", old_cfg), ("new", new_cfg)):
        try:
            routers.append(Router(cfg.get("route")))
        except RoutingError as e:
            return None, f"{side} config: {e}"
    return diff_routing(*routers), None


def _routing_diff_json(rdiff, skipped) -> dict:
    if rdiff is None:
        return {"skipped": skipped}
    return {
        "checked": rdiff.checked,
        "truncated": rdiff.truncated,
        "changes": [{
            "labels": c.labels,
            "similar": c.similar,
            "differences": c.differences(),
            "old": [_route_match_json(m) for m in c.old],
            "new": [_route_match_json(m) for m in c.new],
        } for c in rdiff.changes],
    }


def _print_routing_diff(rdiff, skipped) -> None:
    console.print(Rule("[bold]Routing[/bold]", style="dim"))
    if rdiff is None:
        console.print(f"\n  [yellow]Routing not compared:[/yellow] {escape(skipped)}\n")
        return
    checked = f"{rdiff.checked} label set{'s' if rdiff.checked != 1 else ''} checked"
    if rdiff.truncated:
        checked += " (limit reached, not every combination was tried)"
    if not rdiff.changes:
        console.print(
            f"\n  [bold green]✓[/bold green]  No routing changes  [dim]· {checked}[/dim]\n"
        )
        return
    console.print()
    for c in rdiff.changes:
        console.print(f"  [bold]{escape(_format_labels(c.labels))}[/bold]")
        grid = Table.grid(padding=(0, 3))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column()
        for d in c.differences():
            same = d["setting"] == d["subject"]
            what = d["subject"] if same else f"{d['subject']}  {d['setting']}"
            grid.add_row(what, Text(f"{d['old']} → {d['new']}"))
        console.print(Padding(grid, (0, 0, 0, 4), expand=False))
        if c.similar:
            more = "label set" if c.similar == 1 else "label sets"
            console.print(f"    [dim]and {c.similar} more {more} with the same change[/dim]")
        console.print()
    noun = "change" if len(rdiff.changes) == 1 else "changes"
    console.print(
        f"  [yellow]{len(rdiff.changes)} routing {noun}[/yellow]  [dim]· {checked}[/dim]\n"
    )


def _cmd_diff(args):
    _, ignore, only, severity = _project_settings(args)
    old_cfg, old_findings = _lint_file(args.old, ignore=ignore, severity=severity,
                                       only=only or None)
    new_cfg, new_findings = _lint_file(args.new, ignore=ignore, severity=severity,
                                       only=only or None)
    if not old_cfg:
        # An empty old file (e.g. the config is new): nothing to fix, and nothing was routed.
        old_cfg, old_findings = {"route": {}}, []
    rdiff, skipped = (None, None) if args.no_routing else _routing_diff(old_cfg, new_cfg)

    def key(f):
        return (f.code, f.where)

    old_counts = Counter(key(f) for f in old_findings)
    new_counts = Counter(key(f) for f in new_findings)
    added_keys = new_counts - old_counts
    fixed_keys = old_counts - new_counts

    added, remaining = [], dict(added_keys)
    for f in new_findings:
        k = key(f)
        if remaining.get(k, 0) > 0:
            added.append(f)
            remaining[k] -= 1

    fixed, remaining = [], dict(fixed_keys)
    for f in old_findings:
        k = key(f)
        if remaining.get(k, 0) > 0:
            fixed.append(f)
            remaining[k] -= 1

    unchanged = len(old_findings) - len(fixed)

    if args.format == "json":
        def entry(f):
            return {"code": f.code, "level": f.level, "message": f.msg, "where": f.where,
                    "line": f.line, "column": f.col}
        doc = {
            "added":  [entry(f) for f in added],
            "fixed":  [entry(f) for f in fixed],
            "unchanged": unchanged,
        }
        if not args.no_routing:
            doc["routing"] = _routing_diff_json(rdiff, skipped)
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 1 if added else 0

    if args.format == "markdown":
        print(diff_report(_display(args.old), _display(args.new), added, fixed, unchanged,
                          rdiff, skipped, routing=not args.no_routing), end="")
        return 1 if added else 0

    if not added and not fixed:
        console.print("\n  [bold green]✓[/bold green]  No changes in findings.\n")
        if not args.no_routing:
            _print_routing_diff(rdiff, skipped)
        return 0

    console.print()
    for f in fixed:
        console.print(f"  [bold green]FIXED[/bold green]  {escape(f'[{f.code}]')}"
                      f"  ·  {escape(_location(args.old, f))}"
                      + (f"  ·  [dim]{escape(f.where)}[/dim]" if f.where else ""),
                      soft_wrap=True)
        console.print(f"     {f.msg}")
        console.print()
    for f in added:
        console.print(f"  [bold red]NEW  [/bold red]  {escape(f'[{f.code}]')}"
                      f"  ·  {escape(_location(args.new, f))}"
                      + (f"  ·  [dim]{escape(f.where)}[/dim]" if f.where else ""),
                      soft_wrap=True)
        console.print(f"     {f.msg}")
        console.print()

    console.print(Rule(style="dim"))
    console.print(
        f"  [green]{len(fixed)} fixed[/green]"
        f"  ·  [red]{len(added)} new[/red]"
        f"  ·  {unchanged} unchanged\n"
    )
    if not args.no_routing:
        _print_routing_diff(rdiff, skipped)
    return 1 if added else 0


def _cmd_list():
    from .explains import ALSO_CAUGHT_BY_AMTOOL, EXPLAINS
    level_order = {ERROR: 0, WARN: 1, INFO: 2}
    sorted_items = sorted(
        EXPLAINS.items(),
        key=lambda kv: (level_order.get(kv[1]["level"].split()[0], 3), kv[0]),
    )
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("code", no_wrap=True)
    table.add_column("level", no_wrap=True)
    table.add_column("amtool", no_wrap=True, justify="center")
    table.add_column("description")
    for code, e in sorted_items:
        level = e["level"].split()[0]
        mark = Text("✓", style="dim") if code in ALSO_CAUGHT_BY_AMTOOL else Text("")
        table.add_row(
            Text(code, style="dim"),
            Text(level, style=STYLE[level]),
            mark,
            e["summary"],
        )
    console.print()
    console.print(table)
    console.print()
    n = len(ALSO_CAUGHT_BY_AMTOOL)
    console.print(
        f"  [dim]✓ in the amtool column = also caught by `amtool check-config` "
        f"({n} of {len(EXPLAINS)} checks) — Alertmanager refuses to start on these, "
        f"it doesn't silently misbehave. The rest are configs amtool accepts and "
        f"Alertmanager runs fine, just not as intended.[/dim]"
    )
    console.print()
    return 0


def _cmd_explain(args):
    from .explains import ALSO_CAUGHT_BY_AMTOOL, EXPLAINS
    code = args.code
    if code not in EXPLAINS:
        err_console.print(f"[red]Unknown check code:[/red] {code}\n")
        err_console.print("Available codes:")
        for c in sorted(EXPLAINS):
            err_console.print(f"  {c}")
        return 2
    e = EXPLAINS[code]
    level_style = STYLE.get(e["level"].split()[0], "bold")
    console.print()
    console.print(Rule(f"[bold]{code}[/bold]", style="dim"))
    console.print()
    console.print(f"  Level:  [{level_style}]{e['level'].upper()}[/{level_style}]")
    if code in ALSO_CAUGHT_BY_AMTOOL:
        console.print(
            "  [dim]Also caught by `amtool check-config` — Alertmanager refuses to "
            "start over this. Included here for unified JSON/SARIF/diff output "
            "without needing the Go binary, not because amtool misses it.[/dim]"
        )
    else:
        console.print(
            "  [dim]Not caught by `amtool check-config` — this config loads and "
            "Alertmanager runs fine with the bug in place. This is the gap amlint "
            "exists for.[/dim]"
        )
    console.print()
    console.print(f"  {e['summary']}")
    console.print()
    if e.get("why"):
        console.print("  [bold]Why this matters:[/bold]")
        console.print(f"  {e['why']}")
        console.print()
    console.print("  [bold dim]Bad:[/bold dim]")
    console.print(Syntax(e["bad"], "yaml", theme="ansi_dark", background_color="default",
                         indent_guides=False, padding=(0, 4)))
    console.print()
    console.print("  [bold dim]Fixed:[/bold dim]")
    console.print(Syntax(e["good"], "yaml", theme="ansi_dark", background_color="default",
                         indent_guides=False, padding=(0, 4)))
    console.print()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="amlint",
        description="Semantic linter for Alertmanager configs. Catches what amtool misses.",
    )
    p.add_argument("--version", action="version", version=f"amlint {_VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("check", help="validate one or more config files")
    pc.add_argument("files", nargs="+", metavar="file",
                    help="path(s) to alertmanager.yml, or - for stdin")
    pc.add_argument("--format", choices=["text", "json", "sarif", "github"], default="text",
                    help="github prints GitHub Actions annotations (::error file=...::)")
    pc.add_argument("--strict", action="store_true", help="exit non-zero on WARN as well")
    pc.add_argument("--ignore", action="append", metavar="CODE",
                    help="skip findings with these codes (comma-separated or repeat the flag); "
                         "also configurable via .amlint.yml")
    pc.add_argument("--only", action="append", metavar="CODE",
                    help="run only findings with these codes (comma-separated or repeat the flag)")
    pc.add_argument("--exit-zero", action="store_true", dest="exit_zero",
                    help="always exit 0 regardless of findings; useful for informational CI runs")

    pd = sub.add_parser("diff", help="show findings that changed between two configs")
    pd.add_argument("old", help="baseline config")
    pd.add_argument("new", help="updated config")
    pd.add_argument("--format", choices=["text", "json", "markdown"], default="text",
                    help="markdown is a compact report for a pull-request comment")
    pd.add_argument("--ignore", action="append", metavar="CODE",
                    help="skip findings with these codes")
    pd.add_argument("--only", action="append", metavar="CODE",
                    help="consider only findings with these codes")
    pd.add_argument("--no-routing", action="store_true", dest="no_routing",
                    help="skip the routing comparison (label sets routed differently)")

    pt = sub.add_parser("tree", help="print route tree with matchers, receivers, and issues")
    pt.add_argument("file", help="path to alertmanager.yml, or - for stdin")
    pt.add_argument("--ignore", action="append", metavar="CODE",
                    help="skip findings with these codes when annotating the tree")

    pr = sub.add_parser("route", help="show which route(s) and receiver(s) a label set reaches")
    pr.add_argument("config", help="path to alertmanager.yml, or - for stdin")
    pr.add_argument("labels", nargs="*", metavar="label=value",
                    help="alert labels, e.g. severity=critical team=infra")
    pr.add_argument("--format", choices=["text", "json"], default="text")

    ptst = sub.add_parser("test", help="run routing unit tests against a config")
    ptst.add_argument("config", help="path to alertmanager.yml")
    ptst.add_argument("tests", help="path to a YAML file with a list of test cases")
    ptst.add_argument("--format", choices=["text", "json"], default="text")

    sub.add_parser("init", help="print a minimal valid alertmanager.yml to stdout")

    sub.add_parser("list", help="list all check codes with level and description")

    pe = sub.add_parser("explain", help="show description and examples for a check code")
    pe.add_argument("code", help="check code to explain, e.g. undefined-receiver")

    if _ARGCOMPLETE:
        argcomplete.autocomplete(p)

    args = p.parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args)
    if args.cmd == "diff":
        return _cmd_diff(args)
    if args.cmd == "tree":
        return _cmd_tree(args)
    if args.cmd == "route":
        return _cmd_route(args)
    if args.cmd == "test":
        return _cmd_test(args)
    if args.cmd == "init":
        return _cmd_init()
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "explain":
        return _cmd_explain(args)


if __name__ == "__main__":
    sys.exit(main())
