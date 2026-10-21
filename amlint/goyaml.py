"""
YAML read the way Alertmanager reads its config file (Go's gopkg.in/yaml.v2).

PyYAML types every plain scalar by YAML 1.1 rules: `match: {country: NO}` reads as False,
`version: 1.10` as 1.1 and `code: 0x1F` as 31. Alertmanager decodes these fields into Go
strings, which keep the text as written ("NO", "1.10", "0x1F"), so amlint would route
those alerts differently. This loader keeps the text of plain scalars wherever
Alertmanager expects a string that routing depends on: the keys and values of match /
match_re maps (and their source_ / target_ forms in inhibit_rules), the items of
matchers, group_by, equal and time interval lists, and receiver and name values.
Everything else, such as `continue: true` or `send_resolved: false`, is typed as before.
An empty value or `~` stays None: Go reads it as "" and so does amlint.
"""

import yaml
from yaml.constructor import SafeConstructor

# PyYAML resolves a plain `=` to YAML 1.1's "value" tag and has no constructor for it, so
# Grafana's exported object_matchers (`- =`) fail to load. Go's yaml reads it as "=".
SafeConstructor.add_constructor("tag:yaml.org,2002:value", SafeConstructor.construct_yaml_str)

_STR = "tag:yaml.org,2002:str"
_TYPED = {f"tag:yaml.org,2002:{t}" for t in ("bool", "int", "float", "timestamp")}
_STRING_MAPS = {side + key for side in ("", "source_", "target_") for key in ("match", "match_re")}
_STRING_LISTS = {side + "matchers" for side in ("", "source_", "target_")} | {
    "group_by", "equal", "mute_time_intervals", "active_time_intervals", "object_matchers",
}
_STRING_VALUES = {"receiver", "name"}
# Time interval fields, also in AlertmanagerConfig resources. Go's yaml.v2 has no base-60 ints,
# so Alertmanager, Helm and the Kubernetes API server all read an unquoted 17:00 or 1:5 as
# text, where PyYAML reads 1020 and 65.
_INTERVAL_LISTS = {"weekdays", "days_of_month", "daysOfMonth", "months", "years"}
_INTERVAL_VALUES = {"start_time", "end_time", "startTime", "endTime", "location"}


def _as_text(node: yaml.Node) -> None:
    if isinstance(node, yaml.ScalarNode) and node.tag in _TYPED:
        node.tag = _STR


def _retag(node: yaml.Node, seen: set, maps: set, lists: set, values: set) -> None:
    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            name = key.value if isinstance(key, yaml.ScalarNode) else None
            if name in maps and isinstance(value, yaml.MappingNode):
                for k, v in value.value:
                    _as_text(k)
                    _as_text(v)
            elif name in lists and isinstance(value, yaml.SequenceNode):
                for item in value.value:
                    if isinstance(item, yaml.SequenceNode):  # Grafana's object_matchers
                        for part in item.value:
                            _as_text(part)
                    else:
                        _as_text(item)
            elif name in values:
                _as_text(value)
            _retag(value, seen, maps, lists, values)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _retag(item, seen, maps, lists, values)


def keep_strings(node: yaml.Node) -> yaml.Node:
    """Retag a composed document so that constructing it keeps the text of the scalars
    Alertmanager reads as strings. Returns the node."""
    _retag(node, set(), _STRING_MAPS, _STRING_LISTS | _INTERVAL_LISTS,
           _STRING_VALUES | _INTERVAL_VALUES)
    return node


def keep_interval_strings(node: yaml.Node) -> yaml.Node:
    """Retag only the time interval fields, for YAML that Helm or the Kubernetes API server
    types before the config reaches Alertmanager. Returns the node."""
    _retag(node, set(), set(), _INTERVAL_LISTS, _INTERVAL_VALUES)
    return node


class AlertmanagerLoader(yaml.SafeLoader):
    """yaml.SafeLoader that keeps the text of the scalars Alertmanager reads as strings."""

    def construct_document(self, node):
        return super().construct_document(keep_strings(node))
