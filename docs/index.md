# amlint

**A linter for Prometheus Alertmanager configs that finds the bugs nothing else complains about.**

`amtool check-config` tells you whether your config is *valid*. amlint tells you whether it
*does what you meant*. Does every alert reach someone? Can every route actually match? Does an
inhibit rule mute far more than you think? Configs with these bugs load fine and Alertmanager
runs without a warning, while alerts quietly go to the wrong place, or nowhere.

[Try it in your browser](playground/index.md){ .md-button .md-button--primary }
[Install](install.md){ .md-button }

The playground runs amlint inside the page, so your config never leaves your machine.

## In 30 seconds

```bash
pip install amlint
amlint check alertmanager.yml
```

```
  ✖  ERROR  [undefined-receiver]  ·  alertmanager.yml:7:7  ·  route.routes[1]
     Route references receiver 'pager' which is not defined in receivers.
     Alertmanager refuses to load this config.

  ⚠  WARN   [unreachable-route]  ·  alertmanager.yml:6:7  ·  route.routes[0]
     Catch-all route (no matchers) with continue:false will intercept all alerts
     — 1 subsequent sibling(s) are unreachable.

  ✖ 1 error  ·  ⚠ 1 warning
```

Each finding names the check, the exact spot in your file and what will go wrong. The exit
code is `1` on any error, so CI fails. Add `--strict` to fail on warnings too. To see how to
fix a finding, run `amlint explain <check>`, for example `amlint explain unreachable-route`.

## What you can do with it

| I want to… | Run | Read more |
|---|---|---|
| Find problems in a config | `amlint check alertmanager.yml` | [Usage](usage.md) |
| See where an alert would go | `amlint route alertmanager.yml team=db severity=critical` | [Usage](usage.md#route) |
| Check whether an alert gets muted | `amlint route … --firing alertname=NodeDown` | [Usage](usage.md#route) |
| Write unit tests for routing | `amlint test alertmanager.yml tests.yml` | [Usage](usage.md) |
| See what a change does to routing | `amlint diff old.yml new.yml` | [Usage](usage.md) |
| Catch routes for alerts that don't exist | `amlint check … --rules 'rules/*.yml'` | [Prometheus rules](checks/rules.md) |
| Lint a Secret, Helm values or Mimir config | `amlint check alertmanager-secret.yaml` | [Kubernetes & Mimir](kubernetes.md) |
| Get findings and a routing diff on every PR | the GitHub Action | [GitHub Action](github-action.md) |
| See findings in your editor as you type | `amlint lsp`, started by the editor | [Editors](editors.md) |

## What it catches

There are 48 checks. 26 of them find configs that amtool accepts but that don't behave the
way you expect, or use syntax Alertmanager has deprecated:

- **Alerts that reach nobody.** A route points at a receiver with no integration, or a
  catch-all route sits above more specific ones.
- **Routes that can never match.** An earlier sibling route catches every alert first.
- **Notification templates that break when they're sent**: a missing `}`, a template name
  with a typo, or `.Labels` where the data has `.CommonLabels`.
- **Inhibit rules that mute too much.** No `equal` labels, or rules that mute each other.
- **Timings that contradict themselves.** For example, `group_wait` longer than `group_interval`.
- **Time intervals that never apply, or always do**, so a route never sends anything.
- **Matchers Alertmanager reads differently than you do.** For example, `code=~[0-9]{3}`
  without quotes loses its closing brace.
- **Routes for alerts that don't exist.** With `--rules`, a typo in an `alertname` stops
  being silent.
- **Deprecated syntax** such as `match:`, which [`amlint fix`](usage.md#fix) rewrites.
- **Secrets in plain text**: API keys and webhook URLs written into the config instead of
  read from a file.

The other 22 repeat what amtool already rejects, so one tool gives you one report.
[See all checks](checks/index.md).

## Why not just amtool?

Keep it. amtool is the reference for "is this config valid?", and amlint agrees with it
there. amlint adds what comes after that question:

| | amtool | amlint |
|---|:-:|:-:|
| Rejects invalid configs | ✅ | ✅ |
| Finds valid configs that misroute or over-mute alerts | — | ✅ |
| Where does one alert go? | ✅ | ✅ |
| A file of routing tests in CI, with route coverage | — | ✅ |
| Is this alert muted while that one fires? | — | ✅ |
| What does this change do to routing? | — | ✅ |
| Kubernetes, Helm, Mimir and AlertmanagerConfig input | — | ✅ |
| PR annotations, SARIF, JSON, Markdown output | — | ✅ |

The routing and inhibition simulation is
[checked in CI against real Alertmanager releases](fidelity.md), so `amlint route` and
`amlint test` give the same answers Alertmanager would.
