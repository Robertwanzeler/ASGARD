#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
cd "$ROOT_DIR"

if ! "$GREENRAN_PYTHON_BIN" -c 'import pytest' >/dev/null 2>&1; then
    GREENRAN_PYTHON_BIN="${GREENRAN_TEST_PYTHON:-python3}"
fi
"$GREENRAN_PYTHON_BIN" -c 'import pytest' >/dev/null

TEST_PATHS=(tests)
for path in apps/app1_vigilancia/tests apps/app2_monitoramento/tests apps/app3_veicular/tests; do
    if [[ -d "$path" ]]; then
        TEST_PATHS+=("$path")
    fi
done

# pytest is the official collector and also executes unittest.TestCase classes.
# Strict collection prevents an empty/malformed test file from going unnoticed.
exec "$GREENRAN_PYTHON_BIN" -m pytest --strict --strict-config "${TEST_PATHS[@]}" -ra
