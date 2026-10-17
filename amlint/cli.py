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

from . import mermaid
from .config import load_project_config
from .formats import FORMATS, MATCHER_STRATEGIES, FormatError, apply_locations, load_input
from .goyaml import AlertmanagerLoader
from .linter import (
    ERROR,
    INFO,
    WARN,
    _dict_items,
    _scalar,
    _split_matchers,
    _unquote,
    _walk_routes,
    lint,
)
from .markdown import diff_report
from .routing import GROUP_BY_ALL, Inhibitor, Router, RoutingError, diff_routing, route_coverage
from .rules import RulesError, load_rules, rule_files

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
        err_console.print(f"[red]File not found:[/red] {escape(str(path))}")
        sys.exit(2)
    with open(path) as f:
        return f.read()


def _read_yaml(path, content=None, loader=yaml.SafeLoader):
    """Parse a YAML file, or stdin for "-"; exits 2 if it is missing or unparsable."""
    if content is None:
        content = _read(path)
    try:
        return yaml.load(content, Loader=loader)
    except yaml.YAMLError as e:
        err_console.print(f"[red]Failed to parse YAML[/red] ({escape(str(path))}): "
                          f"{escape(str(e))}")
        sys.exit(2)


def load(path, content=None):
    """Load an Alertmanager config; exits 2 unless its top level is a mapping. Strings
    Alertmanager reads as written (`match: {country: NO}`) keep their text."""
    cfg = _read_yaml(path, content, AlertmanagerLoader)
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        err_console.print(
            f"[red]Not an Alertmanager config[/red] ({escape(str(path))}): "
            f"top level must be a mapping, got {type(cfg).__name__}"
        )
        sys.exit(2)
    return cfg


def _display(path) -> str:
    return "<stdin>" if path == "-" else path


def _load_input(path, content, input_format=None, matcher_strategy=None):
    """The config in `content`, read in its format (a plain alertmanager.yml, a Kubernetes
    Secret, Helm values, a Mimir config, AlertmanagerConfig resources); exits 2 if it can't
    be read. Notes about how it was read go to stderr."""
    try:
        loaded = load_input(content, input_format, matcher_strategy)
    except FormatError as e:
        err_console.print(f"[red]{e.title}[/red] ({escape(path)}): {escape(str(e))}",
                          soft_wrap=True)
        sys.exit(2)
    where = escape(_display(path))
    for line, note in loaded.notes:
        at = f"{where}:{line}" if line else where
        err_console.print(f"[yellow]note:[/yellow] {at}: {escape(note)}", soft_wrap=True)
    return loaded


def _input_options(args) -> dict:
    return {"input_format": getattr(args, "input_format", None),
            "matcher_strategy": getattr(args, "matcher_strategy", None)}


def _lint_file(path, input_format=None, matcher_strategy=None, **kwargs):
    """Load and lint one file. Findings get their line/col, and the ones disabled by
    `# amlint:` comments in the file are dropped. Returns (cfg, findings)."""
    content = _read(path)
    loaded = _load_input(path, content, input_format, matcher_strategy)
    # template files that ship inside the input replace the files next to it on disk
    basedir = _basedir(path) if loaded.templates is None else None
    findings = apply_locations(
        lint(loaded.cfg, basedir=basedir, template_files=loaded.templates, **kwargs), loaded,
    )
    where = escape(_display(path))
    for line, codes in loaded.comments:
        _warn_unknown_codes(codes, f"'# amlint:' comment at {where}:{line}")
    for line, problem in loaded.problems:
        err_console.print(f"[yellow]warning:[/yellow] {where}:{line}: {escape(problem)}",
                          soft_wrap=True)
    return loaded.cfg, findings


def _location(path, f) -> str:
    return f"{_display(path)}:{f.line}:{f.col}"


def _print_findings(findings, path, label=None):
    if label:
        console.print(Rule(f"[bold]{escape(label)}[/bold]", style="dim"))

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
        console.print(f"     {escape(f.msg)}")
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


def _to_sarif(all_findings, version: str) -> dict:
    from .explains import EXPLAINS, docs_url

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
            "helpUri": docs_url(code),
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


