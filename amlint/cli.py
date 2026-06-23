#!/usr/bin/env python3
"""amlint CLI."""

import argparse
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
    _VERSION = "0.1.2"

try:
    import argcomplete
    _ARGCOMPLETE = True
except ImportError:
    _ARGCOMPLETE = False

from rich.console import Console
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .config import load_project_config
from .linter import ERROR, INFO, WARN, lint

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


def load(path):
    try:
        if path == "-":
            content = sys.stdin.read()
        else:
            if not os.path.exists(path):
                err_console.print(f"[red]File not found:[/red] {path}")
                sys.exit(2)
            with open(path) as f:
                content = f.read()
        return yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        err_console.print(f"[red]Failed to parse YAML[/red] ({path}): {e}")
        sys.exit(2)


def _print_findings(findings, label=None):
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
        if f.where:
            header.append(f"  ·  {f.where}", style="dim")
        console.print(header)
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
                        "region": {"startLine": 1},
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


def _cmd_check(args):
    proj = load_project_config()
    ignore = _build_ignore(args.ignore, proj.get("ignore"))
    only = _build_only(args.only)
    strict = args.strict or proj.get("strict", False)
    severity = proj.get("severity") or {}

    multi = len(args.files) > 1
    all_findings = []
    for path in args.files:
        cfg = load(path)
        basedir = os.path.dirname(os.path.abspath(path)) if path != "-" else None
        all_findings.append((path, lint(
            cfg, ignore=ignore, severity=severity, only=only or None, basedir=basedir,
        )))

    if args.format == "json":
        print(json.dumps(
            [{"file": path, "level": f.level, "code": f.code,
              "message": f.msg, "where": f.where}
             for path, findings in all_findings for f in findings],
            ensure_ascii=False, indent=2,
        ))
    elif args.format == "sarif":
        print(json.dumps(_to_sarif(all_findings, _VERSION), ensure_ascii=False, indent=2))
    else:
        for path, findings in all_findings:
            _print_findings(findings, label=path if multi else None)

    if getattr(args, "exit_zero", False):
        return 0
    has_err  = any(f.level == ERROR for _, fs in all_findings for f in fs)
    has_warn = any(f.level == WARN  for _, fs in all_findings for f in fs)
    return 1 if has_err or (strict and has_warn) else 0


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


def _cmd_diff(args):
    proj = load_project_config()
    ignore = _build_ignore(args.ignore, proj.get("ignore"))
    only = _build_only(getattr(args, "only", None))
    severity = proj.get("severity") or {}
    old_findings = lint(load(args.old), ignore=ignore, severity=severity, only=only or None)
    new_findings = lint(load(args.new), ignore=ignore, severity=severity, only=only or None)

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
        print(json.dumps({
            "added":  [{"code": f.code, "level": f.level, "message": f.msg, "where": f.where}
                       for f in added],
            "fixed":  [{"code": f.code, "level": f.level, "message": f.msg, "where": f.where}
                       for f in fixed],
            "unchanged": unchanged,
        }, ensure_ascii=False, indent=2))
        return 1 if added else 0

    if not added and not fixed:
        console.print("\n  [bold green]✓[/bold green]  No changes in findings.\n")
        return 0

    console.print()
    for f in fixed:
        console.print(f"  [bold green]FIXED[/bold green]  [{f.code}]"
                      + (f"  ·  [dim]{f.where}[/dim]" if f.where else ""))
        console.print(f"     {f.msg}")
        console.print()
    for f in added:
        console.print(f"  [bold red]NEW  [/bold red]  [{f.code}]"
                      + (f"  ·  [dim]{f.where}[/dim]" if f.where else ""))
        console.print(f"     {f.msg}")
        console.print()

    console.print(Rule(style="dim"))
    console.print(
        f"  [green]{len(fixed)} fixed[/green]"
        f"  ·  [red]{len(added)} new[/red]"
        f"  ·  {unchanged} unchanged\n"
    )
    return 1 if added else 0


def _cmd_list():
    from .explains import EXPLAINS
    level_order = {ERROR: 0, WARN: 1, INFO: 2}
    sorted_items = sorted(
        EXPLAINS.items(),
        key=lambda kv: (level_order.get(kv[1]["level"].split()[0], 3), kv[0]),
    )
    table = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("code", no_wrap=True)
    table.add_column("level", no_wrap=True)
    table.add_column("description")
    for code, e in sorted_items:
        level = e["level"].split()[0]
        table.add_row(
            Text(code, style="dim"),
            Text(level, style=STYLE[level]),
            e["summary"],
        )
    console.print()
    console.print(table)
    console.print()
    return 0


def _cmd_explain(args):
    from .explains import EXPLAINS
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
    pc.add_argument("--format", choices=["text", "json", "sarif"], default="text")
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
    pd.add_argument("--format", choices=["text", "json"], default="text")
    pd.add_argument("--ignore", action="append", metavar="CODE",
                    help="skip findings with these codes")
    pd.add_argument("--only", action="append", metavar="CODE",
                    help="consider only findings with these codes")

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
    if args.cmd == "init":
        return _cmd_init()
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "explain":
        return _cmd_explain(args)


if __name__ == "__main__":
    sys.exit(main())
