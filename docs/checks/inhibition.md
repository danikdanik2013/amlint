# Inhibition checks

## inhibit-no-equal

**Level:** warn

An inhibition rule has no `equal` field. Without `equal`, the rule silences **any** target alert whenever **any** source alert fires — regardless of whether they are related. This almost always silences more than intended.

Rules with target matchers but **no source matchers** are not flagged: every alert counts as their source, so they mute their targets whenever anything else fires. That is a deliberate "always mute" pattern — kube-prometheus uses it to keep the `InfoInhibitor` alert quiet.

All inhibition checks understand both matcher syntaxes: `source_match` / `source_match_re` / `target_match` / `target_match_re` and the newer `source_matchers` / `target_matchers` lists.

=== "Bad"
    ```yaml
    inhibit_rules:
      - source_match:
          severity: critical
        target_match:
          severity: warning
        # no equal: field — silences ALL warnings when ANY critical fires
    ```

=== "Fixed"
    ```yaml
    inhibit_rules:
      - source_match:
          severity: critical
        target_match:
          severity: warning
        equal: [alertname, cluster]   # only silence warnings for the same alert on the same cluster
    ```

!!! warning
    Without `equal`, a critical alert in one service will silence warning alerts in completely unrelated services. Teams have been paged for incidents that were actually silenced by this.

---

## circular-inhibition

**Level:** warn

Two inhibition rules may silence each other. If both source conditions fire simultaneously, both alerts get silenced and neither is delivered.

=== "Bad"
    ```yaml
    inhibit_rules:
      - source_match: {severity: critical}
        target_match: {severity: warning}
        equal: [alertname]
      - source_match: {severity: warning}   # fires when the first rule's target fires
        target_match: {severity: critical}  # silences the first rule's source!
        equal: [alertname]
    ```

=== "Fixed"
    ```yaml
    # Usually only one direction is needed:
    inhibit_rules:
      - source_match: {severity: critical}
        target_match: {severity: warning}
        equal: [alertname]
    ```

---

## inhibit-same-match

**Level:** info

`source_match` and `target_match` share the same label=value pair. This means the rule may silence the alert that triggered it (source silences itself as a target).

=== "Example"
    ```yaml
    inhibit_rules:
      - source_match:
          severity: critical
          env: prod
        target_match:
          severity: critical    # same as source!
          team: infra
        equal: [alertname]
    ```

    When a `severity=critical` alert fires, it may also match the target criteria and silence itself.

!!! tip
    This is sometimes intentional (e.g. a more specific alert silencing a general one of the same severity). If that's the case, you can safely ignore this finding.
