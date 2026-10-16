#!/usr/bin/env python3
"""Build the playground's generated files into docs/playground/dist/ (not committed):

- the amlint wheel that Pyodide installs in the browser,
- codemirror.js, the editor bundle (esbuild, versions pinned in package-lock.json),
- manifest.json: the wheel's file name, the version and commit, the examples, and the
  docs page of each check.

Run it before `mkdocs serve` or `mkdocs build`:

    python playground/build.py              # needs pip and npm
    python playground/build.py --no-editor  # keep the existing codemirror.js
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "docs", "playground", "dist")
REAL_WORLD = os.path.join(ROOT, "testdata", "real-world")

# Shown in the Examples menu, in this order. The first one loads when the page opens.
EXAMPLES = [
    {
        "id": "common-mistakes",
        "title": "Common mistakes",
        "description": "A config amtool accepts that still drops and mutes alerts",
        "config": os.path.join(HERE, "examples", "common-mistakes.yml"),
    },
    {
        "id": "team-routing",
        "title": "Team routing",
        "description": "Alertmanager's documentation example: teams, a continue fan-out, "
                       "muting and inhibition",
        "config": os.path.join(REAL_WORLD, "team-routing.yml"),
        "rules": os.path.join(REAL_WORLD, "rules", "team-routing.yml"),
        "labels": "alertname=DatabaseReplicationLag service=database severity=warning "
                  "cluster=eu1",
        "firing": "alertname=DatabaseReplicationLag service=database severity=critical "
                  "cluster=eu1",
    },
    {
        "id": "team-routing-change",
        "title": "Team routing: a risky change",
        "description": "The diff of a small edit that stops database warnings from "
                       "reaching Slack",
        "config": os.path.join(HERE, "examples", "team-routing-change.yml"),
        "before": os.path.join(REAL_WORLD, "team-routing.yml"),
        "tab": "diff",
    },
    {
        "id": "kube-prometheus",
        "title": "kube-prometheus",
        "description": "The kube-prometheus defaults: severity inhibition, InfoInhibitor "
                       "and Watchdog",
        "config": os.path.join(REAL_WORLD, "kube-prometheus-style.yml"),
        "rules": os.path.join(REAL_WORLD, "rules", "kube-prometheus-style.yaml"),
        "labels": "alertname=KubePodCrashLooping namespace=shop severity=warning",
        "firing": "alertname=KubePodCrashLooping namespace=shop severity=critical",
    },
]


def _run(cmd, cwd=ROOT):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def build_wheel():
    for name in os.listdir(DIST):
        if name.endswith(".whl"):
            os.remove(os.path.join(DIST, name))
    _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--quiet", "--wheel-dir", DIST,
          ROOT])
    wheels = [n for n in os.listdir(DIST) if n.startswith("amlint-") and n.endswith(".whl")]
    if len(wheels) != 1:
        sys.exit(f"expected one amlint wheel in {DIST}, found {wheels}")
    return wheels[0]


def build_editor():
    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm is needed to bundle the editor (or pass --no-editor)")
    if not os.path.isdir(os.path.join(HERE, "node_modules")):
        _run([npm, "ci", "--no-audit", "--no-fund"], cwd=HERE)
    _run([npm, "run", "--silent", "build:editor"], cwd=HERE)


def examples():
    out = []
    for ex in EXAMPLES:
        item = dict(ex)
        for key in ("config", "rules", "before"):
            if key in item:
                item[key] = _read(item[key])
        out.append(item)
    return out


def check_docs():
    """{check code: its page under docs/}, from the `## code` headings of docs/checks/."""
    checks = os.path.join(ROOT, "docs", "checks")
    docs = {}
    for name in sorted(os.listdir(checks)):
        if name.endswith(".md") and name != "index.md":
            for line in _read(os.path.join(checks, name)).splitlines():
                m = re.match(r"## ([a-z0-9-]+)\s*$", line)
                if m:
                    docs[m.group(1)] = f"checks/{name[:-3]}/"
    return docs


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--no-editor", action="store_true",
                   help="don't rebuild codemirror.js (it must already exist)")
    args = p.parse_args()

    os.makedirs(DIST, exist_ok=True)
    if args.no_editor:
        if not os.path.exists(os.path.join(DIST, "codemirror.js")):
            sys.exit("--no-editor: docs/playground/dist/codemirror.js doesn't exist yet")
    else:
        build_editor()
    wheel = build_wheel()
    version = wheel.split("-")[1]
    manifest = {"wheel": wheel, "version": version, "commit": _commit(),
                "examples": examples(), "docs": check_docs()}
    with open(os.path.join(DIST, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"playground: {os.path.relpath(DIST, ROOT)}/ has {wheel}, codemirror.js "
          f"and manifest.json ({len(manifest['examples'])} examples)")


if __name__ == "__main__":
    main()
