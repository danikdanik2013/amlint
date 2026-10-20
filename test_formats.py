"""Tests for the config formats amlint reads (amlint/formats.py).

Run: python3 -m pytest test_formats.py -v
"""

import base64
import gzip
import json
import os
import textwrap

import pytest
import yaml

from amlint.cli import main
from amlint.formats import FormatError, load_input

_HERE = os.path.dirname(os.path.abspath(__file__))
_FORMATS = os.path.join(_HERE, "testdata", "formats")
_REAL_WORLD = os.path.join(_HERE, "testdata", "real-world")


def fixture(name):
    return os.path.join(_FORMATS, name)


def doc(text):
    return textwrap.dedent(text).lstrip("\n")


def write(tmp_path, text, name="input.yaml"):
    path = tmp_path / name
    path.write_text(doc(text))
    return str(path)


def load(text, **kwargs):
    return load_input(doc(text), **kwargs)


def check(capsys, path, *args):
    """(exit code, JSON findings, stderr) of `amlint check --format json`."""
    code = main(["check", path, "--format", "json", *args])
    out, err = capsys.readouterr()
    return code, json.loads(out), err


def exit_code(argv):
    """main()'s exit code; reading a config that can't be read exits 2 via SystemExit."""
    try:
        return main(argv)
    except SystemExit as e:
        return e.code


def codes(findings):
    return sorted(f["code"] for f in findings)


def line_of(path, needle, nth=1):
    """1-based number of the nth line of `path` that contains `needle`."""
    hits = [i for i, line in enumerate(open(path).read().split("\n"), 1) if needle in line]
    return hits[nth - 1]


def cr(spec="", text=None):
    """_CR with more spec: keys, written as if at the top level."""
    return (text or _CR) + textwrap.indent(doc(spec), "  ")


def b64(text):
    return base64.b64encode(text.encode()).decode()


# A minimal AlertmanagerConfig; tests add to it.
_CR = """
apiVersion: monitoring.coreos.com/v1alpha1
kind: AlertmanagerConfig
metadata:
  name: payments
  namespace: team-a
spec:
  route:
    receiver: slack
    routes:
      - receiver: pager
        matchers:
          - name: severity
            value: critical
  receivers:
    - name: slack
      webhookConfigs:
        - url: http://slack-relay.team-a.svc/hook
    - name: pager
      webhookConfigs:
        - url: http://pager-relay.team-a.svc/hook
"""


# ── detection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, fmt", [
    ("secret.yaml", "secret"),
    ("secret-data.yaml", "secret"),
    ("helm-values.yaml", "helm"),
    ("mimir.yaml", "mimir"),
    ("alertmanagerconfig.yaml", "alertmanagerconfig"),
])
def test_fixture_formats_are_detected(name, fmt):
    loaded = load_input(open(fixture(name)).read())
    assert loaded.format == fmt
    assert isinstance(loaded.cfg.get("route"), dict)


@pytest.mark.parametrize("name", ["kube-prometheus-style.yml", "team-routing.yml"])
def test_plain_config_is_read_as_before(name):
    text = open(os.path.join(_REAL_WORLD, name)).read()
    loaded = load_input(text)
    assert loaded.format == "alertmanager"
    assert loaded.cfg == yaml.safe_load(text)
    assert loaded.templates is None  # templates: is still checked on disk


def test_plain_config_with_alertmanager_key_stays_plain():
    # a top-level route: means it is an Alertmanager config, whatever else it has
    loaded = load("""
        route: {receiver: a}
        receivers: [{name: a}]
        alertmanager: {config: {}}
    """)
    assert loaded.format == "alertmanager"


def test_empty_input_is_an_empty_plain_config():
    loaded = load_input("")
    assert loaded.format == "alertmanager" and loaded.cfg == {}


def test_configmap_holds_a_config():
    loaded = load("""
        apiVersion: v1
        kind: ConfigMap
        metadata: {name: alertmanager}
        data:
          alertmanager.yml: |
            route: {receiver: a}
            receivers: [{name: a}]
    """)
    assert loaded.format == "secret"
    assert loaded.cfg["route"] == {"receiver": "a"}


def test_kubernetes_list_is_flattened():
    encoded = b64("route: {receiver: a}\nreceivers: [{name: a}]\n")
    loaded = load(f"""
        apiVersion: v1
        kind: List
        items:
          - apiVersion: v1
            kind: Secret
            metadata: {{name: alertmanager-main}}
            data:
              alertmanager.yaml: {encoded}
    """)
    assert loaded.format == "secret"
    assert loaded.cfg["receivers"] == [{"name": "a"}]


def test_generated_secret_is_gzipped():
    raw = gzip.compress(b"route: {receiver: a}\nreceivers: [{name: a}]\n")
    loaded = load(f"""
        apiVersion: v1
        kind: Secret
        metadata: {{name: alertmanager-main-generated}}
        data:
          alertmanager.yaml.gz: {base64.b64encode(raw).decode()}
    """)
    assert loaded.cfg["route"] == {"receiver": "a"}


def test_string_data_wins_over_data():
    encoded = b64("route: {receiver: old}")
    loaded = load(f"""
        apiVersion: v1
        kind: Secret
        metadata: {{name: alertmanager-main}}
        data:
          alertmanager.yaml: {encoded}
        stringData:
          alertmanager.yaml: "route: {{receiver: new}}"
    """)
    assert loaded.cfg["route"] == {"receiver": "new"}


def test_input_format_forces_plain(tmp_path, capsys):
    # read as a plain config, a Mimir file has no route at the top level
    path = fixture("mimir.yaml")
    code, findings, _ = check(capsys, path, "--input-format", "alertmanager")
    assert code == 1 and "no-root-route" in codes(findings)


@pytest.mark.parametrize("fmt, wanted", [
    ("secret", "found no Kubernetes Secret or ConfigMap with an alertmanager.yaml key"),
    ("helm", "found no kube-prometheus-stack Helm values"),
    ("mimir", "found no Mimir/Cortex config"),
    ("alertmanagerconfig", "found no AlertmanagerConfig resource"),
])
def test_input_format_that_does_not_fit_exits_2(fmt, wanted, capsys):
    path = os.path.join(_REAL_WORLD, "team-routing.yml")
    assert exit_code(["check", path, "--input-format", fmt]) == 2
    assert wanted in " ".join(capsys.readouterr().err.split())


