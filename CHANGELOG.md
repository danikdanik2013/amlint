# Changelog

## [Unreleased]

### Added
- Python 3.14 and 3.15 support: CI runs the test suite on both, and the package lists them
  in its classifiers. No code changes were needed.
- A browser playground at https://danikdanik2013.github.io/amlint/playground/, published
  with the docs. Paste or edit an `alertmanager.yml` and the page shows, as you type:
  - findings, with the offending lines marked in the editor; click one to jump to its line,
    or open "why it matters and how to fix it" (the `amlint explain` text)
  - the route tree, with each route's matchers, receiver, settings and findings inline
  - "Route an alert": labels in, receivers out, with the path through the tree and the
    grouping and timings each route sets or inherits; alerts firing at the same time show
    whether an inhibit rule mutes it
  - a Diff tab with a second editor for the old config: new and fixed findings, and the
    alerts that are routed differently
  - a Rules tab for Prometheus alerting rules, which turns on the `--rules` checks

  amlint runs in the page through Pyodide (WebAssembly) in a Web Worker and calls the
  CLI's own `--format json` output, so the results match `amlint check`, `route` and
  `diff`. The config is never uploaded. "Share link" compresses the config into the URL
  fragment (after `#`, which browsers don't send to servers), after a warning that lists
  the lines that may hold secrets. Examples, mostly from `testdata/real-world/`, load from
  a menu. Works at phone width and follows the docs' light/dark toggle.
  `template-file-missing` isn't checked there, since the template files aren't in the
  browser.
- The docs workflow now also runs on pull requests. It builds the amlint wheel and the
  playground (`playground/build.py`), runs `mkdocs build --strict` and a headless
  Playwright smoke test that lints a fixture, routes an alert, diffs two configs, opens a
  share link and checks that no request carries the config. It deploys only from `main`.
- amlint reads the config formats Alertmanager is deployed with, not only a plain
  `alertmanager.yml`. The format is detected from the content; `--input-format`
  (`auto`, `alertmanager`, `secret`, `helm`, `mimir`, `alertmanagerconfig`) on `check`,
  `diff`, `tree`, `route` and `test` overrides it. Files can be multi-document streams and
  `kind: List` objects, so `kubectl kustomize ... | amlint check -` works; other Kubernetes
  objects in them are skipped. See the new "Kubernetes & Mimir" docs page.
  - Kubernetes `Secret` (and `ConfigMap`) holding `alertmanager.yaml`, in `stringData` or
    base64 `data`, including the operator's gzipped `alertmanager.yaml.gz`. The Secret's
    other keys are the template files mounted in `/etc/alertmanager/config/`; `templates:`
    entries there are checked against them instead of files on disk.
  - kube-prometheus-stack values: `alertmanager.config` is merged over the default config of
    chart 92.10.1 the way Helm merges values, which catches routes to the chart's `null`
    receiver after `receivers:` was replaced. `alertmanager.templateFiles` are the template
    files; `stringConfig` is used only with `tplConfig: true`, as in the chart.
  - Mimir and Cortex tenant configs: the `alertmanager_config` string and `template_files`.
  - prometheus-operator `AlertmanagerConfig` resources (`monitoring.coreos.com/v1alpha1` and
    `v1beta1`), converted and merged into the base config of the same input as
    prometheus-operator v0.94.1 does it: receiver and time interval names become
    `<namespace>/<name>/<name>`; each resource's route gets a `namespace` matcher and
    `continue: true` and goes before the base routes, in `namespace/name` order; inhibit
    rules get the `namespace` matcher on both sides; `alertmanagerConfigMatcherStrategy`
    (`OnNamespace`, `OnNamespaceExceptForAlertmanagerNamespace`, `None`) is read from an
    `Alertmanager` resource or Helm values in the input, or set with `--matcher-strategy`.
    Secret references (`apiURL`, `routingKey`, `urlSecret`, ...) count as set. Without a
    base config the operator's default (`null` receiver) is used, and findings about it are
    dropped. An `Alertmanager` with `spec.alertmanagerConfiguration` uses the named resource
    as the whole config. Findings that only the operator's namespace matcher or forced
    `continue: true` cause are dropped.
  - Findings keep pointing at `file:line:col` in the file that was read: inside a literal
    block (`alertmanager.yaml: |`) at the exact line and column, and `# amlint:` comments
    there work; for base64 or quoted strings at the key holding the config; for resources at
    the resource's own route, receiver or rule.
- `note:` lines on stderr say how a file was read when it matters: which parts of a Helm
  config come from the chart, a resource without a namespace or without a route, a field the
  API server drops for that API version.
- `lint()` takes `template_files=` (an `amlint.formats.BundledTemplates`) to check
  `templates:` against template files that ship with the config.
- The playground reads these formats too. Its route tree is drawn from the config as amlint
  reads it, with each route at its line in the pasted text (a team's route at its
  `AlertmanagerConfig`'s `spec.route`), and an input amlint can't read shows the same error
  there as under Findings; before, the tree parsed the paste as a plain `alertmanager.yml`
  and came out empty or wrong. New example: AlertmanagerConfig resources. Template files
  that ship in the input are checked by `template-file-missing` there.

### Changed
- A file with several YAML documents that are all plain Alertmanager configs now fails with
  "found 2 Alertmanager configs (lines 1, 3)" instead of a YAML error; it still exits 2.
  `--input-format alertmanager` keeps the old behaviour.

## [0.4.1] - 2026-10-16

### Added
- `amlint diff --format markdown` — a compact, deterministic report for a pull-request
  comment: a one-line summary, a table of label sets whose receivers changed (labels |
  before | after), changed `group_by` and timings, and new and fixed findings with
  `file:line:col`. Each table stops after 50 rows with "…and N more". Names from the config
  are escaped, so they can't add Markdown, HTML or @mentions to the comment.
- GitHub Action: `diff-base` input (e.g. `${{ github.event.pull_request.base.sha }}`). On
  `pull_request` events the action extracts the config at that ref, runs
  `amlint diff --format markdown` and creates or updates one PR comment, found by the
  `<!-- amlint-routing-diff -->` marker, through the REST API with `curl` (no third-party
  actions). It also writes the report to the job summary. A config that doesn't exist at the
  base is diffed as new. Needs `permissions: pull-requests: write` and the base commit in the
  checkout (`fetch-depth: 0`). On other events it does nothing. New `github-token` input.
- GitHub Action: `format` input for the check step, `github` (default) or `text`.
- `amlint test --coverage` lists the routes that no test case reaches, with their path
  (`route.routes[2].routes[0]`), `file:line:col` and matchers, plus the share of routes that
  are covered. A route counts as covered when an alert went through it, so the parents of the
  route it lands on count, and so does every branch of a `continue: true` fan-out.
  Unreachable routes, such as one behind a catch-all, show up here. `--fail-under N` exits 1
  when coverage is below N% and turns on `--coverage` by itself. With `--format json` the
  output becomes `{"tests": [...], "coverage": {...}}`, and `coverage` lists the uncovered
  routes with `line` / `column`. Without these flags the JSON is the same list as before.
- Inhibition simulation, mirroring Alertmanager's `inhibit/inhibit.go`. A test case can list
  `firing:` (other alerts firing at the same time) and assert `inhibited: true|false`,
  alongside a receiver assertion or on its own. With `firing:` and no `inhibited:`, the case
  expects the alert not to be inhibited. The alert is inhibited when a firing alert matches a
  rule's source matchers, the alert matches its target matchers, and every `equal` label has
  the same value on both (a label missing on both sides counts as equal). As in
  Alertmanager, an alert that matches both sides of a rule isn't inhibited by firing alerts
  that also match both sides, so it never inhibits itself. `source_matchers` /
  `target_matchers` and the older `source_match(_re)` / `target_match(_re)` both work, with
  the same anchored RE2 regexes as routing. A failing case names the rule that inhibited the
  alert, with its `file:line:col`. JSON results add `firing`, `expected_inhibited`,
  `inhibited` and `inhibited_by`.
- `amlint route --firing 'alertname=NodeDown,instance=a'` (repeatable) shows whether those
  alerts inhibit the one being routed, and which rule does it (`inhibit_rules[1]` and
  `file:line:col`). JSON output adds `firing`, `inhibited` and `inhibited_by`. An inhibit rule
  amlint can't evaluate exits 2, but only when `--firing` or `firing:` needs the rules.
- `testdata/real-world/`: configs modeled on kube-prometheus and Alertmanager's docs example;
  the test suite fails if any of them gets a WARN or ERROR
- `amlint check --rules GLOB` (repeatable) checks the config against Prometheus alerting
  rules: rule files (`groups:`) and prometheus-operator `PrometheusRule` manifests
  (`spec.groups:`, also several per file or in a `kind: List`). Recording rules are skipped.
  Each alert is described by its static labels, `alertname` plus the rule's `labels:`, with
  templated values unknown. Three new checks, which run only with `--rules`:
  - `route-unknown-alertname` (warn) — a route matches an `alertname` no rule produces, with
    a "did you mean" suggestion. Checks `=` and `!=`, each name of a plain `=~"A|B|C"` list,
    and any other regex that matches none of the alert names
  - `inhibit-unknown-alertname` (warn) — the same for inhibit rule source and target matchers
  - `alert-hits-default-receiver` (info) — based on their static labels, alerts match no
    child route and go to the root receiver. One finding per combination of the labels the
    routes match on (`severity=warning`, "no severity label"), so a deliberate fallback is
    one line. Alerts whose route depends on a templated label, or on a label no rule sets
    (so it comes from the series or `external_labels`), are skipped
- A `--rules` glob that matches no files, or a rule file that isn't valid YAML, exits 2. A
  config to check that looks like a rule file (an unquoted `--rules` glob the shell
  expanded) gets a warning
- `testdata/real-world/rules/`: alerting rules for the real-world configs; the test suite
  fails if `--rules` adds a WARN on them

### Changed
- The GitHub Action reports findings as PR annotations (`--format github`) by default,
  instead of the text report. `format: text` restores the old output; `sarif: true` still
  uploads SARIF.
- `amlint diff` treats an empty old file as no config: every finding in the new file is
  new, none is fixed (the empty file's `no-root-route` used to show up as fixed), and the
  routing section lists every label set's receivers as changed from `(none)`.

### Fixed
- The kube-prometheus-stack default config no longer fails `--strict` out of the box:
  - `inhibit-no-equal` skips rules with target matchers but no source matchers, the
    deliberate "always mute" idiom kube-prometheus uses for `InfoInhibitor`
  - `template-file-missing` no longer checks absolute paths, which point into the
    Alertmanager host or container rather than wherever amlint runs, and no longer claims
    Alertmanager fails to start: it starts and loads no templates from a path that matches
    nothing
- `empty-receiver` reports receivers named `null`, `blackhole`, `devnull`, `dev-null`,
  `discard` or `drop` as info, like the default receiver: they are deliberate drops

## [0.4.0] - 2026-10-16

First PyPI release since 0.2.0: it also ships everything under 0.2.1 and 0.3.0 below, which
were never published. Some configs that passed before get new findings: timing checks now use
inherited values and Alertmanager's defaults, regexes Go RE2 rejects are errors, and
`invalid-root-route` is new. `amlint test` now anchors regexes like Alertmanager, so test
suites that relied on substring matches will fail.

### Added
- Findings carry their source location. Text output shows `file:line:col` (clickable in
  terminals and editors), JSON output has `line` / `column`, and SARIF regions point at the
  real line instead of line 1. Paths that aren't in the file fall back to the nearest
  enclosing part that is. Works for stdin too.
- `--format github` — GitHub Actions workflow commands (`::error file=…,line=…,col=…,title=amlint
  <code>::…`, `::warning`, `::notice` for info), so findings annotate the PR diff without a
  SARIF upload. Paths are made relative to `$GITHUB_WORKSPACE`.
- Inline suppressions: `# amlint: disable=code1,code2` at the end of a finding's line or on
  the line above it, and `# amlint: disable-file=code` in the comment block at the top of the
  file. Unknown codes get the "did you mean" warning; malformed `# amlint:` comments and a
  `disable-file` below the top of the file are reported on stderr. Also applied by `diff`
  and `tree`.
- `invalid-root-route` (error) — root route has no `receiver`, or sets matchers, `continue`,
  `mute_time_intervals` or `active_time_intervals`; Alertmanager refuses to load such a config
  (also caught by `amtool check-config`, marked so in `amlint list`)
- Warning on stderr for unknown check codes in `--ignore`, `--only` and `severity:`, with a
  "did you mean" suggestion
- `amlint route <config> label=value ...` — simulate routing for one label set: prints the
  matched route path(s), the receiver(s), and the effective `group_by`, `group_wait`,
  `group_interval`, `repeat_interval` (and time intervals) with the route each value comes
  from; `--format json`
- `amlint test`: a tests file can now be a plain list of cases, and `expect: [a, b]` asserts
  the receivers in any order. A failing case shows which receivers are missing or unexpected
  and which routes matched; JSON output adds `missing`, `unexpected` and `routes`
- `amlint diff` compares routing behaviour, not just findings: it probes both configs with
  label sets built from their matchers (`=` / `!=` values, strings each regex matches, absent
  labels) and lists the ones whose receivers, `group_by`, timings or time intervals changed,
  e.g. `team=db, severity=critical: pagerduty-db → slack-db`. Identical changes are folded
  together; large configs are capped at 5000 label sets. Informational — the exit code still
  depends on findings only. `--no-routing` turns it off; JSON output gets a `routing` key

### Changed
- Routing simulation (`route`, `test`) moved to `amlint/routing.py`, replacing
  `amlint/simulate.py`, and now mirrors Alertmanager's `dispatch/route.go` more closely:
  `group_by` and timings are inherited, `group_by: []` resets grouping, `group_by: ['...']`
  groups by every label, time intervals are not inherited
- `amlint test` exits 2 when the config can't be simulated (a regex RE2 rejects, an
  unparseable matcher, a malformed duration, matchers on the root route, a `\pL`-style
  Unicode class) instead of guessing
- `empty-receiver` and `unused-receiver` report `where` as `receivers[<name>]` instead of
  `receivers`, `duplicate-receiver` as `receivers[<index>]` of the second definition, and
  `template-file-missing` as `templates[<index>]`, so each finding points at its own entry
  (and can be suppressed there). `unused-receiver` findings come out in name order.
- `amlint diff --format json` includes `line` and `column` for each finding.

### Fixed
- `amlint test` matches labels the way Alertmanager does: regexes are fully anchored like
  Alertmanager's `^(?:pattern)$` (`severity=~"crit"` no longer matches `critical` — suites
  relying on the old substring match will now fail, which is what Alertmanager does too),
  quoted values with escapes and multi-matcher strings (`{a="1", b="2"}`) go through the
  linter's matcher parser, a missing label compares as the empty string, and numeric label
  values (`code: 500`) compare as strings
- `amlint test` evaluates regexes with Go RE2 semantics: `(?i)` flags apply, `\d` / `\w` /
  `\s` are ASCII-only, POSIX classes (`[[:alpha:]]`) work
- `amlint diff` text output dropped the `[code]` of each finding (Rich read it as markup)
- Inhibition checks (`inhibit-no-equal`, `inhibit-same-match`, `circular-inhibition`) now read
  `source_matchers` / `target_matchers`; previously only `source_match` / `target_match` were seen
- `bad-regex` follows Go RE2 instead of Python `re`: lookarounds, backreferences, possessive
  quantifiers and atomic groups are now errors, while Go-only syntax (`\pL`, `\z`, `(?<name>)`,
  `[[:alpha:]]`, `(?U)`) no longer is; regexes in inhibit rules are checked too
- Quoted matcher values (`svc=~"a|b"`) are unquoted before checking, and multi-matcher strings
  (`{a="1", b="2"}`) are split
- `repeat-before-group` and `wait-exceeds-interval` account for values inherited from parent
  routes and Alertmanager defaults (30s / 5m / 4h)
- `empty-receiver` no longer fires for pushover, webex, jira, rocketchat, msteamsv2 and
  incidentio receivers; an empty list (`webhook_configs: []`) now counts as empty
- `undefined-time-interval` accepts intervals defined in legacy top-level `mute_time_intervals:`
- `route-match-collision` treats `match:` and `matchers:` spellings as equal, and no longer
  fires when the earlier sibling has `continue: true`
- `slack-no-api-url` accepts `global.slack_api_url_file`; `victorops-no-api-key` accepts
  `api_key_file`
- Durations are parsed strictly (`30sec` is no longer read as `30s`) and support `y`
- `diff` now runs the `template-file-missing` check
- No more tracebacks on malformed input: a non-mapping top level exits 2 with a message,
  non-mapping list entries are skipped
- `--version` fallback no longer reports a stale `0.1.2` when run from a source checkout

## [0.3.0] - 2026-08-16

### Added
- `amlint test <config> <tests-file>` — routing regression tests. Write a YAML file
  with a `tests:` list asserting label-set → expected receiver(s), run it as a
  suite in CI. Replicates Alertmanager's own dispatch algorithm (deepest-match-wins,
  `continue: true` fan-out in definition order, receiver inheritance) in pure Python
  — no Go binary or live Alertmanager needed.
  - Verified against real `amtool config routes test` output on 7 scenarios
    (deepest-match, no-child-match, continue fan-out with both orderings,
    receiver inheritance) before shipping — all matched exactly.
  - `amtool config routes test` already does single-alert interactive testing;
    this is the missing batch/CI piece, not a wholly new idea — see docs/index.md
    for the honest comparison.
  - Each test case asserts one of `receiver:` (single), `receivers:` (ordered list,
    for `continue` fan-out), or `drop: true` (no receiver reached).
  - `--format json` for machine-readable output.
