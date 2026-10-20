---
name: lint-alertmanager
description: Lint, review and debug Prometheus Alertmanager configs with the amlint CLI. Use when the user asks to lint, check, validate or review an alertmanager.yml (or an Alertmanager config inside a Kubernetes Secret, kube-prometheus-stack Helm values, a Mimir/Cortex tenant config or AlertmanagerConfig resources); asks where an alert will be routed, which receiver gets it, or whether it will be inhibited; wants routing unit tests or to see how a config change affects routing; or asks what an amlint finding means.
allowed-tools: Bash(amlint *) Bash(command -v amlint)
---

# Lint Alertmanager configs with amlint

amlint finds Alertmanager configs that are valid but behave wrong. Examples: alerts that
reach no receiver, routes that can never match, inhibit rules that mute too much,
contradictory timings. It also simulates routing and inhibition the way Alertmanager does.

## 1. Make sure amlint is installed

Run `command -v amlint`. If it's missing, tell the user and suggest one of these, then
stop and wait:

- `pip install amlint` (Python 3.9+)
- `pipx install amlint` or `uvx amlint ...` (no global install)
- `docker run --rm -v "$PWD":/cfg ghcr.io/danikdanik2013/amlint check /cfg/alertmanager.yml`

## 2. Find the config

Look for `alertmanager.yml` or `alertmanager.yaml`, or files containing `route:` and
`receivers:`. amlint also reads these directly and detects them from the content:

- Kubernetes Secrets or ConfigMaps with `alertmanager.yaml`
- kube-prometheus-stack values with `alertmanager.config`
- Mimir/Cortex configs with `alertmanager_config`
- `AlertmanagerConfig` resources (pipe a rendered set in with
  `kubectl kustomize dir/ | amlint check -`)

## 3. Lint and explain

```bash
amlint check <file> --format json     # list of {file, line, column, level, code, message, where}
amlint explain <code>                 # why it matters and how to fix it, with examples
```

- Exit code 1 means at least one `error`. `--strict` also fails on `warn`.
- Report findings grouped by level, with `file:line`. For each one, say in one sentence what
  goes wrong in production (for example "critical alerts never reach the pager"), not just
  the code.
- When proposing a fix, use `amlint explain <code>`. After editing, re-run
  `amlint check` and confirm the finding is gone and nothing new appeared.
- If the user has Prometheus alerting rules, add `--rules 'path/to/rules/*.yml'`. This also
  catches routes on alert names that no rule produces.

## 4. Answer routing questions with the simulator, not by reading YAML

```bash
amlint route <file> team=db severity=critical                 # receivers, path, group_by, timings
amlint route <file> alertname=DiskFull --firing alertname=NodeDown   # is it inhibited, and by which rule
amlint tree <file>                                             # the whole route tree, findings inline
```

Routing has subtle rules: first match wins among siblings unless `continue: true`, settings
are inherited, and regexes are fully anchored. Always run `amlint route` instead of
reasoning about it by hand, and quote its output.

## 5. Changes and tests

- **Reviewing a change:** `amlint diff <old> <new>` lists new and fixed findings and every
  label set that now reaches different receivers. To get the old version from git, run
  `git show HEAD:path/alertmanager.yml > /tmp/old.yml`. `--format markdown` gives a
  PR-comment-sized report.
- **Locking routing in:** offer a `tests.yml` for `amlint test <file> tests.yml`:

  ```yaml
  - name: critical db alerts page the db on-call
    labels: {team: db, severity: critical}
    expect: [db-pager]
  - name: disk alerts are muted while their node is down
    labels: {alertname: DiskFull, instance: db-1}
    firing: [{alertname: NodeDown, instance: db-1}]
    inhibited: true
  ```

  `--coverage` lists routes that no test reaches, and `--fail-under 90` fails below 90%
  route coverage.
- **CI:** suggest the GitHub Action (`uses: danikdanik2013/amlint@v0.8.0`, with
  `config-path:` and `strict: true`) or `amlint check <file> --strict --format github`.

## Limits

- amlint checks config semantics. It does not render notification templates or contact
  receivers.
- With `--rules`, it only knows alerts' static rule labels, not labels added at runtime.
- Docs: https://danikdanik2013.github.io/amlint/ (all checks:
  https://danikdanik2013.github.io/amlint/checks/).
