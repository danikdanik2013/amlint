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
    WARN: _c("33", "WARN "),
    INFO: _c("36", "INFO "),
}


def load(path):
    if not os.path.exists(path):
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(2)
    with open(path) as f:
        try:
            return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            print(f"Failed to parse YAML: {e}", file=sys.stderr)
            sys.exit(2)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="amlint",
        description="Semantic linter for Alertmanager configs. Catches what amtool misses.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("check", help="validate a config file")
    pc.add_argument("file", help="path to alertmanager.yml")
    pc.add_argument("--format", choices=["text", "json"], default="text")
    pc.add_argument("--strict", action="store_true", help="exit non-zero on WARN as well")

    args = p.parse_args(argv)
    cfg = load(args.file)
    findings = lint(cfg)

    if args.format == "json":
        print(json.dumps(
            [{"level": f.level, "code": f.code, "message": f.msg, "where": f.where} for f in findings],
            ensure_ascii=False, indent=2,
        ))
    else:
        if not findings:
            print(_c("32", "  \u2713 Проблем не знайдено.\n"))
        else:
            print()
            for f in findings:
                loc = _c("2", f"  {f.where}") if f.where else ""
                print(f"  {BADGE[f.level]}  {f.msg}")
                if loc:
                    arrow = "\u21b3 "
                    print(f"  {_c('2', arrow + f.where)}  {_c('2', '[' + f.code + ']')}")
                print()
            errs = sum(1 for f in findings if f.level == ERROR)
            warns = sum(1 for f in findings if f.level == WARN)
            infos = sum(1 for f in findings if f.level == INFO)
            print(f"  {errs} error \u00b7 {warns} warn \u00b7 {infos} info\n")

    has_err = any(f.level == ERROR for f in findings)
    has_warn = any(f.level == WARN for f in findings)
    if has_err or (args.strict and has_warn):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