- New module `amlint/simulate.py` with the reusable routing-simulation core.
- 12 new tests (7 unit tests on `simulate_route`, 8 CLI-level) — 119 total.
- docs/usage.md: new `## test` section with a full example.

## [0.2.1] - 2026-08-16

### Changed
- Corrected the "why not just amtool" comparison in README.md and docs/index.md: 17 of
  amlint's 30 checks (undefined-receiver, undefined-time-interval, bad-regex,
  groupby-ellipsis, no-root-route, duplicate-receiver, and the 11 "-no-x" integration
  required-field checks) duplicate validation `amtool check-config` already performs —
  confirmed empirically against amtool v0.33.1. Alertmanager refuses to start on all of
  these; it does not silently misbehave. Previous docs incorrectly claimed amtool misses
  several of them.
- Fixed misleading "why" text on 13 of those checks that claimed silent/partial failure
  ("alerts just vanish", "notifications fail") when the actual behavior is Alertmanager
  refusing to start entirely.
- `amlint list` now shows an `amtool` column marking which checks duplicate amtool's own
  validation vs. which are gaps amtool doesn't cover.
- `amlint explain <code>` now states, for every check, whether amtool also catches it.
- `docs/checks/index.md` overview table annotated the same way.

## [0.2.0] - 2026-08-16

