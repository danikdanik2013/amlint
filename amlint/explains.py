"""Explanations for amlint check codes, used by `amlint explain <code>`."""

EXPLAINS = {
    "undefined-receiver": {
        "level": "error",
        "summary": "A route references a receiver name that is not defined in receivers:",
        "why": (
            "Alertmanager silently drops all alerts matched by this route. "
            "There is no runtime error — alerts just vanish."
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
            "The route will never match anything. "
            "Alerts that should go through this route will fall through to the catch-all."
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
            "Alertmanager uses the first definition and silently ignores the second. "
            "If you intended different configs, only one will ever be used."
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
        "why": "Alertmanager cannot send emails without knowing the SMTP server address.",
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
            "Alertmanager cannot deliver alerts without a target URL."
            " Alerts will fail silently."
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
            "PagerDuty requires an integration key to accept events."
            " Without it, alerts cannot be sent."
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
            "Slack requires an incoming webhook URL to receive messages."
            " Without it, notifications fail."
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
            "OpsGenie requires an API key to accept alerts."
            " Without it, notifications cannot be sent."
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

    "msteams-no-webhook-url": {
        "level": "error",
        "summary": "An msteams_configs entry has no webhook_url or webhook_url_file.",
        "why": (
            "MS Teams requires an incoming webhook URL to receive messages."
            " Without it, notifications fail."
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
}
