#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/4] Core tests"
python3 -m unittest discover -s tests -v

echo "[2/4] App1 tests"
python3 -m unittest discover -s apps/app1_vigilancia/tests -v

echo "[3/4] App2 tests"
python3 -m unittest discover -s apps/app2_monitoramento/tests -v

echo "[4/4] App3 tests"
python3 -m unittest discover -s apps/app3_veicular/tests -v