def test_stream_without_a_config_exits_2(tmp_path, capsys):
    path = write(tmp_path, """
        apiVersion: apps/v1
        kind: Deployment
        metadata: {name: web}
        ---
        apiVersion: v1
        kind: Secret
        metadata: {name: creds}
        stringData: {password: hunter2}
    """)
    assert exit_code(["check", path]) == 2
    err = " ".join(capsys.readouterr().err.split())
    assert "found no Alertmanager config (it has: Deployment, Secret)" in err


def test_prometheus_rule_manifest_hints_at_rules(tmp_path, capsys):
    path = write(tmp_path, """
        apiVersion: monitoring.coreos.com/v1
        kind: PrometheusRule
        metadata: {name: rules}
        spec: {groups: []}
    """)
    assert exit_code(["check", path]) == 2
    assert "--rules 'rules/*.yml'" in " ".join(capsys.readouterr().err.split())


def test_two_configs_in_one_stream_exit_2(tmp_path, capsys):
    path = write(tmp_path, """
        route: {receiver: a}
        ---
        route: {receiver: b}
    """)
    assert exit_code(["check", path]) == 2
    assert "found 2 Alertmanager configs (lines 1, 3)" in " ".join(capsys.readouterr().err.split())


def test_bad_base64_exits_2(tmp_path, capsys):
    path = write(tmp_path, """
        apiVersion: v1
        kind: Secret
        metadata: {name: alertmanager-main}
        data:
          alertmanager.yaml: not*base64
    """)
    assert exit_code(["check", path]) == 2
    assert "is not valid base64" in " ".join(capsys.readouterr().err.split())


def test_parse_error_inside_a_block_points_at_the_file_line():
    with pytest.raises(FormatError) as e:
        load("""
            apiVersion: v1
            kind: Secret
            metadata: {name: alertmanager-main}
            stringData:
              alertmanager.yaml: |
                route:
                  receiver: a: b
        """)
    assert e.value.title == "Failed to parse YAML"
    assert str(e.value) == ("alertmanager.yaml in Secret alertmanager-main: mapping values "
                            "are not allowed here (line 7, column 18)")


# ── AlertmanagerConfig conversion ────────────────────────────────────

def test_receiver_and_interval_names_are_namespaced():
    text = _CR.replace("            value: critical\n",
                       "            value: critical\n        muteTimeIntervals: [weekends]\n")
    loaded = load(cr("""
        muteTimeIntervals:
          - name: weekends
            timeIntervals:
              - weekdays: [saturday, sunday]
    """, text))
    cfg = loaded.cfg
    assert [r["name"] for r in cfg["receivers"]] == [
        "null", "team-a/payments/slack", "team-a/payments/pager",
    ]
    sub = cfg["route"]["routes"][0]
    assert sub["receiver"] == "team-a/payments/slack"
    assert sub["routes"][0]["receiver"] == "team-a/payments/pager"
    assert sub["routes"][0]["mute_time_intervals"] == ["team-a/payments/weekends"]
    assert cfg["time_intervals"] == [
        {"name": "team-a/payments/weekends",
         "time_intervals": [{"weekdays": ["saturday", "sunday"]}]},
    ]


def test_namespace_matcher_and_continue_on_the_top_route_only():
    route = load(_CR).cfg["route"]
    # the operator's default config is the base when the input has none
    assert route["receiver"] == "null"
    sub = route["routes"][0]
    assert sub["matchers"] == ['namespace="team-a"']
    assert sub["continue"] is True
    assert sub["routes"][0]["matchers"] == ['severity="critical"']
    assert "continue" not in sub["routes"][0]


def test_namespace_matcher_on_both_sides_of_inhibit_rules():
    rules = load(cr("""
        inhibitRules:
          - sourceMatch: [{name: severity, value: critical}]
            targetMatch: [{name: severity, value: warning}]
            equal: [alertname]
          - sourceMatch: [{name: namespace, value: team-a, matchType: "="}]
            targetMatch: [{name: alertname, value: Noisy}]
    """)).cfg["inhibit_rules"]
    assert rules[0] == {
        "source_matchers": ['severity="critical"', 'namespace="team-a"'],
        "target_matchers": ['severity="warning"', 'namespace="team-a"'],
        "equal": ["alertname"],
    }
    # not added twice where the rule already has it
    assert rules[1]["source_matchers"] == ['namespace="team-a"']
    assert rules[1]["target_matchers"] == ['alertname="Noisy"', 'namespace="team-a"']


def test_matcher_strategy_none_adds_no_matcher():
    loaded = load(cr("""
        inhibitRules:
          - sourceMatch: [{name: severity, value: critical}]
            targetMatch: [{name: severity, value: warning}]
            equal: [alertname]
    """), matcher_strategy="None")
    sub = loaded.cfg["route"]["routes"][0]
    assert "matchers" not in sub and sub["continue"] is True
    assert loaded.cfg["inhibit_rules"][0]["source_matchers"] == ['severity="critical"']


_ALERTMANAGER = """
apiVersion: monitoring.coreos.com/v1
kind: Alertmanager
metadata: {name: main, namespace: monitoring}
spec:
  alertmanagerConfigMatcherStrategy: {type: %s}
---
"""


def test_matcher_strategy_from_the_alertmanager_resource():
    stream = _ALERTMANAGER % "None" + doc(_CR)
    assert "matchers" not in load_input(stream).cfg["route"]["routes"][0]
    # the flag wins over the resource
    sub = load_input(stream, matcher_strategy="OnNamespace").cfg["route"]["routes"][0]
    assert sub["matchers"] == ['namespace="team-a"']


def test_matcher_strategy_flag(tmp_path, capsys):
    path = write(tmp_path, _CR)
    labels = ["namespace=team-b", "alertname=X", "--format", "json"]
    main(["route", path, *labels])
    assert json.loads(capsys.readouterr().out)["receivers"] == ["null"]
    main(["route", path, *labels, "--matcher-strategy", "None"])
    assert json.loads(capsys.readouterr().out)["receivers"] == ["team-a/payments/slack"]


