# GitHub Action

Run amlint on every push and pull request with one workflow step. Findings show up as
annotations on the pull request diff, the job fails on errors, and the action can post a
comment that shows which alerts a change routes differently.

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
      - uses: danikdanik2013/amlint@v0.6.0
        with:
          config-path: alertmanager.yml
```

Each finding becomes an annotation on the line it points at. The job fails on ERROR
findings; add `strict: true` to fail on WARN findings too.

`config-path` can be a plain `alertmanager.yml` or any file from
[Kubernetes & Mimir](kubernetes.md): a Secret, Helm values, a Mimir config or
`AlertmanagerConfig` resources. amlint detects the format from the content.

## Inputs

| Input | Default | What it does |
|---|---|---|
| `config-path` | required | The file to lint (one file) |
| `strict` | `false` | Fail on WARN as well as ERROR (`--strict`) |
| `ignore` | `""` | Comma-separated check codes to skip (`--ignore`) |
| `only` | `""` | Comma-separated check codes to run, and no others (`--only`) |
| `format` | `github` | `github`: findings become annotations. `text`: the console report in the job log |
| `sarif` | `false` | Also upload findings to GitHub code scanning as SARIF ([below](#with-code-scanning-annotations)) |
| `diff-base` | `""` | Git ref to compare `config-path` against; posts the routing diff as a PR comment ([below](#routing-diff-as-a-pr-comment)) |
| `github-token` | `github.token` | Token used to post the routing diff comment |
| `version` | `""` (latest) | amlint version to install from PyPI, e.g. `0.6.0` ([below](#pinning-a-version)) |

## Post the routing diff on pull requests { #routing-diff-as-a-pr-comment }

Set `diff-base` to the pull request's base commit. The action then compares your config with
its version at that commit, using
[`amlint diff --format markdown`](usage.md#markdown-report), and posts the result as one PR
comment. Each push updates the same comment. When routes changed, the comment includes a
diagram of them.

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
      - uses: danikdanik2013/amlint@v0.6.0
        with:
          config-path: alertmanager.yml
          diff-base: ${{ github.event.pull_request.base.sha }}
```

The comment has a summary line, then the label sets (combinations of alert labels) whose
receivers changed, changed `group_by` and timings, and new and fixed findings with their
`file:line:col`. Sections with nothing in them are left out. For example:

> ### amlint diff
>
> **2 routing changes** · no new findings · 0 fixed · 0 unchanged · 6 label sets checked
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

`(+1 similar)` means one more label set has exactly the same change.

How it behaves:

- **Pull requests only.** It runs on `pull_request` and `pull_request_target` events. On
  other events, such as a push, the step is skipped.
- **One comment.** The comment starts with a hidden `<!-- amlint-routing-diff -->` marker,
  which is how the action finds it again. It posts with `curl` and the GitHub REST API, with
  no third-party actions.
- **New config.** If the file doesn't exist at the base commit, it is new in the PR. The
  diff runs against an empty config, so every finding is new and every receiver was
  `(none)` before.
- **No change.** If the file is the same as at the base and there is no comment yet,
  nothing is posted. An existing comment is still updated, so it doesn't go stale after a
  revert.
- **Job summary.** The report is also written to the job summary. Look there on pull
  requests from forks: their `GITHUB_TOKEN` is read-only, so the comment can't be posted,
  and the step logs a warning instead of failing.
- **Never fails on findings.** The routing diff is informational. If `amlint diff` can't
  read a config, the step logs a warning and posts nothing.
- **Needs the base commit.** If the base commit isn't in the checkout, the step fails and
  says how to fix it. `fetch-depth: 0` is the simplest fix. Fetching only the base commit
  works too:

    ```yaml
          - uses: actions/checkout@v7
          - run: git fetch --no-tags --depth=1 origin ${{ github.event.pull_request.base.sha }}
    ```

- `ignore` and `only` apply to the findings in the comment too.
- A report longer than GitHub allows in a comment is cut at 65,000 characters. Run
  `amlint diff` locally for the rest.

## Upload to code scanning { #with-code-scanning-annotations }

To also see findings in the repository's **Security** tab, turn on `sarif`. The repository
needs GitHub code scanning (free for public repositories).

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v7
      - uses: danikdanik2013/amlint@v0.6.0
        with:
          config-path: alertmanager.yml
          sarif: true
```

- The SARIF upload runs even when the check finds problems, so code scanning shows them on
  the same run that fails.
- The job still fails on ERROR findings. To get code scanning annotations only, without the
  `github` format's annotations, also set `format: text`.
- `ignore` and `only` apply to the SARIF upload too.

## Pin the amlint version { #pinning-a-version }

By default the action installs the latest amlint from PyPI, so a new release with new
checks can start failing your build. To choose when you upgrade, set `version`:

```yaml
      - uses: danikdanik2013/amlint@v0.6.0
        with:
          config-path: alertmanager.yml
          version: '0.6.0'
```

Don't pin below `0.4.1`: the default `format: github` needs `0.4.0`, and `diff-base` needs
`0.4.1`. The formats on the [Kubernetes & Mimir](kubernetes.md) page need `0.5.0` or later, and the
**Changed routes** diagram in the PR comment needs `0.6.0`.

## Limits

- `config-path` takes one file. To lint several, add a step per file.
- There are no inputs for `--input-format`, `--matcher-strategy` or `--rules`. If you need
  them, install amlint and run it yourself:

    ```yaml
          - uses: actions/setup-python@v7
            with:
              python-version: '3.12'
          - run: pip install amlint
          - run: amlint check values.yaml --input-format helm --format github
    ```
