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
    "level": "error",
    "code": "undefined-receiver",
    "message": "Route references receiver 'pager' which is not defined...",
    "where": "route.routes[1]"
  }
]
```

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

### In CI (PR check)

```yaml
- name: Check for regressions
  run: |
    git show origin/main:alertmanager.yml > /tmp/main.yml
    amlint diff /tmp/main.yml alertmanager.yml
```

## init

Generate a minimal valid `alertmanager.yml` to start from:

```bash
amlint init > alertmanager.yml
```

## --version

```bash
amlint --version
# amlint 0.1.2
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
