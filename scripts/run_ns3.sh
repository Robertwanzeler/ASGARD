#!/bin/bash
# GreenRAN - Iniciar ns-3
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NS3_DIR="${GREENRAN_NS3_DIR:-${PROJECT_DIR}/ns-O-RAN-flexric/mmwave-LENA-oran}"
cd "${NS3_DIR}"

SIM_TIME="${SIM_TIME:-600}"
NS3_BINARY="${GREENRAN_NS3_BINARY:-${NS3_DIR}/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default}"
exec "${NS3_BINARY}" --e2TermIp=127.0.0.1 --e2lteEnabled=true --e2nrEnabled=true --simTime="${SIM_TIME}"
