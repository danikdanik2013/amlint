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

### SARIF (GitHub Code Scanning)

SARIF output lets GitHub show findings as annotations directly on the PR diff — no plugins required:

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