def test_matcher_strategy_from_helm_values():
    stream = doc("""
        alertmanager:
          alertmanagerSpec:
            alertmanagerConfigMatcherStrategy: {type: None}
        ---
    """) + doc(_CR)
    loaded = load_input(stream)
    assert loaded.cfg["route"]["receiver"] == "null"  # the chart's default config is the base
    assert "matchers" not in loaded.cfg["route"]["routes"][0]


def test_matcher_strategy_except_for_the_alertmanager_namespace():
    own = doc(_CR).replace("namespace: team-a", "namespace: monitoring").replace(
        "name: payments", "name: platform")
    stream = (_ALERTMANAGER % "OnNamespaceExceptForAlertmanagerNamespace"
              + doc(_CR) + "---\n" + own)
    routes = load_input(stream).cfg["route"]["routes"]
    by_receiver = {r["receiver"]: r for r in routes}
    assert by_receiver["team-a/payments/slack"]["matchers"] == ['namespace="team-a"']
    assert "matchers" not in by_receiver["monitoring/platform/slack"]


def test_resources_merge_in_namespace_name_order_before_the_base_routes():
    team_b = doc(_CR).replace("team-a", "team-b").replace("name: payments", "name: api")
    team_a2 = doc(_CR).replace("name: payments", "name: billing")
    base = doc("""
        route:
          receiver: default
          routes:
            - receiver: default
              matchers: [alertname=Watchdog]
        receivers: [{name: default}]
        ---
    """)
    loaded = load_input(base + team_b + "---\n" + doc(_CR) + "---\n" + team_a2)
    receivers = [r["receiver"] for r in loaded.cfg["route"]["routes"]]
    assert receivers == ["team-a/billing/slack", "team-a/payments/slack", "team-b/api/slack",
                         "default"]
    # the base's route keeps pointing at its own line
    assert loaded.locate("route.routes[3]") == (4, 7)


def test_resource_without_route_keeps_only_its_inhibit_rules():
    loaded = load("""
        apiVersion: monitoring.coreos.com/v1alpha1
        kind: AlertmanagerConfig
        metadata: {name: rules-only, namespace: team-a}
        spec:
          receivers: [{name: unused}]
          inhibitRules:
            - sourceMatch: [{name: severity, value: critical}]
              targetMatch: [{name: severity, value: warning}]
              equal: [alertname]
    """)
    assert [r["name"] for r in loaded.cfg["receivers"]] == ["null"]
    assert len(loaded.cfg["inhibit_rules"]) == 1
    assert any("drops its receivers" in note for _, note in loaded.notes)


def test_v1beta1_time_intervals_and_matchers():
    loaded = load("""
        apiVersion: monitoring.coreos.com/v1beta1
        kind: AlertmanagerConfig
        metadata: {name: search, namespace: team-b}
        spec:
          route:
            receiver: hook
            matchers:
              - {name: service, value: 'search-.*', matchType: "=~"}
              - {name: env, value: prod}
            activeTimeIntervals: [office]
          receivers:
            - name: hook
              webhookConfigs: [{url: http://hook}]
          timeIntervals:
            - name: office
              timeIntervals:
                - times: [{startTime: "09:00", endTime: "17:00"}]
    """)
    sub = loaded.cfg["route"]["routes"][0]
    assert sub["matchers"] == ['service=~"search-.*"', 'env="prod"', 'namespace="team-b"']
    assert sub["active_time_intervals"] == ["team-b/search/office"]
    assert loaded.cfg["time_intervals"][0] == {
        "name": "team-b/search/office",
        "time_intervals": [{"times": [{"start_time": "09:00", "end_time": "17:00"}]}],
    }


def test_wrong_time_intervals_field_for_the_version_is_noted():
    loaded = load(cr("""
        timeIntervals:
          - name: office
    """))
    assert "time_intervals" not in loaded.cfg
    assert any("spec.timeIntervals is not a v1alpha1 field" in n for _, n in loaded.notes)


def test_v1alpha1_regex_matcher_and_value_escaping():
    text = _CR.replace("value: critical", "value: crit|page\n            regex: true")
    loaded = load(cr("""
        inhibitRules:
          - sourceMatch: [{name: msg, value: 'say "hi"\\back'}]
            targetMatch: [{name: severity, value: warning}]
            equal: [alertname]
    """, text))
    assert loaded.cfg["route"]["routes"][0]["routes"][0]["matchers"] == ['severity=~"crit|page"']
    assert loaded.cfg["inhibit_rules"][0]["source_matchers"][0] == r'msg="say \"hi\"\\back"'


def test_zero_intervals_are_dropped_like_the_operator_does():
    loaded = load(_CR.replace("receiver: slack\n", "receiver: slack\n    groupInterval: 0s\n"
                              "    repeatInterval: 0s\n    groupWait: 0s\n", 1))
    sub = loaded.cfg["route"]["routes"][0]
    assert "group_interval" not in sub and "repeat_interval" not in sub
    assert sub["group_wait"] == "0s"


def test_missing_namespace_assumes_default():
    loaded = load(_CR.replace("  namespace: team-a\n", ""))
    assert loaded.cfg["receivers"][1]["name"] == "default/payments/slack"
    assert any("assuming 'default'" in note for _, note in loaded.notes)


def test_alertmanager_configuration_names_the_whole_config():
    stream = doc("""
        apiVersion: monitoring.coreos.com/v1
        kind: Alertmanager
        metadata: {name: main, namespace: monitoring}
        spec:
          alertmanagerConfiguration:
            name: global
            global:
              resolveTimeout: 5m
              slackApiUrl: {name: slack, key: url}
            templates:
              - configMap: {name: templates, key: slack.tmpl}
        ---
        apiVersion: monitoring.coreos.com/v1alpha1
        kind: AlertmanagerConfig
        metadata: {name: global, namespace: monitoring}
        spec:
          route:
            receiver: slack
          receivers:
            - name: slack
              slackConfigs: [{channel: "#alerts"}]
        ---
    """) + doc(_CR)
    cfg = load_input(stream).cfg
    assert cfg["global"] == {"resolve_timeout": "5m",
                             "slack_api_url": "<secret monitoring/slack:url>"}
    assert cfg["templates"] == ["/etc/alertmanager/templates/slack.tmpl"]
    # its route is the root, without a namespace matcher; the other resource goes under it
    assert cfg["route"]["receiver"] == "monitoring/global/slack"
    assert "matchers" not in cfg["route"]
    assert cfg["route"]["routes"][0]["receiver"] == "team-a/payments/slack"