### Added
- `action.yml` — official GitHub Action wrapping the CLI: install + run + optional SARIF
  upload to Code Scanning in one `uses:` step (`config-path`, `strict`, `ignore`, `only`,
  `version`, `sarif` inputs); docs at `docs/github-action.md`
- `amlint tree <file>` — new command, prints the route tree (matchers, receivers, `[continue]`)
  with routing-related findings (`undefined-receiver`, `unreachable-route`, `route-match-collision`,
  `bad-regex`, `groupby-ellipsis`, etc.) annotated inline on the node they apply to
- `--ignore` support on `tree` to quiet specific codes while browsing
- `sns-no-target` (error) — `sns_configs` missing all of `topic_arn`/`phone_number`/`target_arn`
  (exactly one destination is required; AWS rejects the publish call otherwise)
- Completes required-field coverage for every integration key amlint tracks (30 checks total)

## [0.1.12] - 2026-08-16

### Added
- `victorops-no-api-key` (error) — `victorops_configs` without `api_key` and no global fallback
- `wechat-no-corp-id` (error) — `wechat_configs` without `corp_id` and no global fallback
- Completes required-field coverage for all `_INTEGRATION_KEYS` integrations (29 checks total)

## [0.1.11] - 2026-08-16

### Added
- `telegram-no-bot-token` (error) — `telegram_configs` without `bot_token`/`bot_token_file`
- `discord-no-webhook-url` (error) — `discord_configs` without `webhook_url`/`webhook_url_file`
- 2 new receiver-integration checks bring the total to 25 → 27


