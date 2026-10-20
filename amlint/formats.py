"""
The shapes an Alertmanager config is deployed in, read into a plain Alertmanager config.

- alertmanager: a plain alertmanager.yml.
- secret: a Kubernetes Secret (or ConfigMap) holding alertmanager.yaml, in `stringData` or
  base64 `data`. Its other keys are template files.
- helm: kube-prometheus-stack values, `alertmanager.config` (or `stringConfig`) and
  `alertmanager.templateFiles`, merged over the chart's default config as Helm does.
- mimir: a Mimir or Cortex tenant config, the `alertmanager_config` string and the
  `template_files` map.
- alertmanagerconfig: prometheus-operator AlertmanagerConfig resources (v1alpha1 and v1beta1),
  merged into the base config of the same input the way the operator merges them.

Each turns into an Alertmanager-shaped dict plus a map from the paths checks report
(`route.routes[1]`, `receivers[pager]`) to line:column in the file that was read. A config
held in a literal block (`alertmanager.yaml: |`) maps line by line into the block; one held
any other way (base64, a quoted string) maps to the key that holds it.
"""

import base64
import binascii
import copy
import gzip
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml
from yaml.constructor import SafeConstructor
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .goyaml import AlertmanagerLoader, keep_interval_strings, keep_strings
from .linter import (
    _parse_duration,
    _scalar,
    check_circular_inhibition,
    check_dead_inhibitions,
    route_cycle,
)
from .source import Position, SourceMap, Suppressions, _mapping_pairs, parse_suppressions

FORMATS = ("alertmanager", "secret", "helm", "mimir", "alertmanagerconfig")
MATCHER_STRATEGIES = ("OnNamespace", "OnNamespaceExceptForAlertmanagerNamespace", "None")

# The AlertmanagerConfig conversion follows prometheus-operator v0.94.1:
# pkg/alertmanager/amcfg.go (enforcers, AddAlertmanagerConfigs, convertRoute,
# convertReceiver, convertInhibitRule, makeNamespacedString), pkg/alertmanager/operator.go
# (default config, `namespace/name` order) and pkg/apis/monitoring/v1beta1/conversion_to.go.
OPERATOR_VERSION = "v0.94.1"
# The helm format follows kube-prometheus-stack 92.10.1: values.yaml and
# templates/alertmanager/secret.yaml.
CHART_VERSION = "92.10.1"

# Where the operator mounts the config Secret's keys, so where its template files are.
_CONFIG_DIR = "/etc/alertmanager/config"
# Where it mounts spec.alertmanagerConfiguration.templates.
_TEMPLATES_DIR = "/etc/alertmanager/templates"
_CONFIG_KEYS = ("alertmanager.yaml", "alertmanager.yml")
_GZIP_KEY = "alertmanager.yaml.gz"  # the operator's generated Secret

# The operator's config when the Secret has none (defaultAlertmanagerConfiguration).
_OPERATOR_DEFAULT = {"route": {"receiver": "null"}, "receivers": [{"name": "null"}]}

# kube-prometheus-stack's default alertmanager.config. Helm merges the user's values over it,
# so a values file that sets receivers: but not route.routes keeps the Watchdog route to 'null'.
_CHART_DEFAULT = {
    "global": {"resolve_timeout": "5m"},
    "inhibit_rules": [
        {"source_matchers": ["severity = critical"],
         "target_matchers": ["severity =~ warning|info"], "equal": ["namespace", "alertname"]},
        {"source_matchers": ["severity = warning"],
         "target_matchers": ["severity = info"], "equal": ["namespace", "alertname"]},
        {"source_matchers": ["alertname = InfoInhibitor"],
         "target_matchers": ["severity = info"], "equal": ["namespace"]},
        {"target_matchers": ["alertname = InfoInhibitor"]},
    ],
    "route": {
        "group_by": ["namespace"], "group_wait": "30s", "group_interval": "5m",
        "repeat_interval": "12h", "receiver": "null",
        "routes": [{"receiver": "null", "matchers": ['alertname = "Watchdog"']}],
    },
    "receivers": [{"name": "null"}],
    "templates": ["/etc/alertmanager/config/*.tmpl"],
}


class FormatError(ValueError):
    """The input can't be read as an Alertmanager config (in the format asked for)."""

    def __init__(self, title: str, message: str) -> None:
        super().__init__(message)
        self.title = title


@dataclass
class BundledTemplates:
    """Template files that ship inside the input instead of next to it on disk."""
    names: List[str]
    directory: Optional[str]  # where Alertmanager reads them; None: `templates:` isn't checked
    source: str               # where they are, for messages
    files: Dict[str, str] = field(default_factory=dict)  # name -> text, where amlint can read it
    all_loaded: bool = False  # every file is loaded, whatever `templates:` says (Mimir)
    unknown: bool = False     # in a config amlint can't see (the operator's base Secret)


