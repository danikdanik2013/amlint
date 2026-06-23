# Receiver checks

## duplicate-receiver

**Level:** error

Two receivers share the same name. Alertmanager uses the first definition and silently ignores the second, which is almost never intentional.

=== "Bad"
    ```yaml
    receivers:
      - name: team-slack
        slack_configs:
          - channel: '#alerts'
      - name: team-slack          # duplicate!
        slack_configs:
          - channel: '#incidents'
    ```

=== "Fixed"
    ```yaml
    receivers:
      - name: team-slack
        slack_configs:
          - channel: '#alerts'
      - name: team-slack-incidents   # unique name
        slack_configs:
          - channel: '#incidents'
    ```

---

## empty-receiver

**Level:** warn (info if it's the default receiver)

A receiver has no integration configured — no `slack_configs`, `pagerduty_configs`, `webhook_configs`, etc. Alerts routed here will be silently dropped.

Using an empty receiver as the **default** (to blackhole low-priority alerts) is intentional and triggers `info` instead of `warn`.

=== "Bad"
    ```yaml
    receivers:
      - name: default
        slack_configs: [...]
      - name: pager               # no integrations — alerts dropped!
    ```

=== "Fixed"
    ```yaml
    receivers:
      - name: default
        slack_configs: [...]
      - name: pager
        pagerduty_configs:
          - routing_key: 'your-key'
    ```

=== "Intentional (blackhole)"
    ```yaml
    route:
      receiver: null              # default catch-all blackhole
    receivers:
      - name: null                # empty by design — INFO, not WARN
    ```

---

## email-no-smarthost

**Level:** error

An `email_configs` entry has no `smarthost` and `global.smtp_smarthost` is not set. Alertmanager cannot send emails without knowing the SMTP server address.

=== "Bad"
    ```yaml
    receivers:
      - name: team
        email_configs:
          - to: 'team@example.com'
            from: 'alertmanager@example.com'
            # missing smarthost!
    ```

=== "Fixed — per receiver"
    ```yaml
    receivers:
      - name: team
        email_configs:
          - to: 'team@example.com'
            from: 'alertmanager@example.com'
            smarthost: 'smtp.example.com:587'
    ```

=== "Fixed — global"
    ```yaml
    global:
      smtp_smarthost: 'smtp.example.com:587'
      smtp_from: 'alertmanager@example.com'

    receivers:
      - name: team
        email_configs:
          - to: 'team@example.com'
    ```

---

## webhook-no-url

**Level:** error

A `webhook_configs` entry has no `url` or `url_file`. Alertmanager cannot deliver alerts without a target URL — they fail silently.

=== "Bad"
    ```yaml
    receivers:
      - name: team
        webhook_configs:
          - send_resolved: true   # missing url!
    ```

=== "Fixed"
    ```yaml
    receivers:
      - name: team
        webhook_configs:
          - url: 'http://my-service/webhook'
            send_resolved: true
    ```

---

## pagerduty-no-routing-key

**Level:** error

A `pagerduty_configs` entry has no `routing_key` or `routing_key_file`. PagerDuty requires an integration key to accept events.

=== "Bad"
    ```yaml
    receivers:
      - name: pager
        pagerduty_configs:
          - severity: critical   # missing routing_key!
    ```

=== "Fixed"
    ```yaml
    receivers:
      - name: pager
        pagerduty_configs:
          - routing_key: 'your-pagerduty-integration-key'
            severity: critical
    ```

---

## slack-no-api-url

**Level:** error

A `slack_configs` entry has no `api_url` or `api_url_file`, and `global.slack_api_url` is not set. Slack requires an incoming webhook URL to receive messages.

=== "Bad"
    ```yaml
    receivers:
      - name: team
        slack_configs:
          - channel: '#alerts'   # missing api_url!
    ```

=== "Fixed — per receiver"
    ```yaml
    receivers:
      - name: team
        slack_configs:
          - api_url: 'https://hooks.slack.com/services/...'
            channel: '#alerts'
    ```

=== "Fixed — global"
    ```yaml
    global:
      slack_api_url: 'https://hooks.slack.com/services/...'
    receivers:
      - name: team
        slack_configs:
          - channel: '#alerts'
    ```

---

## opsgenie-no-api-key

**Level:** error

An `opsgenie_configs` entry has no `api_key` or `api_key_file`, and `global.opsgenie_api_key` is not set. OpsGenie requires an API key to accept alerts.

=== "Bad"
    ```yaml
    receivers:
      - name: team
        opsgenie_configs:
          - priority: P1   # missing api_key!
    ```

=== "Fixed — per receiver"
    ```yaml
    receivers:
      - name: team
        opsgenie_configs:
          - api_key: 'your-opsgenie-api-key'
            priority: P1
    ```

=== "Fixed — global"
    ```yaml
    global:
      opsgenie_api_key: 'your-opsgenie-api-key'
    ```

---

## msteams-no-webhook-url

**Level:** error

An `msteams_configs` entry has no `webhook_url` or `webhook_url_file`. MS Teams requires an incoming webhook URL to receive messages.

=== "Bad"
    ```yaml
    receivers:
      - name: team
        msteams_configs:
          - title: 'Alert'   # missing webhook_url!
    ```

=== "Fixed"
    ```yaml
    receivers:
      - name: team
        msteams_configs:
          - webhook_url: 'https://outlook.office.com/webhook/...'
            title: 'Alert'
    ```

---

## template-file-missing

**Level:** error (warn for globs)

A path in `templates:` does not exist on disk. Alertmanager loads template files at startup — a missing literal path causes startup failure; a glob that matches no files silently loads no templates.

This check only runs when linting a file (not stdin) and resolves paths relative to the config file's directory.

!!! tip
    If your templates live at a runtime path (e.g. `/etc/alertmanager/templates/`) that isn't present in CI, add `template-file-missing` to your `.amlint.yml` ignore list.

=== "Bad — literal path"
    ```yaml
    templates:
      - /etc/alertmanager/templates/custom.tmpl   # file does not exist
    ```

=== "Bad — glob no match"
    ```yaml
    templates:
      - templates/*.tmpl   # no files match this pattern
    ```

=== "Fixed"
    ```yaml
    templates:
      - templates/custom.tmpl   # file exists in the repo alongside the config
    # or use a glob that actually matches:
    templates:
      - templates/*.tmpl        # and ensure at least one .tmpl file is present
    ```

---

## unused-receiver

**Level:** info

A receiver is defined but never referenced by any route. It may be leftover from a previous config version.

=== "Bad"
    ```yaml
    route:
      receiver: default
    receivers:
      - name: default
        slack_configs: [...]
      - name: old-pager           # never used in any route
        pagerduty_configs: [...]
    ```

=== "Fixed"
    ```yaml
    # Remove the unused receiver, or add a route that uses it
    ```
