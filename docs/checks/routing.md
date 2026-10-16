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

## bad-matcher

**Level:** error

A `matchers:` entry that Alertmanager can't parse, so it refuses to load the config
(`bad matcher format`, or `matcher value contains unescaped double quote`). Alertmanager
reads each entry with its classic matcher parser, and with the UTF-8 parser only when the
classic one fails. This check fires when both fail. Entries in routes (`matchers`) and in
inhibit rules (`source_matchers`, `target_matchers`) are all checked. Common causes:

- an empty matcher between two commas: `a=b,,c=d`
- a stray double quote: `a=b"c`
- a comma in a bare value, which splits the entry: `a=b, c=~x{1,2}`
- a quoted label name (`"service.name"=...`). Alertmanager then reads the whole entry with
  the UTF-8 parser, where values are Go strings. `"x.y"="a\.b"` is an error there, since
  `\.` is no Go escape, and a bare value can't hold a backslash or a brace
  (`"x.y"=~\d+`).

=== "Bad"
    ```yaml
    route:
      receiver: default
      routes:
        - matchers:
            - 'service=api,,env=prod'        # empty matcher between the commas
            - '"service.name"=~"api\.v2"'   # quoted label name: \. is not a Go escape
          receiver: team
    ```

=== "Fixed"
    ```yaml
        - matchers:
            - 'service="api", env="prod"'
            - '"service.name"=~"api\\.v2"'  # Go string: \\ is one backslash
          receiver: team
    ```

---

## stripped-brace

**Level:** warn

A bare (unquoted) value at the end of a `matchers:` entry ends in `}`, and the entry doesn't
start with `{`. Alertmanager's classic parser strips one trailing `}` from every entry,
braces or not. So `code=~[0-9]{3}` is the regex `[0-9]{3`, which matches the text `1{3` and
not `123`. The config loads and `amtool check-config` accepts it. The route just never
matches what you meant. Quote the value, or wrap the whole entry in braces.

=== "Bad"
    ```yaml
    route:
      receiver: default
      routes:
        - matchers:
            - code=~[0-9]{3}        # the regex is [0-9]{3: matches "1{3", not "123"
          receiver: http-errors
    ```

=== "Fixed"
    ```yaml
        - matchers:
            - code=~"[0-9]{3}"      # quoted: the regex is [0-9]{3}
          receiver: http-errors
    # or: - '{code=~[0-9]{3}}'
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

**Level:** error

`group_by` contains `...` (group by all labels) alongside explicit labels. Alertmanager refuses to load the config: `amtool check-config` fails with "cannot have wildcard group_by (`...`) and other labels at the same time".

=== "Bad"
    ```yaml
    route:
      group_by: ['...', alertname, cluster]  # rejected: '...' plus explicit labels
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