def _load_rules(patterns):
    """Alerting rules for --rules, or None if the files have none; exits 2 if a glob
    matches no file or a file can't be parsed."""
    try:
        alerts, notes = load_rules(rule_files(patterns))
    except RulesError as e:
        err_console.print(f"[red]Cannot load --rules:[/red] {escape(str(e))}", soft_wrap=True)
        sys.exit(2)
    for note in notes:
        err_console.print(f"[yellow]warning:[/yellow] {escape(note)}", soft_wrap=True)
    if not alerts:
        err_console.print("[yellow]warning:[/yellow] no alerting rules in the --rules files, "
                          "so the checks against them are skipped", soft_wrap=True)
        return None
    return alerts


def _warn_if_rule_file(path, cfg) -> None:
    """An unquoted --rules glob expands in the shell, and all but the first file end up
    as configs to check."""
    if isinstance(cfg.get("groups"), list) and "route" not in cfg:
        err_console.print(
            f"[yellow]warning:[/yellow] {escape(_display(path))} looks like a Prometheus rule "
            f"file, not an Alertmanager config. Quote the --rules glob so the shell doesn't "
            f"expand it: --rules 'rules/*.yml'", soft_wrap=True,
        )


def _cmd_check(args):
    proj, ignore, only, severity = _project_settings(args)
    strict = args.strict or proj.get("strict", False)
    rules = _load_rules(args.rules) if args.rules else None

    multi = len(args.files) > 1
    all_findings = []
    for path in args.files:
        cfg, findings = _lint_file(path, ignore=ignore, severity=severity, only=only or None,
                                   rules=rules, **_input_options(args))
        if args.rules:
            _warn_if_rule_file(path, cfg)
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

    cfg, findings = _lint_file(args.file, ignore=ignore, severity=severity,
                               **_input_options(args))
    route = cfg.get("route")
    if not isinstance(route, dict):
        err_console.print("[red]No root 'route' defined.[/red]")
        return 2

    if args.format == "mermaid":
        print(mermaid.route_tree(route, findings), end="")
        return 0

    by_path: dict = {}
    for f in findings:
        by_path.setdefault(f.where, []).append(f)
    level_order = {ERROR: 0, WARN: 1, INFO: 2}
    shown: list = []

    def label_for(node: dict, path: str) -> str:
        parts = _format_matchers(node)
        piece = escape("{" + ", ".join(parts) + "}") if parts else (
            "(catch-all)" if path != "route" else "(root)"
        )
        rcv = node.get("receiver")
        if rcv:
            piece += f"  →  [bold]{escape(str(rcv))}[/bold]"
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
            f"'amlint check {escape(args.file)}' for full details.[/dim]\n"
        )
    other = sum(len(v) for p, v in by_path.items()) - len(shown)
    if other:
        noun = "issue" if other == 1 else "issues"
        console.print(
            f"  [dim]{other} more {noun} outside routing (receivers, global, timing) — "
            f"run 'amlint check {escape(args.file)}' for full details.[/dim]\n"
        )
    return 0


def _router(cfg: dict):
    """Router for the config's route tree, or None after printing why it can't be built."""
    try:
        return Router(cfg.get("route"))
    except RoutingError as e:
        err_console.print(f"[red]Cannot simulate routing:[/red] {escape(str(e))}")
        return None


def _inhibitor(cfg: dict):
    """Inhibitor for the config's inhibit_rules, or None after printing why it can't be built."""
    try:
        return Inhibitor(cfg.get("inhibit_rules"))
    except RoutingError as e:
        err_console.print(f"[red]Cannot simulate inhibition:[/red] {escape(str(e))}")
        return None


def _inhibition_json(inhibition, smap):
    if inhibition is None:
        return None
    line, col = smap.locate(inhibition.rule)
    return {"rule": inhibition.rule, "line": line, "column": col, "source": inhibition.source}


