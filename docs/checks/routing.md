# Routing checks

## undefined-receiver

**Level:** error

A route references a receiver that is not defined in `receivers:`. Alerts matched by this route will be silently dropped.

=== "Bad"
    ```yaml
    route:
      receiver: default
      routes:
        - match: {team: infra}
          receiver: pager-team   # not defined below!
    receivers:
      - name: default
        slack_configs: [...]
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default
      routes:
        - match: {team: infra}
          receiver: pager-team
    receivers:
      - name: default
        slack_configs: [...]
      - name: pager-team         # add the missing receiver
        pagerduty_configs: [...]
    ```

---

## no-root-route

**Level:** error

The config has no root `route` at all. Alertmanager will refuse to start.

=== "Bad"
    ```yaml
    receivers:
      - name: default
        slack_configs: [...]
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default
    receivers:
      - name: default
        slack_configs: [...]
    ```

---

## bad-regex

**Level:** error

A `match_re` or `matchers` entry contains a regex that fails to compile. The route will never match anything.

=== "Bad"
    ```yaml
    route:
      receiver: default
      routes:
        - match_re:
            service: "auth(["    # unclosed character class
          receiver: team
    ```

=== "Fixed"
    ```yaml
        - match_re:
            service: "auth(.*)"  # valid regex
          receiver: team
    ```

---

## unreachable-route

**Level:** warn

A catch-all route (no `match`, `match_re`, or `matchers`) with `continue: false` appears before sibling routes. All subsequent siblings are unreachable — they will never receive alerts.

=== "Bad"
    ```yaml
    routes:
      - receiver: default          # catch-all, continue defaults to false
      - match: {severity: critical}
        receiver: pager            # unreachable!
    ```

=== "Fixed"
    ```yaml
    routes:
      - match: {severity: critical}
        receiver: pager            # specific first
      - receiver: default          # catch-all last
    ```

---

## groupby-ellipsis

**Level:** warn

`group_by` contains `...` (group by all labels) alongside explicit labels. The explicit labels are redundant — `...` already includes everything.

=== "Bad"
    ```yaml
    route:
      group_by: ['...', alertname, cluster]  # alertname and cluster are redundant
    ```

=== "Fixed"
    ```yaml
    route:
      group_by: ['...']     # or list only the labels you want
    ```

---

## useless-continue

**Level:** info

A route has `continue: true` but it is the last sibling — there are no subsequent routes to continue to, so the flag has no effect.

=== "Bad"
    ```yaml
    routes:
      - match: {env: prod}
        receiver: team
        continue: true     # last sibling, no effect
    ```

=== "Fixed"
    ```yaml
    routes:
      - match: {env: prod}
        receiver: team
        # remove continue: true, or add a sibling after this route
    ```

---

## route-match-collision

**Level:** warn

Two sibling routes have identical matchers. Alertmanager evaluates siblings in order and stops at the first match — the second route with the same matchers will never receive any alerts.

=== "Bad"
    ```yaml
    routes:
      - match: {team: infra, severity: critical}
        receiver: pager
      - match: {team: infra, severity: critical}   # identical — dead code
        receiver: slack-infra
    ```

=== "Fixed — use continue"
    ```yaml
    routes:
      - match: {team: infra, severity: critical}
        receiver: pager
        continue: true          # pass to next sibling too
      - match: {team: infra, severity: critical}
        receiver: slack-infra
    ```

=== "Fixed — distinct matchers"
    ```yaml
    routes:
      - match: {team: infra, severity: critical}
        receiver: pager
      - match: {team: infra, severity: warning}    # different severity
        receiver: slack-infra
    ```

---

## deep-nesting

**Level:** info

The routing tree is more than 5 levels deep. Very deeply nested configs are hard to read, debug, and maintain.

!!! tip
    Consider flattening the routing tree using `matchers` with multiple conditions instead of nested routes.
