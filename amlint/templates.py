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

from .gotemplate import DATA, GO_TYPE, GRAFANA_MEMBERS, MEMBERS, line_of, parse
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

# Grafana's Alertmanager (a fork of 0.27) has fewer of Alertmanager's functions, and adds its
# own (grafana/alerting templates): namespaces such as coll.Dict and data.JSON, and a few more.
GRAFANA_FUNCS = frozenset({
    "toUpper", "toLower", "title", "trimSpace", "join", "match", "safeHtml", "reReplaceAll",
    "stringSlice", "date", "tz", "coll", "data", "tmpl", "time", "tenantID",
    "grafanaExploreURL", "queryFromGeneratorURL",
})
# Grafana's own templates (default_template.go), on top of Alertmanager's.
GRAFANA_TEMPLATES = DEFAULT_TEMPLATES | frozenset({
    "__subject", "__text_values_list", "__text_alert_list", "default.title",
    "default.message", "slack.default.footer", "__teams_text_alert_list",
    "teams.default.message", "jira.default.summary", "jira.default.description",
    "jira.default.priority", "webhook.default.payload", "webhook.default.payload.state",
})


class Dialect(NamedTuple):
    who: str              # what runs the templates, for messages
    funcs: frozenset
    defaults: frozenset   # the templates it defines itself
    members: dict         # its template data


ALERTMANAGER = Dialect("Alertmanager", AM_FUNCS, DEFAULT_TEMPLATES, MEMBERS)
GRAFANA = Dialect("Grafana", GRAFANA_FUNCS, GRAFANA_TEMPLATES, GRAFANA_MEMBERS)


def _at_send(section: str, who: str = "Alertmanager") -> str:
    """What happens when Alertmanager runs into the problem, at send time."""
    if section == "slack_configs":
        then = "Slack still sends the message, with this field and the ones after it empty"
    else:
        then = "the notification fails and isn't sent"
    return (f"{who} loads the config anyway and only finds out when it sends a "
            f"notification: {then}.")


class Source(NamedTuple):
    where: str  # the `templates:` entry that loads it
    name: str   # for messages
    text: str
    path: Optional[str] = None  # a file on disk: its absolute path, normcase'd


def template_sources(cfg: dict, basedir: Optional[str], bundled,
                     texts: Optional[Dict[str, str]] = None) -> Optional[List[Source]]:
    """The template files Alertmanager loads for `cfg`, in the order it loads them, or None
    when amlint can't read all of them (an absolute path on disk, or no file to read
    relative paths against). `bundled` is the input's BundledTemplates, if any. `texts`
    ({absolute path, normcase'd: text}) is read instead of those files on disk."""
    templates = cfg.get("templates")
    if bundled is not None and bundled.all_loaded:
        return [Source(bundled.paths.get(name, "templates"), f"{name} in {bundled.source}",
                       bundled.files[name])
                for name in bundled.names if name in bundled.files] \
            if set(bundled.names) <= set(bundled.files) else None
    if bundled is not None and bundled.grafana:
        return None  # Grafana's templates are in Grafana, not in this input
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
                    out.append(Source(where, f"{name} in {bundled.source}", bundled.files[name]))
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
            out.append(Source(where, os.path.relpath(path, basedir), text, full))
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


def _field_problem(name: str, typ: str, root: Optional[str] = DATA,
                   d: Dialect = ALERTMANAGER) -> str:
    """`.name` on a value of type `typ`, which has no such field, for messages. `root` is the
    type of $ there."""
    members = d.members
    if typ == "Data" and name in ("Labels", "Annotations"):
        hint = (f". For the {name.lower()} all alerts in the notification share, use "
                f".Common{name}; for each alert's own, use .{name} inside "
                f"{{{{ range .Alerts }}}}")
    elif typ == "Data" and name in members["Alert"]:
        hint = ". It is a field of each alert: use it inside {{ range .Alerts }}"
    elif typ == "Alert" and name in members["Data"] and root == DATA:
        hint = f". Inside {{{{ range .Alerts }}}} the dot is one alert: use $.{name}"
    elif typ == "Alert" and name in members["Data"]:
        hint = ". The template runs on one alert here, not on the notification data"
    else:
        close = difflib.get_close_matches(name, sorted(members.get(typ, {})), n=1, cutoff=0.6)
        hint = f" (did you mean .{close[0]}?)" if close else ""
    return (f".{name} on {_WHAT.get(typ, typ)}, which has no field {name}{hint}. {d.who} "
            f"fails with \"can't evaluate field {name} in type {GO_TYPE.get(typ, typ)}\"")


