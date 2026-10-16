#!/bin/sh
# Differential tests of amlint against a real Alertmanager: fetches the release (cached in
# .cache/alertmanager/, checksum-verified) and runs tests/differential with its amtool and
# alertmanager binaries first on PATH. Extra arguments go to pytest.
#
#   scripts/differential.sh                      # Alertmanager $AM_VERSION, default below
#   AM_VERSION=0.32.2 scripts/differential.sh -x
#   AMLINT_DIFF_EXAMPLES=1000 AMLINT_DIFF_SEED=7 scripts/differential.sh
set -eu
cd "$(dirname "$0")/.."
python="${PYTHON:-python3}"
version="${AM_VERSION:-0.34.1}"
bindir=$("$python" scripts/fetch_alertmanager.py "$version" ${AM_SHA256:+--sha256 "$AM_SHA256"})
PATH="$bindir:$PATH" exec "$python" -m pytest tests/differential "$@"
