#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="$ROOT/systemd/greenran-tasam-energy-calibration.service"
OUTPUT_ROOT="$ROOT/runs/tasam_energy_calibration_sim_v5"
install -D -m 0644 "$UNIT" /etc/systemd/system/greenran-tasam-energy-calibration.service
install -d -o root -g root -m 0755 "$OUTPUT_ROOT"
systemctl daemon-reload
echo "Serviço instalado. Para iniciar:"
echo "  sudo systemctl start greenran-tasam-energy-calibration.service"
echo "Para acompanhar:"
echo "  sudo systemctl status greenran-tasam-energy-calibration.service --no-pager"
