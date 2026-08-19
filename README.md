# ns-3: instalação e debug

Este repositório foi reduzido ao necessário para instalar, compilar, testar e depurar o [ns-3](https://www.nsnam.org/).

## Estrutura

- `ns3-base/`: código-fonte do ns-3 mantido como submódulo.
- `docs/INSTALL.md`: instalação no Ubuntu e compilação local.
- `docs/DEBUG.md`: debug de C++, Python, memória, logs e testes.
- `docker_project/`: ambiente reproduzível com Docker para desenvolvimento e GDB.

## Instalação local rápida

```bash
sudo apt update
sudo apt install -y build-essential cmake ninja-build git \
  python3 python3-dev python3-pip python3-venv pkg-config \
  gdb valgrind libsqlite3-dev libxml2-dev libgtk-3-dev \
  libgsl-dev libeigen3-dev

git clone --recurse-submodules <URL_DO_REPOSITORIO>
cd <diretorio-do-repositorio>/ns3-base
./ns3 configure --build-profile=debug --enable-examples --enable-tests
./ns3 build
./test.py
```

O passo a passo completo está em [docs/INSTALL.md](docs/INSTALL.md).

## Usando Docker

```bash
cd docker_project
make build
make run
make shell
```

Dentro do contêiner:

```bash
cd /workspace/ns3
./ns3 configure --build-profile=debug --enable-examples --enable-tests
./ns3 build
./ns3 run hello-simulator
```

Para depurar um programa pelo GDB:

```bash
./ns3 run hello-simulator --gdb
```

Consulte [docs/DEBUG.md](docs/DEBUG.md) para os comandos completos.
