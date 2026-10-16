"""
Differential tests: amlint against the real Alertmanager. Run them with
`scripts/differential.sh`, which fetches a release; they are skipped when amtool (or, for
the inhibition test, alertmanager) is not on PATH.

    AMLINT_DIFF_SEED       Hypothesis seed (default 0): the same seed generates the same cases
    AMLINT_DIFF_EXAMPLES   generated configs per routing / validation test (default 300)
    AMLINT_DIFF_INHIBIT_EXAMPLES   configs for the inhibition test, one Alertmanager each (40)
"""

import os

import pytest

try:
    import hypothesis
except ImportError:  # pragma: no cover - hypothesis is a dev dependency
    hypothesis = None
    collect_ignore_glob = ["test_*.py"]

SEED = int(os.environ.get("AMLINT_DIFF_SEED", "0"))
EXAMPLES = int(os.environ.get("AMLINT_DIFF_EXAMPLES", "300"))
INHIBIT_EXAMPLES = int(os.environ.get("AMLINT_DIFF_INHIBIT_EXAMPLES", "40"))

if hypothesis is not None:
    from hypothesis import HealthCheck, Phase, settings

    # No shrinking: a failure is reduced by reduce.py, which keeps the failing labels fixed.
    PHASES = (Phase.explicit, Phase.reuse, Phase.generate)

    settings.register_profile(
        "differential",
        deadline=None,
        database=None,  # the seed alone decides which cases run
        print_blob=True,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                               HealthCheck.filter_too_much],
    )


def pytest_report_header(config):
    from harness import binary_version, find_binary

    lines = [f"amlint differential: seed {SEED}, {EXAMPLES} examples per test "
             f"(AMLINT_DIFF_SEED / AMLINT_DIFF_EXAMPLES)"]
    for name in ("amtool", "alertmanager"):
        path = find_binary(name)
        lines.append(f"  {name}: {binary_version(path) + ' at ' + path if path else 'not found'}")
    return lines


def _binary(name: str) -> str:
    from harness import find_binary

    path = find_binary(name)
    if not path:
        pytest.skip(f"{name} is not on PATH; run scripts/differential.sh to fetch Alertmanager")
    return path


@pytest.fixture(scope="session")
def amtool():
    from harness import Amtool

    return Amtool(_binary("amtool"))


@pytest.fixture(scope="session")
def alertmanager_binary():
    return _binary("alertmanager")


@pytest.fixture(scope="session")
def workdir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("differential"))
