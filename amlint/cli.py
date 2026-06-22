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
    _VERSION = "0.1.1"

try:
    import argcomplete
    _ARGCOMPLETE = True
except ImportError:
    _ARGCOMPLETE = False

from .linter import ERROR, INFO, WARN, lint

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
            print(f"  {_c('2', '↳ ' + f.where)}  {_c('2', '[' + f.code + ']')}")
        print()
    errs  = sum(1 for f in findings if f.level == ERROR)
    warns = sum(1 for f in findings if f.level == WARN)
    infos = sum(1 for f in findings if f.level == INFO)
    print(f"  {errs} error · {warns} warn · {infos} info\n")


def _cmd_check(args):
    multi = len(args.files) > 1
    all_findings = []
    for path in args.files:
        cfg = load(path)
        all_findings.append((path, lint(cfg)))

    if args.format == "json":
        print(json.dumps(
            [{"file": path, "level": f.level, "code": f.code,
              "message": f.msg, "where": f.where}
             for path, findings in all_findings for f in findings],
            ensure_ascii=False, indent=2,
        ))
    else:
        for path, findings in all_findings:
            _print_findings(findings, label=path if multi else None)

    has_err  = any(f.level == ERROR for _, fs in all_findings for f in fs)
    has_warn = any(f.level == WARN  for _, fs in all_findings for f in fs)
    return 1 if has_err or (args.strict and has_warn) else 0


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
    old_findings = lint(load(args.old))
    new_findings = lint(load(args.new))

    def key(f):
        return (f.code, f.where)

    old_counts = Counter(key(f) for f in old_findings)
    new_counts = Counter(key(f) for f in new_findings)
    added_keys  = new_counts - old_counts
    fixed_keys  = old_counts - new_counts

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
        print(_c("32", "\n  ✓ No changes in findings.\n"))
        return 0

    print()
    for f in fixed:
        print(f"  {_c('32', 'FIXED')}  {f.msg}")
        if f.where:
            print(f"  {_c('2', '↳ ' + f.where)}  {_c('2', '[' + f.code + ']')}")
        print()
    for f in added:
        print(f"  {_c('31', 'NEW  ')}  {f.msg}")
        if f.where:
            print(f"  {_c('2', '↳ ' + f.where)}  {_c('2', '[' + f.code + ']')}")
        print()

    print(f"  {_c('32', str(len(fixed)) + ' fixed')} · "
          f"{_c('31', str(len(added)) + ' new')} · "
          f"{unchanged} unchanged\n")
    return 1 if added else 0


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
    pc.add_argument("--format", choices=["text", "json"], default="text")
    pc.add_argument("--strict", action="store_true", help="exit non-zero on WARN as well")

    pd = sub.add_parser("diff", help="show findings that changed between two configs")
    pd.add_argument("old", help="baseline config")
    pd.add_argument("new", help="updated config")
    pd.add_argument("--format", choices=["text", "json"], default="text")

    sub.add_parser("init", help="print a minimal valid alertmanager.yml to stdout")

    if _ARGCOMPLETE:
        argcomplete.autocomplete(p)

    args = p.parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args)
    if args.cmd == "diff":
        return _cmd_diff(args)
    if args.cmd == "init":
        return _cmd_init()


if __name__ == "__main__":
    sys.exit(main())
