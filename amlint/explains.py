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
    "groupby-ellipsis",
    "no-root-route",
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

    "bad-regex": {
        "level": "error",
        "summary": "A match_re or matchers entry contains a regex that fails to compile.",
        "why": (
            "This is a whole-config failure, not a partial one — Alertmanager refuses to "
            "load the file at all (`error parsing regexp: ...`), so no route in the file "
            "works, not just this one. amtool catches it too; amlint gives you the exact "
            "route path instead of a bare regexp error."
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
        "level": "warn (info if it is the default receiver)",
        "summary": (
            "A receiver has no integration configured"
            " — no slack_configs, pagerduty_configs, etc."
        ),
        "why": (
            "Alerts routed here will be silently dropped. "
            "An empty default receiver is sometimes intentional (blackhole)"
            " — that case triggers info, not warn."
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
            " which is almost never intended."
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
            "The inhibition rule may silence the same alert that triggered it. "
            "Usually a misconfiguration — verify that source and target are distinct alert types."
        ),
        "bad": """\
inhibit_rules:
  - source_match:
      severity: critical
    target_match:
      severity: critical   # same value — may silence itself
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
            "A catch-all route (no matchers) with continue:false"
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
        "level": "warn",
        "summary": "group_by contains '...' alongside explicit labels.",
        "why": (
            "'...' means group by all labels. "
            "Mixing it with explicit labels is contradictory and makes intent unclear."
        ),
        "bad": """\
route:
  group_by: ['...', alertname, cluster]  # alertname and cluster are redundant""",
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
            "Alertmanager sends repeats before the group has a chance to fire. "
            "You get noisy repeat notifications for alerts that haven't even been grouped yet."
        ),
        "bad": """\
route:
  group_interval: 1h
  repeat_interval: 5m    # fires 12× before the group even re-evaluates""",
        "good": """\
route:
  group_interval: 5m
  repeat_interval: 4h    # repeat_interval > group_interval""",
    },

    "wait-exceeds-interval": {
        "level": "warn",
        "summary": "group_wait is longer than group_interval.",
        "why": (
            "Subsequent alerts fire before the initial group has finished waiting. "
            "You may receive fragmented notifications instead of one grouped alert."
        ),
        "bad": """\
route:
  group_wait: 10m
  group_interval: 2m     # group fires before wait expires""",
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
        "why": "Alertmanager will reject this config at startup.",
        "bad": """\
route:
  receiver: default
  mute_time_intervals: [maintenance]   # not defined below!
# no time_intervals: section""",
        "good": """\
route:
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
        "summary": "A pagerduty_configs entry has no routing_key or routing_key_file.",
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
        "summary": "A slack_configs entry has no api_url and global.slack_api_url is not set.",
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
            "An opsgenie_configs entry has no api_key"
            " and global.opsgenie_api_key is not set."
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
            "The default 5m means alerts are marked resolved 5 minutes after they stop firing."
            " This may be too short or too long for your environment."
            " Setting it explicitly makes the intent reviewable in code."
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
  resolve_timeout: 5m   # or 15m, 1h — whatever fits your alert lifecycle

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

    "template-file-missing": {
        "level": "error (warn for globs)",
        "summary": "templates: references a file path that does not exist on disk.",
        "why": (
            "Alertmanager loads template files at startup. "
            "A missing literal path causes startup failure. "
            "A glob that matches no files silently loads no templates."
        ),
        "bad": """\
templates:
  - /etc/alertmanager/templates/custom.tmpl   # file does not exist""",
        "good": """\
templates:
  - /etc/alertmanager/templates/custom.tmpl   # ensure the file exists before deploying
# or use a glob that matches existing files:
templates:
  - /etc/alertmanager/templates/*.tmpl""",
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
            "A victorops_configs entry has no api_key"
            " and global.victorops_api_key is not set."
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
}
