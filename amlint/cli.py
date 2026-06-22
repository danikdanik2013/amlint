#!/usr/bin/env python3
"""amlint CLI."""

import argparse
import json
import os
import sys

try:
    import yaml
except ImportError:
    print("pyyaml is required:  pip install pyyaml", file=sys.stderr)
    sys.exit(2)

from .linter import lint, ERROR, WARN, INFO

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


BADGE = {
    ERROR: _c("31", "ERROR"),
    WARN:  _c("33", "WARN "),
    INFO:  _c("36", "INFO "),
}


def load(path):
    try:
        if path == "-":
            content = sys.stdin.read()
        else:
            if not os.path.exists(path):
                print(f"File not found: {path}", file=sys.stderr)
                sys.exit(2)
            with open(path) as f:
                content = f.read()
        return yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        print(f"Failed to parse YAML ({path}): {e}", file=sys.stderr)
        sys.exit(2)


def _print_findings(findings, label=None):
    if label:
        print(_c("1", f"\n── {label} ──"))
    if not findings:
        print(_c("32", "  ✓ No issues found.\n"))
        return
    print()
    for f in findings:
        print(f"  {BADGE[f.level]}  {f.msg}")
        if f.where:
            arrow = "↳ "
            print(f"  {_c('2', arrow + f.where)}  {_c('2', '[' + f.code + ']')}")
        print()
    errs  = sum(1 for f in findings if f.level == ERROR)
    warns = sum(1 for f in findings if f.level == WARN)
    infos = sum(1 for f in findings if f.level == INFO)
    print(f"  {errs} error · {warns} warn · {infos} info\n")


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="amlint",
        description="Semantic linter for Alertmanager configs. Catches what amtool misses.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check", help="validate one or more config files")
    pc.add_argument("files", nargs="+", metavar="file",
                    help="path(s) to alertmanager.yml, or - for stdin")
    pc.add_argument("--format", choices=["text", "json"], default="text")
    pc.add_argument("--strict", action="store_true", help="exit non-zero on WARN as well")

    args = p.parse_args(argv)
    multi = len(args.files) > 1

    all_findings = []
    for path in args.files:
        cfg = load(path)
        findings = lint(cfg)
        all_findings.append((path, findings))

    if args.format == "json":
        print(json.dumps(
            [
                {"file": path, "level": f.level, "code": f.code,
                 "message": f.msg, "where": f.where}
                for path, findings in all_findings
                for f in findings
            ],
            ensure_ascii=False, indent=2,
        ))
    else:
        for path, findings in all_findings:
            _print_findings(findings, label=path if multi else None)

    has_err  = any(f.level == ERROR for _, fs in all_findings for f in fs)
    has_warn = any(f.level == WARN  for _, fs in all_findings for f in fs)
    if has_err or (args.strict and has_warn):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
