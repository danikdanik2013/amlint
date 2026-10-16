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

## invalid-root-route

**Level:** error

The root route breaks a rule Alertmanager enforces when loading the config: it must name a
`receiver`, and must not have matchers, `continue`, `mute_time_intervals` or
`active_time_intervals`. Alertmanager will refuse to start.

=== "Bad"
    ```yaml
    route:
      matchers: [team="infra"]   # root route must match everything
      routes:
        - matchers: [severity="critical"]
          receiver: pager
    ```

=== "Fixed"
    ```yaml
    route:
      receiver: default          # required fallback receiver
      routes:
        - matchers: [team="infra"]
          receiver: infra
        - matchers: [severity="critical"]
          receiver: pager
    ```

---

## bad-regex

**Level:** error

A regex matcher fails to compile, or uses syntax that Go RE2 — the engine Alertmanager uses —
does not support: lookahead/lookbehind, backreferences, possessive quantifiers, atomic groups.
Regexes in routes (`match_re`, `matchers`) and in inhibit rules (`source_match_re`,
`target_match_re`, `source_matchers`, `target_matchers`) are all checked.

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

A catch-all route — no `match`, `match_re`, or `matchers`, or only `label=~".*"` matchers — with `continue: false` appears before sibling routes. All subsequent siblings are unreachable — they will never receive alerts.

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

## shadowed-route

**Level:** warn

An earlier sibling route without `continue: true` has a strict subset of this route's matchers. Every alert this route matches also matches the earlier sibling, which comes first — so this route never receives alerts.

Only exact subsets are reported: the same label, operator and value, with `match:`, `match_re:` and `matchers:` spellings comparing equal. amlint does not reason about regexes, so `severity=~"crit.*"` before `severity="critical", team="infra"` is not reported — every finding is a route that really is dead.

=== "Bad"
    ```yaml
    routes:
      - matchers: [severity="critical"]
        receiver: pager
      - matchers: [severity="critical", team="infra"]   # never reached
        receiver: infra-pager
    ```

=== "Fixed — specific first"
    ```yaml
    routes:
      - matchers: [severity="critical", team="infra"]   # more specific first
        receiver: infra-pager
      - matchers: [severity="critical"]
        receiver: pager
    ```

=== "Fixed — use continue"
    ```yaml
    routes:
      - matchers: [severity="critical"]
        receiver: pager
        continue: true          # pass to next sibling too
      - matchers: [severity="critical", team="infra"]
        receiver: infra-pager
    ```

!!! note
    Each dead route is reported by one check. Identical matchers are
    [`route-match-collision`](#route-match-collision), and a catch-all sibling (no matchers, or
    only `label=~".*"`) is [`unreachable-route`](#unreachable-route); `shadowed-route` reports
    the remaining provable case, a strict subset.

---

## deep-nesting

**Level:** info

The routing tree is more than 5 levels deep. Very deeply nested configs are hard to read, debug, and maintain.

!!! tip
    Consider flattening the routing tree using `matchers` with multiple conditions instead of nested routes.