# ── Secret references and receiver fields ────────────────────────────

_INTEGRATIONS = """
apiVersion: monitoring.coreos.com/v1alpha1
kind: AlertmanagerConfig
metadata: {name: all, namespace: team-a}
spec:
  route:
    receiver: r
  receivers:
    - name: r
      slackConfigs: [{apiURL: {name: s, key: k}}]
      pagerdutyConfigs: [{routingKey: {name: s, key: k}}]
      opsgenieConfigs: [{apiKey: {name: s, key: k}}]
      msteamsConfigs: [{webhookUrl: {name: s, key: k}}]
      msteamsv2Configs: [{webhookURL: {name: s, key: k}}]
      telegramConfigs: [{botToken: {name: s, key: k}, chatID: 1}]
      discordConfigs: [{apiURL: {name: s, key: k}}]
      webhookConfigs: [{urlSecret: {name: s, key: k}, url: http://ignored}]
      victoropsConfigs: [{apiKey: {name: s, key: k}, routingKey: team}]
      wechatConfigs: [{corpID: corp, apiSecret: {name: s, key: k}}]
      snsConfigs: [{topicARN: "arn:aws:sns:eu-west-1:1:t"}]
      emailConfigs: [{to: a@example.org, smarthost: "smtp:25"}]
"""


def test_secret_references_count_as_configured(tmp_path, capsys):
    loaded = load(_INTEGRATIONS)
    rcv = loaded.cfg["receivers"][1]
    assert rcv["webhook_configs"] == [{"url": "<secret team-a/s:k>"}]
    assert rcv["discord_configs"] == [{"webhook_url": "<secret team-a/s:k>"}]
    assert rcv["telegram_configs"] == [{"bot_token": "<secret team-a/s:k>", "chat_id": 1}]
    assert rcv["wechat_configs"][0]["corp_id"] == "corp"
    code, findings, _ = check(capsys, write(tmp_path, _INTEGRATIONS))
    assert code == 0 and findings == []


def test_missing_secret_references_are_findings(tmp_path, capsys):
    text = doc("""
        apiVersion: monitoring.coreos.com/v1alpha1
        kind: AlertmanagerConfig
        metadata: {name: all, namespace: team-a}
        spec:
          route:
            receiver: r
          receivers:
            - name: r
              slackConfigs: [{channel: "#a"}]
              pagerdutyConfigs: [{severity: critical}]
              telegramConfigs: [{chatID: 1}]
              webhookConfigs: [{maxAlerts: 1}]
    """)
    path = write(tmp_path, text)
    code, findings, _ = check(capsys, path)
    assert code == 1
    assert codes(findings) == ["pagerduty-no-routing-key", "slack-no-api-url",
                               "telegram-no-bot-token", "webhook-no-url"]
    by_code = {f["code"]: f for f in findings}
    assert by_code["slack-no-api-url"]["where"] == "receivers[team-a/all/r].slack_configs[0]"
    # each one points at its entry in the resource
    assert by_code["slack-no-api-url"]["line"] == line_of(path, "slackConfigs")
    assert by_code["webhook-no-url"]["line"] == line_of(path, "webhookConfigs")


# ── what the operator's namespace matcher does to inhibit checks ─────

def test_namespace_matcher_causes_no_inhibit_findings(tmp_path, capsys):
    path = write(tmp_path, cr("""
        inhibitRules:
          - sourceMatch: [{name: alertname, value: NodeDown}]
            targetMatch: [{name: alertname, value: PodDown}]
            equal: [node]
          - sourceMatch: [{name: alertname, value: ZoneDown}]
            targetMatch: [{name: alertname, value: NodeDown}]
            equal: [zone]
          - targetMatch: [{name: alertname, value: InfoInhibitor}]
    """))
    code, findings, _ = check(capsys, path)
    assert findings == []


def test_genuine_inhibit_findings_on_a_resource_stay(tmp_path, capsys):
    path = write(tmp_path, cr("""
        inhibitRules:
          - sourceMatch: [{name: severity, value: critical}, {name: team, value: a}]
            targetMatch: [{name: severity, value: warning}, {name: team, value: a}]
    """))
    code, findings, _ = check(capsys, path)
    assert codes(findings) == ["inhibit-no-equal", "inhibit-same-match"]
    assert "'team=a'" in [f for f in findings if f["code"] == "inhibit-same-match"][0]["message"]
    assert all(f["line"] == line_of(path, "- sourceMatch") for f in findings)


# ── Helm values ──────────────────────────────────────────────────────

def test_helm_values_merge_over_the_chart_default(tmp_path, capsys):
    # The well-known kube-prometheus-stack trap: replacing receivers drops 'null', but the
    # chart's default Watchdog route still sends there.
    path = write(tmp_path, """
        alertmanager:
          config:
            route:
              receiver: slack
            receivers:
              - name: slack
                slack_configs:
                  - api_url: https://hooks.slack.com/services/T/B/X
    """)
    code, findings, err = check(capsys, path)
    assert code == 1
    undefined = [f for f in findings if f["code"] == "undefined-receiver"]
    assert len(undefined) == 1 and "'null'" in undefined[0]["message"]
    assert undefined[0]["where"] == "route.routes[0]"
    # it isn't in the file: reported at the route: that inherits it
    assert undefined[0]["line"] == line_of(path, "route:")
    err = " ".join(err.split())
    assert "route.routes come from the chart" in err


def test_helm_values_without_a_config_get_the_chart_default():
    loaded = load("""
        alertmanager:
          enabled: true
        grafana:
          enabled: false
    """)
    assert loaded.format == "helm"
    assert loaded.cfg["route"]["routes"] == [
        {"receiver": "null", "matchers": ['alertname = "Watchdog"']},
    ]
    assert loaded.locate("route.routes[0]") == (1, 1)  # the alertmanager: key


