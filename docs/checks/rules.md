# Prometheus rules checks

These checks compare the Alertmanager config with the alerts Prometheus actually defines.
They run only when you pass rule files with `--rules`:

```bash
amlint check alertmanager.yml --rules 'rules/*.yml'
amlint check alertmanager.yml --rules 'rules/**/*.yml' --rules 'k8s/prometheusrules/*.yaml'
```

Quote the glob so amlint expands it, not the shell (`**` matches subdirectories), and
repeat `--rules` for more locations. amlint reads:

- Prometheus rule files: `groups[].rules[]`, the format Prometheus, Thanos, Mimir and the
  Loki ruler use
- prometheus-operator `PrometheusRule` manifests (`spec.groups`), several per file
  separated by `---`, or a `kind: List` of them as `kubectl get prometheusrules -o yaml`
  prints it

Recording rules (`record:`) are skipped; only alerting rules (`alert:`) count.

## What amlint knows about an alert

At runtime an alert's labels come from four places: the PromQL result series, the rule's
`labels:`, `alertname` (always the rule's name), and Prometheus' `external_labels`. Only
two of those are in the rule file, so amlint works with each rule's **static label set**:
`alertname` plus the rule's `labels:`.

- A label whose value is a Go template (`team: '{{ $labels.owner }}'`) has an unknown
  value.
- A label that routes match on but no rule sets at all (`namespace`, `cluster`, `job`) is
  taken to come from the series or `external_labels`, so its value is unknown too.
- A label that some rules set but this rule doesn't is taken to be absent, like
  Alertmanager treats it: that's how a rule that forgot its `severity` label is caught.

Whenever a result depends on a label with an unknown value, amlint says nothing rather
than guess. `alert_relabel_configs` in Prometheus can still change labels on the way out,
which amlint can't see.

All three checks are warn or info, never error, and point at the route or inhibit rule in
`alertmanager.yml`, so `# amlint: disable=<code>` comments work as usual.

---

## route-unknown-alertname

**Level:** warn

A route matches an `alertname` that no alerting rule in the `--rules` files produces. The
config loads and Alertmanager runs, but the branch never receives the alert it was written
for: the name has a typo, or the rule was renamed or removed. The finding suggests the
closest alert name.

What counts:

- `alertname="X"` (and `match: {alertname: X}`): `X` must be a rule's name.
- A regex that is a plain list of names, `alertname=~"A|B|C"` (also `^(?:A|B|C)$`): each
  name is checked on its own, so one misspelled entry in a long list is reported.
- Any other regex, `alertname=~"Kube.*"`: reported when it matches none of the alert names.
  It is fully anchored and follows Go RE2, as in Alertmanager.
- `!=` and `!~`: a name nothing produces excludes nothing, which is just as likely a typo.

=== "Bad"
    ```yaml
    # rules/node.yml
    groups:
      - name: node
        rules:
          - alert: HighCPUUsage
            expr: node_cpu_busy > 0.9

    # alertmanager.yml
    route:
      receiver: default
      routes:
        - matchers: [alertname="HighCpuUsage"]   # no rule produces this alert
          receiver: infra-pager
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default
      routes:
        - matchers: [alertname="HighCPUUsage"]
          receiver: infra-pager
    ```

```
  ⚠  WARN   [route-unknown-alertname]  ·  alertmanager.yml:4:7  ·  route.routes[0]
     Route matches alertname 'HighCpuUsage', which no alerting rule in the --rules files
     produces — did you mean 'HighCPUUsage'? Unless such alerts come from another source,
     this branch is dead.
```

!!! note
    Alerts that don't come from these rule files (another Prometheus, Loki, Grafana, a
    script posting to the Alertmanager API) look unknown too. Pass their rule files as
    well, or suppress the finding on that route.

---

## inhibit-unknown-alertname

**Level:** warn

The same check for inhibit rules: `source_match` / `source_matchers` and `target_match` /
`target_matchers` on an `alertname` that no rule produces. A source that never fires means
the rule never mutes anything; a target that never fires leaves it nothing to mute.

=== "Bad"
    ```yaml
    # rules/node.yml defines NodeDown and NodeHighLoad
    inhibit_rules:
      - source_matchers: [alertname="NodeDwn"]   # typo: never fires
        target_matchers: [alertname="NodeHighLoad"]
        equal: [instance]
    ```

=== "Fixed"
    ```yaml
    inhibit_rules:
      - source_matchers: [alertname="NodeDown"]
        target_matchers: [alertname="NodeHighLoad"]
        equal: [instance]
    ```

---

## alert-hits-default-receiver

**Level:** info

Each alerting rule is routed with its static labels, using the same simulation as
[`amlint route`](../usage.md#route). This finding lists the alerts that match no child
route and end up on the root route's receiver: typically a rule that forgot its
`severity` or `team` label, or uses a value no route handles.

Alerts are grouped by the labels the routes match on, one finding per combination, naming
the first five. A setup that sends every warning to the default receiver on purpose, as
kube-prometheus does, gets one finding for `severity=warning` however many warning alerts
there are, and a rule that lacks a label the others set stands out in a group of its own
("with no severity label").

It's info, not warn, because the static labels aren't the whole story: an alert whose
route depends on a label amlint can't know (see [above](#what-amlint-knows-about-an-alert))
is skipped, but series labels and `external_labels` the routes don't mention can't be
ruled out entirely. If the root route has no child routes, everything goes to its receiver
by design and the check stays quiet.

=== "Bad"
    ```yaml
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
          receiver: pager
    ```

=== "Fixed"
    ```yaml
          - alert: QueueBacklog
            expr: queue_depth > 1000
            labels: {severity: critical}
    ```

```
  ℹ  INFO   [alert-hits-default-receiver]  ·  alertmanager.yml:1:1  ·  route
     Based on static rule labels, 1 alert with no severity label matches no child route and
     goes to the root receiver 'default': QueueBacklog (rules/app.yml:7). Labels from the
     PromQL series or external_labels may still route it elsewhere at runtime.
```

All of these findings point at the root `route:`. When some alerts are meant to use the
default receiver, give them a route of their own (`alertname=~"A|B"` → `default`), which
also documents the choice; or turn the check off for the file with
`# amlint: disable-file=alert-hits-default-receiver`.
