#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/core_runtime.sh"
load_greenran_runtime

RIC_BIN="${GREENRAN_RIC_BIN:-$RIC_DIR/examples/ric/nearRT-RIC}"
exec "$RIC_BIN" -p "$FLEXRIC_LIB" -c "$PROJECT_DIR/flexric/flexric.conf" \
  -e "${GREENRAN_E2_TERM_PORT:-36421}" -x "${GREENRAN_E2_XAPP_PORT:-36422}"
