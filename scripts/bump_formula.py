#!/usr/bin/env python3
"""
Point Formula/amlint.rb at a release's sdist on PyPI. The release workflow runs it after
the upload to PyPI; you can run it by hand too.

    python scripts/bump_formula.py 0.6.0
    python scripts/bump_formula.py 0.6.0 --pyproject /tmp/pyproject.toml --wait 600

It reads the sdist's URL and sha256 from https://pypi.org/pypi/amlint/<version>/json,
retrying for --wait seconds because PyPI can lag behind the upload. It downloads the sdist,
checks its sha256 and rewrites only the formula's top-level `url` and `sha256` lines.

It never touches the `resource` blocks. So it stops, without changing the formula, when the
runtime dependencies in pyproject.toml differ from the formula's `# dependencies:` comment:
the resources then need a refresh by hand. It also leaves the formula alone for a
pre-release, or for a version older than the one it points at.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPI_JSON = "https://pypi.org/pypi/amlint/{version}/json"
SDIST_HOST = "https://files.pythonhosted.org/"

# Top-level lines only: they are indented by two spaces, those in `resource` blocks by four.
URL_LINE = re.compile(r'^  url "([^"]*)"$', re.M)
SHA256_LINE = re.compile(r'^  sha256 "([0-9a-f]*)"$', re.M)
DEPENDENCIES_LINE = re.compile(r"^  # dependencies: (.*)$", re.M)


def _fail(msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::error title=Homebrew formula::" + msg.replace("\n", "%0A"))
    sys.exit(msg)


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 (fixed https URLs)
        return resp.read()


def _release(version: str):
    """(1, 2, 3) for a final release like 1.2.3; None for a pre-release or anything else."""
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _normalize(dependencies) -> list:
    return sorted(re.sub(r"\s+", "", d).lower() for d in dependencies)


def check_dependencies(formula: str, pyproject: dict) -> None:
    """Stop when the runtime dependencies changed since the resources were last refreshed."""
    wanted = _normalize(pyproject["project"].get("dependencies", []))
    comment = "# dependencies: " + ", ".join(wanted)
    m = DEPENDENCIES_LINE.search(formula)
    if m is None:
        _fail(f"Formula/amlint.rb has no '# dependencies:' comment. Add this line above "
              f"the resource blocks:\n  {comment}")
    if _normalize(m.group(1).split(", ")) != wanted:
        _fail(
            "The runtime dependencies changed since the formula's resource blocks were made.\n"
            f"  formula:        {m.group(1)}\n"
            f"  pyproject.toml: {', '.join(wanted)}\n"
            "Formula/amlint.rb was not changed. Update it by hand:\n"
            f"  1. Set the comment above the resource blocks to:\n     {comment}\n"
            "  2. Run this script again to point url and sha256 at the new sdist.\n"
            "  3. Refresh the resource blocks. With the tap installed, this prints them:\n"
            "     brew update-python-resources --print-only --version <version> \\\n"
            "       danikdanik2013/amlint/amlint\n"
            "  4. Commit 'Homebrew: amlint <version>' to main."
        )


def pypi_sdist(version: str, wait: float) -> tuple:
    """The sdist's URL and sha256, from PyPI's JSON API."""
    url = PYPI_JSON.format(version=version)
    deadline = time.monotonic() + wait
    while True:
        try:
            release = json.loads(_get(url))
            sdists = [f for f in release["urls"] if f["packagetype"] == "sdist"]
            if sdists:
                return sdists[0]["url"], sdists[0]["digests"]["sha256"]
            problem = "no sdist yet"
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            problem = "404"
        except urllib.error.URLError as e:
            problem = str(e.reason)
        if time.monotonic() >= deadline:
            _fail(f"PyPI has no sdist for amlint {version} at {url} ({problem})")
        print(f"PyPI: {problem} for amlint {version}, retrying in 15s", flush=True)
        time.sleep(15)


def bump(formula: str, url: str, sha256: str) -> str:
    for name, pattern in (("url", URL_LINE), ("sha256", SHA256_LINE)):
        if len(pattern.findall(formula)) != 1:
            _fail(f"Formula/amlint.rb should have exactly one top-level {name} line")
    formula = URL_LINE.sub(f'  url "{url}"', formula)
    return SHA256_LINE.sub(f'  sha256 "{sha256}"', formula)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("version", help="the release, for example 0.6.0 or v0.6.0")
    parser.add_argument("--formula", default=os.path.join(ROOT, "Formula", "amlint.rb"))
    parser.add_argument("--pyproject", default=os.path.join(ROOT, "pyproject.toml"),
                        help="the release's pyproject.toml (default: the working tree's)")
    parser.add_argument("--wait", type=float, default=300,
                        help="seconds to keep retrying while PyPI has no sdist (default 300)")
    args = parser.parse_args()
    version = args.version.lstrip("v")

    with open(args.formula) as f:
        formula = f.read()
    pinned = re.search(r'^  url ".*/amlint-([^/"]+)\.tar\.gz"$', formula, re.M)
    pinned = pinned.group(1) if pinned else ""
    if _release(version) is None:
        print(f"{version} is a pre-release: Formula/amlint.rb stays as it is")
        return
    if _release(pinned) and _release(pinned) > _release(version):
        print(f"Formula/amlint.rb already points at {pinned}, newer than {version}")
        return

    with open(args.pyproject, "rb") as f:
        pyproject = tomllib.load(f)
    if pyproject["project"]["version"] != version:
        _fail(f"{args.pyproject} is for version {pyproject['project']['version']}, "
              f"not {version}")
    check_dependencies(formula, pyproject)

    url, sha256 = pypi_sdist(version, args.wait)
    if not url.startswith(SDIST_HOST) or not url.endswith(f"/amlint-{version}.tar.gz"):
        _fail(f"unexpected sdist URL from PyPI: {url}")
    got = hashlib.sha256(_get(url)).hexdigest()
    if got != sha256:
        _fail(f"{url} has sha256 {got}, but PyPI lists {sha256}")

    bumped = bump(formula, url, sha256)
    if bumped == formula:
        print(f"Formula/amlint.rb already points at amlint {version}")
        return
    with open(args.formula, "w") as f:
        f.write(bumped)
    print(f"Formula/amlint.rb now points at {url}")
    print(f"sha256 {sha256}, checked against the download")


if __name__ == "__main__":
    main()
