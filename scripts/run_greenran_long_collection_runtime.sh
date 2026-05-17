#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

export GREENRAN_SIM_TIME="${GREENRAN_SIM_TIME:-200000}"
export GREENRAN_APP1_CAMERA_SOURCE_MODE="${GREENRAN_APP1_CAMERA_SOURCE_MODE:-simulated}"
export GREENRAN_APP1_CAMERAS_BOOTSTRAP="${GREENRAN_APP1_CAMERAS_BOOTSTRAP:-$BASE_DIR/config/core/app1_cameras.simulated.json}"
export GREENRAN_CARLA_MODE="${GREENRAN_CARLA_MODE:-mock}"
export GREENRAN_CARLA_FALLBACK_TO_MOCK="${GREENRAN_CARLA_FALLBACK_TO_MOCK:-1}"

cd "$BASE_DIR"
exec ./scripts/run_greenran_carla_ns3.sh
