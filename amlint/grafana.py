"""Grafana Alerting, read as an Alertmanager config.

Grafana runs its own Alertmanager (a fork of 0.27) with the same routing, time intervals
and templates, so amlint checks Grafana's notification policy tree like any other config.
Three inputs:

- file provisioning (`contactPoints`, `policies`, `muteTimes`, `templates`), and the same
  format from Grafana's export. Grafana reads every .yaml, .yml and .json file in the
  provisioning directory together, so the files next to this one are read too: a policy
  here may use a contact point defined there. Findings in those files are left to their
  own run.
- Grafana's Alertmanager config (`route` with `object_matchers`, receivers with
  `grafana_managed_receiver_configs`),
- and the same wrapped in `alertmanager_config` with `template_files`, as Grafana's API
  returns it.

Follows Grafana v13.2.4 (pkg/services/provisioning/alerting, pkg/services/ngalert) and
grafana/alerting.
"""

import copy
import os
import re
from typing import Dict, List, Optional, Set, Tuple

import yaml

GRAFANA_VERSION = "v13.2.4"

# Lists of a provisioning file that make up the notification config.
_LISTS = ("contactPoints", "policies", "muteTimes", "templates")
# Grafana's own Alertmanager has no `global:` section, and creates a route for every contact
# point itself (for alert rules that pick one directly), so these say nothing about it.
SKIP_CODES = {"unused-receiver", "global-resolve-timeout-missing",
              "deprecated-mute-time-intervals"}
_DEFINE = re.compile(r"\{\{-?\s*define\b")


def is_provisioning(value) -> bool:
    """A provisioning file with notification settings in it."""
    if not isinstance(value, dict) or "route" in value:
        return False
    if any(isinstance(value.get(key), list) for key in ("contactPoints", "policies",
                                                        "muteTimes")):
        return True
    templates = value.get("templates")
    return isinstance(templates, list) and any(isinstance(t, dict) for t in templates)


def _routes(route, seen=None):
    seen = set() if seen is None else seen
    if not isinstance(route, dict) or id(route) in seen:
        return
    seen.add(id(route))
    yield route
    for child in route.get("routes") or [] if isinstance(route.get("routes"), list) else []:
        yield from _routes(child, seen)


def is_config(value) -> bool:
    """Grafana's Alertmanager config, wrapped as its API returns it or not."""
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("alertmanager_config"), dict):
        return True
    receivers = value.get("receivers")
    if any(isinstance(r, dict) and "grafana_managed_receiver_configs" in r
           for r in (receivers if isinstance(receivers, list) else [])):
        return True
    return any("object_matchers" in r for r in _routes(value.get("route")))


def _unescape(value):
    """`$$` as `$`: Grafana's export escapes `$` in names and settings, and provisioning
    reads `$$` back as a plain `$`."""
    if isinstance(value, str):
        return value.replace("$$", "$")
    if isinstance(value, list):
        return [_unescape(v) for v in value]
    if isinstance(value, dict):
        return {k: _unescape(v) for k, v in value.items()}
    return value


def _route(node: dict) -> dict:
    """A policy as a route: names unescaped, matchers as written."""
    out = {}
    for key, value in node.items():
        if key in ("orgId", "provenance"):
            continue
        if key == "routes" and isinstance(value, list):
            out[key] = [_route(child) if isinstance(child, dict) else child for child in value]
        elif key in ("object_matchers", "matchers"):
            out[key] = value
        else:
            out[key] = _unescape(value)
    return out


def _org(item) -> int:
    try:
        org = int(item.get("orgId", 1))
    except (TypeError, ValueError):
        return 1
    return org if org >= 1 else 1


def siblings(path: Optional[str]) -> List[Tuple[str, dict]]:
    """(file name, content) of the other provisioning files in the directory of `path`,
    which Grafana reads together with it."""
    if not path:
        return []
    from .formats import _construct_config
    directory, own = os.path.split(os.path.abspath(path))
    out = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    for name in names:
        full = os.path.join(directory, name)
        if name == own or not name.endswith((".yaml", ".yml", ".json")):
            continue
        try:
            if os.path.getsize(full) > 2_000_000:
                continue
            with open(full, encoding="utf-8") as f:
                node = next(iter(yaml.compose_all(f.read(), Loader=yaml.SafeLoader)), None)
            value = _construct_config(node) if node is not None else None
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if is_provisioning(value):
            out.append((name, value))
    return out