def _hint(name: str, defined: Set[str]) -> str:
    close = difflib.get_close_matches(name, sorted(defined), n=1, cutoff=0.85)
    return f" (did you mean \"{close[0]}\"?)" if close else ""


def check_templates(cfg: dict, sources: Optional[List[Source]],
                    d: Dialect = ALERTMANAGER) -> List[Finding]:
    """bad-template-file, bad-template and undefined-template. Templates a field calls are
    only looked up when amlint could read every template file. `d` is Alertmanager's
    templates or Grafana's."""
    out: List[Finding] = []
    defined: Set[str] = set(d.defaults)
    complete = sources is not None
    calls: List[Tuple[Source, List[Tuple[str, int]]]] = []
    dots: Dict[str, Optional[str]] = {}  # define -> the type of data it is called with
    files_ok = True
    for src in sources or []:
        parsed = parse(src.text, d.funcs)
        defined |= set(parsed.defines)
        if parsed.error:
            files_ok = complete = False  # Alertmanager loads nothing; the rest is unknown
            out.append(_in_file(Finding(
                ERROR, "bad-template-file",
                f"Template file {src.name}, line {line_of(src.text, parsed.error.pos)}: "
                f"{parsed.error.message}. {d.who} refuses to load this config.",
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
                    parsed = parse(text, frozenset() if webhook_url else d.funcs, DATA,
                                   members=d.members)
                    if parsed.error:
                        why = ("Alertmanager 0.31 and later parse it when they load the "
                               "config, so they refuse to load it." if webhook_url
                               else _at_send(str(key), d.who))
                        out.append(Finding(
                            ERROR, "bad-template",
                            f"{label} is a template {d.who} can't parse: "
                            f"{_at(text, parsed.error.pos)}{parsed.error.message}. {why}",
                            where,
                        ))
                        continue
                    for _, name, typ, _ in parsed.missing:
                        out.append(Finding(
                            ERROR, "template-unknown-field",
                            f"{label} uses {_field_problem(name, typ, DATA, d)}. "
                            f"{_at_send(str(key), d.who)}",
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
                            f"defines{_hint(name, known)}. {_at_send(str(key), d.who)}",
                            where,
                        ))
    if files_ok and sources:
        out.extend(_file_fields(sources, dots, d))
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
                    f"Notifications that use it fail when {d.who} sends them.",
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


def _file_fields(sources: List[Source], dots: Dict[str, Optional[str]],
                 d: Dialect = ALERTMANAGER) -> List[Finding]:
    """template-unknown-field in the {{define}}s of template files, each checked with the
    type of data the notification fields (and other templates) call it with."""
    for _ in range(10):  # until the types reach every define the calls lead to
        changed = False
        for src in sources:
            for name, typ in parse(src.text, d.funcs, None, dict(dots),
                                   d.members).typed_calls:
                changed = _called(dots, name, typ) or changed
        if not changed:
            break
    out: List[Finding] = []
    seen: Set[Tuple[str, int]] = set()
    for src in sources:
        for pos, name, typ, define in parse(src.text, d.funcs, None, dict(dots),
                                            d.members).missing:
            if (src.name, pos) in seen:
                continue
            seen.add((src.name, pos))
            out.append(_in_file(Finding(
                ERROR, "template-unknown-field",
                f"Template file {src.name}, line {line_of(src.text, pos)}, in \"{define}\": "
                f"{_field_problem(name, typ, dots.get(define), d)}, so notifications that use "
                f"\"{define}\" fail.",
                src.where,
            ), src, pos))
    return out