def _parse_label_set(text: str):
    """`alertname=NodeDown,instance=a` (braces and quoted values allowed) as a dict, or
    None unless every part is label=value."""
    labels = {}
    for part in _split_matchers(text):
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or name.endswith("!") or value.startswith("~"):
            return None
        labels[name] = _unquote(value)
    return labels or None


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
    firing = []
    for item in args.firing or []:
        alert = _parse_label_set(item)
        if alert is None:
            err_console.print(
                f"[red]Invalid --firing[/red] '{escape(item)}': expected label=value,label=value"
            )
            return 2
        firing.append(alert)

    loaded = _load_input(args.config, _read(args.config), **_input_options(args))
    cfg = loaded.cfg
    router = _router(cfg)
    if router is None:
        return 2
    inhibition = None
    if firing:
        inhibitor = _inhibitor(cfg)
        if inhibitor is None:
            return 2
        inhibition = inhibitor.inhibited_by(labels, firing)
    smap = loaded if inhibition else None
    matches = router.match(labels)

    if args.format == "json":
        print(json.dumps({
            "labels": labels,
            "receivers": _receivers_of(matches),
            "routes": [_route_match_json(m) for m in matches],
            "firing": firing,
            "inhibited": inhibition is not None,
            "inhibited_by": _inhibition_json(inhibition, smap),
        }, ensure_ascii=False, indent=2))
        return 0

    console.print()
    console.print(f"  [bold]Labels[/bold]  {escape(_format_labels(labels))}")
    for alert in firing:
        console.print(f"  [bold]Firing[/bold]  {escape(_format_labels(alert))}")
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
    if inhibition:
        line, col = smap.locate(inhibition.rule)
        console.print(
            f"  [bold yellow]Inhibited[/bold yellow] by {escape(inhibition.rule)}"
            f"  ·  {escape(_display(args.config))}:{line}:{col}", soft_wrap=True,
        )
        console.print(f"     while firing: {escape(_format_labels(inhibition.source))}")
        if receivers:
            console.print(f"  Routed to: [bold]{escape(', '.join(receivers))}[/bold], but no "
                          f"notification is sent while it is inhibited.\n")
        else:
            console.print("  [yellow]No receiver, and inhibited: the alert would not be "
                          "delivered.[/yellow]\n")
        return 0
    if receivers:
        console.print(f"  Delivered to: [bold]{escape(', '.join(receivers))}[/bold]")
    else:
        console.print("  [yellow]No receiver: the alert would not be delivered.[/yellow]")
    if firing:
        noun = "alert" if len(firing) == 1 else "alerts"
        console.print(f"  [dim]Not inhibited by the {len(firing)} firing {noun}.[/dim]")
    console.print()
    return 0


_TEST_ASSERTIONS = ("expect", "receiver", "receivers", "drop")


def _load_test_cases(path):
    """The cases of a tests file: a top-level list, or a mapping with a 'tests:' list."""
    doc = _read_yaml(path)
    cases = doc.get("tests") if isinstance(doc, dict) else doc
    return cases if isinstance(cases, list) else None


def _test_error(name: str, labels: dict, error: str):
    return {"name": name, "labels": labels, "passed": False, "error": error}, []


