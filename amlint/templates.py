"""Notification templates: the template files a config loads and the template text in its
notification fields, parsed the way Alertmanager parses them (see gotemplate.py).

Alertmanager parses template files when it loads the config, and refuses a config whose
files don't parse. It parses the text of a notification field, and looks up the templates
it calls, only when it sends a notification; then the notification fails, or with Slack
goes out with that field empty.
"""

import difflib
import fnmatch
import glob as _glob
import os
import posixpath
from typing import Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

from .gotemplate import DATA, GO_TYPE, MEMBERS, line_of, parse
from .linter import ERROR, Finding, _dict_items, _receivers

# DefaultFuncs in Alertmanager 0.34 (template/template.go); Go's own are added by parse().
AM_FUNCS = frozenset({
    "toUpper", "toLower", "title", "join", "match", "safeHtml", "reReplaceAll", "stringSlice",
    "trimSpace", "date", "tz", "since", "humanizeDuration", "safeUrl", "urlUnescape", "toJson",
    "list", "append", "dict", "now", "toDate", "mustToDate", "base64encode", "base64decode",
    "routeLabels",
})

# The templates Alertmanager 0.34 defines itself (template/default.tmpl and email.tmpl).
DEFAULT_TEMPLATES = frozenset({
    "__alertmanager", "__alertmanagerURL", "__subject", "__description", "__text_alert_list",
    "__text_alert_list_markdown",
    "email.default.subject", "email.default.html",
    *(f"slack.default.{n}" for n in (
        "title", "username", "fallback", "callbackid", "pretext", "titlelink", "iconemoji",
        "iconurl", "text", "footer", "color")),
    *(f"pagerduty.default.{n}" for n in ("description", "client", "clientURL", "instances")),
    *(f"opsgenie.default.{n}" for n in ("message", "description", "source")),
    *(f"wechat.default.{n}" for n in ("message", "to_user", "to_party", "to_tag", "agent_id")),
    *(f"victorops.default.{n}" for n in (
        "state_message", "entity_display_name", "monitoring_tool")),
    *(f"pushover.default.{n}" for n in ("title", "message", "url")),
    "sns.default.subject", "sns.default.message", "telegram.default.message",
    *(f"discord.default.{n}" for n in ("content", "title", "message")),
    "webex.default.message",
    *(f"msteams.default.{n}" for n in ("summary", "title", "text")),
    "msteamsv2.default.title", "msteamsv2.default.text",
    *(f"jira.default.{n}" for n in ("summary", "description", "priority")),
    *(f"rocketchat.default.{n}" for n in (
        "title", "alias", "titlelink", "emoji", "iconurl", "text")),
    *(f"mattermost.default.{n}" for n in (
        "color", "username", "title", "titlelink", "fallback", "text")),
})

def _at_send(section: str) -> str:
    """What happens when Alertmanager runs into the problem, at send time."""
    if section == "slack_configs":
        then = "Slack still sends the message, with this field and the ones after it empty"
    else:
        then = "the notification fails and isn't sent"
    return (f"Alertmanager loads the config anyway and only finds out when it sends a "
            f"notification: {then}.")


class Source(NamedTuple):
    where: str  # the `templates:` entry that loads it
    name: str   # for messages
    text: str
    path: Optional[str] = None  # a file on disk: its absolute path, normcase'd
    file: str = ""  # its file name, which Go also makes a template of the file's own text


def template_sources(cfg: dict, basedir: Optional[str], bundled,
                     texts: Optional[Dict[str, str]] = None) -> Optional[List[Source]]:
    """The template files Alertmanager loads for `cfg`, in the order it loads them, or None
    when amlint can't read all of them (an absolute path on disk, or no file to read
    relative paths against). `bundled` is the input's BundledTemplates, if any. `texts`
    ({absolute path, normcase'd: text}) is read instead of those files on disk."""
    templates = cfg.get("templates")
    if bundled is not None and bundled.unknown:
        return None
    if bundled is not None and bundled.all_loaded:
        return [Source("templates", f"{name} in {bundled.source}", bundled.files[name],
                       None, name)
                for name in bundled.names if name in bundled.files] \
            if set(bundled.names) <= set(bundled.files) else None
    if templates is None:
        return []
    if not isinstance(templates, list):
        return None
    out: List[Source] = []
    for i, tpl in enumerate(templates):
        if not isinstance(tpl, str):
            return None
        where = f"templates[{i}]"
        if bundled is not None:
            directory = posixpath.normpath(posixpath.dirname(tpl) or ".")
            if bundled.directory is None or directory != bundled.directory:
                return None
            pattern = posixpath.basename(tpl).replace("[^", "[!")
            for name in sorted(bundled.names):
                if fnmatch.fnmatchcase(name, pattern):
                    if name not in bundled.files:
                        return None
                    out.append(Source(where, f"{name} in {bundled.source}", bundled.files[name],
                                      None, name))
            continue
        if basedir is None or os.path.isabs(tpl):
            return None
        for path in sorted(_glob.glob(os.path.join(basedir, tpl))):
            full = os.path.normcase(os.path.abspath(path))  # one spelling per file
            text = (texts or {}).get(full)
            if text is None:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    return None
            out.append(Source(where, os.path.relpath(path, basedir), text, full,
                              os.path.basename(path)))
    return out


