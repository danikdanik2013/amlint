# amlint

[![CI](https://github.com/danikdanik2013/amlint/actions/workflows/ci.yml/badge.svg)](https://github.com/danikdanik2013/amlint/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/danikdanik2013/amlint/branch/main/graph/badge.svg)](https://codecov.io/gh/danikdanik2013/amlint)
[![PyPI](https://img.shields.io/pypi/v/amlint)](https://pypi.org/project/amlint)
[![Python](https://img.shields.io/pypi/pyversions/amlint)](https://pypi.org/project/amlint)
[![Docker](https://ghcr-badge.egpl.dev/danikdanik2013/amlint/size?tag=latest&label=docker)](https://github.com/danikdanik2013/amlint/pkgs/container/amlint)

Semantic linter for Prometheus Alertmanager configs.

`amtool check-config` validates syntax. **amlint validates semantics:**
will alerts actually reach a receiver, does the inhibition rule do anything,
are there unreachable routing branches? These are the bugs that burn teams —
config is valid, alerts silently vanish.

## Why

Alertmanager configs are YAML routing trees with inhibition rules and receivers.
The most painful mistakes are syntactically valid:

- route references a receiver that doesn't exist → **alerts are dropped**
- inhibition without `equal` → silences unrelated alerts, you think it's quiet, there's actually a fire
- catch-all branch before specific ones → specific branches **are unreachable**
- `match_re` that doesn't compile
- `group_by` that doesn't behave the way you think

`amtool` won't catch any of this. amlint will.

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
amlint check prod.yml staging.yml        # multiple files
cat alertmanager.yml | amlint check -    # stdin
amlint check alertmanager.yml --strict   # WARN also exits non-zero
amlint check alertmanager.yml --format json
```

Example output:

```
  ERROR  Route references receiver 'pager-team' which is not defined in receivers. Alerts matched here will be dropped.
  ↳ route.routes[1]  [undefined-receiver]

  WARN   Catch-all route (no matchers) with continue:false will intercept all alerts — 2 subsequent sibling(s) are unreachable.
  ↳ route.routes[0]  [unreachable-route]

  2 error · 3 warn · 1 info
```

Exit code `1` on ERROR — ready for CI. `--strict` makes WARN block too.

## CI example

```yaml
# .github/workflows/lint.yml
- name: Lint Alertmanager config
  run: amlint check alertmanager.yml --strict
```

## Checks

| code | level | what it catches |
|------|-------|-----------------|
| `undefined-receiver` | error | route references a receiver that doesn't exist |
| `bad-regex` | error | `match_re` pattern fails to compile |
| `no-root-route` | error | no root `route` defined |
| `inhibit-no-equal` | warn | inhibition without `equal` silences too broadly |
| `unreachable-route` | warn | catch-all hides subsequent sibling routes |
| `groupby-ellipsis` | warn | `...` mixed with explicit labels in `group_by` |
| `inhibit-same-match` | info | source and target match the same label value |
| `unused-receiver` | info | receiver defined but not used in any route |
| `duplicate-receiver` | error | receiver name defined more than once |
| `empty-receiver` | warn/info | receiver has no integration configured — alerts will be dropped |
| `undefined-time-interval` | error | `mute_time_intervals` / `active_time_intervals` references unknown interval |
| `repeat-before-group` | warn | `repeat_interval` shorter than `group_interval` |
| `circular-inhibition` | warn | two inhibition rules that silence each other |
| `wait-exceeds-interval` | warn | `group_wait` longer than `group_interval` |
| `useless-continue` | info | `continue:true` on the last sibling route has no effect |

## Tests

```bash
python3 -m pytest test_linter.py -v
```

## License

MIT
