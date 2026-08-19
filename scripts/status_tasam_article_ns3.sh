#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export GREENRAN_STATE_DIR="${GREENRAN_STATE_DIR:-$PROJECT_ROOT/runs/tasam_article_ns3_collection}"
exec python3 "$PROJECT_ROOT/scripts/status.py"