## [0.1.10] - 2026-06-23

### Added
- `template-file-missing` (error/warn) — `templates:` references a file that doesn't exist;
  literal paths → error, globs with no matches → warn
- `--only CODE,CODE` — run only findings with the specified codes (inverse of `--ignore`);
  works on both `check` and `diff` subcommands
- `--exit-zero` — always exit 0 regardless of findings; for informational CI steps
- Python 3.13 added to CI matrix and PyPI classifiers

## [0.1.9] - 2026-06-23

### Added
- `global-resolve-timeout-missing` (info) — `global.resolve_timeout` not set; Alertmanager defaults to 5m silently
- `--format sarif` — GitHub Code Scanning SARIF 2.1.0 output; upload with `github/codeql-action/upload-sarif` to get findings as PR annotations

## [0.1.8] - 2026-06-23

### Added
- `route-match-collision` (warn) — two sibling routes with identical matchers; the second never receives alerts
- `amlint list` — prints all 23 check codes with level and one-line description
- docs: routing.md new section, usage.md `list` command, index count 22→23

## [0.1.7] - 2026-06-23

### Added
- `pyproject.toml [tool.amlint]` support — configure ignore/strict/severity without a separate file
- `.amlint.yml` still takes priority if both files exist
- Python 3.11+ uses stdlib `tomllib`; 3.9/3.10 use `tomli` (optional install)