def _in_file(finding: Finding, src: Source, pos: int) -> Finding:
    """`finding`, noted as being at character `pos` of the template file `src`."""
    if src.path is not None:
        finding.template_at = (src.path, pos)
    return finding


def _fields(value, path: str) -> Iterator[Tuple[str, str]]:
    """(path, text) of each string with template actions below a notifier config. Settings
    that are never templates (http_config, *_file) are skipped."""
    if isinstance(value, dict):
        for key, item in value.items():
            key = str(key)
            if key == "http_config" or key.endswith("_file"):
                continue
            yield from _fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _fields(item, f"{path}[{i}]")
    elif isinstance(value, str) and "{{" in value:
        yield path, value


def _at(text: str, pos: int) -> str:
    return f"line {line_of(text, pos)} of the text: " if "\n" in text.rstrip("\n") else ""


_WHAT = {"Data": "the notification data", "Alert": "one alert", "Alerts": "the list of alerts",
         "[]Alert": "a list of alerts", "KV": "a set of labels", "Pairs": "a list of label pairs",
         "Pair": "a label pair", "Strings": "a list of strings", "string": "a string"}


def _field_problem(name: str, typ: str, root: Optional[str] = DATA) -> str:
    """`.name` on a value of type `typ`, which has no such field, for messages. `root` is the
    type of $ there."""
    if typ == "Data" and name in ("Labels", "Annotations"):
        hint = (f". For the {name.lower()} all alerts in the notification share, use "
                f".Common{name}; for each alert's own, use .{name} inside "
                f"{{{{ range .Alerts }}}}")
    elif typ == "Data" and name in MEMBERS["Alert"]:
        hint = ". It is a field of each alert: use it inside {{ range .Alerts }}"
    elif typ == "Alert" and name in MEMBERS["Data"] and root == DATA:
        hint = f". Inside {{{{ range .Alerts }}}} the dot is one alert: use $.{name}"
    elif typ == "Alert" and name in MEMBERS["Data"]:
        hint = ". The template runs on one alert here, not on the notification data"
    else:
        close = difflib.get_close_matches(name, sorted(MEMBERS.get(typ, {})), n=1, cutoff=0.6)
        hint = f" (did you mean .{close[0]}?)" if close else ""
    return (f".{name} on {_WHAT.get(typ, typ)}, which has no field {name}{hint}. Alertmanager "
            f"fails with \"can't evaluate field {name} in type {GO_TYPE.get(typ, typ)}\"")


def _hint(name: str, defined: Set[str]) -> str:
    close = difflib.get_close_matches(name, sorted(defined), n=1, cutoff=0.85)
    return f" (did you mean \"{close[0]}\"?)" if close else ""


