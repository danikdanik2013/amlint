# Receiver checks

A **receiver** is a named destination for alerts: routes (the branches of the `route:` tree
that pick alerts by their labels) send each alert to one. Each receiver holds one or more
**integrations**, such as `slack_configs`, `pagerduty_configs`, `email_configs` or
`webhook_configs`, which do the actual sending. These checks catch receivers that can't
deliver (a duplicate name, no integration, a missing URL, key or template file) and
receivers that no route uses.

**amtool** below is `amtool check-config`, Alertmanager's own config checker. "Also catches
it" means Alertmanager refuses the config anyway. "Doesn't catch it" means Alertmanager
loads and runs the config without complaint, so only amlint tells you.

---

## duplicate-receiver

**Level:** error · **amtool:** also catches it

**What goes wrong:** Two receivers have the same `name`.

```yaml title="Bad"
receivers:
  - name: team
    webhook_configs:
      - url: 'http://hooks.example.com/team'
  - name: team                  # same name again
    webhook_configs:
      - url: 'http://hooks.example.com/oncall'
```

**What Alertmanager does:** Refuses to load the config (`notification config name "team"
is not unique`). It doesn't use one and ignore the other. amlint points at the second
definition.

**How to fix:** Rename one of them, or merge them, because one receiver can hold several
integrations.

=== "Rename"
    ```yaml
    receivers:
      - name: team
        webhook_configs:
          - url: 'http://hooks.example.com/team'
      - name: team-oncall         # point the route that needs it here
        webhook_configs:
          - url: 'http://hooks.example.com/oncall'
    ```

=== "Merge"
    ```yaml
    receivers:
      - name: team
        webhook_configs:
          - url: 'http://hooks.example.com/team'
          - url: 'http://hooks.example.com/oncall'
    ```

---

## empty-receiver

**Level:** warn, or info when the drop looks intentional · **amtool:** doesn't catch it

**What goes wrong:** A receiver has no integration, so alerts routed to it go nowhere. An
empty list such as `webhook_configs: []` also counts as no integration.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [severity="critical"]
      receiver: pager
receivers:
  - name: default
    webhook_configs:
      - url: 'http://hooks.example.com/default'
  - name: pager                 # no integration
```

**What Alertmanager does:** Loads the config and routes critical alerts to `pager`. Then it
silently drops them.

**How to fix:** Add an integration. If you mean to drop the alerts, make that clear: make
it the default receiver (the `receiver:` at the top of `route:`), or name it `null`,
`blackhole`, `devnull`, `dev-null`, `discard` or `drop` (in any case). amlint then reports
it as info, not warn.

=== "Fixed"
    ```yaml
    receivers:
      - name: default
        webhook_configs:
          - url: 'http://hooks.example.com/default'
      - name: pager
        pagerduty_configs:
          - routing_key: 'your-pagerduty-integration-key'
    ```

=== "Intentional drop"
    ```yaml
    route:
      receiver: default
      routes:
        - matchers: [alertname="Watchdog"]
          receiver: 'null'
    receivers:
      - name: default
        webhook_configs:
          - url: 'http://hooks.example.com/default'
      - name: 'null'              # drops Watchdog on purpose: info, not warn
    ```

Quote `'null'`. Unquoted, YAML reads `null` as no value, not as the name `null`.

**Limits:** amlint knows these integrations: `email`, `pagerduty`, `slack`, `opsgenie`,
`victorops`, `webhook`, `wechat`, `sns`, `msteams`, `msteamsv2`, `telegram`, `discord`,
`pushover`, `webex`, `jira`, `rocketchat`, `incidentio` and `mattermost` (each as
`<name>_configs`). It
reports a receiver that uses only some other key as empty.

---

## unused-receiver

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** A receiver is defined, but no route sends alerts to it. Usually it's
left over from an older config.

```yaml title="Bad"
route:
  receiver: default
receivers:
  - name: default
    webhook_configs:
      - url: 'http://hooks.example.com/default'
  - name: old-pager             # no route uses it
    pagerduty_configs:
      - routing_key: 'your-pagerduty-integration-key'
