# Instalação do ns-3

## Requisitos

Ubuntu 22.04 ou mais recente, compilador C++ moderno e pelo menos 8 GB de RAM. Para o build completo, reserve espaço adicional para os artefatos em `ns3-base/build/`.

## Dependências no Ubuntu

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake ninja-build git pkg-config \
  python3 python3-dev python3-pip python3-venv \
  gdb valgrind ccache \
  libsqlite3-dev libxml2-dev libgtk-3-dev \
  libgsl-dev libeigen3-dev
```

Os pacotes opcionais podem ser retirados se o projeto não usar GTK, GSL, Eigen ou bindings Python.

## Obter o código

Clone o repositório principal com o submódulo:

```bash
git clone --recurse-submodules <URL_DO_REPOSITORIO>
cd <diretorio-do-repositorio>
```

Se o repositório já foi clonado:

```bash
git submodule update --init --recursive
```

## Configurar e compilar

Para desenvolvimento e debug, use o perfil `debug`:

```bash
cd ns3-base
./ns3 configure --build-profile=debug --enable-examples --enable-tests
./ns3 build
```

Para uma compilação mais rápida e menor:

```bash
./ns3 configure --build-profile=release --disable-examples --disable-tests
./ns3 build
```

Depois de mudar as opções de configuração, reconfigure com `--force-refresh` quando necessário:

```bash
./ns3 configure --force-refresh --build-profile=debug \
  --enable-examples --enable-tests
```

## Validar a instalação

```bash
./ns3 show version
./ns3 show targets
./ns3 run hello-simulator
./test.py
```

O build fica em `ns3-base/build/`. Esse diretório é gerado e não deve ser commitado.

## Ambiente Docker

O ambiente em `docker_project/` instala as ferramentas, habilita GDB/Valgrind e monta o checkout local do ns-3.

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

O contêiner usa `SYS_PTRACE` e um perfil seccomp permissivo somente para permitir o GDB. O código e os artefatos continuam montados no diretório local.
