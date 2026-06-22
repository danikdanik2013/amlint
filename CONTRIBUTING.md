# Contributing to amlint

## Setup

```bash
git clone https://github.com/danikdanik2013/amlint
cd amlint
pip install -e ".[dev]"
```

## Running tests

```bash
pytest test_linter.py -v
```

## How to add a new check

Every check is a standalone function in [amlint/linter.py](amlint/linter.py).
Adding one takes about 5 lines of logic and a test.

**1. Write the function:**

```python
def check_your_rule(cfg):
    out = []
    # cfg is the parsed alertmanager.yml dict
    # use _walk_routes(cfg["route"]) to iterate all routes
    if something_wrong:
        out.append(Finding(
            WARN,              # ERROR / WARN / INFO
            "your-rule-code",  # unique kebab-case code
            "Human-readable message explaining what's wrong and why it matters.",
            "route.routes[0]", # where in the config (optional)
        ))
    return out
```

**2. Register it:**

```python
ALL_CHECKS = [
    ...
    check_your_rule,   # add here
]
```

**3. Add a test:**

```python
def test_your_rule():
    cfg = {
        "route": {"receiver": "a"},
        "receivers": [{"name": "a"}],
        # minimal config that triggers the rule
    }
    assert "your-rule-code" in codes(cfg)
```

## Severity guide

| Level | Use when |
|-------|----------|
| `ERROR` | Alertmanager will drop or fail to route alerts |
| `WARN` | Almost certainly not what the author intended |
| `INFO` | Suspicious, worth reviewing, but may be intentional |

## Opening a PR

- One check per PR is easiest to review
- Include a test that fails without the fix and passes with it
- Update the checks table in README.md
