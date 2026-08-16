# Changelog

## [0.3.0] - 2026-08-16

### Added
- `amlint test <config> <tests-file>` — routing regression tests. Write a YAML file
  with a `tests:` list asserting label-set → expected receiver(s), run it as a
  suite in CI. Replicates Alertmanager's own dispatch algorithm (deepest-match-wins,
  `continue: true` fan-out in definition order, receiver inheritance) in pure Python
  — no Go binary or live Alertmanager needed.
  - Verified against real `amtool config routes test` output on 7 scenarios
    (deepest-match, no-child-match, continue fan-out with both orderings,
    receiver inheritance) before shipping — all matched exactly.
  - `amtool config routes test` already does single-alert interactive testing;
    this is the missing batch/CI piece, not a wholly new idea — see docs/index.md
    for the honest comparison.
  - Each test case asserts one of `receiver:` (single), `receivers:` (ordered list,
    for `continue` fan-out), or `drop: true` (no receiver reached).
  - `--format json` for machine-readable output.
- New module `amlint/simulate.py` with the reusable routing-simulation core.
- 12 new tests (7 unit tests on `simulate_route`, 8 CLI-level) — 119 total.
- docs/usage.md: new `## test` section with a full example.

## [0.2.1] - 2026-08-16

### Changed
- Corrected the "why not just amtool" comparison in README.md and docs/index.md: 17 of
  amlint's 30 checks (undefined-receiver, undefined-time-interval, bad-regex,
  groupby-ellipsis, no-root-route, duplicate-receiver, and the 11 "-no-x" integration
  required-field checks) duplicate validation `amtool check-config` already performs —
  confirmed empirically against amtool v0.33.1. Alertmanager refuses to start on all of
  these; it does not silently misbehave. Previous docs incorrectly claimed amtool misses
  several of them.
- Fixed misleading "why" text on 13 of those checks that claimed silent/partial failure
  ("alerts just vanish", "notifications fail") when the actual behavior is Alertmanager
  refusing to start entirely.
- `amlint list` now shows an `amtool` column marking which checks duplicate amtool's own
  validation vs. which are gaps amtool doesn't cover.
- `amlint explain <code>` now states, for every check, whether amtool also catches it.
- `docs/checks/index.md` overview table annotated the same way.

## [0.2.0] - 2026-08-16

### Added
- `action.yml` — official GitHub Action wrapping the CLI: install + run + optional SARIF
  upload to Code Scanning in one `uses:` step (`config-path`, `strict`, `ignore`, `only`,
  `version`, `sarif` inputs); docs at `docs/github-action.md`
- `amlint tree <file>` — new command, prints the route tree (matchers, receivers, `[continue]`)
  with routing-related findings (`undefined-receiver`, `unreachable-route`, `route-match-collision`,
  `bad-regex`, `groupby-ellipsis`, etc.) annotated inline on the node they apply to
- `--ignore` support on `tree` to quiet specific codes while browsing
- `sns-no-target` (error) — `sns_configs` missing all of `topic_arn`/`phone_number`/`target_arn`
  (exactly one destination is required; AWS rejects the publish call otherwise)
- Completes required-field coverage for every integration key amlint tracks (30 checks total)

## [0.1.12] - 2026-08-16

### Added
- `victorops-no-api-key` (error) — `victorops_configs` without `api_key` and no global fallback
- `wechat-no-corp-id` (error) — `wechat_configs` without `corp_id` and no global fallback
- Completes required-field coverage for all `_INTEGRATION_KEYS` integrations (29 checks total)

## [0.1.11] - 2026-08-16

### Added
- `telegram-no-bot-token` (error) — `telegram_configs` without `bot_token`/`bot_token_file`
- `discord-no-webhook-url` (error) — `discord_configs` without `webhook_url`/`webhook_url_file`
- 2 new receiver-integration checks bring the total to 25 → 27

## [0.1.10] - 2026-06-23

### Added
- `template-file-missing` (error/warn) — `templates:` references a file that doesn't exist;
  literal paths → error, globs with no matches → warn
- `--only CODE,CODE` — run only findings with the specified codes (inverse of `--ignore`);
  works on both `check` and `diff` subcommands
- `--exit-zero` — always exit 0 regardless of findings; for informational CI steps
- Python 3.13 added to CI matrix and PyPI classifiers

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
