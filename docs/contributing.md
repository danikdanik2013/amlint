# Contributing

This page gets you from a fresh clone to a pull request: set up, run the tests CI runs, and
add a check. The last two sections cover the tests against the real Alertmanager and the
browser playground, which you only need when you work on those parts.

## Set up { #setup }

```bash
git clone https://github.com/danikdanik2013/amlint
cd amlint
pip install -e ".[dev]"
```

You need Python 3.9 or later. This installs amlint in editable mode, so `amlint` runs your
working copy, plus pytest, ruff and Hypothesis.

## Run the tests { #running-tests }

CI runs these on Python 3.9 to 3.15:

```bash
ruff check amlint/
pytest test_linter.py test_formats.py -v
```

- `test_linter.py` covers the checks and the CLI.
- `test_formats.py` covers the input formats on the [Kubernetes & Mimir](kubernetes.md) page.
- Add `--cov=amlint` for coverage.

## Add a check { #how-to-add-a-new-check }

Every check is a function in
[`amlint/linter.py`](https://github.com/danikdanik2013/amlint/blob/main/amlint/linter.py).
It takes the parsed config as a dict and returns a list of findings.

**1. Write the function.**

```python
def check_your_rule(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    route = _root_route(cfg)
    if not route:
        return out
    for node, path in _walk_routes(route):
        if something_is_wrong(node):
            out.append(Finding(
                WARN,              # ERROR, WARN or INFO
                "your-rule-code",  # unique kebab-case code
                "What's wrong, and what happens to alerts because of it.",
                path,              # where in the config, e.g. route.routes[0]
            ))
    return out
```

`_walk_routes` yields every route with its path. The CLI turns the path into
`file:line:col`, in every input format.

**2. Register it** in `ALL_CHECKS` in the same file:

```python
ALL_CHECKS = [
    ...
    check_your_rule,
]
```

**3. Explain it.** Add an entry for `your-rule-code` to `EXPLAINS` in
[`amlint/explains.py`](https://github.com/danikdanik2013/amlint/blob/main/amlint/explains.py),
with `level`, `summary`, `why`, `bad` and `good`. `amlint list` and `amlint explain` read it,
and `--ignore` warns about codes that aren't there.

If Alertmanager refuses to load a config that your check flags, also add the code to
`ALSO_CAUGHT_BY_AMTOOL` in the same file. The
[differential tests](#differential-tests-against-alertmanager) then check that
`amtool check-config` agrees.

**4. Add a test** to `test_linter.py`:

```python
def test_your_rule():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        # the smallest config that triggers the rule
    }
    assert "your-rule-code" in codes(cfg)
```

**5. Document it.** Add a `## your-rule-code` section to the right page under `docs/checks/`,
and a row to the table in `docs/checks/index.md`. The playground links each finding to its
section by that heading.

## Pick a severity { #severity-guide }

| Level | Use when |
|-------|----------|
| `ERROR` | Alertmanager refuses to load the config, or alerts will be lost |
| `WARN` | Almost certainly not what the author meant |
| `INFO` | Suspicious and worth a look, but may be on purpose |

## Open a pull request { #opening-a-pr }

- One check or one fix per pull request is easiest to review.
- Include a test that fails without your change and passes with it.
- `ruff check amlint/` and `pytest test_linter.py test_formats.py` pass.
- For a new check, add its docs (step 5 above).
- Add a line to `CHANGELOG.md` under `## [Unreleased]`.
- If you changed the docs, `mkdocs build --strict` passes (`pip install mkdocs-material`).

## Differential tests against Alertmanager

amlint simulates Alertmanager's routing, inhibition and config errors in Python. These tests
compare it with the real Alertmanager on generated configs. The
[fidelity page](fidelity.md) explains what that guarantees users. Run them with one command:

```bash
scripts/differential.sh
```

The script downloads an Alertmanager release (0.34.1 by default) into
`.cache/alertmanager/` and checks the tarball's sha256 against the release's
`sha256sums.txt`. Then it runs `tests/differential/` with that release's `amtool` and
`alertmanager` first on `PATH`. Extra arguments go to pytest.

| Test | Compares |
|---|---|
| `test_routing.py` | amlint's receivers with `amtool config routes test`, on generated configs and label sets |
| `test_validation.py` | `amtool check-config` rejects a config exactly when amlint reports an error whose code is in `ALSO_CAUGHT_BY_AMTOOL` |
| `test_inhibition.py` | alerts POSTed to a running `alertmanager`: each one's `status.inhibitedBy` and receivers with amlint's simulation |

Settings, as environment variables:

| Variable | Default | What it sets |
|---|---|---|
| `AM_VERSION` | `0.34.1` | the Alertmanager release |
| `AM_SHA256` | none | the tarball's sha256, checked as well (CI pins it) |
| `AMLINT_DIFF_SEED` | `0` | the Hypothesis seed: the same seed generates the same cases |
| `AMLINT_DIFF_EXAMPLES` | `300` | generated configs per routing and validation test |
| `AMLINT_DIFF_INHIBIT_EXAMPLES` | `40` | inhibition scenarios, each on its own `alertmanager` |

```bash
AM_VERSION=0.32.2 scripts/differential.sh                                  # another release
AMLINT_DIFF_SEED=7 AMLINT_DIFF_EXAMPLES=2000 scripts/differential.sh -x   # explore more
```

- The pytest header prints the seed and the number of examples, so you can replay a CI run
  with the same values.
- On a mismatch, the test reduces the case to a minimal config and label set. It prints both
  results and the `amtool` command that reproduces it.
- Plain `pytest` skips these tests when `amtool` isn't on `PATH`.

**In CI**, `.github/workflows/differential.yml` runs them against the last two minor
Alertmanager releases and the newest, on pull requests and pushes to `main` that touch
`amlint/routing.py`, `linter.py`, `goyaml.py`, `formats.py`, `explains.py` or the
differential tests. It also runs weekly with a fresh seed and more examples, and you can
start it by hand with a seed of your choice. When the weekly run warns that a newer
Alertmanager is out, add it to the workflow's matrix with its sha256, and update the
versions on the fidelity page and in the README.

**When a test fails**, the mismatch is one of two things:

- a bug in amlint: fix it, and add a regression test to `test_linter.py`
- a deliberate difference or an Alertmanager bug: document it under
  [Known differences](fidelity.md#known-differences), and teach the test to recognize it,
  narrowly

## The playground

[The playground](playground/index.md) runs amlint in the browser with
[Pyodide](https://pyodide.org) (Python compiled to WebAssembly).

| File | What it does |
|---|---|
| `docs/playground/app.js` | the UI |
| `docs/playground/worker.js` | loads Pyodide and amlint in a Web Worker, off the main thread |
| `docs/playground/amlint_playground.py` | calls `amlint.cli.main([... "--format", "json"])` for check, route and diff, and builds the route tree |
| `overrides/playground.html` | the page template |
| `playground/build.py` | builds the amlint wheel, the CodeMirror editor bundle and the examples into `docs/playground/dist/` (not committed) |

New checks show up in the playground without any change there. A change to the JSON output
of `check`, `route` or `diff` may need one.

To work on the page locally (needs npm):

```bash
pip install mkdocs-material
python playground/build.py   # again after changing amlint/ or playground/
mkdocs serve                 # http://127.0.0.1:8000/amlint/playground/
```

`python playground/build.py --no-editor` skips rebuilding the editor bundle, if you already
have one.

The docs workflow runs a headless smoke test of the built site on pull requests. To run it
yourself:

```bash
mkdocs build --strict
python -m http.server 8000 --directory site &
cd playground && npx playwright install chromium && node smoke-test.mjs http://127.0.0.1:8000/playground/
```

The examples in the playground's menu are listed in `EXAMPLES` in `playground/build.py`.
Their files are in `playground/examples/` and `testdata/`.
