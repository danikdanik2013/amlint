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
