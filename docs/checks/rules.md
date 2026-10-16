# Prometheus rules checks

These checks compare your Alertmanager config with your Prometheus **alerting rules**, the
`alert:` rules in Prometheus rule files that fire alerts named after the rule. They catch
parts of `alertmanager.yml` that wait for an alert no rule fires (usually a typo or a
renamed rule), and alerts that fall through to your catch-all destination because nothing
more specific picks them up. They run only when you pass your rule files with `--rules`:

```bash
amlint check alertmanager.yml --rules 'rules/*.yml'
```

## Which rule files amlint reads

- Quote the glob so that amlint expands it, not your shell. `**` matches subdirectories:
  `--rules 'rules/**/*.yml'`.
- Repeat `--rules` for more locations:
  `--rules 'rules/*.yml' --rules 'k8s/prometheusrules/*.yaml'`.
- amlint reads Prometheus rule files (`groups[].rules[]`), the format that Prometheus,
  Thanos, Mimir and the Loki ruler use. It also reads prometheus-operator `PrometheusRule`
  manifests (`spec.groups`): several in one file separated by `---`, or a `kind: List` as
  `kubectl get prometheusrules -o yaml` prints it.
- Only alerting rules (`alert:`) count. amlint skips recording rules (`record:`).
- If a glob matches no file, or a file can't be parsed, amlint exits with code 2. If the
  files contain no alerting rules, amlint prints a warning and skips these checks.

## What amlint knows about an alert

Alertmanager routes an alert by its labels: each route, a branch of the `route:` tree,
sends the alerts its matchers select to a receiver, a named notification destination. At
runtime an alert gets its labels from four places:

1. the series that the rule's PromQL `expr` returns (`instance`, `job`, `namespace`, ...)
2. the rule's `labels:`
3. `alertname`, which is always the rule's name
4. Prometheus' `external_labels`, the labels a Prometheus server adds to every alert it
   sends (set in `prometheus.yml`, often `cluster` or `region`)

Only 2 and 3 are in the rule file. Together they are the rule's **static labels**, and
amlint checks routing with those. This matters because routes often match on labels that
only exist at runtime, such as `namespace` or `cluster`, and amlint can only be sure about
the labels it can see. So:

- A label whose value is a Go template (`team: '{{ $labels.owner }}'`) has an unknown
  value.
- A label that routes match on but that no rule sets (`namespace`, `cluster`, `job`) is
  assumed to come from the series or `external_labels`, so its value is unknown too.
- A label that some rules set but this rule doesn't is treated as absent, the way
  Alertmanager would see it. That's how amlint catches a rule that forgot its `severity`
  label.

When a result depends on a label with an unknown value, amlint reports nothing rather than
guess. amlint also can't see `alert_relabel_configs` in Prometheus, which can change labels
on the way out.

All three checks are warn or info, never error. `amtool check-config`, Alertmanager's own
config checker, catches none of them, because the config itself is valid. Each finding
points at a line in `alertmanager.yml`, so `# amlint: disable=<code>` comments work there
as usual.

---

## route-unknown-alertname

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** A route matches an `alertname` that no alerting rule in your
`--rules` files fires. Usually the name has a typo, or the rule was renamed or removed.

```yaml title="rules/node.yml"
groups:
  - name: node
    rules:
      - alert: HighCPUUsage
        expr: node_cpu_busy > 0.9
```

```yaml title="alertmanager.yml (bad)"
route:
  receiver: default
  routes:
    - matchers: [alertname="HighCpuUsage"]   # no rule fires this name
      receiver: infra-pager
```

```
  ⚠  WARN   [route-unknown-alertname]  ·  alertmanager.yml:4:7  ·  route.routes[0]
     Route matches alertname 'HighCpuUsage', which no alerting rule in the --rules files
     produces — did you mean 'HighCPUUsage'? Unless such alerts come from another source,
     this branch is dead.
```

**What Alertmanager does:** Loads the config and runs. The route never receives the alert
it was written for, so the alert goes to whichever route matches it instead, often the
default receiver.

**How to fix:** Use the exact rule name. The finding suggests the closest one.

```yaml title="alertmanager.yml (fixed)"
route:
  receiver: default
  routes:
    - matchers: [alertname="HighCPUUsage"]
      receiver: infra-pager
```

**What amlint checks** in `matchers:`, `match:` and `match_re:`:

- `alertname="X"` (or `match: {alertname: X}`): `X` must be a rule's name.
- A regex that just lists names, such as `alertname=~"A|B|C"` (or `^(?:A|B|C)$`): amlint
  checks each name on its own, so it reports one misspelled entry in a long list.