def _texts(entries, encoded: bool) -> Dict[str, str]:
    """The text of each string value of a mapping, base64-decoded if `encoded`; values that
    can't be read are left out."""
    out: Dict[str, str] = {}
    for key, value in (entries.items() if isinstance(entries, dict) else ()):
        if not isinstance(value, str):
            continue
        if encoded:
            try:
                value = base64.b64decode("".join(value.split()), validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                continue
        out[str(key)] = value
    return out


# Regions: where a part of the config came from. Each maps a path in its own config to a
# position in the input, and tells whether an `# amlint:` comment disables a finding there.

class _Region:
    def locate(self, where: str) -> Position:
        raise NotImplementedError

    def suppressed(self, code: str, where: str) -> bool:
        raise NotImplementedError


def _join(prefix: str, where: str) -> str:
    if not prefix or not where:
        return prefix or where
    return prefix + ("" if where.startswith("[") else ".") + where


class _Direct(_Region):
    """A config that is part of the YAML itself, at `prefix` in a document."""

    def __init__(self, smap: SourceMap, prefix: str, sup: Suppressions) -> None:
        self._smap, self._prefix, self._sup = smap, prefix, sup

    def locate(self, where: str) -> Position:
        return self._smap.locate(_join(self._prefix, where))

    def suppressed(self, code: str, where: str) -> bool:
        return self._sup.suppresses(code, self.locate(where)[0])


class _Embedded(_Region):
    """A config held in a string. `shift` (lines, columns) maps a literal block's positions
    into the file; without it everything is at `key`, the position of the key holding it."""

    def __init__(self, smap: SourceMap, sup: Suppressions, shift: Optional[Tuple[int, int]],
                 key: Position, outer: Suppressions) -> None:
        self._smap, self._sup, self._shift, self._key, self._outer = smap, sup, shift, key, outer

    def outer(self, line: int, col: int = 1) -> Position:
        if self._shift is None:
            return self._key
        return line + self._shift[0], col + self._shift[1]

    def locate(self, where: str) -> Position:
        return self.outer(*self._smap.locate(where))

    def suppressed(self, code: str, where: str) -> bool:
        # a comment inside the string counts at its line there, one outside at the file's line
        return (self._sup.suppresses(code, self._smap.locate(where)[0])
                or self._outer.suppresses(code, self.locate(where)[0]))


class _Default(_Region):
    """The operator's default config, which isn't in the input: findings there are dropped,
    since there is nothing in the input to fix."""

    def __init__(self, pos: Position) -> None:
        self._pos = pos

    def locate(self, where: str) -> Position:
        return self._pos

    def suppressed(self, code: str, where: str) -> bool:
        return True


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


def _camel_path(path: str) -> str:
    """`.slack_configs[0]` as `.slackConfigs[0]`; names in brackets are kept."""
    out, i = [], 0
    while i < len(path):
        if path[i] == "[":
            end = path.find("]", i)
            end = len(path) if end < 0 else end + 1
            out.append(path[i:end])
            i = end
        elif path[i] == ".":
            end = i + 1
            while end < len(path) and path[end] not in ".[":
                end += 1
            out.append("." + _camel(path[i + 1:end]))
            i = end
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


class _Merged(_Region):
    """A config put together from several regions: each registered path prefix maps to a
    path in one region (for AlertmanagerConfig resources, with keys back in camelCase)."""

    def __init__(self, default: _Region) -> None:
        self._parts: Dict[str, Tuple[_Region, str, bool]] = {"": (default, "", False)}

    def add(self, prefix: str, region: _Region, target: str, camel: bool = False) -> None:
        self._parts.setdefault(prefix, (region, target, camel))

    def _resolve(self, where: str) -> Tuple[_Region, str]:
        for i in range(len(where), -1, -1):
            if 0 < i < len(where) and where[i] not in ".[":
                continue
            part = self._parts.get(where[:i])
            if part is not None:
                region, target, camel = part
                rest = _camel_path(where[i:]) if camel else where[i:]
                return region, target + rest if target else rest.lstrip(".")
        raise AssertionError("the empty prefix is always registered")

    def locate(self, where: str) -> Position:
        region, path = self._resolve(where)
        return region.locate(path)

    def suppressed(self, code: str, where: str) -> bool:
        region, path = self._resolve(where)
        return region.suppressed(code, path)


class _Spans:
    """Whether a character is inside a string, over every document of a stream."""

    def __init__(self, smaps: List[SourceMap]) -> None:
        self._smaps = smaps

    def in_string(self, index: int) -> bool:
        return any(smap.in_string(index) for smap in self._smaps)


Note = Tuple[Optional[int], str]  # (line in the input or None, text)


class Loaded:
    """An input read as an Alertmanager config."""

    def __init__(self, cfg: dict, fmt: str, region: _Region,
                 comments: List[Tuple[int, List[str]]], problems: List[Tuple[int, str]],
                 templates: Optional[BundledTemplates] = None,
                 notes: Optional[List[Note]] = None, uninjected: Optional[dict] = None,
                 by_design: Optional[set] = None) -> None:
        self.cfg = cfg
        self.format = fmt
        self.templates = templates   # None: check `templates:` on disk, next to the file
        self.notes = notes or []
        self.comments = comments     # (line, codes) of every `# amlint:` directive
        self.problems = problems     # (line, why) of the directives that can't be used
        # cfg without the namespace matchers the operator adds to inhibit rules
        self.uninjected = uninjected
        # (code, where) of findings about what the operator adds, such as the continue: true
        # on a resource's route when it is the last one
        self.by_design = by_design or set()
        # the config is kept where plain-text secrets belong (a Kubernetes Secret, the
        # operator's resources), or where *_file fields can't be used (Mimir rejects them)
        self.secrets_ok = False
        # Helm renders the config with tpl first, so {{ }} in it may be Helm's, not
        # Alertmanager's: notification templates in it aren't checked
        self.helm_tpl = False
        self._region = region

    def locate(self, where: str) -> Position:
        """Line and column in the input of a path in `cfg`, or of its nearest parent."""
        return self._region.locate(where or "")

    def keep(self, code: str, where: str) -> bool:
        """False when an `# amlint:` comment disables the finding, or it is in the
        operator's default config rather than in the input."""
        return not self._region.suppressed(code, where or "")


_INHIBIT_CODES = {"inhibit-no-equal", "inhibit-same-match", "circular-inhibition"}


def _helm_renders(f) -> bool:
    """A template finding in a config that Helm renders first (tplConfig): the text is
    Helm's, not what Alertmanager gets. Template files aren't rendered by Helm."""
    return f.code in ("bad-template", "undefined-template") or (
        f.code == "template-unknown-field" and f.where.startswith("receivers["))


def apply_locations(findings: list, loaded: Loaded) -> list:
    """Set line/col on every finding; drop the ones `loaded` doesn't keep, and the inhibit
    rule findings that only the operator's namespace matcher causes: it is on both sides of
    every rule of a resource, by design, so it is no sign of a rule muting its own source,
    nor of two rules of one namespace muting each other."""
    for f in findings:
        f.line, f.col = loaded.locate(f.where)
    findings = [f for f in findings if (f.code, f.where) not in loaded.by_design
                and not (loaded.secrets_ok and f.code == "plaintext-secret")
                and not (loaded.helm_tpl and _helm_renders(f))]
    if loaded.uninjected is not None:
        without = {(f.code, f.where, f.msg) for check in (check_dead_inhibitions,
                   check_circular_inhibition) for f in check(loaded.uninjected)}
        findings = [f for f in findings
                    if f.code not in _INHIBIT_CODES or (f.code, f.where, f.msg) in without]
    return [f for f in findings if loaded.keep(f.code, f.where)]


# Reading YAML

def _construct(node: Node):
    return SafeConstructor().construct_document(keep_interval_strings(node))


def _construct_config(node: Node):
    """A config document Alertmanager parses itself (Go's yaml.v2): strings it reads as
    written keep their text, so `match: {country: NO}` is "NO" (see goyaml). Helm values
    and Kubernetes resources are not read this way: Helm and the API server type YAML 1.1
    scalars (`NO` is false) before the config reaches Alertmanager."""
    return SafeConstructor().construct_document(keep_strings(node))


def _get(node: Optional[Node], key: str) -> Tuple[Optional[Node], Optional[Node]]:
    """(key node, value node) of `key` in a mapping; the last one wins, as in safe_load."""
    found: Tuple[Optional[Node], Optional[Node]] = (None, None)
    if isinstance(node, MappingNode):
        for k, v in _mapping_pairs(node):
            if k.value == key:
                found = (k, v)
    return found


def _pos(node: Node) -> Position:
    return node.start_mark.line + 1, node.start_mark.column + 1


def _yaml_error(what: str, e: yaml.YAMLError, place=None) -> FormatError:
    """A parse error in an embedded config, with its position in the input if known."""
    mark = getattr(e, "problem_mark", None)
    problem = getattr(e, "problem", None)
    if mark is None or problem is None:
        return FormatError("Failed to parse YAML", f"{what}: {e}")
    if place is None:
        where = f"line {mark.line + 1} of {what.split(' ')[0]}"
    else:
        line, col = place(mark.line + 1, mark.column + 1)
        where = f"line {line}, column {col}"
    return FormatError("Failed to parse YAML", f"{what}: {problem} ({where})")


class _Doc:
    """One document of the input, or one item of a Kubernetes List."""

    def __init__(self, node: Node, value) -> None:
        self.node = node
        self.value = value
        self.cls = ""  # what _classify makes of it
        self._smap: Optional[SourceMap] = None

    @property
    def smap(self) -> SourceMap:
        if self._smap is None:
            self._smap = SourceMap.of_node(self.node)
        return self._smap

    @property
    def line(self) -> int:
        return self.node.start_mark.line + 1

    def get(self, *keys):
        value = self.value
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    @property
    def kind(self) -> Optional[str]:
        kind = self.get("kind")
        return kind if isinstance(kind, str) and self.get("apiVersion") is not None else None

    @property
    def title(self) -> str:
        """`Secret monitoring/alertmanager-main`, for messages."""
        if self.kind is None:
            return f"the config at line {self.line}"
        name = _scalar(self.get("metadata", "name"))
        namespace = _scalar(self.get("metadata", "namespace"))
        return f"{self.kind} {namespace + '/' if namespace else ''}{name}".rstrip()


def _documents(text: str) -> Tuple[List[_Doc], List[SourceMap]]:
    """The documents of a stream, Lists flattened, and a SourceMap of each whole document."""
    try:
        nodes = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
        docs = [_Doc(n, _construct(n)) for n in nodes if n.tag != "tag:yaml.org,2002:null"]
    except yaml.YAMLError as e:
        raise FormatError("Failed to parse YAML", str(e))
    out: List[_Doc] = []
    for doc in docs:
        out.extend(_flatten(doc))
    return out, [doc.smap for doc in docs]


def _flatten(doc: _Doc) -> List[_Doc]:
    """A Kubernetes List (`kubectl get -o yaml`) as its items."""
    if doc.kind is None or not doc.kind.endswith("List"):
        return [doc]
    _, items = _get(doc.node, "items")
    out: List[_Doc] = []
    for item in items.value if isinstance(items, SequenceNode) else []:
        out.extend(_flatten(_Doc(item, _construct(item))))
    return out


def _config_key(doc: _Doc) -> Optional[Tuple[str, str]]:
    """(section, key) holding the config in a Secret or ConfigMap. For the same key,
    stringData wins over data, as the API server merges them."""
    sections = ("stringData", "data") if doc.kind == "Secret" else ("data", "binaryData")
    for key in _CONFIG_KEYS + (_GZIP_KEY,):
        for section in sections:
            if isinstance(doc.get(section), dict) and key in doc.get(section):
                return section, key
    return None


def _is_helm(doc: _Doc, strict: bool) -> bool:
    """An `alertmanager:` section. Alertmanager rejects unknown top-level keys, so a config
    can't have one; strictly, only when the document doesn't look like a config otherwise."""
    if not isinstance(doc.get("alertmanager"), dict):
        return False
    return not strict or not {"route", "receivers"} & set(doc.value)


def _classify(doc: _Doc, fmt: str) -> str:
    """What a document is: one of FORMATS, "alertmanager-resource", or the Kubernetes kind
    of any other object. With an explicit `fmt`, Helm values are recognised more loosely,
    and Mimir or Helm only when `fmt` allows them."""
    kind = doc.kind
    if kind == "AlertmanagerConfig":
        return "alertmanagerconfig"
    if kind == "Alertmanager" and str(doc.get("apiVersion")).startswith("monitoring.coreos.com/"):
        return "alertmanager-resource"
    if kind in ("Secret", "ConfigMap"):
        return "secret" if _config_key(doc) else kind
    if kind is not None:
        return kind
    if fmt in ("auto", "alertmanagerconfig", "mimir") and isinstance(
            doc.get("alertmanager_config"), str):
        return "mimir"
    if fmt in ("auto", "alertmanagerconfig", "helm") and _is_helm(doc, strict=fmt != "helm"):
        return "helm"
    return "alertmanager"


_BASES = ("alertmanager", "secret", "helm", "mimir")
_FORMAT_NAMES = {
    "alertmanager": "Alertmanager config",
    "secret": "Kubernetes Secret or ConfigMap with an alertmanager.yaml key",
    "helm": "kube-prometheus-stack Helm values (an alertmanager: section)",
    "mimir": "Mimir/Cortex config (an alertmanager_config: string)",
    "alertmanagerconfig": "AlertmanagerConfig resource",
}


def load_input(text: str, input_format: Optional[str] = None,
               matcher_strategy: Optional[str] = None) -> Loaded:
    """Read `text` in `input_format` (one of FORMATS; None or "auto" detects it).
    `matcher_strategy` overrides the alertmanagerConfigMatcherStrategy found in the input."""
    fmt = input_format or "auto"
    if fmt == "alertmanager":
        # exactly what amlint has always read: one plain document
        try:
            node = yaml.compose(text, Loader=yaml.SafeLoader)
            docs = [] if node is None else [_Doc(node, _construct(node))]
        except yaml.YAMLError as e:
            raise FormatError("Failed to parse YAML", str(e))
        smaps = [doc.smap for doc in docs]
    else:
        docs, smaps = _documents(text)
    outer = parse_suppressions(text, smaps[0] if len(smaps) == 1 else _Spans(smaps))
    for doc in docs:
        doc.cls = "alertmanager" if fmt == "alertmanager" else _classify(doc, fmt)

    crs = [d for d in docs if d.cls == "alertmanagerconfig"]
    if fmt not in ("auto", "alertmanagerconfig"):
        bases = [d for d in docs if d.cls == fmt]
        crs = []
    else:
        bases = [d for d in docs if d.cls in _BASES]
    if fmt == "alertmanagerconfig" and not crs:
        raise _not_found(docs, fmt)
    if len(bases) > 1:
        lines = ", ".join(str(d.line) for d in bases)
        raise FormatError("Cannot read the config",
                          f"found {len(bases)} Alertmanager configs (lines {lines}); amlint "
                          f"reads one per file, so split them or pick one with --input-format")
    if not bases and not crs:
        if not docs and fmt in ("auto", "alertmanager"):
            return Loaded({}, "alertmanager", _Direct(SourceMap.of_node(None), "", outer),
                          outer.comments, outer.problems)
        raise _not_found(docs, fmt)

    notes: List[Note] = []
    comments, problems = list(outer.comments), list(outer.problems)
    base = bases[0] if bases else None
    if not crs:
        cfg, region, templates = _read_base(base, text, outer, notes, comments, problems)
        _no_route_cycle(cfg.get("route"), "route")
        loaded = Loaded(cfg, base.cls, region, comments, problems, templates, notes)
        loaded.secrets_ok = base.cls == "mimir" or base.kind == "Secret"
        loaded.helm_tpl = base.cls == "helm" and base.get("alertmanager", "tplConfig") is True
        return loaded
    loaded = _operator(text, docs, crs, base, matcher_strategy, outer, notes, comments, problems)
    loaded.secrets_ok = True
    return loaded


def _no_route_cycle(route, path: str, what: str = "") -> None:
    """Raise if the route tree contains itself through a YAML alias: yaml.safe_load builds a
    recursive dict for it, which every walk over the routes would follow forever."""
    cycle = route_cycle(route, path)
    if cycle:
        raise FormatError("Route contains itself",
                          f"{what}{cycle[0]} is a YAML alias of {cycle[1]}, which contains "
                          f"it, so the route tree never ends")


def _not_found(docs: List[_Doc], fmt: str) -> FormatError:
    wanted = _FORMAT_NAMES.get(fmt, "Alertmanager config")
    kinds = sorted({d.cls for d in docs if d.cls not in _BASES + ("alertmanagerconfig",)})
    has = f" (it has: {', '.join(kinds)})" if kinds else ""
    hint = ""
    if "PrometheusRule" in kinds:
        hint = (". A PrometheusRule goes to --rules; quote the glob so the shell doesn't "
                "expand it: --rules 'rules/*.yml'")
    return FormatError("Not an Alertmanager config", f"found no {wanted}{has}{hint}")


def _read_base(doc: _Doc, text: str, outer: Suppressions, notes: List[Note],
               comments: list, problems: list):
    """(cfg, region, templates) of the document holding the base config."""
    if doc.cls == "alertmanager":
        cfg = _construct_config(doc.node) if doc.value is not None else {}
        if not isinstance(cfg, dict):
            raise FormatError("Not an Alertmanager config",
                              f"top level must be a mapping, got {type(cfg).__name__}")
        return cfg, _Direct(doc.smap, "", outer), None
    if doc.cls == "secret":
        return _read_secret(doc, text, outer, comments, problems)
    if doc.cls == "helm":
        return _read_helm(doc, text, outer, notes, comments, problems)
    key_node, value_node = _get(doc.node, "alertmanager_config")
    cfg, region = _embedded(doc.value["alertmanager_config"], value_node, key_node, text,
                            "alertmanager_config", outer, comments, problems)
    files = doc.get("template_files")
    # Mimir loads every template_files entry, whatever `templates:` says, and rejects
    # `templates:` entries with a path, so they aren't checked against files.
    names = sorted(str(k) for k in files) if isinstance(files, dict) else []
    return cfg, region, BundledTemplates(names, None, "template_files", _texts(files, False),
                                         all_loaded=True)


def _block_shift(node: Node, key_node: Optional[Node], text: str) -> Optional[Tuple[int, int]]:
    """(lines, columns) from a position in a literal block scalar's value to the same place
    in the input. None for other styles: their lines don't map one to one."""
    if not isinstance(node, ScalarNode) or node.style != "|":
        return None
    header = re.match(r"\|[+-]?([1-9]?)", text[node.start_mark.index:])
    if header and header.group(1):
        parent = key_node.start_mark.column if key_node is not None else 0
        indent = parent + int(header.group(1))
    else:
        lines = text.split("\n")[node.start_mark.line + 1:node.end_mark.line + 1]
        body = [line for line in lines if line.strip()]
        if not body:
            return None
        indent = len(body[0]) - len(body[0].lstrip(" "))
    return node.start_mark.line + 1, indent


def _embedded(value, value_node: Optional[Node], key_node: Optional[Node], text: str,
              what: str, outer: Suppressions, comments: list, problems: list,
              decoded: Optional[str] = None):
    """(cfg, region) of a config held in a string; `decoded` is its text when the string
    itself isn't (base64)."""
    inner = decoded if decoded is not None else value
    if not isinstance(inner, str):
        raise FormatError("Not an Alertmanager config",
                          f"{what} must be a string, got {type(inner).__name__}")
    shift = _block_shift(value_node, key_node, text) if decoded is None else None
    key = _pos(key_node) if key_node is not None else (1, 1)
    place = None
    if shift is not None:
        def place(line, col):
            return line + shift[0], col + shift[1]
    try:
        smap = SourceMap(inner)
        cfg = yaml.load(inner, Loader=AlertmanagerLoader)
    except yaml.YAMLError as e:
        raise _yaml_error(what, e, place)
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise FormatError("Not an Alertmanager config",
                          f"{what}: top level must be a mapping, got {type(cfg).__name__}")
    sup = parse_suppressions(inner, smap)
    region = _Embedded(smap, sup, shift, key, outer)
    comments.extend((region.outer(line)[0], codes) for line, codes in sup.comments)
    problems.extend((region.outer(line)[0], why) for line, why in sup.problems)
    return cfg, region


def _read_secret(doc: _Doc, text: str, outer: Suppressions, comments: list, problems: list):
    section, key = _config_key(doc)
    entries = doc.get(section)
    _, section_node = _get(doc.node, section)
    key_node, value_node = _get(section_node, key)
    what = f"{key} in {doc.title}"
    decoded = None
    if section == "binaryData" or section == "data" and doc.kind == "Secret":
        try:
            raw = base64.b64decode("".join(str(entries[key]).split()), validate=True)
            if key == _GZIP_KEY:
                raw = gzip.decompress(raw)
            decoded = raw.decode("utf-8")
        except (binascii.Error, OSError, EOFError, UnicodeDecodeError) as e:
            raise FormatError("Cannot read the config",
                              f"{what} (line {_pos(key_node)[0]}) is not valid "
                              f"base64{' gzip' if key == _GZIP_KEY else ''} text: {e}")
    elif key == _GZIP_KEY:
        raise FormatError("Cannot read the config", f"{what} must be base64 in data:")
    cfg, region = _embedded(entries[key], value_node, key_node, text, what, outer,
                            comments, problems, decoded)
    names = sorted({str(k) for s in ("data", "stringData", "binaryData")
                    for k in (doc.get(s) if isinstance(doc.get(s), dict) else {})
                    if str(k) not in _CONFIG_KEYS + (_GZIP_KEY,)})
    texts = _texts(doc.get("binaryData"), True)
    texts.update(_texts(doc.get("data"), doc.kind == "Secret"))
    texts.update(_texts(doc.get("stringData"), False))  # wins over data, as in the API server
    if doc.kind == "Secret":
        # The operator mounts the Secret's keys in /etc/alertmanager/config. The config itself
        # is read from config_out, so only absolute paths into that directory are checked.
        templates = BundledTemplates(names, _CONFIG_DIR, "the Secret", texts)
    else:
        templates = BundledTemplates(names, None, "the ConfigMap", texts)
    return cfg, region, templates


def _helm_merge(default: dict, values: dict) -> dict:
    """`values` over `default` the way Helm coalesces values: maps key by key, anything else
    replaced, and null deletes the key."""
    out = dict(default)
    for key, value in values.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _helm_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_helm(doc: _Doc, text: str, outer: Suppressions, notes: List[Note],
               comments: list, problems: list):
    am = doc.get("alertmanager")
    _, am_node = _get(doc.node, "alertmanager")
    files = am.get("templateFiles")
    names = sorted(str(k) for k in files) if isinstance(files, dict) else []
    templates = BundledTemplates(names, _CONFIG_DIR, "alertmanager.templateFiles",
                                 _texts(files, False))
    tpl = am.get("tplConfig") is True
    string_config = am.get("stringConfig")
    config = am.get("config")

    # templates/alertmanager/secret.yaml: with tplConfig, a non-empty stringConfig wins,
    # then a string config; otherwise the config map, via toYaml.
    if isinstance(string_config, str) and string_config.strip():
        key_node, value_node = _get(am_node, "stringConfig")
        if tpl:
            cfg, region = _embedded(string_config, value_node, key_node, text,
                                    "alertmanager.stringConfig", outer, comments, problems)
            return cfg, region, templates
        notes.append((_pos(key_node)[0], "alertmanager.stringConfig is only used with "
                      "alertmanager.tplConfig: true, so the chart renders alertmanager.config"))
    if isinstance(config, str):
        key_node, value_node = _get(am_node, "config")
        if not tpl:
            notes.append((_pos(key_node)[0], "the chart renders a string alertmanager.config "
                          "only with alertmanager.tplConfig: true; read as a config anyway"))
        cfg, region = _embedded(config, value_node, key_node, text, "alertmanager.config",
                                outer, comments, problems)
        return cfg, region, templates

    values = config if isinstance(config, dict) else {}
    # before the merge, which copies the root route, so the paths are the ones in the file
    _no_route_cycle(values.get("route"), "alertmanager.config.route")
    cfg = _helm_merge(copy.deepcopy(_CHART_DEFAULT), values)
    from_chart = [k for k in _CHART_DEFAULT if k not in values]
    if isinstance(values.get("route"), dict):
        from_chart += [f"route.{k}" for k in ("receiver", "routes") if k not in values["route"]]
    if from_chart and isinstance(config, dict):
        key_node, _ = _get(am_node, "config")
        notes.append((_pos(key_node)[0], f"alertmanager.config is merged over the "
                      f"kube-prometheus-stack {CHART_VERSION} defaults, as Helm merges values: "
                      f"{', '.join(from_chart)} come from the chart"))
    return cfg, _Direct(doc.smap, "alertmanager.config", outer), templates


# prometheus-operator AlertmanagerConfig resources

def _snake(name: str) -> str:
    """A CRD field name as Alertmanager spells it: apiURL -> api_url, msteamsv2Configs ->
    msteamsv2_configs."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name).lower()


# Integration fields whose Alertmanager name isn't the snake_case of the CRD's (convert*Config).
_RENAMES = {
    "webhookConfigs": {"urlSecret": "url"},     # urlSecret wins over url
    "discordConfigs": {"apiURL": "webhook_url"},
}

_SECRET_REF_KEYS = {"name", "key", "optional"}


def _secret_ref(value) -> bool:
    """A SecretKeySelector ({name, key}): the operator reads the value from the Secret."""
    return isinstance(value, dict) and "key" in value and set(value) <= _SECRET_REF_KEYS


class _CR:
    """One AlertmanagerConfig resource, converted the way the operator does."""

    def __init__(self, doc: _Doc, notes: List[Note]) -> None:
        self.doc = doc
        self.version = "v1beta1" if str(doc.get("apiVersion")).endswith("/v1beta1") else "v1alpha1"
        self.name = _scalar(doc.get("metadata", "name"))
        namespace = _scalar(doc.get("metadata", "namespace"))
        if not namespace:
            notes.append((doc.line, f"AlertmanagerConfig '{self.name}' has no "
                          f"metadata.namespace; assuming 'default'"))
            namespace = "default"
        self.namespace = namespace
        self.key = f"{namespace}/{self.name}"
        spec = doc.get("spec")
        self.spec = spec if isinstance(spec, dict) else {}
        _no_route_cycle(self.spec.get("route"), "spec.route",
                        f"AlertmanagerConfig {self.key} (line {doc.line}): ")
        # v1alpha1 spells the top-level time intervals muteTimeIntervals, v1beta1 timeIntervals
        self.intervals_key = "timeIntervals" if self.version == "v1beta1" else "muteTimeIntervals"
        other = "muteTimeIntervals" if self.version == "v1beta1" else "timeIntervals"
        if other in self.spec:
            notes.append((doc.line, f"AlertmanagerConfig {self.key}: spec.{other} is not a "
                          f"{self.version} field (it is spec.{self.intervals_key} there), so "
                          f"the API server drops it"))
        self._memo: Dict[int, object] = {}

    def namespaced(self, name) -> str:
        """makeNamespacedString: names are prefixed with the resource's namespace and name."""
        name = _scalar(name)
        return f"{self.namespace}/{self.name}/{name}" if name else ""

    def items(self, key: str) -> List[Tuple[int, dict]]:
        value = self.spec.get(key)
        return [(i, v) for i, v in enumerate(value) if isinstance(v, dict)] \
            if isinstance(value, list) else []

    def matcher(self, m) -> str:
        """Matcher.String(): name, operator and the value quoted, as convertMatchersV2 does
        for Alertmanager >= 0.22. A v1alpha1 matcher without matchType uses regex:."""
        if not isinstance(m, dict):
            return _scalar(m)
        op = m.get("matchType")
        if not op:
            op = "=~" if self.version == "v1alpha1" and m.get("regex") is True else "="
        value = _scalar(m.get("value"))
        value = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        return f'{_scalar(m.get("name"))}{_scalar(op)}"{value}"'

    def matchers(self, items) -> List[str]:
        return [self.matcher(m) for m in items] if isinstance(items, list) else []

    def route(self, node: dict) -> dict:
        """convertRoute; nested routes are the same Route type."""
        out: dict = {}
        receiver = self.namespaced(node.get("receiver"))
        if receiver:
            out["receiver"] = receiver
        if "groupBy" in node:
            out["group_by"] = node["groupBy"]
        for key in ("groupWait", "groupInterval", "repeatInterval"):
            value = node.get(key)
            # sanitize() drops a zero group_interval / repeat_interval, so it is inherited
            if value and not (key != "groupWait" and _parse_duration(value) == 0):
                out[_snake(key)] = value
        matchers = self.matchers(node.get("matchers"))
        if matchers:
            out["matchers"] = matchers
        if node.get("continue"):
            out["continue"] = True
        for key in ("muteTimeIntervals", "activeTimeIntervals"):
            names = node.get(key)
            if isinstance(names, list) and names:
                out[_snake(key)] = [self.namespaced(n) for n in names]
        children = node.get("routes")
        if isinstance(children, list) and children:
            out["routes"] = [self.route(c) if isinstance(c, dict) else c for c in children]
        return out

    def value(self, value):
        """A CRD value in Alertmanager's spelling; a Secret reference becomes a stand-in
        string, since the operator fills in the Secret's value. Each object is converted
        once, so YAML aliases don't multiply the work."""
        if _secret_ref(value):
            return f"<secret {self.namespace}/{_scalar(value.get('name'))}:" \
                   f"{_scalar(value.get('key'))}>"
        if not isinstance(value, (dict, list)):
            return value
        if id(value) in self._memo:
            return self._memo[id(value)]
        if isinstance(value, list):
            out: object = [self.value(v) for v in value]
        else:
            out = {_snake(str(k)): self.value(v) for k, v in value.items()}
        self._memo[id(value)] = out
        return out

    def integration(self, field: str, item):
        if not isinstance(item, dict):
            return item
        renames = _RENAMES.get(field, {})
        out = {}
        for key, value in item.items():
            if field == "webhookConfigs" and key == "url" and item.get("urlSecret") is not None:
                continue
            out[renames.get(key) or _snake(str(key))] = self.value(value)
        return out

    def receiver(self, node: dict) -> dict:
        """convertReceiver: the name is namespaced, each *Configs list converted."""
        out: dict = {"name": self.namespaced(node.get("name"))}
        for key, value in node.items():
            if key == "name":
                continue
            if isinstance(value, list):
                out[_snake(key)] = [self.integration(key, item) for item in value]
            else:
                out[_snake(key)] = self.value(value)
        return out

    def inhibit_rule(self, node: dict) -> dict:
        """convertInhibitRule."""
        out: dict = {}
        for side in ("source", "target"):
            matchers = self.matchers(node.get(f"{side}Match"))
            if matchers:
                out[f"{side}_matchers"] = matchers
        if node.get("equal"):
            out["equal"] = node["equal"]
        return out

    def time_interval(self, node: dict) -> dict:
        """convertMuteTimeInterval: the name is namespaced."""
        out = {"name": self.namespaced(node.get("name"))}
        out.update((_snake(k), self.value(v)) for k, v in node.items() if k != "name")
        return out


def _strategy(docs: List[_Doc], base: Optional[_Doc], override: Optional[str],
              notes: List[Note]) -> Tuple[str, Optional[str], Optional[_Doc]]:
    """(matcher strategy, the Alertmanager's namespace, the Alertmanager resource)."""
    resources = [d for d in docs if d.cls == "alertmanager-resource"]
    am = resources[0] if resources else None
    if len(resources) > 1:
        notes.append((resources[1].line, f"{len(resources)} Alertmanager resources; using the "
                      f"one at line {am.line}"))
    strategy = override
    if strategy is None and am is not None:
        strategy = _scalar(am.get("spec", "alertmanagerConfigMatcherStrategy", "type")) or None
    if strategy is None and base is not None and base.cls == "helm":
        strategy = _scalar(base.get("alertmanager", "alertmanagerSpec",
                                    "alertmanagerConfigMatcherStrategy", "type")) or None
    namespace = None
    if am is not None:
        namespace = _scalar(am.get("metadata", "namespace")) or None
    if namespace is None and base is not None and base.cls == "secret":
        # the config Secret lives in the Alertmanager's namespace
        namespace = _scalar(base.get("metadata", "namespace")) or None
    return strategy or "OnNamespace", namespace, am


def _enforce(strategy: str, am_namespace: Optional[str], cr: _CR) -> bool:
    """Whether the operator adds the namespace matcher to this resource's route and inhibit
    rules (getEnforcer): not with None, nor with OnNamespaceExceptForAlertmanagerNamespace
    in the Alertmanager's own namespace. Any other value is OnNamespace."""
    if strategy == "None":
        return False
    if strategy == "OnNamespaceExceptForAlertmanagerNamespace":
        return cr.namespace != am_namespace
    return True


def _global_config(g: dict, cr: _CR) -> dict:
    """convertGlobalConfig, for the keys amlint's checks read."""
    fields = {
        ("resolveTimeout",): "resolve_timeout", ("slackApiUrl",): "slack_api_url",
        ("opsGenieApiUrl",): "opsgenie_api_url", ("opsGenieApiKey",): "opsgenie_api_key",
        ("pagerdutyUrl",): "pagerduty_url", ("smtp", "from"): "smtp_from",
        ("smtp", "hello"): "smtp_hello", ("smtp", "authUsername"): "smtp_auth_username",
        ("smtp", "authPassword"): "smtp_auth_password",
        ("smtp", "authSecret"): "smtp_auth_secret",
        ("smtp", "authIdentity"): "smtp_auth_identity",
        ("victorops", "apiURL"): "victorops_api_url",
        ("victorops", "apiKey"): "victorops_api_key", ("wechat", "apiURL"): "wechat_api_url",
        ("wechat", "apiSecret"): "wechat_api_secret",
        ("wechat", "apiCorpID"): "wechat_api_corp_id",
        ("telegram", "apiURL"): "telegram_api_url", ("webex", "apiURL"): "webex_api_url",
        ("jira", "apiURL"): "jira_api_url", ("rocketChat", "apiURL"): "rocketchat_api_url",
        ("rocketChat", "token"): "rocketchat_token",
        ("rocketChat", "tokenID"): "rocketchat_token_id",
    }
    out: dict = {}
    for path, key in fields.items():
        value = g
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if value not in (None, ""):
            out[key] = cr.value(value)
    host = g.get("smtp", {}).get("smartHost") if isinstance(g.get("smtp"), dict) else None
    if isinstance(host, dict) and host.get("host"):
        out["smtp_smarthost"] = f"{_scalar(host.get('host'))}:{_scalar(host.get('port'))}"
    return out


def _operator(text: str, docs: List[_Doc], crs_docs: List[_Doc], base: Optional[_Doc],
              override: Optional[str], outer: Suppressions, notes: List[Note],
              comments: list, problems: list) -> Loaded:
    """AddAlertmanagerConfigs: merge the resources into the base config."""
    strategy, am_namespace, am = _strategy(docs, base, override, notes)
    if strategy == "OnNamespaceExceptForAlertmanagerNamespace" and am_namespace is None:
        notes.append((None, "the Alertmanager's namespace isn't in the input (no Alertmanager "
                      "resource, no namespace on the config Secret), so every "
                      "AlertmanagerConfig gets the namespace matcher"))

    by_key: Dict[str, _CR] = {}
    for doc in crs_docs:
        cr = _CR(doc, notes)
        if cr.key in by_key:
            notes.append((doc.line, f"AlertmanagerConfig {cr.key} is defined again; the last "
                          f"one is used"))
        by_key[cr.key] = cr

    # spec.alertmanagerConfiguration names a resource that is the whole config
    # (initializeFromAlertmanagerConfig); the operator doesn't merge it like the others.
    global_cr = None
    name = _scalar(am.get("spec", "alertmanagerConfiguration", "name")) if am else ""
    if name:
        global_cr = by_key.pop(f"{am_namespace}/{name}", None)
        if global_cr is None:
            notes.append((am.line, f"spec.alertmanagerConfiguration names AlertmanagerConfig "
                          f"'{name}', which isn't in the input"))

    templates = None
    if global_cr is not None:
        if base is not None:
            notes.append((base.line, f"the Alertmanager uses AlertmanagerConfig "
                          f"{global_cr.key} as its configuration, so {base.title} "
                          f"isn't used"))
        cfg, region = _global_base(global_cr, am, outer)
    elif base is not None:
        cfg, region, templates = _read_base(base, text, outer, notes, comments, problems)
        _no_route_cycle(cfg.get("route"), "route")
    else:
        cfg = copy.deepcopy(_OPERATOR_DEFAULT)
        region = _Default(_pos(crs_docs[0].node))
        # the templates the real base config loads are unknown, so are the ones it defines
        templates = BundledTemplates([], None, "the Alertmanager's config Secret", unknown=True)

    merged = _Merged(region)
    route = dict(cfg["route"]) if isinstance(cfg.get("route"), dict) else {}
    base_routes = route.get("routes") if isinstance(route.get("routes"), list) else []
    receivers = list(cfg["receivers"]) if isinstance(cfg.get("receivers"), list) else []
    rules = list(cfg.get("inhibit_rules")) if isinstance(cfg.get("inhibit_rules"), list) else []
    raw_rules = list(rules)  # the same, without the namespace matcher
    # the operator writes time_intervals:; a base config's own deprecated key stays apart
    intervals = list(cfg.get("time_intervals")) \
        if isinstance(cfg.get("time_intervals"), list) else []
    sub_routes: List[dict] = []
    by_design = set()

    for key in sorted(by_key):
        cr = by_key[key]
        cr_region = _Direct(cr.doc.smap, "", outer)
        enforce = _enforce(strategy, am_namespace, cr)
        namespace_matcher = f'namespace="{cr.namespace}"'
        for j, node in cr.items("inhibitRules"):
            raw = cr.inhibit_rule(node)
            rule = dict(raw)
            if enforce:
                for side in ("source_matchers", "target_matchers"):
                    if namespace_matcher not in rule.get(side, []):
                        rule[side] = rule.get(side, []) + [namespace_matcher]
            merged.add(f"inhibit_rules[{len(rules)}]", cr_region, f"spec.inhibitRules[{j}]")
            rules.append(rule)
            raw_rules.append(raw)
        if not isinstance(cr.spec.get("route"), dict):
            if cr.items("receivers") or cr.items(cr.intervals_key):
                notes.append((cr.doc.line, f"AlertmanagerConfig {cr.key} has no spec.route, "
                              f"so the operator adds only its inhibit rules and drops its "
                              f"receivers and time intervals"))
            continue
        sub = cr.route(cr.spec["route"])
        if enforce:
            sub["matchers"] = sub.get("matchers", []) + [namespace_matcher]
        sub["continue"] = True  # continueToNextRoute: alerts go on to the other routes
        by_design.add(("useless-continue", f"route.routes[{len(sub_routes)}]"))
        merged.add(f"route.routes[{len(sub_routes)}]", cr_region, "spec.route", camel=True)
        sub_routes.append(sub)
        for j, node in cr.items("receivers"):
            rcv = cr.receiver(node)
            target = f"spec.receivers[{j}]"
            merged.add(f"receivers[{len(receivers)}]", cr_region, target, camel=True)
            merged.add(f"receivers[{rcv['name']}]", cr_region, target, camel=True)
            receivers.append(rcv)
        for j, node in cr.items(cr.intervals_key):
            interval = cr.time_interval(node)
            target = f"spec.{cr.intervals_key}[{j}]"
            merged.add(f"time_intervals[{len(intervals)}]", cr_region, target, camel=True)
            merged.add(f"time_intervals[{interval['name']}]", cr_region, target, camel=True)
            intervals.append(interval)

    # The resources' routes go first; with continue: true, alerts fall through to the base's.
    for i in range(len(base_routes)):
        merged.add(f"route.routes[{len(sub_routes) + i}]", region, f"route.routes[{i}]")
    route["routes"] = sub_routes + base_routes
    cfg = dict(cfg, route=route, receivers=receivers)
    if intervals:
        cfg["time_intervals"] = intervals
    uninjected = None
    if rules:
        cfg["inhibit_rules"] = rules
        if rules != raw_rules:
            uninjected = dict(cfg, inhibit_rules=raw_rules)
    return Loaded(cfg, "alertmanagerconfig", merged, comments, problems, templates, notes,
                  uninjected, by_design)


def _global_base(cr: _CR, am: _Doc, outer: Suppressions) -> Tuple[dict, _Region]:
    """initializeFromAlertmanagerConfig: the resource's route is the root route, no namespace
    matcher is added, and global settings come from the Alertmanager resource."""
    cr_region = _Direct(cr.doc.smap, "", outer)
    am_region = _Direct(am.smap, "spec.alertmanagerConfiguration", outer)
    region = _Merged(_Direct(cr.doc.smap, "spec", outer))
    cfg: dict = {}
    settings = am.get("spec", "alertmanagerConfiguration")
    g = settings.get("global") if isinstance(settings, dict) else None
    if isinstance(g, dict):
        cfg["global"] = _global_config(g, cr)
        region.add("global", am_region, "global", camel=True)
    if isinstance(cr.spec.get("route"), dict):
        cfg["route"] = cr.route(cr.spec["route"])
        region.add("route", cr_region, "spec.route", camel=True)
    cfg["receivers"] = []
    for j, node in cr.items("receivers"):
        rcv = cr.receiver(node)
        region.add(f"receivers[{j}]", cr_region, f"spec.receivers[{j}]", camel=True)
        region.add(f"receivers[{rcv['name']}]", cr_region, f"spec.receivers[{j}]", camel=True)
        cfg["receivers"].append(rcv)
    rules = []
    for j, node in cr.items("inhibitRules"):
        region.add(f"inhibit_rules[{j}]", cr_region, f"spec.inhibitRules[{j}]")
        rules.append(cr.inhibit_rule(node))
    if rules:
        cfg["inhibit_rules"] = rules
    intervals = [cr.time_interval(node) for _, node in cr.items(cr.intervals_key)]
    if intervals:
        cfg["time_intervals"] = intervals
    templates = []
    for item in (settings.get("templates") or []) if isinstance(settings, dict) else []:
        for kind in ("configMap", "secret"):
            ref = item.get(kind) if isinstance(item, dict) else None
            if isinstance(ref, dict) and ref.get("key"):
                templates.append(f"{_TEMPLATES_DIR}/{_scalar(ref['key'])}")
    if templates:
        cfg["templates"] = templates
        region.add("templates", am_region, "templates")
    return cfg, region