def _run_test_case(router, inhibitor, smap, i: int, case):
    """(result, matches) of one case; matches is empty when the case is malformed."""
    if not isinstance(case, dict):
        return _test_error(f"test #{i + 1}", {},
                           "test case must be a mapping with labels: and expect:")
    name = str(case.get("name") or f"test #{i + 1}")
    labels = case.get("labels") or {}
    if not isinstance(labels, dict):
        return _test_error(name, {}, "labels: must be a mapping of label names to values")
    labels = {str(k): _scalar(v) for k, v in labels.items()}
    asserted = [k for k in _TEST_ASSERTIONS if k in case]
    if len(asserted) > 1 or not asserted and "inhibited" not in case:
        return _test_error(name, labels, "test case must set exactly one of: expect, receiver, "
                                         "receivers, drop (or only inhibited:)")
    if "drop" in case and case["drop"] is not True:
        return _test_error(name, labels, "drop: only accepts true — use expect: to list receivers")
    firing = case.get("firing") or []
    if not isinstance(firing, list) or not all(isinstance(a, dict) for a in firing):
        return _test_error(name, labels, "firing: must be a list of label sets, "
                                         "e.g. [{alertname: NodeDown, instance: a}]")
    firing = [{str(k): _scalar(v) for k, v in a.items()} for a in firing]
    if "inhibited" in case and not isinstance(case["inhibited"], bool):
        return _test_error(name, labels, "inhibited: must be true or false")
    # with firing: and no inhibited:, the case expects the alert to get through
    expect_inhibited = case.get("inhibited", False if "firing" in case else None)

    matches = router.match(labels)
    actual = _receivers_of(matches)
    inhibition = inhibitor.inhibited_by(labels, firing) if firing else None
    expected, missing, unexpected, ordered, routed_ok = None, [], [], False, True
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
    elif "drop" in case:
        expected = []

    if expected is not None:
        missing = list((Counter(expected) - Counter(actual)).elements())
        unexpected = list((Counter(actual) - Counter(expected)).elements())
        routed_ok = actual == expected if ordered else not missing and not unexpected
    inhibited_ok = expect_inhibited is None or expect_inhibited == (inhibition is not None)
    return {
        "name": name, "labels": labels, "passed": routed_ok and inhibited_ok,
        "expected": None if expected is None else ", ".join(expected) or "(no receiver)",
        "actual": ", ".join(actual) or "(no receiver)",
        "missing": missing, "unexpected": unexpected,
        "routes": [m.path[-1] for m in matches],
        "order_only": ordered and not routed_ok and not missing and not unexpected,
        "firing": firing,
        "expected_inhibited": expect_inhibited,
        "inhibited": inhibition is not None,
        "inhibited_by": _inhibition_json(inhibition, smap),
    }, matches


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _print_test_result(r: dict, config: str) -> None:
    if r["passed"]:
        console.print(f"  [bold green]✓[/bold green]  {escape(r['name'])}")
        return
    console.print(f"  [bold red]✗[/bold red]  {escape(r['name'])}")
    if "error" in r:
        console.print(f"     [red]{escape(r['error'])}[/red]")
    else:
        console.print(f"     [dim]labels:[/dim]    {escape(_format_labels(r['labels']))}")
        for alert in r["firing"]:
            console.print(f"     [dim]firing:[/dim]    {escape(_format_labels(alert))}")
        if r["expected"] is not None:
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
        if r["expected_inhibited"] is not None:
            console.print(f"     [dim]inhibited:[/dim] expected {_yes_no(r['expected_inhibited'])}"
                          f", actual {_yes_no(r['inhibited'])}")
        by = r["inhibited_by"]
        if by:
            console.print(
                f"       [dim]by[/dim] {escape(by['rule'])}  [dim]·[/dim]  "
                f"{escape(_display(config))}:{by['line']}:{by['column']}  [dim]·  while firing:"
                f"[/dim] {escape(_format_labels(by['source']))}", soft_wrap=True,
            )
        console.print(f"     [dim]routes:[/dim]    {escape(', '.join(r['routes']))}")
    console.print()


def _coverage_percent(cov) -> float:
    """Coverage as shown, to one decimal; 100.0 only when every route is covered."""
    percent = round(cov.percent, 1)
    return 99.9 if percent == 100 and cov.uncovered else percent


def _coverage_json(cov, smap, fail_under) -> dict:
    uncovered = []
    for path in cov.uncovered:
        line, col = smap.locate(path)
        uncovered.append({"path": path, "line": line, "column": col})
    return {
        "routes": len(cov.routes), "covered": len(cov.covered),
        "percent": _coverage_percent(cov), "fail_under": fail_under, "uncovered": uncovered,
    }


def _route_summary(node: dict, path: str) -> str:
    parts = _format_matchers(node)
    piece = "{" + ", ".join(parts) + "}" if parts else (
        "(root)" if path == "route" else "(catch-all)"
    )
    return f"{piece}  →  {node['receiver']}" if node.get("receiver") else piece


