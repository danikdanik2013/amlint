# amlint

**Semantic linter for Prometheus Alertmanager configs.**

Alertmanager's own `amtool check-config` already validates most schema-level mistakes and
refuses to start over them. **amlint covers that same ground in one dependency-free CLI
with JSON/SARIF/diff/tree output, plus checks that amtool doesn't do** — configs that load
fine, that Alertmanager runs without complaint, and that still silently don't do what you
meant: unreachable routes behind a catch-all, timing that contradicts itself, inhibition
rules that silence too broadly.

## Quick example

```bash
pip install amlint
amlint check alertmanager.yml
```

```
  ERROR  Route references receiver 'pager-team' which is not defined in receivers.
         Alerts matched here will be dropped.
  ↳ route.routes[1]  [undefined-receiver]

  WARN   Catch-all route (no matchers) with continue:false will intercept all alerts
         — 2 subsequent sibling(s) are unreachable.
  ↳ route.routes[0]  [unreachable-route]

  2 error · 3 warn · 1 info
```

Exit code `1` on any ERROR — ready for CI. `--strict` treats WARN as failure too.

## Why not just amtool?

| | amtool | amlint |
|---|---|---|
| Syntax errors | ✅ | ✅ |
| Undefined receiver | ✅ | ✅ |
| Missing integration fields (webhook url, PagerDuty key, etc.) | ✅ | ✅ |
| Bad regex in matchers | ✅ | ✅ |
| Unreachable routes (catch-all swallows siblings) | ❌ | ✅ |
| Route matcher collisions | ❌ | ✅ |
| Inhibition rules that silence too broadly, or never fire | ❌ | ✅ |
| Timing misconfig (`group_wait`/`group_interval`/`repeat_interval`) | ❌ | ✅ |
| Unused or empty receivers | ❌ | ✅ |
| Batch routing regression tests (label-set → expected receiver, run from a file in CI) | single alert, interactive only | ✅ |

amtool already covers the first four rows — amlint re-implements those so you get one
tool with unified JSON/SARIF/diff/tree output instead of needing the Go binary too, not
because amtool misses them. Run `amlint list` to see exactly which of the 30 checks
duplicate amtool and which don't; `amlint explain <code>` says so for any individual check.

On the last row: `amtool config routes test` already lets you test one alert's routing
interactively — that part isn't new either. What it doesn't have is a way to run a whole
suite of these as regression tests from a file, in CI, without a live Alertmanager. That's
what `amlint test` adds.

## 30 checks — 13 of them amtool can't do

See [all checks](checks/index.md) for the full list, and which column each falls in.

