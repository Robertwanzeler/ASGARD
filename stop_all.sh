#!/bin/bash
set -euo pipefail
exec "$(dirname "$0")/scripts/stop_all.sh" "$@"
