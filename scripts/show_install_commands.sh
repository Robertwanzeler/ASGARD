#!/usr/bin/env bash
set -u

cat <<'COMMANDS'
# Este script apenas imprime comandos. Nada será executado.

export ORAN_DEBUG_ROOT="$HOME/orange_nuclear_debug_runtime"
mkdir -p "$ORAN_DEBUG_ROOT"
cd "$ORAN_DEBUG_ROOT"

sudo apt update
sudo apt install -y git g++ cmake ninja-build build-essential \
  make pkg-config python3 python3-dev python3-pip python3-venv \
  libgsl-dev libxml2-dev libsqlite3-dev libeigen3-dev libsctp-dev \
  libboost-all-dev autoconf automake libtool bison flex gdb valgrind ccache

git clone https://gitlab.eurecom.fr/mosaic5g/flexric.git flexric
cd flexric
git checkout oie-ric-taap-xapps
# Referência validada no guia: 76cee3821f54b3429a2a0c58eadb79d7f274b3ab
sed -i 's/# add_subdirectory(examples)/add_subdirectory(examples)/g' CMakeLists.txt
sed -i 's/add_subdirectory(xApp)/# add_subdirectory(xApp)/g' examples/CMakeLists.txt
mkdir -p build
cd build
cmake .. -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00 -G Ninja
ninja
sudo ninja install
sudo ldconfig

cd "$ORAN_DEBUG_ROOT"
git clone https://github.com/Orange-OpenSource/ns-O-RAN-flexric.git ns-O-RAN-flexric
cd ns-O-RAN-flexric
# Referência validada no guia: 78cacdadb493c941f1a15efde22c5da4ee574426
git submodule update --init --recursive

# E2SIM (opcional para o primeiro build, necessário para o fluxo E2 completo)
cd e2sim-kpmv3/e2sim
mkdir -p build
sudo ./build_e2sim.sh 2

cd mmwave-LENA-oran
./ns3 clean
./ns3 configure --disable-examples --disable-tests \
  --build-profile=debug \
  --enable-modules=nr,mmwave,oran-interface,lte,energy
./ns3 build -j1
./ns3 build -j"$(nproc)"

# Terminal 1
cd "$ORAN_DEBUG_ROOT/flexric/build/examples/ric"
./nearRT-RIC

# Terminal 2
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran"
./ns3 run scratch/scenario-zero-with_parallel_loging
COMMANDS
