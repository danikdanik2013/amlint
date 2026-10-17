# Contributing to amlint

This is the short version: set up, run the tests, open a pull request. The full guide, with
how to add a check, the tests against the real Alertmanager and the browser playground, is
[docs/contributing.md](docs/contributing.md), also on the
[docs site](https://danikdanik2013.github.io/amlint/contributing/).

## Set up

```bash
git clone https://github.com/danikdanik2013/amlint
cd amlint
pip install -e ".[dev]"
```

You need Python 3.9 or later.

## Run the tests

CI runs these on Python 3.9 to 3.15:

```bash
ruff check amlint/
pytest test_linter.py test_formats.py test_lsp.py -v
```

If you change routing, inhibition or how configs are read, also run the differential tests
against a real Alertmanager: `scripts/differential.sh`. See
[Differential tests](docs/contributing.md#differential-tests-against-alertmanager).

## Open a pull request

- One check or one fix per pull request is easiest to review.
- Include a test that fails without your change and passes with it.
- `ruff check amlint/` and `pytest test_linter.py test_formats.py test_lsp.py` pass.
- For a new check, follow [Add a check](docs/contributing.md#how-to-add-a-new-check): the
  function, `ALL_CHECKS`, `EXPLAINS` and `_DOCS_PAGES`, a test, and its docs under
  `docs/checks/`.
- Add a line to `CHANGELOG.md` under `## [Unreleased]`.

## More

- [Add a check](docs/contributing.md#how-to-add-a-new-check), and which severity to give it
- [Differential tests against Alertmanager](docs/contributing.md#differential-tests-against-alertmanager)
  and what to do when they fail
- [Working on the playground](docs/contributing.md#the-playground)
- Security issues: don't open a public issue; see [SECURITY.md](SECURITY.md).
- Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
