# AGENTS.md

Guidance for AI coding agents working in this repository. For humans, see
[CONTRIBUTING.md](CONTRIBUTING.md); for using amlint itself, see [README.md](README.md) and
[docs/llms.txt](docs/llms.txt).

## What this is

amlint is a Python CLI that lints Prometheus Alertmanager configs and simulates their
routing and inhibition. It is published to PyPI (`amlint`), as a Docker image
(`ghcr.io/danikdanik2013/amlint`), as a GitHub Action (`action.yml`) and as a Homebrew
formula (`Formula/amlint.rb`). A VS Code extension in `editors/vscode/` runs `amlint lsp`;
it isn't on the Marketplace yet. Runtime dependencies are only PyYAML and Rich, and it
supports Python 3.9+, so don't use newer syntax or add dependencies casually.

## Layout

| Path | What lives there |
|---|---|
| `amlint/cli.py` | argparse CLI: `check`, `diff`, `tree`, `route`, `test`, `init`, `list`, `explain`, `lsp`, and the output formats |
| `amlint/linter.py` | every check (`check_*` functions) and `lint()`, which runs them |
| `amlint/explains.py` | `amlint explain` text per check code, `ALSO_CAUGHT_BY_AMTOOL`, and each check's docs page (`_DOCS_PAGES`) |
| `amlint/lsp.py` | `amlint lsp`: the language server (diagnostics, hover on routes, quick fixes), standard library only |
| `amlint/routing.py` | the Alertmanager routing and inhibition simulation behind `route`, `test` and `diff` |
| `amlint/implication.py` | matcher implication for `shadowed-route` |
| `amlint/formats.py` | input formats: Secret, ConfigMap, Helm values, Mimir/Cortex, AlertmanagerConfig |
| `amlint/rules.py` | Prometheus alerting rules for `--rules` |
| `amlint/source.py` | maps findings to `file:line:col`; inline `# amlint: disable=` comments |
| `amlint/goyaml.py` | YAML parsed the way Alertmanager (Go yaml.v2) parses it |
| `test_linter.py`, `test_formats.py`, `test_lsp.py` | the main test suites; `test_lsp.py` drives `amlint lsp` over stdio |
| `tests/differential/` | tests against a real `amtool` and `alertmanager` (`scripts/differential.sh`) |
| `docs/` | MkDocs site; `docs/checks/*.md` documents every check |
| `playground/`, `docs/playground/` | browser playground (Pyodide) |
| `docs/llms.txt`, `scripts/build_llms_full.py` | summary for AI assistants; `llms-full.txt` is generated in the docs workflow |
| `editors/vscode/` | VS Code extension (TypeScript) that starts `amlint lsp`; `PUBLISHING.md` for the Marketplace |
| `.claude-plugin/`, `plugins/amlint/` | Claude Code marketplace and plugin; the skill that teaches Claude to use amlint |

## Commands

```bash
pip install -e ".[dev]"
pytest test_linter.py test_formats.py test_lsp.py -q    # what CI runs
ruff check .                                # lint; line length 100
scripts/differential.sh                     # optional: compare with real Alertmanager
```

## Conventions

- **Adding a check.** Add a `check_*` function in `linter.py` and register it in `lint()`.
  Add an entry in `explains.py`, and in `ALSO_CAUGHT_BY_AMTOOL` only if `amtool
  check-config` rejects the same config. Add the code to `_DOCS_PAGES` there too: editors
  link findings to that docs section. Add tests for both a positive and a negative case.
  Add a section in the right `docs/checks/*.md` page with the heading exactly the check code.
  Add a row in `docs/checks/index.md`, and update the check counts in README.md and
  docs/index.md.
- **Findings must be quiet on real configs.** Run `amlint check` on
  `testdata/real-world/*` and `testdata/formats/*`, and don't add noise there.
- **The routing simulation must match Alertmanager.** Change `routing.py` only together
  with a test. If behaviour could differ from Alertmanager, run the differential tests.
- **CHANGELOG.md:** add every user-visible change under `## [Unreleased]`.
- **Docs are written for busy SREs.** Use short sentences and lead with what goes wrong
  and how to fix it. Every example must be true for the current CLI.
- **Releases.** Bump `version` in `pyproject.toml`, move `[Unreleased]` to the new version
  in CHANGELOG.md, and push a `vX.Y.Z` tag. CI then publishes to PyPI and the Docker image,
  and creates the GitHub release. After the release is on PyPI, update `Formula/amlint.rb`.
