# Routing checks

These checks catch two kinds of routing mistakes: a routing tree Alertmanager refuses to
load, and routes that load fine but never get the alerts you meant for them.

A **route** is a node in the `route:` tree. Its **matchers** (label conditions such as
`severity="critical"`, written under `matchers:`, `match:` or `match_re:`) pick alerts, and
its **receiver** (a named entry under `receivers:`) gets them. Routes in the same `routes:`
list are **siblings**: Alertmanager tries them top to bottom and stops at the first match,
unless that route sets **`continue: true`** ("keep trying the siblings below me").

To see where a given alert ends up, run
[`amlint route alertmanager.yml team=infra severity=critical`](../usage.md#route).

---

## no-root-route

**Level:** error · **amtool:** also catches it

**What goes wrong:** The config has no `route:` key, so there is no routing tree.

```yaml title="Bad"
receivers:
  - name: default
    webhook_configs:
      - url: http://alerts.example.com/hook
```

**What Alertmanager does:** Refuses to load the config: `no routes provided`.

**How to fix:** Add a root route that names the receiver every alert falls back to.

```yaml title="Fixed"
route:
  receiver: default
receivers:
  - name: default
    webhook_configs:
      - url: http://alerts.example.com/hook
```

---

## invalid-root-route

**Level:** error · **amtool:** also catches it

**What goes wrong:** The root route (the top-level `route:`, which every alert enters)
breaks one of Alertmanager's rules for it. It must name a `receiver`, and it can't have
matchers, `continue: true`, `mute_time_intervals` or `active_time_intervals`.

```yaml title="Bad"
route:
  matchers: [team="infra"]   # the root route must match every alert
  routes:                    # ...and it has no receiver
    - matchers: [severity="critical"]
      receiver: pager
```

**What Alertmanager does:** Refuses to load the config, with an error such as
`root route must specify a default receiver` or `root route must not have any matchers`.
amlint reports every broken rule at once.

**How to fix:** Give the root route a receiver. Move matchers and time intervals down to a
child route.

```yaml title="Fixed"
route:
  receiver: default          # gets every alert no child route matches
  routes:
    - matchers: [team="infra", severity="critical"]
      receiver: pager
```

---

## undefined-receiver

**Level:** error · **amtool:** also catches it

**What goes wrong:** A route names a receiver that isn't defined under `receivers:`.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [team="infra"]
      receiver: pager-team   # not defined below
receivers:
  - name: default
    webhook_configs:
      - url: http://alerts.example.com/hook
```

**What Alertmanager does:** Refuses to load the config:
`undefined receiver "pager-team" used in route`.

**How to fix:** Define the receiver, or correct the name to one that exists.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers: [team="infra"]
      receiver: pager-team
receivers:
  - name: default
    webhook_configs:
      - url: http://alerts.example.com/hook
  - name: pager-team         # added
    pagerduty_configs:
      - routing_key: <integration-key>
```

---

## groupby-ellipsis

**Level:** error · **amtool:** also catches it

**What goes wrong:** A route's `group_by` (the labels that decide which alerts share one
notification) lists `'...'` next to label names. `'...'` already means "group by every
label".

```yaml title="Bad"
route:
  receiver: default
  group_by: ['...', alertname, cluster]
```

**What Alertmanager does:** Refuses to load the config:
``cannot have wildcard group_by (`...`) and other labels at the same time``.

**How to fix:** Keep either `'...'` or the label names, not both.

```yaml title="Fixed"
route:
  receiver: default
  group_by: [alertname, cluster]   # or: ['...']
```

---

## bad-regex

**Level:** error · **amtool:** also catches it

**What goes wrong:** A regex matcher (`=~` or `!~`) doesn't compile, or uses syntax that
Go's RE2 engine, which Alertmanager uses, doesn't have: lookahead or lookbehind,
backreferences, possessive quantifiers, atomic groups, or a repeat count over 1000. amlint
checks regexes in routes and in inhibition rules.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers:
        - service=~"(?!test-).*"   # lookahead: RE2 has none
      receiver: team
```

**What Alertmanager does:** Refuses to load the whole config
(`error parsing regexp: invalid or unsupported Perl syntax`), so no route works, not just
this one.

**How to fix:** Rewrite the regex in RE2 syntax. To exclude values, use a negative matcher
(`!~`) instead of a lookahead.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers:
        - service!~"test-.*"       # every service not starting with test-
      receiver: team
```

---

## bad-matcher

**Level:** error · **amtool:** also catches it

