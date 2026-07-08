#!/bin/bash
set -euo pipefail
exec "$(dirname "$0")/scripts/run_simulation.sh" "$@"
