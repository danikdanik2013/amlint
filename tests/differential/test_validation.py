"""`amtool check-config` rejects a config exactly when amlint reports an error that
ALSO_CAUGHT_BY_AMTOOL says amtool catches too."""

import re

from conftest import EXAMPLES, PHASES, SEED
from harness import dump, write_config
from hypothesis import event, given, seed, settings
from reduce import reduce
from strategies import validation_configs

from amlint.cli import load
from amlint.explains import ALSO_CAUGHT_BY_AMTOOL
from amlint.linter import ERROR, lint

# A known gap (docs/fidelity.md): amlint doesn't check Unicode class names in \p{...}.
# Which ones Go accepts depends on the Go version Alertmanager is built with (Go 1.25 added
# aliases such as \p{Letter} and case-insensitive lookup).
UNKNOWN_UNICODE_CLASS = re.compile(r"invalid character class range: `\\[pP]")


def outcome(amtool, workdir: str, cfg: dict, name: str = "validation.yml"):
    """(amtool accepts, its message, amlint's errors that amtool also catches)."""
    path = write_config(cfg, workdir, name)
    accepted, output = amtool.check_config(path)
    # "Checking '<path>'  FAILED: <why>" -> "<why>"
    why = re.sub(r"(?s)^Checking '[^']*'\s*", "", output).split("\n")[0]
    errors = [f for f in lint(load(path)) if f.level == ERROR and f.code in ALSO_CAUGHT_BY_AMTOOL]
    return accepted, why, errors


@seed(SEED)
@settings(settings.get_profile("differential"), max_examples=EXAMPLES, phases=PHASES)
@given(cfg=validation_configs())
def test_check_config_parity(amtool, workdir, cfg):
    accepted, why, errors = outcome(amtool, workdir, cfg)
    for f in errors:
        event(f.code)
    if accepted == (not errors):
        return
    if not accepted and not errors and UNKNOWN_UNICODE_CLASS.search(why):
        event("known gap: amlint does not check Unicode class names")
        return

    def fails(candidate) -> bool:  # still disagrees, the same way and for the same reason
        if not isinstance(candidate, dict):
            return False
        a, w, e = outcome(amtool, workdir, candidate, "reduce.yml")
        return a == accepted and w == why and bool(e) == bool(errors) and (
            {f.code for f in e} == {f.code for f in errors})

    cfg = reduce(cfg, fails)
    accepted, why, errors = outcome(amtool, workdir, cfg, "reduced.yml")
    found = "\n".join(f"  {f.code} at {f.where}: {f.msg}" for f in errors) or "  (none)"
    raise AssertionError(
        f"amtool check-config {'accepts' if accepted else 'rejects'} the config, but amlint "
        f"reports {'errors' if errors else 'no error'} that amtool catches too "
        f"(Alertmanager {amtool.version})\n\namtool:  {why}\namlint:\n{found}\n\n"
        f"config.yml (reduced):\n{dump(cfg)}"
    )
