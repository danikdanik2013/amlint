"""Explanations for amlint check codes, used by `amlint explain <code>`."""

# Checks in this set duplicate validation that Alertmanager's own config loader already
# performs — confirmed empirically against amtool v0.33.1 (`amtool check-config`), which
# ships free with every Alertmanager install and is what most teams already run in CI.
# Alertmanager refuses to start on any of these; it does not silently misbehave.
# amlint still runs them so you get one dependency-free tool with unified JSON/SARIF/diff
# output, without needing the Go binary — but they are NOT gaps amtool misses.
# The remaining checks (not in this set) are the genuine differentiator: configs that are
# syntactically/structurally valid, load fine, and Alertmanager runs without complaint —
# they just don't do what you meant.
ALSO_CAUGHT_BY_AMTOOL = {
    "undefined-receiver",
    "undefined-time-interval",
    "bad-regex",
    "bad-match-label",
    "bad-matcher",
    "groupby-ellipsis",
    "no-root-route",
    "invalid-root-route",
    "duplicate-receiver",
    "email-no-smarthost",
    "webhook-no-url",
    "slack-no-api-url",
    "pagerduty-no-routing-key",
    "opsgenie-no-api-key",
    "msteams-no-webhook-url",
    "telegram-no-bot-token",
    "discord-no-webhook-url",
    "victorops-no-api-key",
    "wechat-no-corp-id",
    "sns-no-target",
}

# The docs page (docs/checks/<page>.md) with a section for each check, headed by its code.
_DOCS_PAGES = {
    "routing": (
        "no-root-route", "invalid-root-route", "undefined-receiver", "groupby-ellipsis",
        "bad-regex", "bad-matcher", "bad-match-label", "unreachable-route",
        "route-match-collision", "shadowed-route", "stripped-brace", "useless-continue",
        "deep-nesting",
    ),
    "receivers": (
        "duplicate-receiver", "empty-receiver", "unused-receiver", "template-file-missing",
        "email-no-smarthost", "webhook-no-url", "pagerduty-no-routing-key", "slack-no-api-url",
        "opsgenie-no-api-key", "msteams-no-webhook-url", "telegram-no-bot-token",
        "discord-no-webhook-url", "victorops-no-api-key", "wechat-no-corp-id", "sns-no-target",
    ),
    "inhibition": ("inhibit-no-equal", "circular-inhibition", "inhibit-same-match"),
    "timing": (
        "repeat-before-group", "wait-exceeds-interval", "undefined-time-interval",
        "global-resolve-timeout-missing",
    ),
    "rules": (
        "route-unknown-alertname", "inhibit-unknown-alertname", "alert-hits-default-receiver",
    ),
}
DOCS_PAGE = {code: page for page, codes in _DOCS_PAGES.items() for code in codes}
DOCS_BASE = "https://danikdanik2013.github.io/amlint/checks/"


def docs_url(code: str) -> str:
    """The check's section on the docs site, or the list of all checks."""
    page = DOCS_PAGE.get(code)
    return f"{DOCS_BASE}{page}/#{code}" if page else DOCS_BASE