def _print_coverage(cov, cfg: dict, smap, config: str, fail_under) -> None:
    percent = _coverage_percent(cov)
    console.print(f"  [bold]Route coverage[/bold]  {len(cov.covered)} of {len(cov.routes)} "
                  f"routes  ·  {percent:.1f}%")
    if cov.uncovered:
        nodes = {path: node for node, path in _walk_routes(cfg["route"])}
        console.print("  [dim]Not matched by any test case:[/dim]")
        rows = [(path, "{}:{}:{}".format(_display(config), *smap.locate(path)))
                for path in cov.uncovered]
        path_w = max(len(path) for path, _ in rows)
        loc_w = max(len(loc) for _, loc in rows)
        for path, loc in rows:
            row = Text(f"    {path.ljust(path_w)}   {loc.ljust(loc_w)}   ")
            row.append(_route_summary(nodes[path], path), style="dim")
            console.print(row, soft_wrap=True)  # keep file:line:col in one piece
    if fail_under is not None and percent < fail_under:
        console.print(f"  [red]✖ Route coverage {percent:.1f}% is below --fail-under "
                      f"{fail_under:g}%[/red]")
    console.print()


def _cmd_test(args) -> int:
    loaded = _load_input(args.config, _read(args.config), **_input_options(args))
    cfg = loaded.cfg
    router = _router(cfg)
    if router is None:
        return 2

    cases = _load_test_cases(args.tests)
    if cases is None:
        err_console.print(
            f"[red]Invalid test file:[/red] {escape(args.tests)} must be a list of test cases, "
            f"or have a top-level 'tests:' list."
        )
        return 2

    inhibitor = None
    if any(isinstance(case, dict) and "firing" in case for case in cases):
        inhibitor = _inhibitor(cfg)
        if inhibitor is None:
            return 2
    coverage = args.coverage or args.fail_under is not None
    smap = loaded if coverage or inhibitor is not None else None

    runs = [_run_test_case(router, inhibitor, smap, i, case) for i, case in enumerate(cases)]
    results = [r for r, _ in runs]
    cov = route_coverage(router, (matches for _, matches in runs)) if coverage else None
    below = (cov is not None and args.fail_under is not None
             and _coverage_percent(cov) < args.fail_under)

    if args.format == "json":
        doc = results if cov is None else {
            "tests": results, "coverage": _coverage_json(cov, smap, args.fail_under),
        }
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        console.print()
        for r in results:
            _print_test_result(r, args.config)
        passed_n = sum(1 for r in results if r["passed"])
        failed_n = len(results) - passed_n
        console.print(Rule(style="dim"))
        if failed_n:
            console.print(f"  [red]{failed_n} failed[/red]  ·  [green]{passed_n} passed[/green]\n")
        else:
            console.print(f"  [green]{passed_n} passed[/green]\n")
        if cov is not None:
            _print_coverage(cov, cfg, smap, args.config, args.fail_under)

    return 1 if below or any(not r["passed"] for r in results) else 0


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
            grid.add_row(Text(what), Text(f"{d['old']} → {d['new']}"))
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
                                       only=only or None, **_input_options(args))
    new_cfg, new_findings = _lint_file(args.new, ignore=ignore, severity=severity,
                                       only=only or None, **_input_options(args))
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
        tree = None if args.no_routing else mermaid.changed_routes(old_cfg.get("route"),
                                                                   new_cfg.get("route"))
        print(diff_report(_display(args.old), _display(args.new), added, fixed, unchanged,
                          rdiff, skipped, routing=not args.no_routing, tree=tree), end="")
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
        console.print(f"     {escape(f.msg)}")
        console.print()
    for f in added:
        console.print(f"  [bold red]NEW  [/bold red]  {escape(f'[{f.code}]')}"
                      f"  ·  {escape(_location(args.new, f))}"
                      + (f"  ·  [dim]{escape(f.where)}[/dim]" if f.where else ""),
                      soft_wrap=True)
        console.print(f"     {escape(f.msg)}")
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
            Text(e["summary"]),
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
        err_console.print(f"[red]Unknown check code:[/red] {escape(code)}\n")
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
    console.print(f"  {escape(e['summary'])}")
    console.print()
    if e.get("why"):
        console.print("  [bold]Why this matters:[/bold]")
        console.print(f"  {escape(e['why'])}")
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


