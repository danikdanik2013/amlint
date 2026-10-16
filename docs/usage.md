# Usage

## check

Validate one or more config files:

```bash
amlint check alertmanager.yml
amlint check prod.yml staging.yml    # multiple files
cat alertmanager.yml | amlint check -  # stdin
```

**Options:**

| flag | description |
|------|-------------|
| `--strict` | Exit non-zero on WARN as well as ERROR |
| `--format json` | Machine-readable JSON output |
| `--format sarif` | GitHub Code Scanning SARIF output |
| `--format github` | GitHub Actions annotations (`::error file=...,line=...::`) |
| `--ignore CODE` | Skip findings with these codes; repeat or comma-separate |
| `--only CODE` | Show only findings with these codes; repeat or comma-separate |
| `--exit-zero` | Always exit 0 regardless of findings; useful for informational CI runs |

**Project config** — amlint loads config automatically. Two options (first found wins):

=== ".amlint.yml"
    ```yaml
    ignore:
      - empty-receiver
      - unused-receiver
    strict: true
    severity:
      empty-receiver: info      # downgrade warn → info
      unused-receiver: error    # upgrade info → error
    ```

=== "pyproject.toml"
    ```toml
    [tool.amlint]
    ignore = ["empty-receiver", "unused-receiver"]
    strict = true

    [tool.amlint.severity]
    empty-receiver = "info"
    unused-receiver = "error"
    ```

`.amlint.yml` takes priority over `pyproject.toml` if both exist.
`--ignore` on the CLI merges with the `ignore:` list from the config file.

### Locations

Every finding points at a line in the file. Text output prints it as `file:line:col`, which
terminals and editors turn into a jump link:

```
  ✖  ERROR  [undefined-receiver]  ·  alertmanager.yml:10:7  ·  route.routes[1]
     Route references receiver 'pager-team' which is not defined in receivers. ...
```

The line is the start of the part of the config the finding is about: the `- ` entry for a
route, receiver or inhibit rule, the key for things like `route` or `global`. When that part
doesn't exist in the file (`global-resolve-timeout-missing` with no `global:` block), the
finding points at the nearest enclosing part that does. JSON and SARIF output carry the same
line and column; stdin (`-`) works the same way.

### Inline suppressions

Turn a check off for one spot with a comment, so the exception and its reason live next to
the config instead of in a global `ignore:` list:

```yaml
# amlint: disable-file=global-resolve-timeout-missing

route:
  receiver: default
  routes:
    - receiver: catch-all   # amlint: disable=unreachable-route
    - matchers: [team="infra"]
      receiver: infra

receivers:
  # amlint: disable=empty-receiver, unused-receiver
  # drops alerts on purpose
  - name: blackhole
```

| comment | where | suppresses |
|---------|-------|------------|
| `# amlint: disable=code1,code2` | at the end of a line | those codes for findings on that line |
| `# amlint: disable=code1,code2` | on a line of its own | those codes for findings on the next line of YAML (other comment lines in between are fine) |
| `# amlint: disable-file=code1,code2` | in the comment block at the top of the file, before any YAML | those codes for the whole file |

A finding's line is the one shown in the output. Unknown codes get the same "did you mean"
warning as `--ignore`, and a `disable-file` comment further down the file is ignored with a
warning. Suppressions also apply to `diff` and `tree`.

### Exit codes

| code | meaning |
|------|---------|
| `0` | No issues (or only WARN/INFO without `--strict`) |
| `1` | One or more ERROR found (or WARN with `--strict`) |
| `2` | File not found or YAML parse error |

### JSON output

```bash
amlint check alertmanager.yml --format json
```

```json
[
  {
    "file": "alertmanager.yml",
    "line": 10,
    "column": 7,
    "level": "error",
    "code": "undefined-receiver",
    "message": "Route references receiver 'pager' which is not defined...",
    "where": "route.routes[1]"
  }
]
```

`file` is `-` for stdin. `amlint diff --format json` adds `line` and `column` to each
finding too: from the new file for `added`, from the old one for `fixed`.

## diff

Show what changed between two configs. Useful in CI to catch regressions on PRs:

```bash
amlint diff main.yml feature-branch.yml
```

```
  FIXED  Route references receiver 'pager-team'...
  ↳ route.routes[1]  [undefined-receiver]

  NEW    match_re for 'service' does not compile...
  ↳ route.routes[3]  [bad-regex]

  1 fixed · 1 new · 4 unchanged
```

