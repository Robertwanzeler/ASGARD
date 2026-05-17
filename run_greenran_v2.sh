#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

# Habilita monitoramento por padrão; o usuário ainda pode sobrescrever no shell.
export GREENRAN_ENABLE_MONITORING_STACK="${GREENRAN_ENABLE_MONITORING_STACK:-1}"

cd "$BASE_DIR"
exec bash "$BASE_DIR/scripts/run_greenran_v2.sh" "$@"