- Any other regex, such as `alertname=~"Kube.*"`: amlint reports it when it matches none of
  the alert names. Like Alertmanager, it anchors the regex at both ends and uses Go RE2
  syntax.
- `!=` and `!~`: excluding a name that nothing fires excludes nothing, so that's probably a
  typo too.

**Limits:** Alerts that don't come from these rule files (another Prometheus, Loki,
Grafana, a script posting to the Alertmanager API) also look unknown. Pass their rule files
too, or add `# amlint: disable=route-unknown-alertname` to that route.

---

## inhibit-unknown-alertname

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** An inhibit rule (a rule that mutes alerts matching its target while an
alert matching its source is firing) has a source or target that matches an `alertname` no
alerting rule fires. amlint checks `source_match`, `source_match_re` and `source_matchers`,
and the same `target_*` keys, with the same matching as
[`route-unknown-alertname`](#route-unknown-alertname).

```yaml title="rules/node.yml"
groups:
  - name: node
    rules:
      - alert: NodeDown
        expr: up{job="node"} == 0
      - alert: NodeHighLoad
        expr: node_load5 > 10
```

```yaml title="alertmanager.yml (bad)"
inhibit_rules:
  - source_matchers: [alertname="NodeDwn"]   # typo: no rule fires this
    target_matchers: [alertname="NodeHighLoad"]
    equal: [instance]
```

**What Alertmanager does:** Loads the rule and gives no sign of a problem. If the source
never fires, the rule never mutes anything. If the target never fires, the rule has
nothing to mute.

**How to fix:** Use the exact rule names.

```yaml title="alertmanager.yml (fixed)"
inhibit_rules:
  - source_matchers: [alertname="NodeDown"]
    target_matchers: [alertname="NodeHighLoad"]
    equal: [instance]
```

**Limits:** The same as for [`route-unknown-alertname`](#route-unknown-alertname). Alerts
from other sources look unknown.

---

## alert-hits-default-receiver

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** Based on its static labels, an alert matches none of the child routes
(the `routes:` under the top-level `route:`), so it goes to the default receiver, the
`receiver:` set at the top of `route:`. Typically the rule forgot its `severity` or `team`
label, or uses a value that no route handles.

```yaml title="rules/app.yml"
groups:
  - name: app
    rules:
      - alert: DiskFull
        expr: disk_free_ratio < 0.05
        labels:
          severity: critical
      - alert: QueueBacklog
        expr: queue_depth > 1000   # no severity label
```

```yaml title="alertmanager.yml"
route:
  receiver: default
  routes:
    - matchers: [severity="critical"]
      receiver: pager
```

```
  ℹ  INFO   [alert-hits-default-receiver]  ·  alertmanager.yml:1:1  ·  route
     Based on static rule labels, 1 alert with no severity label matches no child route and
     goes to the root receiver 'default': QueueBacklog (rules/app.yml:8). Labels from the
     PromQL series or external_labels may still route it elsewhere at runtime.
```

**What Alertmanager does:** Delivers these alerts to the default receiver, which is often
a channel nobody watches.

**How to fix:** Add the missing label to the rule, or add a route for the alert.

```yaml title="rules/app.yml (fixed)"
groups:
  - name: app
    rules:
      - alert: DiskFull
        expr: disk_free_ratio < 0.05
        labels:
          severity: critical
      - alert: QueueBacklog
        expr: queue_depth > 1000
        labels:
          severity: critical
```

If some alerts should use the default receiver, give them a route of their own
(`alertname=~"A|B"` to `default`). That also documents the choice. Or turn the check off
for the file with `# amlint: disable-file=alert-hits-default-receiver`.

**How findings are grouped:** amlint routes each rule with the same simulation as
[`amlint route`](../usage.md#route). You get one finding per combination of the label
values that the routes match on, and each finding names the first five alerts. So if you
send every warning to the default receiver on purpose, as kube-prometheus does, you get one
finding for `severity=warning`, however many warning alerts there are. A rule that lacks a
label the other rules set gets a finding of its own ("with no severity label"). All of
these findings point at the root `route:`.

**Limits:**

- It's info, not warn, because static labels aren't the whole story. amlint treats a label
  that this rule doesn't set as absent. If the label actually comes from the PromQL series
  or `external_labels`, the alert may still route elsewhere at runtime.
- amlint skips an alert when its route depends on a label with an unknown value (see
  [above](#what-amlint-knows-about-an-alert)).
- If the root route has no child routes, everything goes to its receiver by design, and
  the check stays quiet.