EXPLAINS = {
    "undefined-receiver": {
        "level": "error",
        "summary": "A route references a receiver name that is not defined in receivers:",
        "why": (
            "Alertmanager refuses to start — `amtool check-config` and the alertmanager "
            "binary both reject this config at load time with "
            "'undefined receiver \"x\" used in route'. Still worth catching in amlint: "
            "it means one CI failure with a clear JSON/SARIF location instead of a crashed "
            "pod and a log line to go dig up."
        ),
        "bad": """\
route:
  receiver: default
  routes:
    - match: {team: infra}
      receiver: pager-team   # not defined below!
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'""",
        "good": """\
route:
  receiver: default
  routes:
    - match: {team: infra}
      receiver: pager-team
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
  - name: pager-team          # add the missing receiver
    pagerduty_configs:
      - routing_key: 'your-key'""",
    },

    "no-root-route": {
        "level": "error",
        "summary": "The config has no root route: key.",
        "why": "Alertmanager will refuse to start without a root route.",
        "bad": """\
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
# missing 'route:' entirely""",
        "good": """\
route:
  receiver: default
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'""",
    },

    "invalid-root-route": {
        "level": "error",
        "summary": "The root route breaks a rule Alertmanager enforces at load time.",
        "why": (
            "The root route is the fallback for every alert: it must name a receiver and "
            "must not have matchers, continue: true, mute_time_intervals or active_time_intervals. "
            "Alertmanager refuses to load a config that breaks any of these rules — amtool "
            "catches it too; amlint reports it alongside everything else in one run."
        ),
        "bad": """\
route:
  matchers: [team="infra"]   # root route must match everything
  routes:
    - matchers: [severity="critical"]
      receiver: pager""",
        "good": """\
route:
  receiver: default          # required fallback receiver
  routes:
    - matchers: [team="infra"]
      receiver: infra
    - matchers: [severity="critical"]
      receiver: pager""",
    },

    "bad-regex": {
        "level": "error",
        "summary": "A regex matcher fails to compile or uses syntax Go RE2 does not support.",
        "why": (
            "Alertmanager compiles regexes with Go RE2, which also rejects lookarounds, "
            "backreferences and possessive quantifiers that Python accepts. This is a "
            "whole-config failure, not a partial one — Alertmanager refuses to load the file "
            "at all (`error parsing regexp: ...`), so no route in the file works, not just "
            "this one. Routes and inhibit rules (source_match_re / target_match_re / "
            "*_matchers) are all checked. amtool catches it too; amlint gives you the exact "
            "route or rule path instead of a bare regexp error."
        ),
        "bad": """\
routes:
  - match_re:
      service: "auth(["    # unclosed character class — fails to compile
    receiver: team""",
        "good": """\
routes:
  - match_re:
      service: "auth(.*)"  # valid regex
    receiver: team""",
    },

    "bad-match-label": {
        "level": "error",
        "summary": "A match: or match_re: label name that isn't a classic one, like k8s.ns.",
        "why": (
            "match: and match_re: (and source_match / target_match and their _re forms in "
            "inhibit rules) only take classic label names: a letter or underscore, then "
            "letters, digits and underscores. Alertmanager refuses to load a config with any "
            "other key there ('invalid label name'), so no route in the file works, even "
            "though matchers: takes dotted and other UTF-8 names just fine. Move the matcher "
            "to matchers: (or source_matchers / target_matchers) and quote the name. amtool "
            "catches it too; amlint names the route or rule and writes the matchers: entry "
            "for you."
        ),
        "bad": """\
routes:
  - match:
      k8s.namespace: payments   # not a classic label name
    receiver: payments""",
        "good": """\
routes:
  - matchers:
      - '"k8s.namespace"="payments"'   # quoted: any UTF-8 name
    receiver: payments""",
    },

    "bad-matcher": {
        "level": "error",
        "summary": "A matchers: entry that neither of Alertmanager's matcher parsers can read.",
        "why": (
            "Alertmanager reads each matchers: / source_matchers: / target_matchers: entry "
            "with its classic parser, and with the UTF-8 one only when the classic one "
            "fails. When both fail it refuses to load the config ('bad matcher format', "
            "'matcher value contains unescaped double quote'), so no route in the file "
            "works. Typical causes: an empty matcher between two commas, a stray double "
            "quote, a comma or brace in a bare value, and in an entry with a quoted label "
            "name (\"service.name\"=...), where values are Go strings, a regex escape like "
            "\\. that Go doesn't know. amtool catches it too; amlint names the entry and "
            "the route or rule it is in."
        ),
        "bad": """\
routes:
  - matchers:
      - 'service=api,,env=prod'        # empty matcher between the commas
      - '"service.name"=~"api\\.v2"'   # quoted label name: \\. is not a Go escape
    receiver: team""",
        "good": """\
routes:
  - matchers:
      - 'service="api", env="prod"'
      - '"service.name"=~"api\\\\.v2"'  # Go string: \\\\ is one backslash
    receiver: team""",
    },

    "stripped-brace": {
        "level": "warn",
        "summary": "A bare matcher value ends in '}', which Alertmanager strips off.",
        "why": (
            "Alertmanager's classic matcher parser removes one trailing '}' from a "
            "matchers: entry whether or not the entry starts with '{'. So the bare regex in "
            "`code=~[0-9]{3}` becomes `[0-9]{3`, which matches the literal text '1{3' and "
            "not '123'. The config loads and the route just never matches what you meant. "
            "amtool accepts it without a word. Quote the value, or wrap the whole entry in "
            "braces, and the closing brace stays part of it."
        ),
        "bad": """\
routes:
  - matchers:
      - code=~[0-9]{3}        # the regex is [0-9]{3: matches "1{3", not "123"
    receiver: http-errors""",
        "good": """\
routes:
  - matchers:
      - code=~"[0-9]{3}"      # quoted: the regex is [0-9]{3}
    receiver: http-errors
# or: - '{code=~[0-9]{3}}'""",
    },

    "duplicate-receiver": {
        "level": "error",
        "summary": "Two receivers share the same name.",
        "why": (
            "Alertmanager refuses to start over this — 'notification config name \"x\" is "
            "not unique' — it does not pick one and ignore the other. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team-slack
    slack_configs:
      - channel: '#alerts'
  - name: team-slack          # duplicate!
    slack_configs:
      - channel: '#incidents'""",
        "good": """\
receivers:
  - name: team-slack-alerts
    slack_configs:
      - channel: '#alerts'
  - name: team-slack-incidents
    slack_configs:
      - channel: '#incidents'""",
    },

    "empty-receiver": {
        "level": "warn (info for the default receiver or one named like null)",
        "summary": (
            "A receiver has no integration configured"
            " — no slack_configs, pagerduty_configs, etc."
        ),
        "why": (
            "Alerts routed here will be silently dropped. "
            "An empty default receiver is sometimes intentional (blackhole), and so is"
            " one named null, blackhole, devnull, dev-null, discard or drop"
            " (kube-prometheus routes Watchdog there) — those trigger info, not warn."
        ),
        "bad": """\
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
  - name: pager               # no integrations — alerts dropped!""",
        "good": """\
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
  - name: pager
    pagerduty_configs:
      - routing_key: 'your-key'""",
    },

    "email-no-smarthost": {
        "level": "error",
        "summary": "An email_configs entry has no smarthost and global.smtp_smarthost is not set.",
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just email delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    email_configs:
      - to: 'team@example.com'
        # missing smarthost!""",
        "good": """\
global:
  smtp_smarthost: 'smtp.example.com:587'

receivers:
  - name: team
    email_configs:
      - to: 'team@example.com'""",
    },

    "unused-receiver": {
        "level": "info",
        "summary": "A receiver is defined but never referenced by any route.",
        "why": (
            "Likely leftover from a previous config version. "
            "Harmless but adds noise and confusion."
        ),
        "bad": """\
route:
  receiver: default
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
  - name: old-pager           # never used in any route""",
        "good": """\
# Remove old-pager, or add a route that uses it:
routes:
  - match: {severity: critical}
    receiver: old-pager""",
    },

    "inhibit-no-equal": {
        "level": "warn",
        "summary": "An inhibition rule has no equal: field.",
        "why": (
            "Without equal:, the rule silences target alerts"
            " regardless of which instance fired the source. "
            "A critical alert on host-A will silence warnings on host-B,"
            " which is almost never intended. Rules with target matchers but no source"
            " matchers are skipped: they mute their targets whenever anything else"
            " fires, a deliberate pattern (kube-prometheus's InfoInhibitor)."
        ),
        "bad": """\
inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: warning
    # no equal: — silences warnings across ALL sources""",
        "good": """\
inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: warning
    equal: [alertname, cluster, namespace]  # only silence same alert on same instance""",
    },

    "circular-inhibition": {
        "level": "warn",
        "summary": "Two inhibition rules can silence each other.",
        "why": (
            "When both conditions fire simultaneously, neither alert is delivered. "
            "The team receives no notification even though real incidents are active."
        ),
        "bad": """\
inhibit_rules:
  - source_match: {severity: critical}
    target_match: {severity: warning}
    equal: [alertname]
  - source_match: {severity: warning}
    target_match: {severity: critical}
    equal: [alertname]""",
        "good": """\
inhibit_rules:
  - source_match: {severity: critical}
    target_match: {severity: warning}
    equal: [alertname]
  # Remove the reverse rule unless you have a specific reason for it""",
    },

    "inhibit-same-match": {
        "level": "info",
        "summary": "source_match and target_match share an identical label=value pair.",
        "why": (
            "An alert never mutes itself, and an alert that matches both sides of a rule"
            " never mutes another alert that also matches both. So if the source and target"
            " matchers can't tell two alerts apart, the rule mutes nothing. It's fine when"
            " other matchers make the sides different (for example a different alertname);"
            " otherwise the rule is usually a copy-paste mistake."
        ),
        "bad": """\
inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: critical   # same alerts on both sides — mutes nothing
    equal: [alertname]""",
        "good": """\
inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: warning    # distinct severity levels
    equal: [alertname]""",
    },

    "unreachable-route": {
        "level": "warn",
        "summary": (
            "A catch-all route (no matchers, or only =~\".*\") with continue:false"
            " appears before sibling routes."
        ),
        "why": (
            "Alertmanager evaluates siblings in order and stops at the first match. "
            "All routes after the catch-all will never receive alerts."
        ),
        "bad": """\
routes:
  - receiver: default          # catch-all — matches everything
  - match: {severity: critical}
    receiver: pager            # unreachable!""",
        "good": """\
routes:
  - match: {severity: critical}
    receiver: pager            # specific routes first
  - receiver: default          # catch-all last""",
    },

    "groupby-ellipsis": {
        "level": "error",
        "summary": "group_by contains '...' alongside explicit labels.",
        "why": (
            "'...' means group by all labels, so explicit labels next to it contradict it. "
            "Alertmanager refuses to load such a config: `amtool check-config` fails with "
            "'cannot have wildcard group_by (`...`) and other labels at the same time'."
        ),
        "bad": """\
route:
  group_by: ['...', alertname, cluster]  # rejected: '...' plus explicit labels""",
        "good": """\
route:
  group_by: ['...']       # group by everything
# or
route:
  group_by: [alertname, cluster]  # group by specific labels only""",
    },

    "useless-continue": {
        "level": "info",
        "summary": "continue:true on the last sibling route has no effect.",
        "why": "There are no subsequent siblings to continue to. The flag is dead config.",
        "bad": """\
routes:
  - match: {env: prod}
    receiver: team
    continue: true          # last sibling, nothing follows""",
        "good": """\
routes:
  - match: {env: prod}
    receiver: team
    # remove continue: true""",
    },

    "deep-nesting": {
        "level": "info",
        "summary": "The routing tree is more than 5 levels deep.",
        "why": (
            "Deeply nested configs are hard to read, debug, and reason about. "
            "Consider flattening using matchers with multiple conditions."
        ),
        "bad": """\
route:
  routes:
    - routes:
        - routes:
            - routes:
                - routes:
                    - receiver: deep   # level 6""",
        "good": """\
route:
  routes:
    - matchers: [env=prod, severity=critical, team=infra]
      receiver: infra-pager   # flatten with multi-condition matchers""",
    },

    "repeat-before-group": {
        "level": "warn",
        "summary": "repeat_interval is shorter than group_interval.",
        "why": (
            "Alertmanager re-sends a notification only when it flushes the group, which"
            " happens once per group_interval. A repeat_interval shorter than that can't take"
            " effect: repeats come every group_interval instead, so the number in the config"
            " doesn't match what people get."
        ),
        "bad": """\
route:
  group_interval: 1h
  repeat_interval: 5m    # repeats actually come every 1h, not every 5m""",
        "good": """\
route:
  group_interval: 5m
  repeat_interval: 4h    # repeat_interval > group_interval""",
    },

    "wait-exceeds-interval": {
        "level": "warn",
        "summary": "group_wait is longer than group_interval.",
        "why": (
            "group_wait is how long a new group waits before its first notification;"
            " group_interval is how long Alertmanager waits before sending an update for"
            " alerts added to a group it already notified about. With group_wait longer,"
            " the first notification about a new problem is the slowest one. That's"
            " usually the opposite of what you want."
        ),
        "bad": """\
route:
  group_wait: 10m
  group_interval: 2m     # first page after 10m, later updates every 2m""",
        "good": """\
route:
  group_wait: 30s
  group_interval: 5m     # wait < interval""",
    },

    "undefined-time-interval": {
        "level": "error",
        "summary": (
            "mute_time_intervals or active_time_intervals"
            " references an interval not in time_intervals:"
        ),
        "why": "Alertmanager refuses to load this config. amtool catches it too.",
        "bad": """\
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: default
      mute_time_intervals: [maintenance]   # not defined below!
# no time_intervals: section""",
        "good": """\
route:
  receiver: default
  routes:
    - matchers: [team="db"]
      receiver: default
      mute_time_intervals: [maintenance]
time_intervals:
  - name: maintenance
    time_intervals:
      - weekdays: [saturday, sunday]""",
    },

    "webhook-no-url": {
        "level": "error",
        "summary": "A webhook_configs entry has no url or url_file.",
        "why": (
            "Alertmanager refuses to start without it — this isn't a silent delivery "
            "failure, the whole instance won't come up. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    webhook_configs:
      - send_resolved: true   # missing url!""",
        "good": """\
receivers:
  - name: team
    webhook_configs:
      - url: 'http://my-service/webhook'
        send_resolved: true""",
    },

    "pagerduty-no-routing-key": {
        "level": "error",
        "summary": (
            "A pagerduty_configs entry has no routing_key, routing_key_file,"
            " service_key or service_key_file."
        ),
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just PagerDuty delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: pager
    pagerduty_configs:
      - severity: critical   # missing routing_key!""",
        "good": """\
receivers:
  - name: pager
    pagerduty_configs:
      - routing_key: 'your-pagerduty-integration-key'
        severity: critical""",
    },

    "slack-no-api-url": {
        "level": "error",
        "summary": (
            "A slack_configs entry has no api_url or api_url_file, and neither"
            " global.slack_api_url nor global.slack_api_url_file is set (or api_url is empty)."
        ),
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just Slack delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    slack_configs:
      - channel: '#alerts'   # missing api_url!""",
        "good": """\
# Option 1 — per receiver:
receivers:
  - name: team
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/...'
        channel: '#alerts'

# Option 2 — global default:
global:
  slack_api_url: 'https://hooks.slack.com/services/...'""",
    },

    "opsgenie-no-api-key": {
        "level": "error",
        "summary": (
            "An opsgenie_configs entry has no api_key or api_key_file, and neither"
            " global.opsgenie_api_key nor global.opsgenie_api_key_file is set."
        ),
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just OpsGenie delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    opsgenie_configs:
      - priority: P1   # missing api_key!""",
        "good": """\
# Option 1 — per receiver:
receivers:
  - name: team
    opsgenie_configs:
      - api_key: 'your-opsgenie-api-key'
        priority: P1

# Option 2 — global default:
global:
  opsgenie_api_key: 'your-opsgenie-api-key'""",
    },

    "global-resolve-timeout-missing": {
        "level": "info",
        "summary": "global.resolve_timeout is not set; Alertmanager defaults to 5m.",
        "why": (
            "resolve_timeout is how long Alertmanager waits before marking an alert resolved"
            " when the alert arrived without an end time. Prometheus always sends an end time,"
            " so for alerts from Prometheus this setting does nothing; it matters for other"
            " tools that post alerts to Alertmanager. Setting it explicitly makes the intent"
            " reviewable."
        ),
        "bad": """\
route:
  receiver: default
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'
# no global: section — resolve_timeout defaults to 5m""",
        "good": """\
global:
  resolve_timeout: 5m   # for alerts sent without an end time (not Prometheus's)

route:
  receiver: default
receivers:
  - name: default
    slack_configs:
      - api_url: 'https://hooks.slack.com/...'""",
    },

    "route-match-collision": {
        "level": "warn",
        "summary": "Two sibling routes have identical matchers — the second never receives alerts.",
        "why": (
            "Alertmanager evaluates siblings in order and stops at the first match."
            " A duplicate matcher set means the second route is dead code."
        ),
        "bad": """\
routes:
  - match: {team: infra, severity: critical}
    receiver: pager
  - match: {team: infra, severity: critical}   # identical — never reached
    receiver: slack-infra""",
        "good": """\
routes:
  - match: {team: infra, severity: critical}
    receiver: pager
    continue: true          # deliver to both
  - match: {team: infra, severity: critical}
    receiver: slack-infra
# or: use distinct matchers for each route""",
    },

    "shadowed-route": {
        "level": "warn",
        "summary": (
            "An earlier sibling route matches every alert this route matches"
            " — this route never receives alerts."
        ),
        "why": (
            "Alertmanager evaluates siblings in order and stops at the first match"
            " unless that route has continue: true. If every label value this route allows"
            " is one an earlier sibling allows too (severity=\"critical\" against"
            " severity=~\"warning|critical\", team=\"infra\" against team!=\"db\", or"
            " a subset of its matchers), that sibling takes all of this route's alerts and"
            " this route is dead code. Only what amlint can prove is reported."
            " Identical matchers are reported as route-match-collision, catch-alls as"
            " unreachable-route."
        ),
        "bad": """\
routes:
  - matchers: [severity=~"warning|critical"]
    receiver: ops
  - matchers: [severity="critical"]   # never reached
    receiver: pager""",
        "good": """\
routes:
  - matchers: [severity="critical"]   # specific first
    receiver: pager
  - matchers: [severity=~"warning|critical"]
    receiver: ops
# or: continue: true on the broader route to notify both""",
    },

    "template-file-missing": {
        "level": "error (warn for globs)",
        "summary": "A relative templates: path matches no file next to the config.",
        "why": (
            "Alertmanager resolves relative template paths against the config file's "
            "directory and starts even when one matches nothing — it just loads no "
            "templates from it, so notifications that use them fail to render. Absolute "
            "paths point into the Alertmanager host or container rather than wherever "
            "amlint runs, so they aren't checked."
        ),
        "bad": """\
templates:
  - templates/custom.tmpl   # no such file next to alertmanager.yml""",
        "good": """\
templates:
  - templates/custom.tmpl   # file exists in the repo alongside the config
# or use a glob that matches existing files:
templates:
  - templates/*.tmpl""",
    },

    "msteams-no-webhook-url": {
        "level": "error",
        "summary": "An msteams_configs entry has no webhook_url or webhook_url_file.",
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just MS Teams delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    msteams_configs:
      - title: 'Alert'   # missing webhook_url!""",
        "good": """\
receivers:
  - name: team
    msteams_configs:
      - webhook_url: 'https://outlook.office.com/webhook/...'
        title: 'Alert'""",
    },

    "telegram-no-bot-token": {
        "level": "error",
        "summary": "A telegram_configs entry has no bot_token or bot_token_file.",
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just Telegram delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    telegram_configs:
      - chat_id: -1001234567890   # missing bot_token!""",
        "good": """\
receivers:
  - name: team
    telegram_configs:
      - bot_token: '123456:ABC-DEF...'
        chat_id: -1001234567890""",
    },

    "discord-no-webhook-url": {
        "level": "error",
        "summary": "A discord_configs entry has no webhook_url or webhook_url_file.",
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just Discord delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    discord_configs:
      - title: 'Alert'   # missing webhook_url!""",
        "good": """\
receivers:
  - name: team
    discord_configs:
      - webhook_url: 'https://discord.com/api/webhooks/...'
        title: 'Alert'""",
    },

    "victorops-no-api-key": {
        "level": "error",
        "summary": (
            "A victorops_configs entry has no api_key or api_key_file, and neither"
            " global.victorops_api_key nor global.victorops_api_key_file is set."
        ),
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just VictorOps delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    victorops_configs:
      - routing_key: 'team-routing-key'   # missing api_key!""",
        "good": """\
# Option 1 — per receiver:
receivers:
  - name: team
    victorops_configs:
      - api_key: 'your-victorops-api-key'
        routing_key: 'team-routing-key'

# Option 2 — global default:
global:
  victorops_api_key: 'your-victorops-api-key'""",
    },

    "wechat-no-corp-id": {
        "level": "error",
        "summary": (
            "A wechat_configs entry has no corp_id"
            " and global.wechat_api_corp_id is not set."
        ),
        "why": (
            "Alertmanager refuses to start without it — this fails the whole instance, "
            "not just WeChat delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    wechat_configs:
      - agent_id: '1000002'   # missing corp_id!""",
        "good": """\
# Option 1 — per receiver:
receivers:
  - name: team
    wechat_configs:
      - corp_id: 'your-corp-id'
        agent_id: '1000002'

# Option 2 — global default:
global:
  wechat_api_corp_id: 'your-corp-id'""",
    },

    "sns-no-target": {
        "level": "error",
        "summary": (
            "An sns_configs entry has none of topic_arn, phone_number,"
            " or target_arn set."
        ),
        "why": (
            "Alertmanager refuses to start without one of these set — this fails the "
            "whole instance, not just SNS delivery. amtool catches it too."
        ),
        "bad": """\
receivers:
  - name: team
    sns_configs:
      - sigv4:
          region: us-east-2
        # missing topic_arn / phone_number / target_arn!""",
        "good": """\
receivers:
  - name: team
    sns_configs:
      - topic_arn: 'arn:aws:sns:us-east-2:123456789012:My-Topic'
        sigv4:
          region: us-east-2
# or use phone_number (SMS) or target_arn (mobile endpoint) instead""",
    },

    "route-unknown-alertname": {
        "level": "warn",
        "summary": (
            "With --rules: a route matches an alertname that no alerting rule in the rule"
            " files produces."
        ),
        "why": (
            "The route is valid, so Alertmanager loads it — it just never receives the alert "
            "it was written for. Usually the name has a typo or the rule was renamed, and the "
            "alert falls through to another route instead. Equality matchers, each name of a "
            "plain alternation like alertname=~\"A|B|C\", and other regexes that match none of "
            "the rules' alert names are checked; != and !~ ones too, since excluding a name "
            "nothing produces excludes nothing. Alerts from other sources (another Prometheus, "
            "Loki, Grafana) aren't in the rule files: pass those files too, or suppress the "
            "finding with `# amlint: disable=route-unknown-alertname`."
        ),
        "bad": """\
# rules/node.yml
groups:
  - name: node
    rules:
      - alert: HighCPUUsage
        expr: node_cpu_busy > 0.9

# alertmanager.yml   (amlint check alertmanager.yml --rules 'rules/*.yml')
route:
  receiver: default
  routes:
    - matchers: [alertname="HighCpuUsage"]   # no rule produces this alert
      receiver: infra-pager""",
        "good": """\
route:
  receiver: default
  routes:
    - matchers: [alertname="HighCPUUsage"]
      receiver: infra-pager""",
    },

    "inhibit-unknown-alertname": {
        "level": "warn",
        "summary": (
            "With --rules: an inhibit rule's source or target matches an alertname that no"
            " alerting rule in the rule files produces."
        ),
        "why": (
            "An inhibit rule whose source alert never fires never mutes anything, and one "
            "whose target alert never fires has nothing to mute — Alertmanager loads it either "
            "way and gives no sign. Same matching as route-unknown-alertname, for "
            "source_match / source_matchers and target_match / target_matchers."
        ),
        "bad": """\
# rules/node.yml defines NodeDown and NodeHighLoad
inhibit_rules:
  - source_matchers: [alertname="NodeDwn"]   # typo: never fires
    target_matchers: [alertname="NodeHighLoad"]
    equal: [instance]""",
        "good": """\
inhibit_rules:
  - source_matchers: [alertname="NodeDown"]
    target_matchers: [alertname="NodeHighLoad"]
    equal: [instance]""",
    },

    "alert-hits-default-receiver": {
        "level": "info",
        "summary": (
            "With --rules: based on their static labels, alerts match no child route and"
            " go to the root route's receiver."
        ),
        "why": (
            "Each alerting rule is routed with alertname plus its own labels: — typically a "
            "rule that forgot its severity or team label and lands on the catch-all receiver "
            "nobody watches. At runtime an alert also has the labels of its PromQL series and "
            "Prometheus' external_labels, so amlint doesn't guess about those: when the route "
            "taken depends on a label that no rule sets, or on a label whose value is a "
            "template, the alert is skipped. Alerts are grouped by the labels the routes match "
            "on, so sending every severity=warning alert to the default receiver on purpose is "
            "one finding, and a rule missing a label the others set stands out on its own. "
            "Info only, since static labels are not the whole story. To make a fallback "
            "explicit, give those alerts their own route to the default receiver."
        ),
        "bad": """\
# rules/app.yml
groups:
  - name: app
    rules:
      - alert: DiskFull
        expr: disk_free_ratio < 0.05
        labels: {severity: critical}
      - alert: QueueBacklog
        expr: queue_depth > 1000   # no severity label: lands on 'default'

# alertmanager.yml
route:
  receiver: default
  routes:
    - matchers: [severity="critical"]
      receiver: pager""",
        "good": """\
      - alert: QueueBacklog
        expr: queue_depth > 1000
        labels: {severity: critical}""",
    },
}