### Fixed
- ruff E501 in `explains.py` and `linter.py` — all long lines split

## [0.1.6] - 2026-06-23

### Added
- 2 new integration checks:
  - `opsgenie-no-api-key` (error) — opsgenie_configs without `api_key` and no global
  - `msteams-no-webhook-url` (error) — msteams_configs without `webhook_url`
- `amlint explain <code>` — detailed description, why it matters, bad/good YAML examples for all 22 checks
- Updated docs: checks table (17→22), usage page with `--ignore` and `.amlint.yml` docs, `explain` command

## [0.1.5] - 2026-06-23

### Added
- 3 new checks for missing required integration fields:
  - `webhook-no-url` (error) — webhook_configs without `url` or `url_file`
  - `pagerduty-no-routing-key` (error) — pagerduty_configs without `routing_key`/`service_key`
  - `slack-no-api-url` (error) — slack_configs without `api_url` and no `global.slack_api_url`
- `.amlint.yml` `severity:` map — override the level of any check per-project:
  ```yaml
  severity:
    empty-receiver: info   # downgrade
    unused-receiver: error # upgrade
  ```

## [0.1.4] - 2026-06-23

### Added
- `--ignore CODE,CODE` flag — skip specific checks per invocation
- `.amlint.yml` project config file — set `ignore:` list and `strict: true` once instead of repeating flags
- Config file and CLI `--ignore` merge (union of both)

