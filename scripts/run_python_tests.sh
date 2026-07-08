#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$(dirname "$0")/core_runtime.sh"
load_greenran_runtime
cd "$ROOT_DIR"

python_has_module() {
    "$GREENRAN_PYTHON_BIN" -c "import $1" >/dev/null 2>&1
}

echo "[1/4] Core tests"
"$GREENRAN_PYTHON_BIN" -m unittest discover -s tests -v

echo "[2/4] App1 tests"
if python_has_module flask; then
    "$GREENRAN_PYTHON_BIN" -m unittest discover -s apps/app1_vigilancia/tests -v
else
    echo "SKIPPED: Flask não está instalado no host para App1"
fi

echo "[3/4] App2 tests"
if python_has_module flask; then
    "$GREENRAN_PYTHON_BIN" -m unittest discover -s apps/app2_monitoramento/tests -v
else
    echo "SKIPPED: Flask não está instalado no host para App2"
fi

echo "[4/4] App3 tests"
"$GREENRAN_PYTHON_BIN" -m unittest discover -s apps/app3_veicular/tests -v
