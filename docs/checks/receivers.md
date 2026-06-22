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
