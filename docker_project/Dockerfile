FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build git pkg-config \
    python3 python3-dev python3-pip python3-venv \
    gdb valgrind ccache \
    libsqlite3-dev libxml2-dev libgtk-3-dev \
    libgsl-dev libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/ns3
COPY ns3-base/ /workspace/ns3/

CMD ["sleep", "infinity"]
