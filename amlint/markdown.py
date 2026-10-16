"""
Markdown report for `amlint diff --format markdown`, sized for a pull-request comment.

Everything that comes from a config (label values, receiver names, messages quoting them) is
escaped, so it renders as the literal text: no Markdown, no HTML, no @mentions.
"""

import re
from typing import List, Optional

from .linter import ERROR, INFO, WARN

MAX_ROWS = 50  # per table, so a large config can't produce a giant comment

_LEVEL = {ERROR: "✖ error", WARN: "⚠ warning", INFO: "ℹ info"}
_SPECIAL = re.compile(r"([\\`*_\[\]<>|~&])")
_NEWLINES = re.compile(r"[\r\n]+")


def code(value) -> str:
    """`value` as an inline code span that is safe inside a table cell."""
    text = _NEWLINES.sub(" ", str(value))
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    if text.startswith(("`", " ")) or text.endswith(("`", " ")):
        text = f" {text} "  # CommonMark strips one space from each side
    return fence + text.replace("|", "\\|") + fence


def text(value) -> str:
    """`value` as plain Markdown text that renders literally."""
    escaped = _SPECIAL.sub(r"\\\1", _NEWLINES.sub(" ", str(value)))
    return escaped.replace("@", "@&#8203;")  # a zero-width space stops @mentions


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _labels(labels: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in labels.items()) if labels else "(no labels)"


def _table(header: List[str], rows: List[List[str]], more: str) -> List[str]:
    out = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    out += ["| " + " | ".join(row) + " |" for row in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        out += ["", f"…and {len(rows) - MAX_ROWS} more {more}"]
    return out + [""]


def _routing_rows(rdiff):
    receivers, settings = [], []
    for c in rdiff.changes:
        labels = code(_labels(c.labels))
        if c.similar:
            labels += f" (+{c.similar} similar)"
        for d in c.differences():
            if d["setting"] == "receivers":
                receivers.append([labels, code(d["old"]), code(d["new"])])
            else:
                receiver = "—" if d["subject"] == "routes" else code(d["subject"])
                settings.append([labels, receiver, code(d["setting"]),
                                 code(d["old"]), code(d["new"])])
    return receivers, settings


def _finding_rows(path: str, findings) -> List[List[str]]:
    rows = []
    for f in findings:
        location = code(f"{path}:{f.line}:{f.col}")
        if f.where:
            location += f" ({code(f.where)})"
        rows.append([_LEVEL[f.level], code(f.code), location, text(f.msg)])
    return rows


def diff_report(old: str, new: str, added, fixed, unchanged: int,
                rdiff=None, skipped: Optional[str] = None, routing: bool = True) -> str:
    """The report: a summary line, routing changes, then new and fixed findings.
    `old` / `new` are the file names shown in finding locations."""
    summary = []
    if routing and rdiff is not None:
        n = len(rdiff.changes)
        summary.append(f"**{_plural(n, 'routing change')}**" if n else "no routing changes")
    elif routing:
        summary.append("routing not compared")
    summary.append(f"**{_plural(len(added), 'new finding')}**" if added else "no new findings")
    summary += [f"{len(fixed)} fixed", f"{unchanged} unchanged"]
    if routing and rdiff is not None:
        checked = _plural(rdiff.checked, "label set") + " checked"
        if rdiff.truncated:
            checked += " (limit reached, not every combination was tried)"
        summary.append(checked)
    line = " · ".join(summary)

    out = ["### amlint diff", "", line[0].upper() + line[1:], ""]
    if routing and rdiff is None:
        out += [f"Routing not compared: {text(skipped)}", ""]
    elif routing:
        receivers, settings = _routing_rows(rdiff)
        if receivers:
            out += ["#### Receivers", ""]
            out += _table(["labels", "before", "after"], receivers, "label sets")
        if settings:
            out += ["#### Grouping and timings", ""]
            out += _table(["labels", "receiver", "setting", "before", "after"], settings,
                          "changed settings")
    header = ["level", "check", "location", "message"]
    if added:
        out += ["#### New findings", ""]
        out += _table(header, _finding_rows(new, added), "new findings")
    if fixed:
        out += ["#### Fixed findings", ""]
        out += _table(header, _finding_rows(old, fixed), "fixed findings")
    return "\n".join(out).rstrip("\n") + "\n"
