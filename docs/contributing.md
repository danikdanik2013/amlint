# Contributing

## Setup

```bash
git clone https://github.com/danikdanik2013/amlint
cd amlint
pip install -e ".[dev]"
```

## Running tests

```bash
pytest test_linter.py -v
pytest test_linter.py -v --cov=amlint   # with coverage
```

## Differential tests against Alertmanager

amlint simulates Alertmanager in Python, so its routing, inhibition and the config errors
it shares with `amtool check-config` are tested against the real thing. One command:

```bash
scripts/differential.sh
```

It downloads an Alertmanager release (0.34.1 by default) into `.cache/alertmanager/`,
checks the tarball's sha256 against the release's `sha256sums.txt`, and runs
`tests/differential/` with that release's `amtool` and `alertmanager` binaries:

- `test_routing.py`: generated configs and label sets, amlint's receivers against
  `amtool config routes test`
- `test_validation.py`: `amtool check-config` rejects a config exactly when amlint reports
  an error-level finding of a code amtool also catches (`ALSO_CAUGHT_BY_AMTOOL`)
- `test_inhibition.py`: alerts POSTed to a running `alertmanager`; each one's
  `status.inhibitedBy` and receivers against amlint's simulation

```bash
AM_VERSION=0.32.2 scripts/differential.sh                       # another release
AMLINT_DIFF_SEED=7 AMLINT_DIFF_EXAMPLES=2000 scripts/differential.sh -x   # explore more
```

Cases come from Hypothesis with a fixed seed (`AMLINT_DIFF_SEED`, default 0), so a run is
reproducible. CI prints the seed in the pytest header. On a mismatch the test reduces the
case to a minimal config and label set and prints both results, plus the `amtool` command
that reproduces it. Plain `pytest` skips these tests when `amtool` isn't on PATH.

CI runs them in `.github/workflows/differential.yml` against the last two minor releases
and the newest, on pull requests that touch the routing engine, and weekly with a fresh
seed. A mismatch is either a bug in amlint, which you fix with a regression test in
`test_linter.py`, or a deliberate difference or an Alertmanager bug, which you document in
[the fidelity page](fidelity.md) and teach the test to recognize, narrowly.

## Linting

```bash
ruff check amlint/
```

## How to add a new check

Every check is a standalone function in [`amlint/linter.py`](https://github.com/danikdanik2013/amlint/blob/main/amlint/linter.py).
Adding one takes about 10 lines of code and a test.

**1. Write the function:**

```python
def check_your_rule(cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    route = cfg.get("route")
    if not route:
        return out
    for node, path in _walk_routes(route):
        if something_is_wrong(node):
            out.append(Finding(
                WARN,              # ERROR / WARN / INFO
                "your-rule-code",  # unique kebab-case identifier
                "Clear message explaining what's wrong and why it matters.",
                path,
            ))
    return out
```

**2. Register it in `ALL_CHECKS`:**

```python
ALL_CHECKS = [
    ...
    check_your_rule,
]
```

**3. Add a test:**

```python
def test_your_rule():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        # config that triggers the rule
    }
    assert "your-rule-code" in codes(cfg)
```

**4. Add docs:**

Add a section to the relevant page under `docs/checks/`.

## Severity guide

| Level | Use when |
|-------|----------|
| `ERROR` | Alertmanager will drop alerts or refuse to start |
| `WARN` | Almost certainly not what the author intended |
| `INFO` | Suspicious, worth reviewing, but may be intentional |

## Opening a PR

- One check per PR is easiest to review
- Tests must pass: `pytest test_linter.py -v`
- No ruff errors: `ruff check amlint/`
- Update the checks table in `docs/checks/index.md`
- Update `CHANGELOG.md`
