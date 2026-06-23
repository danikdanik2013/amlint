# Changelog

## [0.1.9] - 2026-06-23

### Added
- `global-resolve-timeout-missing` (info) — `global.resolve_timeout` not set; Alertmanager defaults to 5m silently
- `--format sarif` — GitHub Code Scanning SARIF 2.1.0 output; upload with `github/codeql-action/upload-sarif` to get findings as PR annotations

## [0.1.8] - 2026-06-23

### Added
- `route-match-collision` (warn) — two sibling routes with identical matchers; the second never receives alerts
- `amlint list` — prints all 23 check codes with level and one-line description
- docs: routing.md new section, usage.md `list` command, index count 22→23

## [0.1.7] - 2026-06-23

### Added
- `pyproject.toml [tool.amlint]` support — configure ignore/strict/severity without a separate file
- `.amlint.yml` still takes priority if both files exist
- Python 3.11+ uses stdlib `tomllib`; 3.9/3.10 use `tomli` (optional install)

### Fixed
- ruff E501 in `explains.py` and `linter.py` — all long lines split

## [0.1.6] - 2026-06-23

### Added
- 2 new integration checks:
  - `opsgenie-no-api-key` (error) — opsgenie_configs without `api_key` and no global
  - `msteams-no-webhook-url` (error) — msteams_configs without `webhook_url`
- `amlint explain <code>` — detailed description, why it matters, bad/good YAML examples for all 22 checks
- Updated docs: checks table (17→22), usage page with `--ignore` and `.amlint.yml` docs, `explain` command

## [0.1.5] - 2026-06-23

### Added
- 3 new checks for missing required integration fields:
  - `webhook-no-url` (error) — webhook_configs without `url` or `url_file`
  - `pagerduty-no-routing-key` (error) — pagerduty_configs without `routing_key`/`service_key`
  - `slack-no-api-url` (error) — slack_configs without `api_url` and no `global.slack_api_url`
- `.amlint.yml` `severity:` map — override the level of any check per-project:
  ```yaml
  severity:
    empty-receiver: info   # downgrade
    unused-receiver: error # upgrade
  ```

## [0.1.4] - 2026-06-23

### Added
- `--ignore CODE,CODE` flag — skip specific checks per invocation
- `.amlint.yml` project config file — set `ignore:` list and `strict: true` once instead of repeating flags
- Config file and CLI `--ignore` merge (union of both)

## [0.1.3] - 2026-06-22

### Changed
- Rich-based output: icons (✖ ⚠ ℹ), word wrap, separator line, colored summary

## [0.1.1] - 2026-06-22

### Added
- `amlint --version` flag
- `amlint diff old.yml new.yml` — shows fixed vs new findings between two configs,
  exits non-zero if new regressions appeared (useful in PR CI)
- `dependabot.yml` — auto-updates GitHub Actions and pip dependencies weekly
- `SECURITY.md` — vulnerability reporting policy

## [0.1.0] - 2026-06-22

### Added

**13 semantic checks:**

| code | level | what it catches |
|------|-------|-----------------|
| `undefined-receiver` | error | route references a receiver that doesn't exist |
| `bad-regex` | error | `match_re` / `matchers` regex fails to compile |
| `no-root-route` | error | no root `route` defined |
| `duplicate-receiver` | error | receiver name defined more than once |
| `undefined-time-interval` | error | `mute_time_intervals` / `active_time_intervals` references unknown interval |
| `inhibit-no-equal` | warn | inhibition without `equal` silences too broadly |
| `unreachable-route` | warn | catch-all hides subsequent sibling routes |
| `groupby-ellipsis` | warn | `...` mixed with explicit labels in `group_by` |
| `empty-receiver` | warn/info | receiver has no integration configured |
| `repeat-before-group` | warn | `repeat_interval` shorter than `group_interval` |
| `circular-inhibition` | warn | two inhibition rules that silence each other |
| `wait-exceeds-interval` | warn | `group_wait` longer than `group_interval` |
| `inhibit-same-match` | info | source and target match the same label value |
| `unused-receiver` | info | receiver defined but not used in any route |
| `useless-continue` | info | `continue:true` on the last sibling route |

**CLI:**
- Multiple files: `amlint check prod.yml staging.yml`
- Stdin: `cat alertmanager.yml | amlint check -`
- `--format json` for integrations
- `--strict` to treat WARN as failure

**Integrations:**
- Pre-commit hook via `.pre-commit-hooks.yaml`
- GitHub Actions CI on Python 3.9–3.12
