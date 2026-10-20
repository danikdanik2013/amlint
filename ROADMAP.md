# Roadmap

Where amlint is going after 0.7.0, release by release. This is a plan, not a promise: items
can move between releases or drop out. Want to work on one? Open an issue first, so two
people don't build the same thing.

Last updated: 2026-10-20.

## 0.8: templates and notifications

The biggest class of silent failures amlint doesn't catch yet.

- **Check notification templates.** Go template syntax in `.tmpl` files and in fields such as
  `title:` and `text:`, and `{{ template "name" }}` calls to templates that don't exist.
  Alertmanager only notices at send time, and the notification goes out empty. This is a big
  item: amlint needs its own template parser, with no new dependencies.
- **Deprecated syntax, with fixes.** `match`/`match_re` → `matchers`,
  `source_match`/`target_match` → `source_matchers`/`target_matchers`, top-level
  `mute_time_intervals` → `time_intervals`. Fixed by `amlint fix` or a quick fix in the editor.
- **Secrets in plain text.** API keys and webhook URLs with tokens written into the config;
  suggest the `*_file` fields. Must stay quiet on `testdata/real-world`.
- **Time intervals:** an invalid `location`, intervals that are never active, and a route
  whose intervals mute it all the time.

## 0.9: scale and team policies

- **Policies in `.amlint.yml`, no code.** For example: every `severity=critical` alert
  reaches a PagerDuty receiver, every `group_by` includes `alertname`, no `repeat_interval`
  over 12h. Checked with the same routing simulation as `amlint route`.
- **A running Alertmanager.** `amlint check --url http://alertmanager:9093` reads the loaded
  config through its API, and `amlint diff` shows drift between the repository and what
  actually runs.
- **Matcher parsing modes and versions.** Mirror Alertmanager's `utf8-strict-mode` and
  `classic-mode` feature flags, and add `--alertmanager-version` for checks that depend on it.
  Extend the differential tests to `group_by` and timings, which
  [fidelity](docs/fidelity.md#not-covered-yet) lists as not covered yet.
- **CI formats:** `--format gitlab` for GitLab Code Quality, and `amlint test --format junit`
  for test reports.

## 1.0: a stable contract

1.0 is less about new features and more about a promise not to break what works.

- **Stable without a major release:** check codes (renamed only with an alias), the JSON and
  SARIF output (with a published JSON Schema), exit codes, and the formats of `.amlint.yml`
  and of the tests file.
- **JSON Schemas for `.amlint.yml` and the tests file in SchemaStore**, so
  yaml-language-server completes and checks them.
- **Written policies:** which Alertmanager versions are supported, and how long a deprecated
  check code or flag keeps working.
- **Python API, or not:** either document `lint()`, `load_input()` and `Router` as public, or
  say amlint is CLI only.
- **Supply chain:** a signed Docker image (cosign), an SBOM, and multi-arch images.
- **Distribution:** Homebrew core instead of the tap, and conda-forge.
- **Docs per version** (mike), and the `Development Status :: 5 - Production/Stable`
  classifier.
- **Quiet on real configs:** run amlint on dozens of public configs (Helm charts,
  kube-prometheus) and fix the noise before the release.
