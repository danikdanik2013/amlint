"""
Hypothesis strategies for the differential tests: route trees, matchers in every spelling
Alertmanager accepts, label sets, inhibit rules with alerts, and receiver configs with
the mistakes `amtool check-config` rejects.

Label names and values come from small pools, so generated alerts actually hit the
generated matchers, and receiver names come from the route's position (`r0_1`), so a
receiver list says exactly which routes an alert reached.
"""

from typing import Dict, List, Tuple

from harness import Plain, quote
from hypothesis import strategies as st

from amlint.linter import _matchers
from amlint.routing import (
    Inhibitor,
    RoutingError,
    _matcher_ok,
    _regex_examples,
    compile_regex,
)

# "k8s.ns" needs a quoted name (or the UTF-8 parser) in matchers, and match: maps reject it.
LABEL_NAMES = ("sev", "team", "env", "svc", "k8s.ns")

# Ordinary values, and ones that stress quoting, escaping, anchoring and case folding
# (\u212a is the Kelvin sign, which (?i) folds to k).
VALUES = (
    "crit", "warn", "prod", "api", "api-gw", "db", "a", "A", "x y", "a.b", "a,b", "a|b",
    "{x}", 'q"t', "b\\s", "c\\\\d", "é", "\u212a", "a\nb", "1", "-", "=x", "~",
)

# Plain YAML scalars that YAML 1.1 reads as something other than a string.
YAML_TYPED = (
    "yes", "no", "on", "true", "False", "0x1F", "010", "1.10", "1_000", "1:20", "+1",
    ".5", "0b11", "1e3", "~", "null", "2001-12-14",
)

REGEX_POOL = (
    "crit|warn", "prod.*", ".*-gw", ".+", ".*", "", "api|db", "(api|db)-.*", "[a-z]+",
    "^api$", "a|", "(?i)API", "\\d+", "a.b", "a\\.b", "(?i:crit)", "[[:alpha:]]+",
    "\\Qa.b\\E", "a$\\n?", "a$\\nb", "\\x41", "\\x{e9}", "a\\b.*", ".*\\Bb", "(?s)a.b",
    "(?m)a$\\nb", "[^a]+", "\\w+", "\\s", "é|ü", "k", "(?i)k", "a{2}", "a{,2}", "x{1,}",
    "(?U)a+", "a\\z", "\\Aa", "[\\d-]+", "\\pL+",
)

# Hypothesis favours "simple" choices: sampled_from the first and last items of a pool,
# integers their bounds. That helps shrinking, which these tests do their own way (see
# reduce.py), but it leaves the middle of a pool all but untried. A random.Random that
# Hypothesis seeds picks evenly and is still reproducible from the seed.
_random = st.randoms(use_true_random=True)


def pick(pool):
    """A choice from `pool`, each item about as likely as the others."""
    pool = tuple(pool)
    return _random.map(lambda r: r.choice(pool))


def one_in(n: int):
    """True with probability 1/n."""
    return _random.map(lambda r: r.randrange(n) == 0)


_ATOMS = ("a", "b", "p", "-", ".", "\\.", "[a-c]", "[^a]", "\\d", "\\w", "\\s", "é", " ",
          "\\n", "k")
_ANCHORS = ("^", "$", "\\b", "\\B", "\\A", "\\z")
_QUANTIFIERS = ("", "", "", "*", "+", "?", "{1,2}", "*?")


def _structural_regex():
    piece = st.one_of(
        st.tuples(pick(_ATOMS), pick(_QUANTIFIERS)).map("".join),
        pick(_ANCHORS),
    )
    concat = st.lists(piece, max_size=3).map("".join)
    alternation = st.lists(concat, min_size=1, max_size=3).map("|".join)
    grouped = st.one_of(
        alternation,
        st.tuples(alternation, pick(_QUANTIFIERS)).map(lambda t: f"({t[0]}){t[1]}"),
    )
    flags = pick(("", "", "", "(?i)", "(?s)", "(?m)"))
    return st.tuples(flags, grouped).map("".join)


regexes = st.one_of(pick(REGEX_POOL), _structural_regex())
values = pick(("",) + VALUES)

# (label, op, value) as the matcher means it, before it is spelled in YAML.
matcher_specs = st.one_of(
    st.tuples(pick(LABEL_NAMES), pick(("=", "!=")), values),
    st.tuples(pick(LABEL_NAMES), pick(("=~", "!~")), regexes),
)


