# GitHub Action

amlint ships as a composite GitHub Action, so you don't need to write the `pip install` /
`amlint check` steps yourself.

## Quickstart

```yaml
# .github/workflows/alertmanager.yml
name: Lint Alertmanager config

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: danikdanik2013/amlint@v0.2.0
        with:
          config-path: alertmanager.yml
          strict: true
```

## Inputs

| input | required | default | description |
|-------|----------|---------|-------------|
| `config-path` | yes | — | Path to the `alertmanager.yml` file to lint |
| `strict` | no | `false` | Exit non-zero on WARN as well as ERROR |
| `ignore` | no | `""` | Comma-separated check codes to skip |
| `only` | no | `""` | Comma-separated check codes to run exclusively |
| `version` | no | `""` (latest) | Pin a specific amlint version, e.g. `0.2.0` |
| `sarif` | no | `false` | Upload findings to GitHub Code Scanning as SARIF (PR annotations) |

## With Code Scanning annotations

Requires Code Scanning enabled on the repo (on by default for public repos):

```yaml
    steps:
      - uses: actions/checkout@v7
      - uses: danikdanik2013/amlint@v0.2.0
        with:
          config-path: alertmanager.yml
          sarif: true
        permissions:
          security-events: write
```

Findings then show up as inline annotations on the PR diff, in addition to the job failing on
ERROR-level findings. SARIF upload happens even if the check step finds issues, so annotations
appear on the same run that fails.

## Pinning a version

Use `version` to pin amlint itself independently of the action tag — useful if you want the
action's argument-handling to stay current while freezing the linter's rule set:

```yaml
      - uses: danikdanik2013/amlint@v0.2.0
        with:
          config-path: alertmanager.yml
          version: '0.2.0'
```
