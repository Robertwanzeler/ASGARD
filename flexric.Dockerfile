# Dockerfile para o FlexRIC
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y \
    build-essential cmake git libssl-dev libpcre3-dev \
    swig python3-dev pkg-config

WORKDIR /flexric
COPY flexric/ .

# Build do FlexRIC
RUN mkdir build && cd build && cmake .. && make -j$(nproc)

# Final
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y libssl-dev
WORKDIR /flexric
COPY --from=builder /flexric/build/ /flexric/build/

# Setup para rodar xApps
ENTRYPOINT ["/flexric/build/examples/xApp/c/energy_saver/xapp_energy_saver"]
