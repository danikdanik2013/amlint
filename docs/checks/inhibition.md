# Inhibition checks

These checks catch inhibition rules that mute more alerts than you meant, or that mute each
other so nobody gets notified.

An **inhibition rule** (an entry under `inhibit_rules:`) mutes alerts that match its
*target* matchers while an alert that matches its *source* matchers is firing, but only
when both alerts have the same values for the labels listed in `equal:`. **Matchers** are
label conditions such as `severity="critical"`. amlint reads both styles:
`source_matchers` / `target_matchers`, and the older `source_match` / `source_match_re` /
`target_match` / `target_match_re`.

To see whether one alert mutes another, run
[`amlint route alertmanager.yml alertname=DiskFull --firing 'alertname=NodeDown'`](../usage.md#inhibition).

---

## inhibit-no-equal

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** An inhibition rule has no `equal:` field, so source and target alerts
don't need to have anything in common.

```yaml title="Bad"
inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]
    # no equal:
```

**What Alertmanager does:** Loads the config. While any critical alert fires, it mutes every
warning alert, in every cluster and service. A critical alert in one service hides warnings
from unrelated ones.

**How to fix:** List the labels that tie a target alert to its source, such as the alert
name and the cluster.

```yaml title="Fixed"
inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]
    equal: [alertname, cluster]   # only mute warnings of the same alert on the same cluster
```

**Limits:** Rules with no source matchers at all are not flagged. Every alert counts as
their source, so they mute their targets whenever any alert that isn't a target fires. That
is a deliberate "always mute" pattern: kube-prometheus uses it to keep its `InfoInhibitor`
alert quiet.

---

## circular-inhibition

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** Two inhibition rules can mute each other: the source of each rule
matches the target of the other.

```yaml title="Bad"
inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]
    equal: [alertname]
  - source_matchers: [severity="warning"]    # the first rule's target...
    target_matchers: [severity="critical"]   # ...mutes the first rule's source
    equal: [alertname]
```

**What Alertmanager does:** Loads the config. When a critical and a warning alert with the
same `alertname` fire together, each mutes the other, and you get no notification for
either. A muted alert still mutes others.

**How to fix:** Keep only the direction you need, usually the more severe alert muting the
less severe one.

```yaml title="Fixed"
inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]
    equal: [alertname]
```

**Limits:** amlint compares only the `label="value"` matchers of each side. Regex and `!=`
matchers are ignored, so a pair of rules that differ only in those can still be flagged.

---

## inhibit-same-match

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** The source and target side of an inhibition rule require the same
`label="value"`, such as `severity="critical"` on both sides. The rule then mutes alerts of
the same kind as the alerts that trigger it.

```yaml title="Bad"
inhibit_rules:
  - source_matchers: [severity="critical", env="prod"]
    target_matchers: [severity="critical", team="infra"]   # same severity as the source
    equal: [alertname]
```

**What Alertmanager does:** Loads the config. A critical `env="prod"` alert mutes critical
`team="infra"` alerts with the same `alertname`, including ones that are sources of this
rule themselves. An alert never mutes itself, and two alerts that both match both sides
don't mute each other. If both sides match exactly the same alerts, the rule mutes nothing.

**How to fix:** If a critical alert should only mute less severe ones, change the target
side. If critical alerts are meant to mute other critical alerts, keep the rule and turn
the finding off with `# amlint: disable=inhibit-same-match` at the end of the rule's first
line.

```yaml title="Fixed"
inhibit_rules:
  - source_matchers: [severity="critical", env="prod"]
    target_matchers: [severity="warning", team="infra"]
    equal: [alertname]
```

**Limits:** Like [`circular-inhibition`](#circular-inhibition), this compares only
`label="value"` matchers.
