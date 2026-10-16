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
      - uses: danikdanik2013/amlint@v0.4.0
        with:
          config-path: alertmanager.yml
          strict: true
```

Findings show up as annotations on the PR diff, on the line each one points at, and the job
fails on ERROR-level findings (or WARN too, with `strict: true`).

## Inputs

| input | required | default | description |
|-------|----------|---------|-------------|
| `config-path` | yes | — | Path to the `alertmanager.yml` file to lint |
| `strict` | no | `false` | Exit non-zero on WARN as well as ERROR |
| `ignore` | no | `""` | Comma-separated check codes to skip |
| `only` | no | `""` | Comma-separated check codes to run exclusively |
| `version` | no | `""` (latest) | Pin a specific amlint version, e.g. `0.4.0` |
| `format` | no | `github` | `github`: findings become PR annotations. `text`: the console report in the job log |
| `sarif` | no | `false` | Also upload findings to GitHub Code Scanning as SARIF |
| `diff-base` | no | `""` | Git ref to compare `config-path` against; posts the routing diff as a PR comment (see below) |
| `github-token` | no | `github.token` | Token used to post the routing diff comment |

`format`, `diff-base` and `github-token` are new since v0.4.0 and ship in the next release.

## Routing diff as a PR comment

Set `diff-base` to the pull request's base commit, and the action compares the config with
its version there using [`amlint diff --format markdown`](usage.md#markdown-report). The
report goes into one PR comment: a summary line, the label sets whose receivers changed
(labels | before | after), changed `group_by` and timings, and new and fixed findings with
their `file:line:col`. Each push updates the same comment instead of adding a new one.

```yaml
# .github/workflows/alertmanager.yml
name: Lint Alertmanager config

on: [push, pull_request]

permissions:
  contents: read
  pull-requests: write   # to create and update the comment

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0   # the base commit has to be in the checkout
      - uses: danikdanik2013/amlint@v0.4.0
        with:
          config-path: alertmanager.yml
          diff-base: ${{ github.event.pull_request.base.sha }}
```

The comment looks like this:

> ### amlint diff
>
> **2 routing changes** · no new findings · 0 fixed · 4 unchanged · 6 label sets checked
>
> #### Receivers
>
> | labels | before | after |
> | --- | --- | --- |
> | `team=db, severity=critical` | `pagerduty-db` | `slack-db` |
>
> #### Grouping and timings
>
> | labels | receiver | setting | before | after |
> | --- | --- | --- | --- | --- |
> | `team=infra` (+1 similar) | `infra-slack` | `group_wait` | `30s` | `10s` |
>
> <sub>Compared with `alertmanager.yml` at 3f2c1a9d8e7b. Updated on every push.</sub>

How it behaves:

- It runs only on `pull_request` (and `pull_request_target`) events. On a push, `diff-base`
  is empty and the step is skipped without a message.
- The comment is found by the hidden marker `<!-- amlint-routing-diff -->` it starts with,
  and is created or updated through the GitHub REST API with `curl`. No third-party actions.
- If the config doesn't exist at the base, it is new in the PR. The diff runs against an
  empty config, so every finding is new and every receiver shows `(none)` before.
- If the config is the same as at the base and there is no comment yet, nothing is posted.
  An earlier comment is still updated, so it doesn't go stale after a revert.
- The report is also written to the job summary. That's where to look on pull requests from
  forks, whose `GITHUB_TOKEN` is read-only: the comment can't be posted, and the step logs a
  warning instead of failing.
- The routing diff is informational and never fails the job. If the base commit isn't in the
  checkout, the step fails and says how to fix it. `fetch-depth: 0` is the simplest fix;
  fetching just the base commit works too:

  ```yaml
      - uses: actions/checkout@v7
      - run: git fetch --no-tags --depth=1 origin ${{ github.event.pull_request.base.sha }}
  ```

- `ignore` and `only` apply to the findings in the comment too.

## With Code Scanning annotations

Requires Code Scanning enabled on the repo (on by default for public repos):

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v7
      - uses: danikdanik2013/amlint@v0.4.0
        with:
          config-path: alertmanager.yml
          sarif: true
```

Findings then also show up in the repository's Security tab, in addition to the job failing
on ERROR-level findings. SARIF upload happens even if the check step finds issues, so
annotations appear on the same run that fails. To get only the Code Scanning annotations, set
`format: text` as well.

## Pinning a version

Use `version` to pin amlint itself independently of the action tag — useful if you want the
action's argument-handling to stay current while freezing the linter's rule set:

```yaml
      - uses: danikdanik2013/amlint@v0.4.0
        with:
          config-path: alertmanager.yml
          version: '0.4.0'
```

`diff-base` runs `amlint diff --format markdown`, which amlint 0.4.0 doesn't have, so don't pin
`version` below the release that adds it.