**What goes wrong:** An entry under `matchers:` (or `source_matchers:` / `target_matchers:`
in an inhibition rule) is not a matcher Alertmanager can parse. Common causes:

- an empty matcher between two commas: `a=b,,c=d`
- a stray double quote: `a=b"c`
- a comma in a bare (unquoted) value, which splits the entry: `a=b, c=~x{1,2}`
- a quoted label name, such as `"service.name"=...`, followed by a value that isn't a valid
  Go string: `"x.y"="a\.b"` (`\.` is not a Go escape), or a bare value with a backslash or
  brace (`"x.y"=~\d+`)

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers:
        - 'service=api,,env=prod'        # empty matcher between the commas
        - '"service.name"=~"api\.v2"'   # \. is not a Go escape
      receiver: team
```

**What Alertmanager does:** Refuses to load the config (`bad matcher format: ...` or
`matcher value contains unescaped double quote: ...`). It reads each entry with its classic
parser, and with its UTF-8 parser only if the classic one fails. amlint flags the entries
both parsers reject.

**How to fix:** Write each matcher as `label="value"` (or `!=`, `=~`, `!~`), with one comma
between matchers. A quoted label name sends the whole entry to the UTF-8 parser, where
values are Go strings: quote the value and double each backslash.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers:
        - 'service="api", env="prod"'
        - '"service.name"=~"api\\.v2"'  # \\ is one backslash in a Go string
      receiver: team
```

---

## bad-match-label

**Level:** error · **amtool:** also catches it

**What goes wrong:** A key under `match:` or `match_re:` isn't a classic label name: a
letter or underscore, then letters, digits and underscores (`[a-zA-Z_][a-zA-Z0-9_]*`).
Inhibition rules' `source_match`, `target_match` and their `_re` forms are checked too.
Names like `k8s.namespace` only work in `matchers:`, quoted.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - match:
        k8s.namespace: payments   # not a classic label name
      receiver: payments
```

**What Alertmanager does:** Refuses to load the config:
`invalid label name "k8s.namespace"`.

**How to fix:** Move the matcher to `matchers:` (or `source_matchers:` /
`target_matchers:`) and quote the label name. For a child route or an inhibition rule, the
amlint finding spells out the entry to use.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers:
        - '"k8s.namespace"="payments"'   # quoted: any UTF-8 name works
      receiver: payments
```

---

## unreachable-route

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** A catch-all route (no matchers, or only `label=~".*"` matchers) without
`continue: true` sits above other siblings. It takes every alert, so the siblings below it
never get any.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - receiver: team-slack       # no matchers: matches every alert
    - matchers: [severity="critical"]
      receiver: pager            # never reached
```

**What Alertmanager does:** Loads the config. Every alert goes to `team-slack` and stops
there, so `pager` never gets an alert.

**How to fix:** Put the catch-all last. If every alert should reach the catch-all *and* the
routes below it, give it `continue: true`.

=== "Catch-all last"

    ```yaml title="Fixed"
    route:
      receiver: default
      routes:
        - matchers: [severity="critical"]
          receiver: pager
        - receiver: team-slack
    ```

=== "Notify both"

    ```yaml title="Fixed"
    route:
      receiver: default
      routes:
        - receiver: team-slack
          continue: true         # keep trying the routes below
        - matchers: [severity="critical"]
          receiver: pager
    ```

---

## route-match-collision

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** Two sibling routes have identical matchers, and the first one has no
`continue: true`. Different spellings of the same matcher count as identical:
`match: {team: infra}` and `matchers: [team="infra"]`.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [team="infra", severity="critical"]
      receiver: pager
    - matchers: [team="infra", severity="critical"]   # same matchers
      receiver: infra-slack                            # never reached
```

**What Alertmanager does:** Loads the config. Every matching alert stops at the first
route, so `infra-slack` never gets an alert.

**How to fix:** If both receivers should get these alerts, add `continue: true` to the
first route. Otherwise, give the second route different matchers, or remove it.

=== "Notify both"

    ```yaml title="Fixed"
    route:
      receiver: default
      routes:
        - matchers: [team="infra", severity="critical"]
          receiver: pager
          continue: true         # keep trying the routes below
        - matchers: [team="infra", severity="critical"]
          receiver: infra-slack
    ```

=== "Different matchers"

    ```yaml title="Fixed"
    route:
      receiver: default
      routes:
        - matchers: [team="infra", severity="critical"]
          receiver: pager
        - matchers: [team="infra", severity="warning"]
          receiver: infra-slack
    ```

---

## shadowed-route

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** An earlier sibling without `continue: true` matches every alert this
route matches, even though their matchers differ. amlint compares the label values each
matcher allows, not how it's written. In each pair below, the earlier route hides the later
one:

