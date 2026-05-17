#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTANCE_NAME="${1:-parallel01}"
PORT_OFFSET="${GREENRAN_PORT_OFFSET:-100}"
STATE_BASE="${GREENRAN_PARALLEL_STATE_BASE:-/tmp/greenran_instances}"
STATE_DIR="${GREENRAN_STATE_DIR:-$STATE_BASE/$INSTANCE_NAME}"

mkdir -p "$STATE_DIR"

export GREENRAN_INSTANCE_NAME="$INSTANCE_NAME"
export GREENRAN_STATE_DIR="$STATE_DIR"
export GREENRAN_CLEAN_SCOPE="${GREENRAN_CLEAN_SCOPE:-instance}"
export GREENRAN_ENABLE_MONITORING_STACK="${GREENRAN_ENABLE_MONITORING_STACK:-0}"
export GREENRAN_DISABLE_RIC="${GREENRAN_DISABLE_RIC:-1}"
export GREENRAN_PORT_OFFSET="$PORT_OFFSET"

echo "Instância paralela GreenRAN"
echo "  instance: $GREENRAN_INSTANCE_NAME"
echo "  state_dir: $GREENRAN_STATE_DIR"
echo "  port_offset: $GREENRAN_PORT_OFFSET"
echo "  clean_scope: $GREENRAN_CLEAN_SCOPE"
echo "  disable_ric: $GREENRAN_DISABLE_RIC"
echo "  monitoring_stack: $GREENRAN_ENABLE_MONITORING_STACK"
echo "  app1_port: $((5100 + PORT_OFFSET))"
echo "  app2_port: $((5200 + PORT_OFFSET))"
echo "  app3_port: $((5300 + PORT_OFFSET))"

exec "$PROJECT_DIR/scripts/run_greenran_v2.sh"