## [0.1.3] - 2026-06-22

### Changed
- Rich-based output: icons (✖ ⚠ ℹ), word wrap, separator line, colored summary

## [0.1.1] - 2026-06-22

### Added
- `amlint --version` flag
- `amlint diff old.yml new.yml` — shows fixed vs new findings between two configs,
  exits non-zero if new regressions appeared (useful in PR CI)
- `dependabot.yml` — auto-updates GitHub Actions and pip dependencies weekly
- `SECURITY.md` — vulnerability reporting policy

## [0.1.0] - 2026-06-22

### Added

**13 semantic checks:**

| code | level | what it catches |
|------|-------|-----------------|
| `undefined-receiver` | error | route references a receiver that doesn't exist |
| `bad-regex` | error | `match_re` / `matchers` regex fails to compile |
| `no-root-route` | error | no root `route` defined |
| `duplicate-receiver` | error | receiver name defined more than once |
| `undefined-time-interval` | error | `mute_time_intervals` / `active_time_intervals` references unknown interval |
| `inhibit-no-equal` | warn | inhibition without `equal` silences too broadly |
| `unreachable-route` | warn | catch-all hides subsequent sibling routes |
| `groupby-ellipsis` | warn | `...` mixed with explicit labels in `group_by` |
| `empty-receiver` | warn/info | receiver has no integration configured |
| `repeat-before-group` | warn | `repeat_interval` shorter than `group_interval` |
| `circular-inhibition` | warn | two inhibition rules that silence each other |
| `wait-exceeds-interval` | warn | `group_wait` longer than `group_interval` |
| `inhibit-same-match` | info | source and target match the same label value |
| `unused-receiver` | info | receiver defined but not used in any route |
| `useless-continue` | info | `continue:true` on the last sibling route |

**CLI:**
- Multiple files: `amlint check prod.yml staging.yml`
- Stdin: `cat alertmanager.yml | amlint check -`
- `--format json` for integrations
- `--strict` to treat WARN as failure

**Integrations:**
- Pre-commit hook via `.pre-commit-hooks.yaml`
- GitHub Actions CI on Python 3.9–3.12
