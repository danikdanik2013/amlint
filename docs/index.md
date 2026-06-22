# amlint

**Semantic linter for Prometheus Alertmanager configs.**

`amtool check-config` validates syntax. **amlint validates semantics** — will alerts actually reach a receiver, does the inhibition rule do anything, are there unreachable routing branches?

These are the bugs that burn teams on-call: config is valid, CI passes, alerts silently vanish.

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
| Undefined receiver | ❌ | ✅ |
| Unreachable routes | ❌ | ✅ |
| Bad inhibition rules | ❌ | ✅ |
| Invalid regex | ❌ | ✅ |
| Timing misconfig | ❌ | ✅ |

## 17 checks, zero false positives

amlint catches mistakes that are **syntactically valid** but **semantically broken**.
See [all checks](checks/index.md) for the full list.
