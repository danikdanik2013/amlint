#!/usr/bin/env python3
"""
Write docs/llms-full.txt: every documentation page in MkDocs nav order, concatenated into
one plain-text file that AI assistants can read in a single request. docs/llms.txt is the
hand-written index that links to it.

    python scripts/build_llms_full.py

The docs workflow runs this before `mkdocs build`, which publishes the file at
https://danikdanik2013.github.io/amlint/llms-full.txt. The output is generated, so it's
git-ignored.
"""

import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
SITE = "https://danikdanik2013.github.io/amlint/"
OUT = os.path.join(DOCS, "llms-full.txt")


def _pages(nav):
    """Markdown paths from the MkDocs nav, depth-first, in the order the site shows them."""
    for item in nav:
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for value in item.values():
                if isinstance(value, str):
                    yield value
                elif isinstance(value, list):
                    yield from _pages(value)


def _url(page: str) -> str:
    path = page[: -len(".md")]
    if path == "index":
        return SITE
    if path.endswith("/index"):
        path = path[: -len("index")]
    else:
        path += "/"
    return SITE + path


class _Loader(yaml.SafeLoader):
    """SafeLoader that reads MkDocs-specific tags (!ENV, !!python/name:...) as null:
    only `nav` is needed here."""


_Loader.add_multi_constructor("", lambda loader, suffix, node: None)


def main() -> int:
    with open(os.path.join(ROOT, "mkdocs.yml")) as f:
        config = yaml.load(f, Loader=_Loader)
    parts = [
        "# amlint — full documentation\n\n"
        "> Linter for Prometheus Alertmanager configs. Every page of "
        f"{SITE} in one file. Index: {SITE}llms.txt\n"
    ]
    for page in _pages(config.get("nav", [])):
        path = os.path.join(DOCS, page)
        if not os.path.isfile(path):
            print(f"build_llms_full: {page} is in the nav but missing", file=sys.stderr)
            return 1
        with open(path, encoding="utf-8") as f:
            body = f.read().strip()
        parts.append(f"\n\n---\n\nSource: {_url(page)}\n\n{body}\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print(f"wrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