- `severity=~"warning|critical"` before `severity="critical"`: `critical` matches the regex
- `team!="db"` before `team="infra"`: `infra` is not `db`
- `alertname=~"Kube.*"` before `alertname=~"KubePod.*"`: every `KubePod…` name starts with
  `Kube`
- `service!~"db-.*"` before `service=~"api-[0-9]+"`: no `api-…` name starts with `db-`
- `team=~"|infra"` before `team=""`: a missing label counts as the empty string, as in
  Alertmanager
- `severity="critical"` before `severity="critical", team="infra"`: the earlier route asks
  for less

Parent routes count too, because every alert that reaches the siblings matched their
parents. Under a parent with `env="prod"`, a sibling with
`env=~"prod|staging", severity="critical"` hides a later sibling with `severity="critical"`.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [severity=~"warning|critical"]
      receiver: ops
    - matchers: [severity="critical"]   # never reached
      receiver: pager
```

**What Alertmanager does:** Loads the config. Critical alerts match the first route and
stop there, so `pager` never gets them.

**How to fix:** Put the narrower route first. If both receivers should get the alerts, give
the broader route `continue: true` instead.

=== "Narrower first"

    ```yaml title="Fixed"
    route:
      receiver: default
      routes:
        - matchers: [severity="critical"]
          receiver: pager
        - matchers: [severity=~"warning|critical"]
          receiver: ops
    ```

=== "Notify both"

    ```yaml title="Fixed"
    route:
      receiver: default
      routes:
        - matchers: [severity=~"warning|critical"]
          receiver: ops
          continue: true         # keep trying the routes below
        - matchers: [severity="critical"]
          receiver: pager
    ```

**Limits:** amlint reports only routes it can prove dead. It stays silent when a regex uses
case folding (`(?i)`), word boundaries (`\b`), anchors anywhere but the very start or end,
or is too large to compare, even if the route is in fact dead. Each dead route gets one
finding: identical matchers are [`route-match-collision`](#route-match-collision), and a
catch-all sibling is [`unreachable-route`](#unreachable-route).

---

## stripped-brace

**Level:** warn · **amtool:** doesn't catch it

**What goes wrong:** A bare (unquoted) value at the end of a `matchers:` entry ends in `}`,
and the entry doesn't start with `{`. Alertmanager drops that `}`. Entries in routes and in
inhibition rules' `source_matchers` / `target_matchers` are checked.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers:
        - code=~[0-9]{3}      # Alertmanager reads the regex as [0-9]{3
      receiver: http-errors
```

**What Alertmanager does:** Loads the config. Its classic matcher parser strips one trailing
`}` from every entry, braces or not. The regex here becomes `[0-9]{3`, which matches the
text `1{3` and not `123`, so the route never matches the alerts you meant.

**How to fix:** Quote the value, or wrap the whole entry in braces.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers:
        - code=~"[0-9]{3}"    # or: '{code=~[0-9]{3}}'
      receiver: http-errors
```

---

## useless-continue

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** The last route in a `routes:` list has `continue: true`. No sibling
comes after it, so the flag does nothing.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [env="prod"]
      receiver: team
      continue: true           # last sibling: nothing to continue to
```

**What Alertmanager does:** Ignores the flag. `continue` only passes the alert on to later
siblings in the same `routes:` list, never to the routes after the parent.

**How to fix:** Remove `continue: true`. If you meant alerts to also reach the routes after
the parent, set `continue: true` on the parent instead.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers: [env="prod"]
      receiver: team
```

---

## deep-nesting

**Level:** info · **amtool:** doesn't catch it

**What goes wrong:** The routing tree goes more than 5 levels below the root route. That
is hard to read, review and debug.

```yaml title="Bad"
route:
  receiver: default
  routes:
    - matchers: [env="prod"]
      routes:
        - matchers: [region="eu"]
          routes:
            - matchers: [team="infra"]
              routes:
                - matchers: [service="db"]
                  routes:
                    - matchers: [severity="critical"]
                      routes:
                        - matchers: [component="disk"]   # level 6
                          receiver: db-pager
```

**What Alertmanager does:** Routes alerts as usual. It has no depth limit.

**How to fix:** Flatten the tree. One route with several matchers replaces a chain of
single-matcher routes.

```yaml title="Fixed"
route:
  receiver: default
  routes:
    - matchers:
        - env="prod"
        - region="eu"
        - team="infra"
        - service="db"
        - severity="critical"
        - component="disk"
      receiver: db-pager
```
