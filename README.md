# amlint

[![CI](https://github.com/danikdanik2013/amlint/actions/workflows/ci.yml/badge.svg)](https://github.com/danikdanik2013/amlint/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-github.io-blue)](https://danikdanik2013.github.io/amlint)
[![Coverage](https://coveralls.io/repos/github/danikdanik2013/amlint/badge.svg?branch=main)](https://coveralls.io/github/danikdanik2013/amlint)
[![PyPI](https://img.shields.io/pypi/v/amlint)](https://pypi.org/project/amlint)
[![Python](https://img.shields.io/pypi/pyversions/amlint)](https://pypi.org/project/amlint)
[![Docker](https://ghcr-badge.egpl.dev/danikdanik2013/amlint/size?tag=latest&label=docker)](https://github.com/danikdanik2013/amlint/pkgs/container/amlint)
[![Verified against Alertmanager](https://github.com/danikdanik2013/amlint/actions/workflows/differential.yml/badge.svg)](https://danikdanik2013.github.io/amlint/fidelity/)

**A linter for Prometheus Alertmanager configs that finds the bugs nothing else complains about.**

`amtool check-config` tells you whether your config is *valid*. amlint tells you whether it
*does what you meant*: whether every alert reaches someone, whether a route can ever match,
whether an inhibit rule mutes far more than you think. These configs load fine, Alertmanager
runs without a single warning, and alerts quietly go to the wrong place, or nowhere.

**[Try it in your browser →](https://danikdanik2013.github.io/amlint/playground/)**
Paste a config and see the problems, the route tree and where any alert would go. It runs
entirely in the page, so your config never leaves your machine.

## What it looks like

```yaml
# alertmanager.yml
route:
  receiver: default
  routes:
    - receiver: slack-all            # no matchers: catches every alert
    - receiver: pager
      matchers: [severity="critical"]
inhibit_rules:
  - source_matchers: [severity="critical"]
    target_matchers: [severity="warning"]   # no `equal`
receivers:
  - name: default
    webhook_configs: [{url: "http://hooks.internal/default"}]
  - name: slack-all
    slack_configs: [{api_url: "https://hooks.slack.com/x", channel: "#alerts"}]
```

```
$ amlint check alertmanager.yml

  ✖  ERROR  [undefined-receiver]  ·  alertmanager.yml:5:7  ·  route.routes[1]
     Route references receiver 'pager' which is not defined in receivers.
     Alertmanager refuses to load this config.

  ⚠  WARN   [inhibit-no-equal]  ·  alertmanager.yml:8:5  ·  inhibit_rules[0]
     Inhibition rule has no 'equal' field: it will silence alerts across
     unrelated firing sources (no shared label binding them). Almost always a mistake.

  ⚠  WARN   [unreachable-route]  ·  alertmanager.yml:4:7  ·  route.routes[0]
     Catch-all route (no matchers) with continue:false will intercept all alerts
     — 1 subsequent sibling(s) are unreachable.

  ℹ  INFO   [global-resolve-timeout-missing]  ·  alertmanager.yml:1:1  ·  global
     global.resolve_timeout is not set, so Alertmanager uses 5m. It only applies
     to alerts sent without an end time; Prometheus always sets one.

  ✖ 1 error  ·  ⚠ 2 warnings  ·  ℹ 1 info
```

In plain words: the `pager` receiver doesn't exist, so Alertmanager won't even load this.
Fix that, and the first route still catches every alert, so the critical route is never
reached. And one critical alert anywhere
mutes every warning everywhere, because the inhibit rule doesn't say which alerts belong
together. amtool would flag only the first problem.

Every finding points to `file:line:col`. The exit code is `1` on any error, so CI fails.

## Install

```bash
pip install amlint
```

Other ways:

```bash
# Docker, no Python needed
docker run --rm -v "$PWD":/cfg ghcr.io/danikdanik2013/amlint check /cfg/alertmanager.yml

# Homebrew
brew tap danikdanik2013/amlint https://github.com/danikdanik2013/amlint
brew install amlint
```

Or don't install anything and use the
[playground](https://danikdanik2013.github.io/amlint/playground/).

**Using an AI coding assistant?** In Claude Code, run
`/plugin marketplace add danikdanik2013/amlint` and then `/plugin install amlint@amlint`. Claude
will then use amlint to lint your configs and answer "where does this alert go?". Other
assistants can read [llms.txt](https://danikdanik2013.github.io/amlint/llms.txt).

## What you can do with it

| I want to… | Run |
|---|---|
| Find problems in a config | `amlint check alertmanager.yml` |
| Fail CI on warnings too, not only errors | `amlint check alertmanager.yml --strict` |
| See where an alert would go | `amlint route alertmanager.yml team=db severity=critical` |
| Check whether an alert gets muted | `amlint route alertmanager.yml alertname=DiskFull --firing alertname=NodeDown` |
| Write unit tests for routing | `amlint test alertmanager.yml tests.yml` |
| See what a change does to routing | `amlint diff old.yml new.yml` |
| Look at the route tree | `amlint tree alertmanager.yml` |
| Draw the route tree as a Mermaid diagram | `amlint tree alertmanager.yml --format mermaid` |
| Catch routes for alerts that don't exist | `amlint check alertmanager.yml --rules 'rules/*.yml'` |
| Lint what you deploy to Kubernetes | `amlint check alertmanager-secret.yaml` or `kubectl kustomize deploy/ \| amlint check -` |
| Learn what a check means and how to fix it | `amlint explain shadowed-route` |
| See every check | `amlint list` |

Full guide: [usage docs](https://danikdanik2013.github.io/amlint/usage/).

## What it catches

38 checks. 18 of them catch things amtool doesn't, because the config is valid and simply
behaves differently than you expect:

- **Alerts that reach nobody.** A route points at a receiver that has no integration, or a
  catch-all route sits above more specific ones so they never match.
- **Routes that can never match.** An earlier sibling route catches every alert first. This
  can be an exact duplicate (`route-match-collision`) or a broader one, such as
  `severity=~"warning|critical"` before `severity="critical"` (`shadowed-route`).
- **Inhibit rules that mute too much.** No `equal` labels, rules that mute each other, or
  source and target that match the same alert.
- **Timings that contradict themselves.** `group_wait` longer than `group_interval`, or
  `repeat_interval` shorter than `group_interval`.
- **Matchers Alertmanager reads differently than you do.** For example, `code=~[0-9]{3}`
  without quotes loses its closing brace.
- **Routes for alerts that don't exist** (with `--rules`). A typo like `HighCpuUsage`
  vs `HighCPUUsage` silently kills a branch.

The other 20 checks repeat what amtool already rejects: undefined receivers, missing
webhook URLs or API keys, bad regexes. They're there so one tool gives you one report, in
one format.

[All checks with examples →](https://danikdanik2013.github.io/amlint/checks/)

## Test your routing like code

Write down which alerts should go where, and let CI tell you when that stops being true:

```yaml
# tests.yml
- name: critical infra alerts page infra and the on-call
  labels: {team: infra, severity: critical}
  expect: [infra-pager, pager]        # any order
- name: unknown teams fall back to the default
  labels: {team: unknown}
  expect: [default]
- name: disk alerts are muted while their node is down
  labels: {alertname: DiskFull, instance: db-1}
  firing:                             # other alerts firing at the same time
    - {alertname: NodeDown, instance: db-1}
  inhibited: true
```

```bash
amlint test alertmanager.yml tests.yml                  # fails with a diff if a case breaks
amlint test alertmanager.yml tests.yml --fail-under 90  # and if tests reach < 90% of routes
```

`amlint diff old.yml new.yml` works the other way round. You don't write any cases. It
compares two versions and lists every kind of alert that now goes somewhere else, for
example `team=db, severity=critical: pagerduty-db → slack-db`.

### Can I trust the simulation?

amlint simulates routing and inhibition in Python. To prove it behaves like the real
thing, a [CI job](https://danikdanik2013.github.io/amlint/fidelity/) generates thousands of
configs and alerts and runs them through `amtool` and a running Alertmanager, **v0.32.2,
v0.33.1 and v0.34.1**. It checks that both give the same receivers, the same inhibitions
and reject the same configs. The few known differences are listed on that page.

## Use it in CI

**GitHub Action.** Findings show up as annotations on the PR diff:

```yaml
- uses: actions/checkout@v7
  with:
    fetch-depth: 0
- uses: danikdanik2013/amlint@v0.5.0
  with:
    config-path: alertmanager.yml
    strict: true
    diff-base: ${{ github.event.pull_request.base.sha }}   # optional: post a routing diff comment
```

With `diff-base` the action also posts one PR comment, updated on every push. It shows which
alerts now reach different receivers and which findings are new or fixed. It needs
`permissions: pull-requests: write`.
[Action docs →](https://danikdanik2013.github.io/amlint/github-action/)

**Anywhere else:** `pip install amlint && amlint check alertmanager.yml --strict`. Use
`--format github`, `--format sarif` (GitHub Code Scanning), `--format json`, or
`amlint diff --format markdown` for a PR comment.

**pre-commit:**

```yaml
repos:
  - repo: https://github.com/danikdanik2013/amlint
    rev: v0.5.0
    hooks:
      - id: amlint        # runs on files ending in alertmanager.yml or alertmanager.yaml
```

## Configure it

Turn checks off or change their severity for the whole project, in `.amlint.yml` or
`pyproject.toml`:

```yaml
# .amlint.yml
ignore: [empty-receiver]
strict: true
severity:
  unused-receiver: error
```

Or silence one spot, with the reason right next to it:

```yaml
# amlint: disable-file=global-resolve-timeout-missing   ← whole file

receivers:
  - name: blackhole   # amlint: disable=empty-receiver   ← this line
```

## Kubernetes, Helm and Mimir

Point amlint at the file you actually deploy. It detects the format and finds the
Alertmanager config inside:

- a Kubernetes **Secret** or **ConfigMap** with `alertmanager.yaml`
- **kube-prometheus-stack** Helm values (`alertmanager.config`)
- a **Mimir** or **Cortex** tenant config (`alertmanager_config`)
- prometheus-operator **AlertmanagerConfig** resources, merged the way the operator does it

Line numbers still point into your file.
[Details →](https://danikdanik2013.github.io/amlint/kubernetes/)

## How is this different from amtool?

| | amtool | amlint |
|---|:-:|:-:|
| Rejects invalid configs (undefined receiver, bad regex, missing API key) | ✅ | ✅ |
| Finds valid configs that misroute or over-mute alerts | — | ✅ |
| Where does one alert go? | ✅ | ✅ |
| A file of routing tests, run in CI, with route coverage | — | ✅ |
| Inhibition: is this alert muted while that one fires? | — | ✅ |
| What does this change do to routing? | — | ✅ |
| Kubernetes / Helm / Mimir / AlertmanagerConfig input | — | ✅ |
| PR annotations, SARIF, JSON, Markdown | — | ✅ |
| Needs a Go binary | ✅ | — |

They work well together. If you already run `amtool check-config` in CI, keep it, and add
amlint for everything after "is it valid?".

## Links

- [Documentation](https://danikdanik2013.github.io/amlint/)
- [Playground](https://danikdanik2013.github.io/amlint/playground/)
- [Changelog](https://github.com/danikdanik2013/amlint/blob/main/CHANGELOG.md)
- [Contributing](https://github.com/danikdanik2013/amlint/blob/main/CONTRIBUTING.md), including how to run the tests
- License: MIT