def read(doc, text, outer, notes, comments, problems, path):
    """Loaded for a document that is_provisioning or is_config."""
    from .formats import (
        BundledTemplates,
        Loaded,
        _construct_config,
        _Default,
        _Direct,
        _Merged,
        _pos,
    )
    value = _construct_config(doc.node)
    here = _Direct(doc.smap, "", outer)

    if isinstance(value.get("alertmanager_config"), dict):
        cfg = copy.deepcopy(value["alertmanager_config"])
        region = _Merged(_Direct(doc.smap, "alertmanager_config", outer))
        files = value.get("template_files")
        files = {str(k): v for k, v in files.items() if isinstance(v, str)} \
            if isinstance(files, dict) else {}
        for name in files:
            region.add(f"template_files.{name}", here, f"template_files.{name}")
        templates = BundledTemplates(sorted(files), None, "template_files", files,
                                     all_loaded=True, grafana=True,
                                     paths={n: f"template_files.{n}" for n in files})
        return _done(Loaded(cfg, "grafana", region, comments, problems, templates, notes))

    if not is_provisioning(value):
        # Grafana's config in Alertmanager form: its templates live in Grafana, unknown here
        templates = BundledTemplates([], None, "Grafana", grafana=True)
        return _done(Loaded(value, "grafana", here, comments, problems, templates, notes))

    own = os.path.basename(path) if path else ""
    sources = sorted([(own, value, here)] + [(name, v, _Default(_pos(doc.node)))
                                             for name, v in siblings(path)],
                     key=lambda s: s[0])
    orgs = sorted({_org(item) for _, v, _ in sources for key in _LISTS
                   for item in (v.get(key) if isinstance(v.get(key), list) else [])
                   if isinstance(item, dict)})
    org = 1 if 1 in orgs or not orgs else orgs[0]
    if len(orgs) > 1:
        notes.append((doc.line, f"these files configure Grafana orgs {', '.join(map(str, orgs))};"
                                f" amlint checks org {org}"))

    def items(key):
        for name, v, region in sources:
            entries = v.get(key)
            for j, item in enumerate(entries if isinstance(entries, list) else []):
                if isinstance(item, dict) and _org(item) == org:
                    yield region, j, item

    merged = _Merged(_Default(_pos(doc.node)))
    cfg: dict = {}
    by_design: Set[Tuple[str, str]] = set()

    # the last policy tree for the org wins, as each one replaces the one before
    policies = list(items("policies"))
    if policies:
        region, j, policy = policies[-1]
        cfg["route"] = _route(policy)
        merged.add("route", region, f"policies[{j}]")
    else:
        notes.append((doc.line, "no notification policies for this org in these files: "
                                "Grafana keeps the policy tree it has, so routing isn't "
                                "checked"))

    # Provisioning upserts: a contact point integration with a UID seen before, a mute
    # timing or a template with a name seen before, replaces the earlier one.
    points: Dict[str, Dict[str, tuple]] = {}
    first: Dict[str, tuple] = {}
    for region, j, point in items("contactPoints"):
        name = _unescape(str(point.get("name") or "")).strip()
        if not name:
            continue
        first.setdefault(name, (region, j))
        entries = point.get("receivers")
        for k, integration in enumerate(entries if isinstance(entries, list) else []):
            if isinstance(integration, dict):
                uid = str(integration.get("uid") or f"#{j}.{k}")
                points.setdefault(name, {})[uid] = (region, f"contactPoints[{j}].receivers[{k}]",
                                                    integration)
    receivers = []
    for index, (name, (region, j)) in enumerate(first.items()):
        merged.add(f"receivers[{index}]", region, f"contactPoints[{j}]")
        merged.add(f"receivers[{name}]", region, f"contactPoints[{j}]")
        configs = []
        for i, (region_i, target, integration) in enumerate(points.get(name, {}).values()):
            merged.add(f"receivers[{name}].grafana_managed_receiver_configs[{i}]", region_i,
                       target)
            configs.append(_unescape(integration))
        receivers.append({"name": name, "grafana_managed_receiver_configs": configs})
    cfg["receivers"] = receivers

    latest: Dict[str, tuple] = {}
    for region, j, mute in items("muteTimes"):
        latest[str(mute.get("name") or "")] = (region, j, mute)
    intervals = []
    for name, (region, j, mute) in latest.items():
        merged.add(f"time_intervals[{len(intervals)}]", region, f"muteTimes[{j}]")
        merged.add(f"time_intervals[{name}]", region, f"muteTimes[{j}]")
        intervals.append({k: v for k, v in mute.items() if k != "orgId"})
    if intervals:
        cfg["time_intervals"] = intervals

    latest = {}
    for region, j, tpl in items("templates"):
        name, content = str(tpl.get("name") or ""), tpl.get("template")
        if name and isinstance(content, str):
            latest[name] = (region, j, content)
    names, files, paths = [], {}, {}
    for name, (region, j, content) in latest.items():
        if not _DEFINE.search(content):  # Grafana wraps a template without a define in one
            content = f'{{{{ define "{name}" }}}}{content}{{{{ end }}}}'
        paths[name] = f"grafana_templates[{len(names)}]"
        merged.add(paths[name], region, f"templates[{j}]")
        names.append(name)
        files[name] = content
    # templates made in Grafana's UI can't be seen here: only with some in these files are
    # the ones a contact point uses looked up
    templates = BundledTemplates(names, None, "the Grafana templates", files,
                                 all_loaded=bool(names), grafana=True, paths=paths)

    # Contact points and mute timings made in Grafana's UI aren't in these files either: a
    # policy may use them, so a name these files don't define isn't reported.
    defined = set(first)
    timings = {str(i.get("name")) for i in intervals}
    unknown_receivers, unknown_timings = set(), set()
    for route, where in _walk(cfg.get("route"), "route"):
        name = route.get("receiver")
        if name and str(name) not in defined:
            unknown_receivers.add(str(name))
            by_design.add(("undefined-receiver", where))
        for key in ("mute_time_intervals", "active_time_intervals"):
            for timing in route.get(key) or [] if isinstance(route.get(key), list) else []:
                if str(timing) not in timings:
                    unknown_timings.add(str(timing))
                    by_design.add(("undefined-time-interval", where))
    if unknown_receivers or unknown_timings:
        said = ", ".join(sorted(unknown_receivers | unknown_timings))
        notes.append((doc.line, f"not defined in these files, so taken to be in Grafana: "
                                f"{said}"))
    loaded = Loaded(cfg, "grafana", merged, comments, problems, templates, notes,
                    by_design=by_design)
    return _done(loaded)


def _walk(route, path):
    if not isinstance(route, dict):
        return
    yield route, path
    children = route.get("routes")
    for i, child in enumerate(children if isinstance(children, list) else []):
        yield from _walk(child, f"{path}.routes[{i}]")


def _done(loaded):
    loaded.skip_codes = set(SKIP_CODES)
    return loaded