def test_helm_null_removes_a_chart_default():
    loaded = load("""
        alertmanager:
          config:
            inhibit_rules: null
            route: {receiver: a, routes: []}
            receivers: [{name: a}]
    """)
    assert "inhibit_rules" not in loaded.cfg
    assert loaded.cfg["global"] == {"resolve_timeout": "5m"}


def test_helm_string_config_needs_tpl_config():
    values = """
        alertmanager:
          tplConfig: %s
          stringConfig: |
            route:
              receiver: from-string
            receivers: [{name: from-string}]
          config:
            route: {receiver: from-map}
            receivers: [{name: from-map}]
    """
    loaded = load(values % "false")
    assert loaded.cfg["route"]["receiver"] == "from-map"
    assert any("only used with alertmanager.tplConfig" in note for _, note in loaded.notes)
    loaded = load(values % "true")
    assert loaded.cfg["route"]["receiver"] == "from-string"
    assert loaded.locate("route") == (4, 5)


def test_helm_template_files(tmp_path, capsys):
    values = """
        alertmanager:
          config:
            route: {receiver: a, routes: []}
            receivers: [{name: a, webhook_configs: [{url: http://a}]}]
            templates: [%s]
          templateFiles:
            slack.tpl: '{{ define "x" }}{{ end }}'
    """
    path = write(tmp_path, values % "/etc/alertmanager/config/*.tmpl")
    code, findings, _ = check(capsys, path)
    assert codes(findings) == ["template-file-missing"]
    assert findings[0]["level"] == "warn" and "(slack.tpl)" in findings[0]["message"]
    assert findings[0]["line"] == line_of(path, "templates:")
    path = write(tmp_path, values % "/etc/alertmanager/config/*.tpl")
    assert check(capsys, path)[1] == []


def test_helm_chart_default_templates_without_template_files(tmp_path, capsys):
    # the chart's default `*.tmpl` with no templateFiles loads nothing, on purpose
    path = write(tmp_path, """
        alertmanager:
          config:
            route: {receiver: a, routes: []}
            receivers: [{name: a, webhook_configs: [{url: http://a}]}]
    """)
    assert check(capsys, path)[1] == []


# ── Secret: template files, lines, suppressions ──────────────────────

_SECRET = """
apiVersion: v1
kind: Secret
metadata: {name: alertmanager-main, namespace: monitoring}
stringData:
  alertmanager.yaml: |
    global:
      resolve_timeout: 5m
    route:
      receiver: default
      routes:
        - receiver: ghost
    receivers:
      - name: default
        webhook_configs: [{url: http://a}]
    templates:
      - /etc/alertmanager/config/slack.tmpl
      - relative/*.tmpl
      - /somewhere/else.tmpl
  notify.tmpl: '{{ define "x" }}{{ end }}'
"""


def test_secret_findings_point_into_the_block(tmp_path, capsys):
    path = write(tmp_path, _SECRET)
    code, findings, _ = check(capsys, path)
    assert code == 1
    by_code = {f["code"]: f for f in findings}
    assert set(by_code) == {"undefined-receiver", "template-file-missing"}
    # same line and column as the YAML inside the block
    assert (by_code["undefined-receiver"]["line"], by_code["undefined-receiver"]["column"]) == \
        (line_of(path, "- receiver: ghost"), 11)
    missing = by_code["template-file-missing"]
    assert missing["level"] == "error" and missing["where"] == "templates[0]"
    assert "(notify.tmpl)" in missing["message"]
    assert missing["line"] == line_of(path, "slack.tmpl")


def test_secret_data_findings_point_at_the_key(tmp_path, capsys):
    inner = "global: {resolve_timeout: 5m}\n" \
            "route:\n  receiver: default\n  routes: [{receiver: ghost}]\n" \
            "receivers: [{name: default, webhook_configs: [{url: http://a}]}]\n"
    path = write(tmp_path, f"""
        apiVersion: v1
        kind: Secret
        metadata: {{name: alertmanager-main}}
        data:
          alertmanager.yaml: {b64(inner)}
    """)
    code, findings, _ = check(capsys, path)
    assert codes(findings) == ["undefined-receiver"]
    assert (findings[0]["line"], findings[0]["column"]) == (5, 3)


def test_suppression_inside_an_embedded_block(tmp_path, capsys):
    text = _SECRET.replace("- receiver: ghost",
                           "- receiver: ghost  # amlint: disable=undefined-receiver")
    code, findings, _ = check(capsys, write(tmp_path, text))
    assert codes(findings) == ["template-file-missing"]


def test_disable_file_inside_an_embedded_block(tmp_path, capsys):
    text = _SECRET.replace("    global:\n",
                           "    # amlint: disable-file=template-file-missing\n    global:\n")
    code, findings, _ = check(capsys, write(tmp_path, text))
    assert codes(findings) == ["undefined-receiver"]


def test_unknown_code_in_an_embedded_comment_is_reported_at_the_file_line(tmp_path, capsys):
    text = _SECRET.replace("- receiver: ghost", "- receiver: ghost  # amlint: disable=undefind")
    path = write(tmp_path, text)
    _, _, err = check(capsys, path)
    assert f"comment at {path}:{line_of(path, 'disable=undefind')}" in " ".join(err.split())


def test_github_and_sarif_use_file_lines(tmp_path, capsys):
    path = write(tmp_path, _SECRET)
    ghost = line_of(path, "- receiver: ghost")
    main(["check", path, "--format", "github"])
    assert f"line={ghost},col=11" in capsys.readouterr().out
    main(["check", path, "--format", "sarif"])
    regions = [r["locations"][0]["physicalLocation"]["region"]
               for r in json.loads(capsys.readouterr().out)["runs"][0]["results"]]
    assert {"startLine": ghost, "startColumn": 11} in regions


def test_stdin_secret(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(doc(_SECRET)))
    code = main(["check", "-", "--format", "json"])
    findings = json.loads(capsys.readouterr().out)
    assert code == 1 and {f["line"] for f in findings} == {11, 16}


# ── AlertmanagerConfig lines and suppressions ────────────────────────

