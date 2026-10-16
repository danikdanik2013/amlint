# amlint

[![CI](https://github.com/danikdanik2013/amlint/actions/workflows/ci.yml/badge.svg)](https://github.com/danikdanik2013/amlint/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-github.io-blue)](https://danikdanik2013.github.io/amlint)
[![Coverage](https://coveralls.io/repos/github/danikdanik2013/amlint/badge.svg?branch=main)](https://coveralls.io/github/danikdanik2013/amlint)
[![PyPI](https://img.shields.io/pypi/v/amlint)](https://pypi.org/project/amlint)
[![Python](https://img.shields.io/pypi/pyversions/amlint)](https://pypi.org/project/amlint)
[![Docker](https://ghcr-badge.egpl.dev/danikdanik2013/amlint/size?tag=latest&label=docker)](https://github.com/danikdanik2013/amlint/pkgs/container/amlint)

Semantic linter for Prometheus Alertmanager configs.

Alertmanager's own `amtool check-config` already validates most schema-level mistakes —
undefined receivers, missing integration fields, bad regex — and refuses to start over them.
**amlint covers that same ground in one dependency-free CLI with JSON/SARIF/diff output,
plus 13 checks that amtool doesn't do: bugs that are structurally valid, so Alertmanager
starts and runs, and just silently doesn't do what you meant.**

## Why

Alertmanager configs are YAML routing trees with inhibition rules and receivers. The
checks that actually earn their keep are the ones amtool can't do, because they're not
schema violations — they're valid configs that behave wrong:

- catch-all branch before specific ones → specific branches **are unreachable** (config loads fine)
- inhibition without `equal` → silences unrelated alerts, config loads fine, you think it's quiet
- two sibling routes with identical matchers → the second **never fires**, no error anywhere
- `group_wait` longer than `group_interval`, or `repeat_interval` shorter than `group_interval` → timing that quietly doesn't do what the numbers suggest
- receiver defined but never routed to, or routed to but has no integration configured

None of this fails `amtool check-config` or stops Alertmanager from starting. Run
`amlint list` to see which of the 31 checks are these vs. which duplicate amtool's own
validation (included for convenience — one tool, one output format, no Go binary needed).

## Install

```bash
pip install amlint
```

Or with Docker (no Python required):

```bash
docker run --rm -v $(pwd):/cfg ghcr.io/danikdanik2013/amlint check /cfg/alertmanager.yml
```

## Usage

```bash
amlint check alertmanager.yml
amlint check prod.yml staging.yml           # multiple files
cat alertmanager.yml | amlint check -       # stdin
amlint check alertmanager.yml --strict      # WARN also exits non-zero
amlint check alertmanager.yml --format json
amlint check alertmanager.yml --format sarif > results.sarif  # GitHub Code Scanning
amlint check alertmanager.yml --format github  # PR annotations straight from a workflow step
amlint check alertmanager.yml --ignore empty-receiver,unused-receiver
amlint check alertmanager.yml --only undefined-receiver,bad-regex  # run only these checks
amlint check alertmanager.yml --exit-zero   # always exits 0, just show findings
amlint check alertmanager.yml --rules 'rules/*.yml'  # also check against Prometheus alerting rules
amlint diff old.yml new.yml                 # what changed: findings, and alerts routed differently
amlint diff old.yml new.yml --format markdown  # the same as a compact PR comment
amlint tree alertmanager.yml                # visualize the route tree, issues inline
amlint route alertmanager.yml team=infra severity=critical  # where does this alert go?
amlint route alertmanager.yml alertname=DiskFull --firing alertname=NodeDown  # inhibited?
amlint test alertmanager.yml tests.yml      # routing unit tests: labels -> expected receivers
amlint test alertmanager.yml tests.yml --fail-under 90  # and fail if tests reach < 90% of routes
amlint init > alertmanager.yml              # generate minimal valid config
amlint list                                 # all check codes with level + description
amlint explain undefined-receiver           # detailed explanation + examples
```

**Project config** — `.amlint.yml` or `pyproject.toml [tool.amlint]`:

```yaml
# .amlint.yml
ignore:
  - empty-receiver
strict: true
severity:
  unused-receiver: error   # upgrade info → error
```

```toml
# pyproject.toml
[tool.amlint]
ignore = ["empty-receiver"]
strict = true
```

**Inline suppressions** — silence a check for one spot, with the reason next to it:

```yaml
# amlint: disable-file=global-resolve-timeout-missing     ← top of file: whole file

receivers:
  - name: blackhole   # amlint: disable=empty-receiver     ← this line
  # amlint: disable=unused-receiver                       ← or the line below
  - name: spare
```

**Shell completions (bash/zsh):**
```bash
pip install "amlint[completions]"
eval "$(register-python-argcomplete amlint)"  # add to ~/.zshrc or ~/.bashrc
```

Example output:

```
  ✖  ERROR  [undefined-receiver]  ·  alertmanager.yml:10:7  ·  route.routes[1]
     Route references receiver 'pager-team' which is not defined in receivers. Alerts matched here will be dropped.

  ⚠  WARN   [unreachable-route]  ·  alertmanager.yml:8:7  ·  route.routes[0]
     Catch-all route (no matchers) with continue:false will intercept all alerts — 2 subsequent sibling(s) are unreachable.

  ✖ 2 errors  ·  ⚠ 5 warnings  ·  ℹ 3 info
```

Every finding carries `file:line:col`, so terminals and editors can jump straight to it.

Exit code `1` on ERROR — ready for CI. `--strict` makes WARN block too.

## Routing tests

`amlint route` shows where one alert goes: the routes it matches, its receivers, and the
`group_by` and timings it inherits. `amlint test` turns that into unit tests for CI:

```yaml
# tests.yml
- name: critical infra alerts page infra and the on-call
  labels: {team: infra, severity: critical}
  expect: [infra-pager, pager]   # any order
- labels: {team: unknown}
  expect: [default]
- name: disk alerts are muted while their node is down
  labels: {alertname: DiskFull, instance: db-1}
  firing:                        # other alerts firing at the same time
    - {alertname: NodeDown, instance: db-1}
  inhibited: true
```

```bash
amlint test alertmanager.yml tests.yml              # exit 1 on any failing case, with a diff
amlint test alertmanager.yml tests.yml --coverage   # also list routes no case reaches, file:line
amlint test alertmanager.yml tests.yml --fail-under 90   # exit 1 below 90% route coverage
```

The simulation follows Alertmanager's own routing code: `continue: true` fan-out,
inherited settings, fully anchored Go RE2 regexes, missing labels matching as `""`.
Inhibition follows Alertmanager's `inhibit_rules` evaluation, `equal` labels and the rule
that an alert matching both sides of a rule doesn't inhibit itself. `amlint route --firing`
shows which rule mutes an alert.
`amlint diff` uses it too: after the findings it lists the label sets that two configs
route differently, e.g. `team=db, severity=critical: pagerduty-db → slack-db`.
Details in [docs/usage.md](docs/usage.md#route).

## Checking against Prometheus rules

Pass your alerting rules and amlint checks the config against the alerts that actually
exist: a route or inhibit rule on an `alertname` no rule produces (with a "did you mean"),
and alerts that, by their static labels, match no child route and land on the root
receiver:

```bash
amlint check alertmanager.yml --rules 'rules/*.yml'   # quote the glob; repeat for more
```

```
  ⚠  WARN   [route-unknown-alertname]  ·  alertmanager.yml:4:7  ·  route.routes[0]
     Route matches alertname 'HighCpuUsage', which no alerting rule in the --rules files
     produces — did you mean 'HighCPUUsage'? Unless such alerts come from another source,
     this branch is dead.
```

Prometheus rule files and prometheus-operator `PrometheusRule` manifests both work. At
runtime alerts also carry labels from their PromQL series and `external_labels`, so amlint
goes by the rule's static labels only and skips anything that depends on a label it can't
know. Details in [docs/checks/rules.md](docs/checks/rules.md).

## CI example

```yaml
# .github/workflows/lint.yml
- name: Lint Alertmanager config
  run: amlint check alertmanager.yml --strict --format github  # findings annotate the PR diff
```

### As a GitHub Action

```yaml
- uses: actions/checkout@v7
- uses: danikdanik2013/amlint@v0.4.0
  with:
    config-path: alertmanager.yml
    strict: true
```

No `pip install` step needed. Findings annotate the PR diff; `--ignore`/`--only` work via
inputs, and SARIF upload to Code Scanning is optional.

Add `diff-base: ${{ github.event.pull_request.base.sha }}` and the action also posts the
routing diff against the PR's base as one comment: which label sets now reach other
receivers, changed `group_by` and timings, and new and fixed findings. It updates that
comment on every push. It needs `permissions: pull-requests: write` and the base commit in
the checkout (`fetch-depth: 0`); see the [GitHub Action docs](https://danikdanik2013.github.io/amlint/github-action/).

## Checks

| code | level | what it catches |
|------|-------|-----------------|
| `undefined-receiver` | error | route references a receiver that doesn't exist |
| `invalid-root-route` | error | root route has no `receiver`, or has matchers / `continue` / time intervals |
| `bad-regex` | error | regex fails to compile or uses syntax Go RE2 rejects (lookarounds, backrefs) |
| `no-root-route` | error | no root `route` defined |
| `duplicate-receiver` | error | receiver name defined more than once |
| `undefined-time-interval` | error | `mute_time_intervals` / `active_time_intervals` references unknown interval |
| `email-no-smarthost` | error | `email_configs` without `smarthost` and no global SMTP |
| `webhook-no-url` | error | `webhook_configs` without `url` or `url_file` |
| `pagerduty-no-routing-key` | error | `pagerduty_configs` without `routing_key` |
| `slack-no-api-url` | error | `slack_configs` without `api_url` and no global |
| `opsgenie-no-api-key` | error | `opsgenie_configs` without `api_key` and no global |
| `msteams-no-webhook-url` | error | `msteams_configs` without `webhook_url` |
| `telegram-no-bot-token` | error | `telegram_configs` without `bot_token` |
| `discord-no-webhook-url` | error | `discord_configs` without `webhook_url` |
| `victorops-no-api-key` | error | `victorops_configs` without `api_key` and no global |
| `wechat-no-corp-id` | error | `wechat_configs` without `corp_id` and no global |
| `sns-no-target` | error | `sns_configs` missing `topic_arn`/`phone_number`/`target_arn` |
| `template-file-missing` | error/warn | `templates:` references a file that doesn't exist |
| `inhibit-no-equal` | warn | inhibition without `equal` silences too broadly |
| `unreachable-route` | warn | catch-all hides subsequent sibling routes |
| `groupby-ellipsis` | warn | `...` mixed with explicit labels in `group_by` |
| `repeat-before-group` | warn | `repeat_interval` shorter than `group_interval` |
| `circular-inhibition` | warn | two inhibition rules that silence each other |
| `wait-exceeds-interval` | warn | `group_wait` longer than `group_interval` |
| `empty-receiver` | warn/info | receiver has no integration configured — alerts will be dropped |
| `unused-receiver` | info | receiver defined but not used in any route |
| `inhibit-same-match` | info | source and target match the same label value |
| `useless-continue` | info | `continue:true` on the last sibling route has no effect |
| `deep-nesting` | info | route tree deeper than 5 levels |
| `global-resolve-timeout-missing` | info | `global.resolve_timeout` not set; defaults to 5m |
| `route-match-collision` | warn | two sibling routes with identical matchers |
| `route-unknown-alertname` | warn | with `--rules`: route matches an `alertname` no alerting rule produces |
| `inhibit-unknown-alertname` | warn | with `--rules`: inhibit rule matches an `alertname` no alerting rule produces |
| `alert-hits-default-receiver` | info | with `--rules`: by their static labels, alerts match no child route (grouped by routing labels) |

Run `amlint explain <code>` for detailed description and examples of any check.

## Tests

```bash
python3 -m pytest test_linter.py -v
```

## License

MIT