def _percentage(value: str) -> float:
    try:
        percent = float(value)
    except ValueError:
        percent = -1.0
    if not 0 <= percent <= 100:
        raise argparse.ArgumentTypeError(f"expected a percentage from 0 to 100, got {value!r}")
    return percent


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="amlint",
        description="Semantic linter for Alertmanager configs. Catches what amtool misses.",
    )
    p.add_argument("--version", action="version", version=f"amlint {_VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    inputs = argparse.ArgumentParser(add_help=False)
    inputs.add_argument(
        "--input-format", choices=("auto",) + FORMATS, default="auto", dest="input_format",
        help="what the config file is: a plain alertmanager.yml, a Kubernetes Secret or "
             "ConfigMap, kube-prometheus-stack Helm values, a Mimir/Cortex config, or "
             "prometheus-operator AlertmanagerConfig resources (default: auto, detected)",
    )
    inputs.add_argument(
        "--matcher-strategy", choices=MATCHER_STRATEGIES, dest="matcher_strategy",
        help="alertmanagerConfigMatcherStrategy for AlertmanagerConfig resources (default: "
             "the Alertmanager resource's in the input, else OnNamespace)",
    )

    pc = sub.add_parser("check", parents=[inputs], help="validate one or more config files")
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
    pc.add_argument("--rules", action="append", metavar="GLOB",
                    help="Prometheus rule files (or PrometheusRule manifests) to check routes "
                         "and inhibit rules against; quote the glob, repeat the flag for more")

    pd = sub.add_parser("diff", parents=[inputs],
                        help="show findings that changed between two configs")
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

    pt = sub.add_parser("tree", parents=[inputs],
                        help="print route tree with matchers, receivers, and issues")
    pt.add_argument("file", help="path to alertmanager.yml, or - for stdin")
    pt.add_argument("--ignore", action="append", metavar="CODE",
                    help="skip findings with these codes when annotating the tree")
    pt.add_argument("--format", choices=["text", "mermaid"], default="text",
                    help="mermaid prints a Mermaid flowchart, which GitHub renders in Markdown")

    pr = sub.add_parser("route", parents=[inputs],
                        help="show which route(s) and receiver(s) a label set reaches")
    pr.add_argument("config", help="path to alertmanager.yml, or - for stdin")
    pr.add_argument("labels", nargs="*", metavar="label=value",
                    help="alert labels, e.g. severity=critical team=infra")
    pr.add_argument("--firing", action="append", metavar="LABELS",
                    help="another alert firing at the same time, e.g. "
                         "'alertname=NodeDown,instance=a' (repeat the flag for more); shows "
                         "whether an inhibit rule mutes this alert")
    pr.add_argument("--format", choices=["text", "json"], default="text")

    ptst = sub.add_parser("test", parents=[inputs],
                          help="run routing unit tests against a config")
    ptst.add_argument("config", help="path to alertmanager.yml")
    ptst.add_argument("tests", help="path to a YAML file with a list of test cases")
    ptst.add_argument("--format", choices=["text", "json"], default="text")
    ptst.add_argument("--coverage", action="store_true",
                      help="list the routes no test case reaches, and the share that is reached")
    ptst.add_argument("--fail-under", type=_percentage, metavar="N", dest="fail_under",
                      help="exit 1 if route coverage is below N%% (implies --coverage)")

    sub.add_parser("init", help="print a minimal valid alertmanager.yml to stdout")

    sub.add_parser("list", help="list all check codes with level and description")

    pe = sub.add_parser("explain", help="show description and examples for a check code")
    pe.add_argument("code", help="check code to explain, e.g. undefined-receiver")

    sub.add_parser("lsp", help="run a language server over stdio, for editors such as VS Code",
                   description="Language Server Protocol server over stdin/stdout. Your "
                               "editor starts it; it shows findings as you type and what a "
                               "route does on hover.")

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
    if args.cmd == "lsp":
        from .lsp import main as lsp_main
        lsp_main()  # ends the process


if __name__ == "__main__":
    sys.exit(main())