Exits `1` if any **new** findings appeared (regressions), `0` otherwise.

### Routing changes

After the findings, `diff` compares how the two configs route alerts — the same simulation
as [`route`](#route), run against both files with the same label sets:

```
───────────────────────────── Routing ─────────────────────────────

  team=infra
    infra-slack  group_wait   30s → 10s
    and 5 more label sets with the same change

  team=db, severity=critical
    receivers   pagerduty-db → slack-db
    and 2 more label sets with the same change

  2 routing changes  · 18 label sets checked
```

A label set shows up when its receivers change, or when a receiver it reaches gets a
different `group_by`, `group_wait`, `group_interval`, `repeat_interval` or time intervals.
Edits that still deliver every alert the same way (`match:` rewritten as `matchers:`,
siblings reordered that never match the same alert) don't.

The label sets come from the matchers of both configs: for every label, the values used
with `=` and `!=`, a few strings each `=~` / `!~` regex fully matches, and the label being
absent. Small configs get every combination. Larger ones get, for every route, a label set
that reaches it and one that stops on it, then single-label variations of those, spread
evenly over the routes and capped at 5000 label sets — the summary says when the cap was
hit. Label sets with exactly the same change are folded into one, shown with the smallest
label set.

Routing changes are informational: they don't change the exit code, since a PR that edits
routing usually means to. If either config can't be simulated (see [`route`](#route)), the
section says why and the findings diff still runs. `--no-routing` skips the comparison.
With `--format json`, a `routing` key holds `checked`, `truncated` and `changes` — each
with the `labels`, the `differences`, the full `old` / `new` routes, and how many `similar`
label sets were folded into it — or `skipped` with the reason.

An empty old file (`/dev/null`, or a config that is new in a PR) counts as no config at
all: every finding is new, none is fixed, and every label set's receivers were `(none)`
before.

### Markdown report

`--format markdown` prints a compact report meant for a pull-request comment:

```bash
amlint diff main.yml alertmanager.yml --format markdown
```

```markdown
### amlint diff

**2 routing changes** · **1 new finding** · 0 fixed · 4 unchanged · 18 label sets checked

#### Receivers

| labels | before | after |
| --- | --- | --- |
| `team=db, severity=critical` | `pagerduty-db` | `slack-db` |

#### Grouping and timings

| labels | receiver | setting | before | after |
| --- | --- | --- | --- | --- |
| `team=infra` (+5 similar) | `infra-slack` | `group_wait` | `30s` | `10s` |

#### New findings

| level | check | location | message |
| --- | --- | --- | --- |
| ✖ error | `undefined-receiver` | `alertmanager.yml:12:7` (`route.routes[1]`) | Route references receiver 'pager-team' which is not defined in receivers. Alerts matched here will be dropped. |
```

The summary line always comes first. The tables and finding sections only appear when
there is something in them, so an unchanged config gives just the heading and the summary.
`(+5 similar)` means five more label sets with exactly the same change. New findings point
into the new file and fixed ones into the old file. Each table stops after 50 rows with
`…and N more`, so a large config can't produce a huge comment. The output is deterministic,
and names from the config are escaped, so a receiver called `@team` doesn't ping anyone.
Exit codes are the same as for text output.

The [GitHub Action](github-action.md#routing-diff-as-a-pr-comment) posts this report as a
PR comment and keeps it up to date.

**Options:**

| flag | description |
|------|-------------|
| `--format json` | Machine-readable output |
| `--format markdown` | Compact report for a PR comment |
| `--no-routing` | Compare findings only |

### In CI (PR check)

```yaml
- name: Check for regressions
  run: |
    git show origin/main:alertmanager.yml > /tmp/main.yml
    amlint diff /tmp/main.yml alertmanager.yml
```

To get the routing diff as a PR comment, use the GitHub Action's
[`diff-base`](github-action.md#routing-diff-as-a-pr-comment) input.

## init

Generate a minimal valid `alertmanager.yml` to start from:

```bash
amlint init > alertmanager.yml
```

## tree

Print the route tree with matchers, receivers, and any routing-related issues inline — a fast way to see how alerts flow without opening the YAML:

```bash
amlint tree alertmanager.yml
cat alertmanager.yml | amlint tree -   # stdin
```

```
⚠  (root)  →  default-team  (groupby-ellipsis)
├── ⚠  (catch-all)  →  catch-all-team  (unreachable-route)
├── ✖  {severity=critical}  →  pager-team  (undefined-receiver)
└── ✖  {service=~auth([}  →  default-team  (bad-regex)

  4 routing issues flagged above — run 'amlint check alertmanager.yml' for full details.
  6 more issues outside routing (receivers, global, timing) — run 'amlint check alertmanager.yml' for full details.
```

Each node shows its matchers, the receiver it routes to, `[continue]` when set, and — if any routing check
(`undefined-receiver`, `unreachable-route`, `route-match-collision`, `bad-regex`, `groupby-ellipsis`, and similar)
fired on that node — an icon and the offending code(s). Findings outside the route tree (receiver config, global
settings, timing) are counted separately since they don't map to a specific node.

**Options:**

| flag | description |
|------|-------------|
| `--ignore CODE` | Skip these codes when annotating the tree (comma-separated or repeat) |

## route

Show where one alert goes: which route(s) it matches, which receiver(s) it is delivered
to, and the `group_by` and timings it ends up with after inheritance.

```bash
amlint route alertmanager.yml team=infra severity=critical alertname=DiskFull
```

```
  Labels  team=infra, severity=critical, alertname=DiskFull

  1. route → route.routes[0] → route.routes[0].routes[0]
    receiver          infra-pager          set here
    group_by          alertname, cluster   inherited from route
    group_wait        10s                  inherited from route.routes[0]
    group_interval    5m                   Alertmanager default
    repeat_interval   1h                   set here

  2. route → route.routes[1]
    receiver          pager                set here
    group_by          alertname, cluster   inherited from route
    group_wait        30s                  Alertmanager default
    group_interval    5m                   Alertmanager default
    repeat_interval   4h                   Alertmanager default

  Delivered to: infra-pager, pager
```

Here `route.routes[0]` has `continue: true`, so after the alert lands on its critical
child, `route.routes[1]` is checked too and matches as well. `mute_time_intervals` and
`active_time_intervals` are shown when the matched route sets them.

The simulation follows Alertmanager's own `dispatch/route.go`:

- Routes are checked depth-first, children in definition order. A child matches when all
  of its matchers match. The first matching child ends the search at its level, unless it
  has `continue: true`. A route none of whose children match is itself the match — a
  matched parent with a matched child does not also fire.
- `receiver`, `group_by`, `group_wait`, `group_interval` and `repeat_interval` are
  inherited from the parent, starting from Alertmanager's defaults (30s / 5m / 4h).
  `group_by: []` resets grouping to none, `group_by: ['...']` groups by every label.
  Time intervals are not inherited: each route has only its own.
- A label the alert doesn't have matches as the empty string, so `env!="prod"` matches an
  alert without `env`, and `env=""` matches it too.
- Regexes are fully anchored, like Alertmanager's `^(?:pattern)$`: `service=~"api"`
  matches `api`, not `api-gateway`. They follow Go RE2: `(?i)` flags, ASCII-only `\d`,
  `\w`, `\s` and POSIX classes such as `[[:alpha:]]` all behave as in Alertmanager.

Configs Alertmanager would refuse to load (a regex RE2 rejects, a malformed duration,
matchers on the root route) exit `2` with the route that causes it. So does a regex with
Unicode classes (`\pL`), which Python can't evaluate the way Go does — check those with
`amtool config routes test`.

`--format json` prints the receivers, and for every matched route its path, effective
settings, and `set_by`: which route set each value (a value missing from `set_by` is
Alertmanager's default).

`amtool config routes test` also takes a label set and prints the receivers it reaches.
`amlint route` also shows the effective `group_by` and timings and which route each one
comes from, and needs no Go binary.

### Inhibition

`--firing` adds another alert that is firing at the same time, as comma-separated
`label=value` pairs (quote a value that contains a comma: `summary="a, b"`). Repeat the flag
for more alerts. amlint then checks the config's `inhibit_rules` and shows the rule that
mutes the alert, if any:

```bash
amlint route alertmanager.yml alertname=DiskFull instance=db-1 team=infra \
  --firing 'alertname=NodeDown,instance=db-1'
```

```
  Labels  alertname=DiskFull, instance=db-1, team=infra
  Firing  alertname=NodeDown, instance=db-1

  route → route.routes[0]
    receiver          infra-slack          set here
    ...

  Inhibited by inhibit_rules[1]  ·  alertmanager.yml:31:5
     while firing: alertname=NodeDown, instance=db-1
  Routed to: infra-slack, but no notification is sent while it is inhibited.
```

The evaluation follows Alertmanager's `inhibit/inhibit.go`. The alert is inhibited when, for
some rule:

- the alert matches the rule's `target_matchers`,
- a firing alert matches its `source_matchers`, and
- every label in `equal` has the same value on both alerts. A label missing on both sides
  counts as equal, since Alertmanager compares missing labels as `""`.

If the alert matches the `source_matchers` as well, firing alerts that also match both sides
don't count. An alert therefore never inhibits itself, and alerts on both sides of a rule
don't inhibit each other. A firing alert still inhibits when it is inhibited or silenced
itself, as in Alertmanager. Rules are checked in order, and the first one that applies is
shown.

`source_matchers` / `target_matchers` and the older `source_match` / `source_match_re` /
`target_match` / `target_match_re` both work, with the same anchored Go RE2 regexes as
routing. Inhibition doesn't change routing: the alert still lands on the same routes, but
Alertmanager sends no notification for it. A rule amlint can't evaluate exits `2`, but only
when `--firing` is given. With `--format json` the output adds `firing`, `inhibited` and
`inhibited_by` (`rule`, `line`, `column`, and the `source` alert that matched).

**Options:**

| flag | description |
|------|-------------|
| `--firing 'label=value,...'` | Another alert firing at the same time; repeatable. Shows whether an inhibit rule mutes this alert |
| `--format json` | Machine-readable output |

## test

Unit tests for your routing tree: for each set of alert labels, assert which receivers the
alert reaches. Runs entirely against the local config file, with the same simulation as
[`route`](#route) — no live Alertmanager or Go toolchain needed:

```bash
amlint test alertmanager.yml routing-tests.yml
```

`routing-tests.yml` is a list of cases:

```yaml
- name: critical infra alerts page infra and the on-call
  labels: {team: infra, severity: critical}
  expect: [infra-pager, pager]

- name: warning infra alerts go to slack only
  labels: {team: infra, severity: warning}
  expect: [infra-slack]

- name: unrelated alerts aren't swallowed
  labels: {team: unknown}
  expect: [default]
```

`expect:` lists the receivers the alert must reach, in any order: no more, no fewer.
`name:` is optional. Each case sets exactly one of:

| key | asserts |
|-----|---------|
| `expect: [a, b]` | alert reaches exactly these receivers, in any order |
| `receiver: name` | alert reaches exactly this one receiver |
| `receivers: [a, b]` | alert reaches exactly these receivers, in this order (`continue: true` fan-out follows definition order) |
| `drop: true` | alert reaches no receiver at all |

Files from amlint 0.3.0, with the cases under a top-level `tests:` key, still work.

### Inhibition in tests

A case can list `firing:`, the label sets of other alerts firing at the same time, and
assert `inhibited: true` or `inhibited: false`. The alert is checked against the config's
`inhibit_rules` the same way [`route --firing`](#inhibition) does:

```yaml
- name: disk alerts are muted while their node is down
  labels: {alertname: DiskFull, instance: db-1, team: db}
  firing:
    - {alertname: NodeDown, instance: db-1}
  expect: [db-slack]       # where it is routed
  inhibited: true          # ...and that nothing is sent

- name: another node's outage doesn't mute it
  labels: {alertname: DiskFull, instance: db-1, team: db}
  firing:
    - {alertname: NodeDown, instance: db-2}
  expect: [db-slack]       # firing: without inhibited: expects inhibited: false
```

`inhibited:` goes alongside the receiver assertion, which still checks routing, or on its
own. With `firing:` and no `inhibited:`, the case expects the alert not to be inhibited. A
case that fails on inhibition names the rule. Had the second case listed `NodeDown` on
`db-1`:

```
  ✗  another node's outage doesn't mute it
     labels:    alertname=DiskFull, instance=db-1, team=db
     firing:    alertname=NodeDown, instance=db-1
     expected:  db-slack
     actual:    db-slack
     inhibited: expected no, actual yes
       by inhibit_rules[1]  ·  alertmanager.yml:31:5  ·  while firing: alertname=NodeDown, instance=db-1
     routes:    route.routes[2]
```

A failing case shows what differs, and which routes the alert matched. Had the second
case said `expect: [infra-slack, pager]`:

```
  ✓  critical infra alerts page infra and the on-call
  ✗  warning infra alerts go to slack only
     labels:    team=infra, severity=warning
     expected:  infra-slack, pager
     actual:    infra-slack
       - pager  expected, not delivered
     routes:    route.routes[0]

  1 failed  ·  1 passed
```

Exit code `1` if any case fails — same CI story as `check` — and `2` if the config can't
be simulated (see [`route`](#route)). `--format json` gives, per case, `passed`,
`expected`, `actual`, `missing`, `unexpected`, the matched `routes`, and `firing`,
`expected_inhibited`, `inhibited` and `inhibited_by`. `expected` is `null` for a case that
only asserts `inhibited:`.

### Coverage

`--coverage` lists the routes that no case reaches, so you can see what the suite doesn't
test:

```bash
amlint test alertmanager.yml routing-tests.yml --coverage
```

```
  ✓  critical infra alerts page infra and the on-call
  ✓  warning infra alerts go to slack only
  ✓  unrelated alerts aren't swallowed

  3 passed

  Route coverage  4 of 6 routes  ·  66.7%
  Not matched by any test case:
    route.routes[2]             alertmanager.yml:13:7    {team="db"}  →  db-slack
    route.routes[2].routes[0]   alertmanager.yml:16:11   {severity="critical"}  →  db-pager
```

A route is covered when a case's alert went through it. That includes the parents of the
route the alert lands on, and every branch of a `continue: true` fan-out. Cases that fail
still count. Malformed ones don't, since they aren't routed. The root route counts too.

A route that no label set can reach stays uncovered. Above, `route.routes[2].routes[0]`
can't be covered: `route.routes[1]` takes every critical alert first. Adding a case for
`{team: db, severity: critical}` shows it going to `pager`.

`--fail-under N` exits `1` when coverage is below N% (it turns on `--coverage` by itself),
so CI can require a minimum. The percentage is rounded to one decimal before the
comparison, and is only 100 when every route is covered. With `--format json` and coverage,
the output is an object instead of a list:

```json
{
  "tests": [ ... ],
  "coverage": {
    "routes": 6, "covered": 4, "percent": 66.7, "fail_under": 90.0,
    "uncovered": [
      {"path": "route.routes[2]", "line": 13, "column": 7},
      {"path": "route.routes[2].routes[0]", "line": 16, "column": 11}
    ]
  }
}
```

`amtool config routes test` checks one alert at a time from the command line; `amlint test`
runs a whole suite from a file, in CI, without the Go binary.

**Options:**

| flag | description |
|------|-------------|
| `--format json` | Machine-readable output |
| `--coverage` | List the routes no case reaches, with `file:line:col`, and the coverage percentage |
| `--fail-under N` | Exit 1 if route coverage is below N% (implies `--coverage`) |

## list

Print all check codes with their level and a one-line description:

```bash
amlint list
```

## explain

Show a detailed description, why it matters, and bad/good examples for any check code:

```bash
amlint explain undefined-receiver
amlint explain inhibit-no-equal
```

Useful for onboarding new team members or understanding an unfamiliar finding.

## --version

```bash
amlint --version
```

### GitHub annotations

`--format github` prints [workflow commands](https://docs.github.com/en/actions/reference/workflow-commands-for-github-actions)
that GitHub turns into annotations on the PR diff, with no SARIF upload and no Code
Scanning needed:

```yaml
- name: Lint Alertmanager config
  run: amlint check alertmanager.yml --strict --format github
```

```
::error file=alertmanager.yml,line=10,col=7,title=amlint undefined-receiver::Route references receiver 'pager-team' ... (route.routes[1])
::warning file=alertmanager.yml,line=8,col=7,title=amlint unreachable-route::Catch-all route ... (route.routes[0])
```

ERROR becomes `::error`, WARN `::warning` and INFO `::notice`. Paths are made relative to
`$GITHUB_WORKSPACE`, so annotations land on the right file even when you pass an absolute
path. Exit codes are the same as for text output.

### SARIF (GitHub Code Scanning)

SARIF output lets GitHub show findings as annotations directly on the PR diff, on the line
each finding points at — no plugins required:

```yaml
- name: Run amlint
  run: amlint check alertmanager.yml --format sarif > results.sarif

- name: Upload to GitHub Code Scanning
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

## CI integration

```yaml
# .github/workflows/alertmanager.yml
name: Lint Alertmanager config

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: pip install amlint
      - run: amlint check alertmanager.yml --strict
```