def test_resource_findings_point_at_the_resource(tmp_path, capsys):
    path = write(tmp_path, _CR.replace("receiver: pager", "receiver: pagr"))
    code, findings, _ = check(capsys, path)
    by_code = {f["code"]: f for f in findings}
    assert set(by_code) == {"undefined-receiver", "unused-receiver"}
    assert by_code["undefined-receiver"]["where"] == "route.routes[0].routes[0]"
    assert by_code["undefined-receiver"]["line"] == line_of(path, "receiver: pagr")
    assert by_code["unused-receiver"]["where"] == "receivers[team-a/payments/pager]"
    assert by_code["unused-receiver"]["line"] == line_of(path, "- name: pager")


def test_suppression_in_a_resource(tmp_path, capsys):
    text = _CR.replace("receiver: pager", "receiver: pagr  # amlint: disable=undefined-receiver")
    code, findings, _ = check(capsys, write(tmp_path, text))
    assert codes(findings) == ["unused-receiver"]


def test_findings_in_the_operator_default_config_are_dropped(tmp_path, capsys):
    # the default config has no resolve_timeout and a 'null' receiver; neither is in the file
    code, findings, _ = check(capsys, write(tmp_path, _CR))
    assert code == 0 and findings == []


# ── commands on every fixture ────────────────────────────────────────

@pytest.mark.parametrize("name", ["secret.yaml", "secret-data.yaml", "helm-values.yaml",
                                  "mimir.yaml", "alertmanagerconfig.yaml"])
