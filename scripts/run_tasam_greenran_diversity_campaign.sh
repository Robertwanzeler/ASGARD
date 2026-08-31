#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${GREENRAN_CAMPAIGN_MANIFEST:-$ROOT/config/tasam_greenran_diversity_campaign.json}"
CAMPAIGN_ROOT="${GREENRAN_CAMPAIGN_ROOT:-$ROOT/runs/tasam_greenran_diversity_$(date +%Y%m%d_%H%M%S)}"
SHADOW_STATE="${GREENRAN_SHADOW_STATE_DIR:-$ROOT/runs/greenran_tasam_3du_article_adapted_20260730}"
ROUND_SECONDS="${GREENRAN_CAMPAIGN_ROUND_SECONDS:-1200}"
EVAL_MANIFEST="${GREENRAN_TASAM_EVAL_MANIFEST:-$ROOT/runs/tasam_greenran_real/tasam_candidate_evaluation_latest.json}"

mkdir -p "$CAMPAIGN_ROOT"
cp "$MANIFEST" "$CAMPAIGN_ROOT/campaign_manifest.json"

echo "[CAMPAIGN] preservando dados em $SHADOW_STATE"
GREENRAN_STATE_DIR="$SHADOW_STATE" "$ROOT/scripts/stop_tasam_article_ns3_collection.sh" > "$CAMPAIGN_ROOT/stop_shadow.log" 2>&1 || true

mapfile -t VARIANTS < <(/usr/bin/python3 - "$MANIFEST" <<'PY'
import json, sys
for item in json.load(open(sys.argv[1], encoding='utf-8'))['variants']:
    print(json.dumps(item, separators=(',', ':')))
PY
)

for raw in "${VARIANTS[@]}"; do
  id=$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$raw")
  state="$CAMPAIGN_ROOT/$id"
  log="$state/launcher.log"
  mkdir -p "$state"
  ue=$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["ue_count"])' "$raw")
  cam=$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["camera_ue_count"])' "$raw")
  veh=$(/usr/bin/python3 -c 'import json,sys; print(json.loads(sys.argv[1])["vehicle_ue_count"])' "$raw")
  echo "[CAMPAIGN] iniciando $id: ue=$ue camera=$cam vehicle=$veh"
  env GREENRAN_STATE_DIR="$state" \
      GREENRAN_TASAM_EVAL_MANIFEST="$EVAL_MANIFEST" \
      GREENRAN_NS3_UE_COUNT="$ue" GREENRAN_NS3_CAMERA_UE_COUNT="$cam" \
      GREENRAN_NS3_VEHICLE_UE_COUNT="$veh" GREENRAN_NS3_MMWAVE_ENB_NODES=3 \
      GREENRAN_START_RIC=1 GREENRAN_TASAM_TRUE_ONLINE_ENABLED=0 \
      GREENRAN_ML_RETRAIN_ENABLED=false GREENRAN_REQUIRE_REAL_PDCP=0 \
      GREENRAN_SIM_TIME=600 "$ROOT/scripts/run_greenran_tasam_3du_collection.sh" > "$log" 2>&1 &
  launcher=$!
  echo "$launcher" > "$state/launcher.pid"
  sleep "$ROUND_SECONDS"
  GREENRAN_STATE_DIR="$state" "$ROOT/scripts/stop_tasam_article_ns3_collection.sh" >> "$log" 2>&1 || true
  echo "[CAMPAIGN] finalizada $id"
done

echo "[CAMPAIGN] coletas concluídas em $CAMPAIGN_ROOT"
