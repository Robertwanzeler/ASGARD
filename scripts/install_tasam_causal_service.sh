#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SOURCE="$ROOT/systemd/greenran-tasam-causal-pilot.service"
UNIT_TARGET="/etc/systemd/system/greenran-tasam-causal-pilot.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERRO: execute este instalador com sudo." >&2
  exit 4
fi
if [[ ! -f "$UNIT_SOURCE" ]]; then
  echo "ERRO: unidade ausente: $UNIT_SOURCE" >&2
  exit 2
fi

install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
install -d -o root -g root -m 0755 "$ROOT/runs/tasam_local_causal_pilot_seed47_20260906_v11"
systemctl daemon-reload
systemctl enable greenran-tasam-causal-pilot.service
echo "Serviço instalado. Para iniciar a campanha v11:"
echo "  sudo systemctl start greenran-tasam-causal-pilot.service"
echo "Para acompanhar:"
echo "  sudo systemctl status greenran-tasam-causal-pilot.service"
echo "  tail -f $ROOT/runs/tasam_local_causal_pilot_seed47_20260906_v11/campaign_console.log"
