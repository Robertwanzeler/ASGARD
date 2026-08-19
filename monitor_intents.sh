#!/bin/bash
set -euo pipefail
exec "$(dirname "$0")/monitoring/monitor_intents.sh" "$@"