def _loose_escape(value: str) -> str:
    """Escape only what must be: what people write by hand, e.g. "a\\.b" for a regex.
    The classic parser keeps a backslash that starts no escape."""
    out = []
    for i, ch in enumerate(value):
        nxt = value[i + 1:i + 2]
        if ch == '"' or ch == "\n":
            out.append({'"': '\\"', "\n": "\\n"}[ch])
        elif ch == "\\" and (nxt in ('"', "\\", "n", "")):
            out.append("\\\\")
        else:
            out.append(ch)
    return "".join(out)


def _can_be_bare(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(c in value for c in '",\n')


@st.composite
def matcher_text(draw, spec) -> str:
    """One matcher as a string, quoted or bare, with or without spaces around the op. A
    quoted label name makes Alertmanager read the whole entry with its UTF-8 parser."""
    label, op, value = spec
    utf8 = "." in label or draw(one_in(6))
    if utf8 and ("." not in label or not draw(one_in(4))):
        label = quote(label)
    # The UTF-8 parser rejects loose escapes (\.), so those entries are mostly well quoted.
    styles = ("quoted",) * 6 + ("loose", "bare") if utf8 else ("quoted", "quoted", "loose", "bare")
    style = draw(pick(styles))
    if style == "bare" and _can_be_bare(value):
        text = _loose_escape(value) if draw(st.booleans()) else value
    elif style == "loose":
        text = '"' + _loose_escape(value) + '"'
    else:
        text = quote(value)
    space = " " if draw(one_in(5)) else ""
    return f"{label}{space}{op}{space}{text}"


@st.composite
def spelled_matchers(draw, specs) -> dict:
    """Route (or inhibit rule side, with a prefix added later) keys for these matchers:
    `matchers:` entries, several matchers in one entry, or match / match_re maps."""
    out: dict = {}
    entries: List[str] = []
    form = draw(pick(("list", "list", "joined", "maps")))
    for spec in specs:
        label, op, value = spec
        # match: maps reject a name like k8s.ns: now and then, to check amlint does too
        legacy = "." not in label or draw(one_in(5))
        if form == "maps" and op in ("=", "=~") and legacy:
            key = "match" if op == "=" else "match_re"
            if label not in out.get(key, {}):
                out.setdefault(key, {})[label] = value
                continue
        entries.append(draw(matcher_text(spec)))
    if entries and form == "joined":
        joined = draw(pick((", ", ","))).join(entries)
        if draw(st.booleans()):
            joined = "{" + joined + "}"
        entries = [joined]
    if entries:
        out["matchers"] = entries
    return out


@st.composite
def _route(draw, depth: int, max_depth: int, max_children: int, spec_strategy) -> dict:
    node: dict = {}
    if depth:
        specs = draw(st.lists(spec_strategy, max_size=3))
        node.update(draw(spelled_matchers(specs)))
        if draw(one_in(4)):  # match: values YAML 1.1 types
            label = draw(pick(LABEL_NAMES[:4]))
            node.setdefault("match", {})[label] = Plain(draw(pick(YAML_TYPED)))
        if draw(st.booleans()):
            node["receiver"] = True  # named after its path once the tree is built
        cont = draw(pick((None, None, True, False)))
        if cont is not None:
            node["continue"] = cont
    else:
        node["receiver"] = True
    if depth < max_depth:
        children = draw(st.lists(_route(depth + 1, max_depth, max_children, spec_strategy),
                                 max_size=max_children))
        if children:
            node["routes"] = children
    return node


def _name_receivers(node: dict, name: str, names: List[str]) -> None:
    if node.get("receiver") is True:
        node["receiver"] = name
        names.append(name)
    for i, child in enumerate(node.get("routes", [])):
        _name_receivers(child, f"{name}_{i}" if name != "root" else f"r{i}", names)


def _order(node: dict) -> dict:
    """Keys in the order people write them, so failing configs read naturally."""
    keys = ("receiver", "match", "match_re", "matchers", "continue", "routes")
    out = {k: node[k] for k in keys if k in node}
    if "routes" in out:
        out["routes"] = [_order(child) for child in out["routes"]]
    return out


@st.composite
def route_configs(draw, max_depth: int = 3, max_children: int = 3, specs=matcher_specs) -> dict:
    """A valid config whose route tree has nested routes, continue and every matcher
    form; every receiver is defined (and empty, which Alertmanager accepts)."""
    root = draw(_route(0, max_depth, max_children, specs))
    names: List[str] = []
    _name_receivers(root, "root", names)
    return {"route": _order(root), "receivers": [{"name": n} for n in names]}


def _walk(node: dict):
    yield node
    for child in node.get("routes", []):
        yield from _walk(child)


def config_values(cfg: dict) -> List[str]:
    """Strings worth using as label values for this config: its literal matcher values,
    a few strings each regex matches, and the fixed pools."""
    found = list(VALUES) + list(YAML_TYPED)
    nodes = list(_walk(cfg["route"])) if isinstance(cfg.get("route"), dict) else []
    for rule in cfg.get("inhibit_rules") or []:
        nodes += [{k[len(p):]: v for k, v in rule.items() if k.startswith(p)}
                  for p in ("source_", "target_")]
    for node in nodes:
        for _, op, value in _matchers(node):
            if op in ("=~", "!~"):
                try:
                    found += _regex_examples(compile_regex(value))
                except RoutingError:
                    pass
            found.append(value)
    return list(dict.fromkeys(found))


def label_sets(candidates: List[str], allow_empty: bool = True):
    value = st.one_of(
        pick(candidates),
        st.text(alphabet="ab-. é\nAK\u212a", max_size=3),
    )
    if not allow_empty:
        value = value.filter(bool)
    return st.dictionaries(pick(LABEL_NAMES), value, max_size=len(LABEL_NAMES))


# ── inhibition ───────────────────────────────────────────────────────
#
# A small value pool, so rules and alerts actually meet: the routing test already covers
# matcher parsing, this one is about equal:, two-sided rules and alerts inhibiting each other.

_INHIBIT_VALUES = ("crit", "warn", "prod", "a")
_simple_equality = st.tuples(pick(LABEL_NAMES), pick(("=", "=", "!=")),
                             pick(("",) + _INHIBIT_VALUES))
_simple_regex = st.tuples(pick(LABEL_NAMES), pick(("=~", "!~")),
                          pick(("crit|warn", "c.*", ".+", ".*", "a|", "prod")))
_inhibit_specs = st.one_of(_simple_equality, _simple_equality, _simple_regex, matcher_specs)


@st.composite
def inhibit_rules(draw) -> dict:
    rule: dict = {}
    for side in ("source_", "target_"):
        # an empty side matches every alert; now and then is enough
        specs = draw(st.one_of(st.lists(_inhibit_specs, min_size=1, max_size=2),
                               st.lists(_inhibit_specs, min_size=1, max_size=2), st.just([])))
        spelled = draw(spelled_matchers(specs))
        rule.update({side + key: value for key, value in spelled.items()})
    equal = draw(st.lists(pick(LABEL_NAMES), max_size=2, unique=True))
    if equal or draw(st.booleans()):
        rule["equal"] = equal
    return rule


@st.composite
def _satisfying(draw, matchers, base: Dict[str, str], candidates: List[str]):
    """`base` changed so that it passes these (label, op, compiled value) matchers, or
    None if no candidate value does. Only picks inputs: Alertmanager stays the oracle."""
    alert = dict(base)
    for label in dict.fromkeys(m[0] for m in matchers):
        ok = [v for v in [None] + candidates
              if all(_matcher_ok(op, value, v or "") for lbl, op, value in matchers
                     if lbl == label)]
        if not ok:
            return None
        v = draw(pick(ok))
        if v is None:
            alert.pop(label, None)
        else:
            alert[label] = v
    return alert


@st.composite
def inhibition_scenarios(draw) -> Tuple[dict, List[Dict[str, str]]]:
    """A config with inhibit rules (and a small route tree), plus the alerts firing: an
    alert passing one rule's source matchers, one passing its target matchers (often with
    the same `equal:` labels), and variations of them."""
    # a simple route tree: the routing test covers the hard matchers
    cfg = draw(route_configs(2, 2, st.one_of(_simple_equality, _simple_regex)))
    cfg["inhibit_rules"] = draw(st.lists(inhibit_rules(), min_size=1, max_size=3))
    candidates = [v for v in config_values(cfg) if v]
    small = pick(_INHIBIT_VALUES)
    value = st.one_of(small, small, small, pick(candidates))
    base = draw(st.dictionaries(pick(LABEL_NAMES), value, min_size=1))
    alerts = [base]
    try:
        rules = Inhibitor(cfg["inhibit_rules"])._rules
    except RoutingError:
        rules = []
    if rules:
        rule = draw(pick(rules))
        source = draw(_satisfying(rule.source, base, candidates))
        target = draw(_satisfying(rule.target, base, candidates))
        if source and target:
            for label in rule.equal:
                if label in source and not draw(one_in(4)):  # mostly keep `equal:` true
                    target[label] = source[label]
        alerts += [a for a in (source, target) if a]

    changes = st.dictionaries(pick(LABEL_NAMES), st.one_of(st.none(), value),
                              min_size=1, max_size=2)
    for change in draw(st.lists(changes, max_size=4)):
        alert = {**draw(pick(alerts)), **change}
        alerts.append({k: v for k, v in alert.items() if v is not None})
    return cfg, list({frozenset(a.items()): a for a in alerts if a}.values())


# ── config validation ────────────────────────────────────────────────
#
# Each integration: a minimal valid config, the key amlint checks for, the alternatives
# that also satisfy Alertmanager (a *_file variant, a global default) and their values.

INTEGRATIONS = {
    "email_configs": (
        {"to": "team@example.com", "smarthost": "smtp.example.com:25"},
        "smarthost", (), ("smtp_smarthost", "smtp.example.com:25"),
    ),
    "webhook_configs": (
        {"url": "http://example.com/hook"}, "url", ("url_file",), None,
    ),
    "slack_configs": (
        {"api_url": "https://hooks.slack.com/services/x"}, "api_url", ("api_url_file",),
        ("slack_api_url", "https://hooks.slack.com/services/x"),
    ),
    "pagerduty_configs": (
        {"routing_key": "key"}, "routing_key",
        ("routing_key_file", "service_key", "service_key_file"), None,
    ),
    "opsgenie_configs": (
        {"api_key": "key"}, "api_key", ("api_key_file",), ("opsgenie_api_key", "key"),
    ),
    "msteams_configs": (
        {"webhook_url": "https://example.com/teams"}, "webhook_url", ("webhook_url_file",), None,
    ),
    "telegram_configs": (
        {"bot_token": "token", "chat_id": 1}, "bot_token", ("bot_token_file",), None,
    ),
    "discord_configs": (
        {"webhook_url": "https://discord.com/api/webhooks/1/x"}, "webhook_url",
        ("webhook_url_file",), None,
    ),
    "victorops_configs": (
        {"api_key": "key", "routing_key": "team"}, "api_key", ("api_key_file",),
        ("victorops_api_key", "key"),
    ),
    "wechat_configs": (
        {"corp_id": "corp", "api_secret": "secret"}, "corp_id", (),
        ("wechat_api_corp_id", "corp"),
    ),
    "sns_configs": (
        {"topic_arn": "arn:aws:sns:us-east-1:123456789012:alerts",
         "sigv4": {"region": "us-east-1"}},
        "topic_arn", ("phone_number", "target_arn"), None,
    ),
}

_ALTERNATIVE_VALUES = {
    "phone_number": "+15555550100",
    "target_arn": "arn:aws:sns:us-east-1:123456789012:endpoint/APNS/app/1",
}

VALID_REGEXES = ("crit|warn", "a\\.b", "(?i)crit", "\\d{3}", "[[:alpha:]]+", "(?P<x>a)",
                 "(?<x>a)", "a{,3}", "\\x{41}", "\\pL+", "\\Qa.b\\E", "a\\z")
INVALID_REGEXES = ("(?=a)", "(?<=a)b", "(a)\\1", "a{1001}", "(", "[a", "a**", "(?x)a",
                   "\\Z", "a++", "(?P=x)", "\\8", "\\p{Foo}", "[z-a]", "x{2,1}", "\\e")

# matchers: entries that neither of Alertmanager's matcher parsers reads ("bad matcher
# format", "matcher value contains unescaped double quote"), and so amlint's bad-matcher.
BAD_MATCHERS = (
    "sev=crit,,team=a",           # an empty matcher between two commas
    "sev=crit,,",
    'sev=cr"it',                  # a stray double quote
    'sev="crit',
    '"k8s.ns"="a\\.b"',           # quoted label name: Go string, \. is no escape there
    '"k8s.ns"=~\\d+',             # quoted label name: a bare value can't hold a backslash
    '"k8s.ns"=~[0-9]{3}',         # ... nor a brace
    "team=a, sev=~x{1,2}",        # the comma in the bare regex splits the entry
    '{sev="crit"}}',
    "=crit", "sev", "sev crit", "team name=a",
)

# match: / match_re: keys that aren't classic label names ("invalid label name"), and so
# amlint's bad-match-label. A null key is "" to Alertmanager.
BAD_LABEL_NAMES = ("k8s.ns", "1a", "a-b", "a:b", "a b", "ä", "", None)


@st.composite
def _integration(draw, kind: str, broken: bool = False) -> dict:
    """A valid entry (the checked key, or one of its alternatives), or with `broken`, one
    whose checked key is missing or empty."""
    base, key, alternatives, _ = INTEGRATIONS[kind]
    entry = dict(base)
    how = draw(pick(("drop", "empty") if broken else ("keep",) + alternatives))
    if how != "keep":
        del entry[key]
    if how == "empty":
        entry[key] = ""
    elif how in alternatives:
        entry[how] = _ALTERNATIVE_VALUES.get(how, "/etc/alertmanager/secret")
    return entry


# Time interval fields, valid and not (bad-time-interval): formats, ranges that end before
# they start, out-of-range days, names that aren't weekdays, months or time zones.
INTERVAL_TIMES = ("00:00", "06:00", "09:30", "17:00", "23:59", "24:00", "8:00", "24:01",
                  "12:60", "", "7", "07:5")
INTERVAL_WEEKDAYS = ("monday", "Sunday", "monday:friday", "friday:monday", "saturday:sunday",
                     "1", "mon", "MONDAY:wednesday", "monday:", "a:b:c")
INTERVAL_DAYS = ("1", "31", "-1", "1:5", "-3:-1", "0", "32", "-32", "-1:5", "-1:-5", "5:1",
                 "28:-1", "1:31", "x", "-31:-28")
INTERVAL_MONTHS = ("january", "1:3", "march:january", "13", "0", "december", "dec", "jan:12",
                   "2:1", "February:April")
INTERVAL_YEARS = ("2024", "2020:2030", "2030:2020", "-5", "x", "2024:")
INTERVAL_LOCATIONS = ("UTC", "Local", "", "Europe/Berlin", "America/New_York", "Etc/GMT+3",
                      "Europe/Kiyv", "Mars/Olympus", "Europe/")


@st.composite
def interval_entries(draw) -> list:
    """The time_intervals: entries of one interval, each field set or not."""
    entries = []
    for _ in range(draw(pick((1, 1, 2)))):
        entry: dict = {}
        if draw(st.booleans()):
            entry["times"] = [{"start_time": draw(pick(INTERVAL_TIMES)),
                               "end_time": draw(pick(INTERVAL_TIMES))}]
        for key, values in (("weekdays", INTERVAL_WEEKDAYS), ("days_of_month", INTERVAL_DAYS),
                            ("months", INTERVAL_MONTHS), ("years", INTERVAL_YEARS)):
            if draw(one_in(3)):
                entry[key] = [draw(pick(values))]
        if draw(one_in(3)):
            entry["location"] = draw(pick(INTERVAL_LOCATIONS))
        entries.append(entry)
    return entries


# Each mistake is made in 1 of N configs, independently. Integration fields, regexes,
# matchers and label names have many variants each (11 integrations with globals and _file
# forms, 16 regexes, 13 matchers in a route or an inhibit rule, 8 label names under 6 keys).
MISTAKES = {
    "undefined-receiver": 12, "undefined-time-interval": 12, "bad-regex": 4, "bad-matcher": 4,
    "bad-match-label": 6,
    "groupby-ellipsis": 12, "no-root-route": 20, "root-receiver": 20, "root-matchers": 20,
    "root-match": 20, "root-continue": 20, "root-time-intervals": 20,
    "duplicate-receiver": 12, "integration-key": 3, "time-interval": 3, "null-regex": 20,
}


@st.composite
def validation_configs(draw) -> dict:
    """A valid config, with mistakes from ALSO_CAUGHT_BY_AMTOOL now and then. A missing
    integration key is a mistake only when no global default covers it."""
    mistakes = {mistake for mistake, n in MISTAKES.items() if draw(one_in(n))}
    names = ["default", "team0", "team1"]
    receivers = []
    for name in names:
        receiver: dict = {"name": name}
        for kind in draw(st.lists(pick(sorted(INTEGRATIONS)), max_size=2,
                                  unique=True)):
            receiver[kind] = [draw(_integration(kind))]
        receivers.append(receiver)
    if "integration-key" in mistakes:
        receiver = draw(pick(receivers))
        kind = draw(pick(sorted(INTEGRATIONS)))
        receiver.setdefault(kind, []).append(draw(_integration(kind, broken=True)))
    if "duplicate-receiver" in mistakes:
        receivers.append({"name": draw(pick(names))})

    glob: dict = {"smtp_from": "alertmanager@example.com", "wechat_api_secret": "secret"}
    for kind in sorted(INTEGRATIONS):
        fallback = INTEGRATIONS[kind][3]
        if fallback and draw(st.booleans()):
            glob[fallback[0]] = fallback[1]

    root: dict = {"receiver": "default"}
    children = []
    for _ in range(draw(pick((1, 2, 3)))):
        child: dict = {"receiver": draw(pick(names)),
                       "matchers": [f"sev=~{quote(draw(pick(VALID_REGEXES)))}"]}
        group_by = draw(pick((None, [], ["alertname"], ["..."])))
        if group_by is not None:
            child["group_by"] = group_by
        if draw(st.booleans()):
            key = draw(pick(("mute_time_intervals", "active_time_intervals")))
            child[key] = ["night"]
        children.append(child)
    root["routes"] = children

    def some_route() -> dict:
        return draw(pick([root] + children))

    if "undefined-receiver" in mistakes:
        some_route()["receiver"] = "ghost"
    if "undefined-time-interval" in mistakes:
        draw(pick(children))["mute_time_intervals"] = ["ghost-interval"]
    if "bad-regex" in mistakes:
        regex = draw(pick(INVALID_REGEXES))
        draw(pick(children))["matchers"] = [f"sev=~{quote(regex)}"]
    rule = {"source_matchers": ['sev="crit"'], "target_matchers": ['sev="warn"'],
            "equal": ["team"]}
    if "bad-matcher" in mistakes:
        node, key = draw(pick([(child, "matchers") for child in children]
                              + [(rule, "source_matchers"), (rule, "target_matchers")]))
        entries = list(node[key])
        entries.insert(draw(pick(range(len(entries) + 1))), draw(pick(BAD_MATCHERS)))
        node[key] = entries
    if "bad-match-label" in mistakes:
        node, prefix = draw(pick([(child, "") for child in children]
                                 + [(rule, "source_"), (rule, "target_")]))
        node[prefix + draw(pick(("match", "match_re")))] = {draw(pick(BAD_LABEL_NAMES)): "crit"}
    if "null-regex" in mistakes:  # match_re: {sev: } with no value
        node, prefix = draw(pick([(child, "") for child in children]
                                 + [(rule, "source_"), (rule, "target_")]))
        node[prefix + "match_re"] = {"sev": None}
    if "groupby-ellipsis" in mistakes:
        some_route()["group_by"] = ["...", "alertname"]
    if "root-receiver" in mistakes:
        del root["receiver"]
    if "root-matchers" in mistakes:
        root["matchers"] = ['sev="crit"']
    if "root-match" in mistakes:
        root["match"] = {"sev": "crit"}
    if "root-continue" in mistakes:
        root["continue"] = True
    if "root-time-intervals" in mistakes:
        root[draw(pick(("mute_time_intervals", "active_time_intervals")))] = ["night"]

    cfg: dict = {"global": glob}
    if "no-root-route" not in mistakes:
        cfg["route"] = root
    cfg["receivers"] = receivers
    cfg["inhibit_rules"] = [rule]
    night = [{"times": [{"start_time": "00:00", "end_time": "06:00"}]}]
    if "time-interval" in mistakes:
        night = draw(interval_entries())
    cfg["time_intervals"] = [{"name": "night", "time_intervals": night}]
    return cfg
