#!/bin/bash
set -euo pipefail
exec "$(dirname "$0")/scripts/test_cenario.sh" "$@"