```

**What Alertmanager does:** Loads it and never sends anything to it. That's harmless, but
it's clutter, and readers may think alerts go there.

**How to fix:** Delete the receiver, or add a route that uses it.

=== "Delete it"
    ```yaml
    route:
      receiver: default
    receivers:
      - name: default
        webhook_configs:
          - url: 'http://hooks.example.com/default'
    ```

=== "Route to it"
    ```yaml
    route:
      receiver: default
      routes:
        - matchers: [severity="critical"]
          receiver: old-pager
    receivers:
      - name: default
        webhook_configs:
          - url: 'http://hooks.example.com/default'
      - name: old-pager
        pagerduty_configs:
          - routing_key: 'your-pagerduty-integration-key'
    ```

---

## template-file-missing

**Level:** error for a file path, warn for a glob · **amtool:** doesn't catch it

**What goes wrong:** A relative path under `templates:` matches no file. These files hold
the notification templates (`{{ define "..." }}`) that your integrations use.

```yaml title="Bad"
templates:
  - templates/custom.tmpl       # no such file next to alertmanager.yml: error
  - tmpl/*.tmpl                 # glob that matches no file: warn
```

**What Alertmanager does:** Resolves relative paths against the config file's directory.
It starts even when a path matches nothing, but it loads no templates from that path, so
notifications that use those templates fail to render.

**How to fix:** Correct the path, or add the file. Paths are relative to the config file.

```yaml title="Fixed"
templates:
  - templates/custom.tmpl       # exists next to alertmanager.yml
  - templates/*.tmpl            # matches at least one file
```

**Limits:**

- amlint doesn't check absolute paths (`/etc/alertmanager/templates/*.tmpl`). They point
  into the Alertmanager host or container, not to where amlint runs.
- amlint doesn't run this check on a plain config read from stdin (`amlint check -`),
  because there's no directory to resolve the paths against.
- For a Kubernetes Secret or kube-prometheus-stack values, amlint checks entries against
  the template files that ship with the config, not against files on disk. See
  [Kubernetes & Mimir](../kubernetes.md#kubernetes-secrets).

---

## Missing integration fields

Each integration needs one field before it can send anything: a server, a URL, a key or a
target. If that field is missing, Alertmanager rejects the whole config. It won't start,
and a reload fails and keeps the old config. All of these checks are **error** level, and
amtool also catches them. amlint includes them so that one run reports every problem, as
text, JSON or SARIF, without the Go binary.

| check | integration | needs | or instead |
|-------|-------------|-------|------------|
| [`email-no-smarthost`](#email-no-smarthost) | `email_configs` | `smarthost` | `global.smtp_smarthost` |
| [`webhook-no-url`](#webhook-no-url) | `webhook_configs` | `url` | `url_file` |
| [`pagerduty-no-routing-key`](#pagerduty-no-routing-key) | `pagerduty_configs` | `routing_key` | `routing_key_file`, `service_key`, `service_key_file` |
| [`slack-no-api-url`](#slack-no-api-url) | `slack_configs` | `api_url` | `api_url_file`, `global.slack_api_url`, `global.slack_api_url_file` |
| [`opsgenie-no-api-key`](#opsgenie-no-api-key) | `opsgenie_configs` | `api_key` | `api_key_file`, `global.opsgenie_api_key`, `global.opsgenie_api_key_file` |
| [`msteams-no-webhook-url`](#msteams-no-webhook-url) | `msteams_configs` | `webhook_url` | `webhook_url_file` |
| [`telegram-no-bot-token`](#telegram-no-bot-token) | `telegram_configs` | `bot_token` | `bot_token_file` |
| [`discord-no-webhook-url`](#discord-no-webhook-url) | `discord_configs` | `webhook_url` | `webhook_url_file` |
| [`victorops-no-api-key`](#victorops-no-api-key) | `victorops_configs` | `api_key` | `api_key_file`, `global.victorops_api_key`, `global.victorops_api_key_file` |
| [`wechat-no-corp-id`](#wechat-no-corp-id) | `wechat_configs` | `corp_id` | `global.wechat_api_corp_id` |
| [`sns-no-target`](#sns-no-target) | `sns_configs` | one of `topic_arn`, `phone_number`, `target_arn` | none |

- A `*_file` field reads the value from a file, which keeps the secret out of the config.
- A `global.*` field is the default for every entry of that integration that doesn't set
  its own.
- A field set to an empty string (`url: ''`) counts as missing.

---

## email-no-smarthost

**Level:** error · **amtool:** also catches it

**What goes wrong:** An `email_configs` entry has no `smarthost` (the SMTP server, as
`host:port`), and `global.smtp_smarthost` isn't set.

```yaml title="Bad"
receivers:
  - name: team
    email_configs:
      - to: 'team@example.com'
        from: 'alertmanager@example.com'
        # no smarthost
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `smarthost` on the entry, or set `global.smtp_smarthost` once for all
email entries.

=== "On the entry"
    ```yaml
    receivers:
      - name: team
        email_configs:
          - to: 'team@example.com'
            from: 'alertmanager@example.com'
            smarthost: 'smtp.example.com:587'
    ```

=== "Global default"
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

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `webhook_configs` entry has no `url` and no `url_file`.

```yaml title="Bad"
receivers:
  - name: team
    webhook_configs:
      - send_resolved: true     # no url
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `url`, or set `url_file` to read the URL from a file.

```yaml title="Fixed"
receivers:
  - name: team
    webhook_configs:
      - url: 'http://my-service/webhook'
        send_resolved: true
```

---

## pagerduty-no-routing-key

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `pagerduty_configs` entry has no PagerDuty integration key. None of
`routing_key`, `routing_key_file`, `service_key` or `service_key_file` is set.

```yaml title="Bad"
receivers:
  - name: pager
    pagerduty_configs:
      - severity: critical      # no routing_key
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `routing_key`, or `routing_key_file` to read it from a file. amlint
also accepts `service_key` and `service_key_file`.

```yaml title="Fixed"
receivers:
  - name: pager
    pagerduty_configs:
      - routing_key: 'your-pagerduty-integration-key'
        severity: critical
```

---

## slack-no-api-url

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `slack_configs` entry has no Slack webhook URL. It has no `api_url`
or `api_url_file`, and neither `global.slack_api_url` nor `global.slack_api_url_file` is
set. amlint also flags an empty `api_url: ''`, even when a global URL is set.

=== "Bad: no URL"
    ```yaml
    receivers:
      - name: team
        slack_configs:
          - channel: '#alerts'    # no api_url, and no global one
    ```

=== "Bad: empty api_url"
    ```yaml
    global:
      slack_api_url: 'https://hooks.slack.com/services/T000/B000/XXXX'
    receivers:
      - name: team
        slack_configs:
          - channel: '#alerts'
            api_url: ''           # empty: the global URL isn't used
    ```

**What Alertmanager does:** Rejects the config. An empty `api_url` doesn't fall back to
the global URL. Alertmanager tries to read the empty string as a URL, and that fails.

**How to fix:** Set `api_url` (or `api_url_file`) on the entry, or set
`global.slack_api_url` (or `global.slack_api_url_file`) once for all Slack entries. Delete
any empty `api_url:` line.

=== "On the entry"
    ```yaml
    receivers:
      - name: team
        slack_configs:
          - api_url: 'https://hooks.slack.com/services/T000/B000/XXXX'
            channel: '#alerts'
    ```

=== "Global default"
    ```yaml
    global:
      slack_api_url: 'https://hooks.slack.com/services/T000/B000/XXXX'
    receivers:
      - name: team
        slack_configs:
          - channel: '#alerts'
    ```

---

## opsgenie-no-api-key

**Level:** error · **amtool:** also catches it

**What goes wrong:** An `opsgenie_configs` entry has no `api_key` or `api_key_file`, and
neither `global.opsgenie_api_key` nor `global.opsgenie_api_key_file` is set.

```yaml title="Bad"
receivers:
  - name: team
    opsgenie_configs:
      - priority: P1            # no api_key
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `api_key` (or `api_key_file`) on the entry, or set
`global.opsgenie_api_key` (or `global.opsgenie_api_key_file`) once for all OpsGenie
entries.

=== "On the entry"
    ```yaml
    receivers:
      - name: team
        opsgenie_configs:
          - api_key: 'your-opsgenie-api-key'
            priority: P1
    ```

=== "Global default"
    ```yaml
    global:
      opsgenie_api_key: 'your-opsgenie-api-key'
    receivers:
      - name: team
        opsgenie_configs:
          - priority: P1
    ```

---

## msteams-no-webhook-url

**Level:** error · **amtool:** also catches it

**What goes wrong:** An `msteams_configs` entry has no `webhook_url` and no
`webhook_url_file`.

```yaml title="Bad"
receivers:
  - name: team
    msteams_configs:
      - title: 'Alert'          # no webhook_url
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `webhook_url` to the Teams incoming webhook URL, or set
`webhook_url_file` to read it from a file.

```yaml title="Fixed"
receivers:
  - name: team
    msteams_configs:
      - webhook_url: 'https://outlook.office.com/webhook/...'
        title: 'Alert'
```

---

## telegram-no-bot-token

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `telegram_configs` entry has no `bot_token` and no
`bot_token_file`.

```yaml title="Bad"
receivers:
  - name: team
    telegram_configs:
      - chat_id: -1001234567890   # no bot_token
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `bot_token`, or set `bot_token_file` to read it from a file.

```yaml title="Fixed"
receivers:
  - name: team
    telegram_configs:
      - bot_token: '123456:ABC-DEF...'
        chat_id: -1001234567890
```

---

## discord-no-webhook-url

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `discord_configs` entry has no `webhook_url` and no
`webhook_url_file`.

```yaml title="Bad"
receivers:
  - name: team
    discord_configs:
      - title: 'Alert'          # no webhook_url
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `webhook_url` to the channel's webhook URL, or set `webhook_url_file`
to read it from a file.

```yaml title="Fixed"
receivers:
  - name: team
    discord_configs:
      - webhook_url: 'https://discord.com/api/webhooks/...'
        title: 'Alert'
```

---

## victorops-no-api-key

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `victorops_configs` entry has no `api_key` or `api_key_file`, and
neither `global.victorops_api_key` nor `global.victorops_api_key_file` is set.

```yaml title="Bad"
receivers:
  - name: team
    victorops_configs:
      - routing_key: 'team-routing-key'   # no api_key
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `api_key` (or `api_key_file`) on the entry, or set
`global.victorops_api_key` (or `global.victorops_api_key_file`) once for all VictorOps
entries.

=== "On the entry"
    ```yaml
    receivers:
      - name: team
        victorops_configs:
          - api_key: 'your-victorops-api-key'
            routing_key: 'team-routing-key'
    ```

=== "Global default"
    ```yaml
    global:
      victorops_api_key: 'your-victorops-api-key'
    receivers:
      - name: team
        victorops_configs:
          - routing_key: 'team-routing-key'
    ```

---

## wechat-no-corp-id

**Level:** error · **amtool:** also catches it

**What goes wrong:** A `wechat_configs` entry has no `corp_id` (your WeChat Work
enterprise ID), and `global.wechat_api_corp_id` isn't set. There's no `*_file` variant for
this field.

```yaml title="Bad"
receivers:
  - name: team
    wechat_configs:
      - agent_id: '1000002'     # no corp_id
        api_secret: 'your-api-secret'
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set `corp_id` on the entry, or set `global.wechat_api_corp_id` once for all
WeChat entries.

=== "On the entry"
    ```yaml
    receivers:
      - name: team
        wechat_configs:
          - corp_id: 'your-corp-id'
            agent_id: '1000002'
            api_secret: 'your-api-secret'
    ```

=== "Global default"
    ```yaml
    global:
      wechat_api_corp_id: 'your-corp-id'
    receivers:
      - name: team
        wechat_configs:
          - agent_id: '1000002'
            api_secret: 'your-api-secret'
    ```

---

## sns-no-target

**Level:** error · **amtool:** also catches it

**What goes wrong:** An `sns_configs` entry has no destination. None of `topic_arn`,
`phone_number` or `target_arn` is set.

```yaml title="Bad"
receivers:
  - name: team
    sns_configs:
      - sigv4:
          region: us-east-2
        # no topic_arn, phone_number or target_arn
```

**What Alertmanager does:** Rejects the config.

**How to fix:** Set one of `topic_arn` (an SNS topic), `phone_number` (SMS) or
`target_arn` (a mobile push endpoint).

```yaml title="Fixed"
receivers:
  - name: team
    sns_configs:
      - topic_arn: 'arn:aws:sns:us-east-2:123456789012:My-Topic'
        sigv4:
          region: us-east-2
```