def check_templates(cfg: dict, sources: Optional[List[Source]]) -> List[Finding]:
    """bad-template-file, bad-template and undefined-template. Templates a field calls are
    only looked up when amlint could read every template file."""
    out: List[Finding] = []
    defined: Set[str] = set(DEFAULT_TEMPLATES)
    complete = sources is not None
    calls: List[Tuple[Source, List[Tuple[str, int]]]] = []
    dots: Dict[str, Optional[str]] = {}  # define -> the type of data it is called with
    files_ok = True
    for src in sources or []:
        parsed = parse(src.text, AM_FUNCS)
        defined |= set(parsed.defines) | ({src.file} if src.file else set())
        if parsed.error:
            files_ok = complete = False  # Alertmanager loads nothing; the rest is unknown
            out.append(_in_file(Finding(
                ERROR, "bad-template-file",
                f"Template file {src.name}, line {line_of(src.text, parsed.error.pos)}: "
                f"{parsed.error.message}. Alertmanager refuses to load this config.",
                src.where,
            ), src, parsed.error.pos))
        calls.append((src, parsed.calls))

    for r in _receivers(cfg):
        for key, configs in r.items():
            if not str(key).endswith("_configs"):
                continue
            for i, entry in _dict_items(configs):
                base = f"{key}[{i}]"
                for path, text in _fields(entry, ""):
                    label = f"{base}{path} in receiver '{r.get('name')}'"
                    where = f"receivers[{r.get('name')}].{base}{path}"
                    webhook_url = key == "webhook_configs" and path == ".url"
                    # a webhook url is parsed when the config loads, with no functions added
                    parsed = parse(text, frozenset() if webhook_url else AM_FUNCS, DATA)
                    if parsed.error:
                        why = ("Alertmanager 0.31 and later parse it when they load the "
                               "config, so they refuse to load it." if webhook_url
                               else _at_send(str(key)))
                        out.append(Finding(
                            ERROR, "bad-template",
                            f"{label} is a template Alertmanager can't parse: "
                            f"{_at(text, parsed.error.pos)}{parsed.error.message}. {why}",
                            where,
                        ))
                        continue
                    for _, name, typ, _ in parsed.missing:
                        out.append(Finding(
                            ERROR, "template-unknown-field",
                            f"{label} uses {_field_problem(name, typ)}. {_at_send(str(key))}",
                            where,
                        ))
                    for name, typ in parsed.typed_calls:
                        _called(dots, name, typ)
                    if not complete:
                        continue
                    known = defined | set(parsed.defines)
                    for name in dict.fromkeys(n for n, _ in parsed.calls if n not in known):
                        out.append(Finding(
                            ERROR, "undefined-template",
                            f"{label} calls template \"{name}\", which no template file "
                            f"defines{_hint(name, known)}. {_at_send(str(key))}",
                            where,
                        ))
    if files_ok and sources:
        out.extend(_file_fields(sources, dots))
    if complete:
        for src, src_calls in calls:
            seen: Set[str] = set()
            for name, pos in src_calls:
                if name in defined or name in seen:
                    continue
                seen.add(name)
                out.append(_in_file(Finding(
                    ERROR, "undefined-template",
                    f"Template file {src.name}, line {line_of(src.text, pos)}, calls template "
                    f"\"{name}\", which no template file defines{_hint(name, defined)}. "
                    f"Notifications that use it fail when Alertmanager sends them.",
                    src.where,
                ), src, pos))
    return out


def check_template_file(path: str, name: str, text: str) -> List[Finding]:
    """bad-template-file for the template file at `path` on its own, when no config amlint
    knows loads it."""
    parsed = parse(text, AM_FUNCS)
    if parsed.error is None:
        return []
    return [_in_file(Finding(
        ERROR, "bad-template-file",
        f"Template file {name}, line {line_of(text, parsed.error.pos)}: "
        f"{parsed.error.message}. Alertmanager refuses to load a config that loads this file.",
    ), Source("", name, text, path), parsed.error.pos)]


def _called(dots: Dict[str, Optional[str]], name: str, typ: Optional[str]) -> bool:
    """Note that template `name` is called with data of type `typ`; a template called with
    different types is not checked. Returns whether that changed anything."""
    if name not in dots:
        dots[name] = typ
        return True
    if dots[name] is not None and dots[name] != typ:
        dots[name] = None
        return True
    return False


def _file_fields(sources: List[Source], dots: Dict[str, Optional[str]]) -> List[Finding]:
    """template-unknown-field in the {{define}}s of template files, each checked with the
    type of data the notification fields (and other templates) call it with."""
    for _ in range(10):  # until the types reach every define the calls lead to
        changed = False
        for src in sources:
            for name, typ in parse(src.text, AM_FUNCS, None, dict(dots)).typed_calls:
                changed = _called(dots, name, typ) or changed
        if not changed:
            break
    out: List[Finding] = []
    seen: Set[Tuple[str, int]] = set()
    parsed = [(src, parse(src.text, AM_FUNCS, None, dict(dots))) for src in sources]
    kept = {name: src for src, p in parsed for name in p.defines}  # Go keeps the last one
    for src, p in parsed:
        for pos, name, typ, define in p.missing:
            if (src.name, pos) in seen or define and kept.get(define) is not src:
                continue
            seen.add((src.name, pos))
            out.append(_in_file(Finding(
                ERROR, "template-unknown-field",
                f"Template file {src.name}, line {line_of(src.text, pos)}, in \"{define}\": "
                f"{_field_problem(name, typ, dots.get(define))}, so notifications that use "
                f"\"{define}\" fail.",
                src.where,
            ), src, pos))
    return out