def test_commands_work_on_every_fixture(name, capsys):
    path = fixture(name)
    code, findings, _ = check(capsys, path, "--strict")
    assert code == 0, findings
    assert {f["code"] for f in findings} <= {"empty-receiver"}  # the 'null' blackholes
    assert main(["tree", path]) == 0
    assert main(["diff", path, path]) == 0
    assert "No routing changes" in capsys.readouterr().out
    assert main(["route", path, "alertname=X", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["receivers"]


def test_routing_tests_on_the_resource_fixture(capsys):
    code = main(["test", fixture("alertmanagerconfig.yaml"),
                 fixture("alertmanagerconfig-tests.yaml"), "--coverage", "--format", "json"])
    doc_ = json.loads(capsys.readouterr().out)
    assert code == 0, [t for t in doc_["tests"] if not t["passed"]]
    assert doc_["coverage"]["percent"] == 100.0


def test_route_shows_the_resource_inhibit_rule(capsys):
    path = fixture("alertmanagerconfig.yaml")
    main(["route", path, "namespace=team-a", "alertname=Latency", "severity=warning",
          "--firing", "namespace=team-a,alertname=Latency,severity=critical", "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    assert out["receivers"] == ["team-a/payments/slack"]
    assert out["inhibited_by"]["rule"] == "inhibit_rules[0]"
    assert out["inhibited_by"]["line"] == line_of(path, "- sourceMatch:")


def test_tree_shows_the_merged_routes(capsys):
    main(["tree", fixture("alertmanagerconfig.yaml")])
    out = capsys.readouterr().out
    assert '{namespace="team-a"}  →  team-a/payments/slack  [continue]' in out
    assert "team-b/search/webhook" in out


def test_diff_between_two_versions_of_a_resource(tmp_path, capsys):
    old = write(tmp_path, _CR, "old.yaml")
    new = write(tmp_path, _CR.replace("value: critical", "value: page"), "new.yaml")
    assert main(["diff", old, new, "--format", "json"]) == 0
    changes = json.loads(capsys.readouterr().out)["routing"]["changes"]
    assert any(c["labels"].get("namespace") == "team-a" and c["labels"].get("severity")
               for c in changes)


def test_input_format_flag_on_every_command(capsys):
    path = fixture("secret.yaml")
    for argv in (["check", path], ["tree", path], ["diff", path, path],
                 ["route", path, "alertname=X"],
                 ["test", path, fixture("alertmanagerconfig-tests.yaml")]):
        assert exit_code(argv + ["--input-format", "helm"]) == 2, argv
        assert "found no kube-prometheus-stack Helm values" in \
            " ".join(capsys.readouterr().err.split())


# ── a route tree that contains itself ────────────────────────────────

@pytest.mark.parametrize("text, message", [
    ("""
        apiVersion: v1
        kind: Secret
        metadata: {name: alertmanager-main}
        stringData:
          alertmanager.yaml: |
            route: &r
              receiver: a
              routes: [*r]
            receivers: [{name: a}]
     """, "route.routes[0] is a YAML alias of route, which contains it"),
    ("""
        alertmanager:
          config:
            route: &r
              receiver: "null"
              routes: [*r]
     """, "alertmanager.config.route.routes[0] is a YAML alias of alertmanager.config.route"),
    ("""
        alertmanager_config: |
          route:
            receiver: a
            routes:
              - &x
                receiver: a
                routes: [*x]
          receivers: [{name: a}]
     """, "route.routes[0].routes[0] is a YAML alias of route.routes[0], which contains it"),
    (_CR.replace("  route:\n    receiver: slack\n    routes:",
                 "  route: &r\n    receiver: slack\n    routes:\n      - *r"),
     "AlertmanagerConfig team-a/payments (line 1): spec.route.routes[0] is a YAML alias of "
     "spec.route, which contains it"),
])
def test_route_that_contains_itself_exits_2(tmp_path, capsys, text, message):
    """yaml.safe_load builds a recursive dict for it, which every route walk (and the
    AlertmanagerConfig conversion) would follow until RecursionError."""
    path = write(tmp_path, text)
    for argv in (["check", path], ["tree", path], ["route", path, "a=b"],
                 ["test", path, fixture("alertmanagerconfig-tests.yaml")],
                 ["diff", path, path]):
        assert exit_code(argv) == 2, argv
        err = capsys.readouterr().err
        assert f"Route contains itself ({path}): {message}" in err, argv
        assert "Traceback" not in err


def test_routes_shared_by_alias_in_a_resource_still_work(tmp_path, capsys):
    path = write(tmp_path, _CR.replace("""
    routes:
      - receiver: pager
        matchers:
          - name: severity
            value: critical
""", """
    routes:
      - matchers: [{name: team, value: db}]
        routes: &common
          - receiver: pager
            matchers: [{name: severity, value: critical}]
      - matchers: [{name: team, value: web}]
        routes: *common
"""))
    code, findings, _ = check(capsys, path)
    assert code == 0, findings
    assert main(["route", path, "namespace=team-a", "team=web", "severity=critical",
                 "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["receivers"] == ["team-a/payments/pager"]


# ── Grafana Alerting ──────────────────────────────────────────────────

_GRAFANA = os.path.join(_FORMATS, "grafana")


def test_grafana_fixture_works_with_every_command(capsys):
    path = os.path.join(_GRAFANA, "alerting.yaml")
    code, findings, err = check(capsys, path, "--strict")
    assert code == 0 and findings == [], findings
    loaded = load_input(open(path).read(), path=path)
    assert loaded.format == "grafana"
    assert [r["name"] for r in loaded.cfg["receivers"]] == ["ops-email", "ops-slack", "db-pager"]
    assert main(["tree", path]) == 0
    assert "{team=db, severity=critical}  →  db-pager" in capsys.readouterr().out
    assert main(["diff", path, path]) == 0
    capsys.readouterr()
    main(["route", path, "team=db", "severity=critical", "--format", "json"])
    assert json.loads(capsys.readouterr().out)["receivers"] == ["db-pager"]
    main(["route", path, "team=api", "--format", "json"])
    assert json.loads(capsys.readouterr().out)["receivers"] == ["ops-slack"]


def test_grafana_object_matchers():
    loaded = load("""
        policies:
          - receiver: a
            routes:
              - receiver: b
                object_matchers:
                  - - env
                    - =
                    - '"NO"'
                  - [code, =~, "5.."]
              - receiver: c
                object_matchers: [[team, "!=", db]]
        contactPoints:
          - {name: a, receivers: [{uid: a, type: email, settings: {addresses: a@b.c}}]}
          - {name: b, receivers: [{uid: b, type: email, settings: {addresses: a@b.c}}]}
          - {name: c, receivers: [{uid: c, type: email, settings: {addresses: a@b.c}}]}
    """)
    from amlint.linter import _matchers
    route = loaded.cfg["route"]
    # Grafana strips one double quote from each end; YAML's NO stays text
    assert _matchers(route["routes"][0]) == [("env", "=", "NO"), ("code", "=~", "5..")]
    assert _matchers(route["routes"][1]) == [("team", "!=", "db")]


def test_grafana_exported_object_matchers_parse():
    """Grafana's export writes `- =` unquoted, which YAML 1.1 reads as the "value" tag."""
    loaded = load("""
        apiVersion: 1
        policies:
          - orgId: 1
            receiver: grafana-default-email
            routes:
              - object_matchers:
                  - - severity
                    - =
                    - warn
    """)
    from amlint.linter import _matchers
    assert _matchers(loaded.cfg["route"]["routes"][0]) == [("severity", "=", "warn")]


def test_grafana_names_defined_in_grafana(tmp_path, capsys):
    """An export of the policies alone uses contact points and mute timings that are in
    Grafana, not in the file: no undefined-receiver, and a note says so."""
    path = write(tmp_path, """
        apiVersion: 1
        policies:
          - orgId: 1
            receiver: grafana-default-email
            routes:
              - receiver: team-pager
                object_matchers: [[team, =, db]]
                mute_time_intervals: [weekends]
    """, "policies.yaml")
    code, findings, err = check(capsys, path)
    assert code == 0 and codes(findings) == []
    assert "taken to be in Grafana: grafana-default-email, team-pager, weekends" in err


def test_grafana_files_next_to_each_other(tmp_path, capsys):
    """Grafana reads every file in the directory: a policy here uses a contact point there,
    and each file's findings are reported when that file is checked."""
    policies = write(tmp_path, """
        apiVersion: 1
        policies:
          - orgId: 1
            receiver: ops
            routes:
              - receiver: ops
                object_matchers: [[team, =, db]]
              - receiver: ops
                object_matchers: [[team, =, db]]
    """, "policies.yaml")
    points = write(tmp_path, """
        apiVersion: 1
        contactPoints:
          - orgId: 1
            name: ops
            receivers:
              - uid: ops
                type: slack
                settings:
                  recipient: "#ops"
    """, "contact-points.yaml")
    write(tmp_path, "not: [grafana", "broken.yaml")
    _, findings, err = check(capsys, policies)
    assert codes(findings) == ["route-match-collision"]
    assert "taken to be in Grafana" not in err
    _, findings, _ = check(capsys, points)
    assert codes(findings) == ["grafana-contact-point"]
    assert findings[0]["line"] == line_of(points, "settings:")


def test_grafana_provisioning_upserts(tmp_path, capsys):
    """A mute timing, template or integration seen again replaces the earlier one."""
    path = write(tmp_path, """
        apiVersion: 1
        policies:
          - receiver: a
            routes: [{receiver: a, object_matchers: [[x, =, y]], mute_time_intervals: [n]}]
        contactPoints:
          - {name: a, receivers: [{uid: a1, type: email, settings: {addresses: a@b.c}}]}
          - {name: a, receivers: [{uid: a1, type: email, settings: {addresses: c@d.e}}]}
        muteTimes:
          - {name: n, time_intervals: [{weekdays: [monday]}]}
          - {name: n, time_intervals: [{weekdays: [sunday]}]}
    """)
    _, findings, _ = check(capsys, path)
    assert codes(findings) == []
    loaded = load_input(open(path).read(), path=path)
    [receiver] = loaded.cfg["receivers"]
    assert receiver["grafana_managed_receiver_configs"][0]["settings"]["addresses"] == "c@d.e"
    assert loaded.cfg["time_intervals"] == [{"name": "n",
                                             "time_intervals": [{"weekdays": ["sunday"]}]}]


def test_grafana_orgs(capsys, tmp_path):
    path = write(tmp_path, """
        policies:
          - {orgId: 2, receiver: b}
          - {orgId: 1, receiver: a}
        contactPoints:
          - {orgId: 1, name: a, receivers: [{uid: a, type: email, settings: {addresses: x}}]}
          - {orgId: 2, name: b, receivers: [{uid: b, type: email, settings: {addresses: x}}]}
    """)
    _, findings, err = check(capsys, path)
    assert "these files configure Grafana orgs 1, 2; amlint checks org 1" in err
    assert load_input(open(path).read()).cfg["route"]["receiver"] == "a"


@pytest.mark.parametrize("integration,missing", [
    ({"type": "slack", "settings": {"recipient": "#x"}}, "has no url setting"),
    ({"type": "pagerduty", "settings": {"severity": "critical"}}, "has no integrationKey"),
    ({"type": "telegram", "settings": {"chatid": "1"}}, "has no bottoken setting"),
    ({"type": "slak", "settings": {"url": "x"}}, "type 'slak', which Grafana doesn't have "
                                                 "(did you mean 'slack'?)"),
])
def test_grafana_contact_point(integration, missing):
    from amlint.linter import lint
    cfg = {"route": {"receiver": "x"},
           "receivers": [{"name": "x", "grafana_managed_receiver_configs": [integration]}]}
    [f] = [f for f in lint(cfg) if f.code == "grafana-contact-point"]
    assert missing in f.msg and "Grafana refuses to provision or save it" in f.msg


@pytest.mark.parametrize("integration", [
    {"type": "slack", "settings": {"token": "xoxb-1", "recipient": "#x"}},
    {"type": "Slack", "settings": {}, "secureFields": {"url": True}},
    {"type": "msteams", "settings": {"url": "https://x"}},
    {"type": "LINE", "settings": {"token": "t"}},
    {"type": "webex", "settings": {}},
])
def test_grafana_contact_point_ok(integration):
    from amlint.linter import lint
    cfg = {"route": {"receiver": "x"},
           "receivers": [{"name": "x", "grafana_managed_receiver_configs": [integration]}]}
    assert [f for f in lint(cfg) if f.code == "grafana-contact-point"] == []


@pytest.mark.parametrize("value,reported", [
    ("https://hooks.slack.com/services/" "T024BE7LD/B01ABCDEF/9xQmL2vK7pR4tY8wZ3nB6cD1", True),
    ("$__env{SLACK_URL}", False), ("$__file{/run/secrets/slack}", False),
    ("${SLACK_URL}", False), ("$SLACK_URL", False), ("[REDACTED]", False),
    ("https://slack.example.org/hook", False),
])
def test_grafana_plaintext_secret(value, reported):
    from amlint.linter import lint
    cfg = {"route": {"receiver": "x"}, "receivers": [{"name": "x",
           "grafana_managed_receiver_configs": [{"type": "slack", "settings": {"url": value}}]}]}
    found = [f for f in lint(cfg) if f.code == "plaintext-secret"]
    assert bool(found) is reported
    if found:
        assert "settings.url of the slack integration in contact point 'x'" in found[0].msg
        assert "$__env{VAR} or $__file{/path/to/secret}" in found[0].msg


def test_grafana_secret_in_a_file(tmp_path, capsys):
    path = write(tmp_path, """
        contactPoints:
          - name: pager
            receivers:
              - uid: pager
                type: pagerduty
                settings:
                  integrationKey: 3f9a1c0be44d4f0d8d6c2b7e5a1f0c9d
    """)
    _, findings, _ = check(capsys, path)
    [f] = [f for f in findings if f["code"] == "plaintext-secret"]
    assert f["line"] == line_of(path, "integrationKey")
    assert f["where"] == ("receivers[pager].grafana_managed_receiver_configs[0]"
                          ".settings.integrationKey")


def test_grafana_templates(tmp_path, capsys):
    """Grafana's own functions and fields; a template without a define is wrapped in one."""
    path = write(tmp_path, """
        policies:
          - receiver: ops
        contactPoints:
          - name: ops
            receivers:
              - uid: ops
                type: slack
                settings:
                  url: $__env{URL}
                  title: '{{ template "ops.title" . }} {{ template "default.title" . }}'
                  text: >-
                    {{ range .Alerts }}{{ .ValueString }} {{ .DashboardURL }}
                    {{ .Values.B }}{{ end }} {{ coll.Dict "a" 1 }}
        templates:
          - name: ops.title
            template: '[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }}'
    """)
    _, findings, _ = check(capsys, path)
    assert codes(findings) == []
    text = open(path).read()
    for old, new, code in [
        ("ops.title\" . }} {{", "ops.titel\" . }} {{", "undefined-template"),
        ("{{ .ValueString }}", "{{ .Labels.x | humanizeDuration }}", "bad-template"),
        ("{{ .ValueString }}", "{{ end }}{{ .Labels.x }}{{ range .Alerts }}",
         "template-unknown-field"),
    ]:
        open(path, "w").write(text.replace(old, new))
        _, findings, _ = check(capsys, path)
        assert codes(findings) == [code], (old, findings)
        assert "Grafana" in findings[0]["message"]


def test_grafana_alertmanager_config_from_the_api(tmp_path, capsys):
    path = write(tmp_path, json.dumps({
        "template_files": {"custom": '{{ define "custom.title" }}{{ .Labels.x }}{{ end }}'},
        "alertmanager_config": {
            "route": {"receiver": "ops", "routes": [
                {"receiver": "ops", "object_matchers": [["team", "=", "db"]]}]},
            "receivers": [{"name": "ops", "grafana_managed_receiver_configs": [{
                "uid": "x", "name": "ops", "type": "slack", "settings": {
                    "title": '{{ template "custom.title" . }}'},
                "secureFields": {"url": True}}]}],
        },
    }, indent=2), "config.json")
    _, findings, _ = check(capsys, path)
    assert codes(findings) == ["template-unknown-field"]
    assert "in \"custom.title\": .Labels on the notification data" in findings[0]["message"]
    assert findings[0]["line"] == line_of(path, '"custom"')


def test_grafana_skips_alertmanager_only_codes(tmp_path, capsys):
    """No global: section and a route Grafana makes for every contact point: these codes
    say nothing about Grafana."""
    path = write(tmp_path, """
        route:
          receiver: a
          routes: [{receiver: a, object_matchers: [[x, =, y]]}]
        receivers:
          - {name: a, grafana_managed_receiver_configs: [{type: email, settings: {addresses: x}}]}
          - {name: b, grafana_managed_receiver_configs: [{type: email, settings: {addresses: x}}]}
    """)
    _, findings, _ = check(capsys, path)
    assert codes(findings) == []
    _, findings, _ = check(capsys, path, "--input-format", "alertmanager")
    assert {"unused-receiver", "global-resolve-timeout-missing"} <= set(codes(findings))
