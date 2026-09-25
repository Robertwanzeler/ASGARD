# Manual de instalação do Orange-Nuclear

**Alvo:** Ubuntu 24.04 LTS<br>
**Stack:** FlexRIC + E2SIM + ns-O-RAN-flexric + ns-3/mmWave/LENA<br>
**Objetivo:** obter um simulador O-RAN funcional, com caminho documentado para build, execução e debug.

> Este arquivo é um guia. Nenhum comando é executado pelo Git de documentação. Faça a instalação somente em um servidor, VM, contêiner ou runtime autorizado.

## 0. Regra de isolamento

Crie um runtime novo e não use o diretório de outra simulação:

```bash
export ORAN_DEBUG_ROOT="$HOME/orange_nuclear_runtime"
mkdir -p "$ORAN_DEBUG_ROOT"
cd "$ORAN_DEBUG_ROOT"
```

Não execute `./ns3 clean`, `git checkout`, `docker rm`, `kill`, `pkill` ou `sudo` dentro de um ambiente que já esteja rodando uma simulação.

## 1. Requisitos

Recomendação mínima: 8 GB de RAM, 20 GB livres para fontes e build, conexão de rede e compilador C++ moderno. O ns-3 upstream lista C++, Python 3, CMake e Ninja/Make como requisitos básicos; o stack O-RAN acrescenta SCTP, ASN.1, Boost e bibliotecas de seus módulos.

Confira as ferramentas sem instalar nada:

```bash
cd /caminho/para/orange-nuclear-debug
./scripts/check_environment.sh
```

### 1.1 Dependências Ubuntu 24.04

Em um servidor autorizado:

```bash
sudo apt update
sudo apt install -y \
  build-essential git g++ cmake ninja-build make pkg-config \
  python3 python3-dev python3-pip python3-venv \
  libgsl-dev libxml2-dev libsqlite3-dev libeigen3-dev \
  libsctp-dev libboost-all-dev \
  autoconf automake libtool bison flex \
  gdb valgrind ccache
```

Pacotes opcionais para recursos adicionais:

```bash
sudo apt install -y libgtk-3-dev libclang-dev llvm-dev
```

Após a instalação:

```bash
./scripts/check_environment.sh
```

### 1.2 Python 3.8 legado

O ns-3 básico usa Python 3 do Ubuntu. Algumas ferramentas antigas do stack/GUI podem exigir Python 3.8. No Ubuntu 24.04, não substitua `/usr/bin/python3`; crie um ambiente virtual separado somente se a ferramenta exigir:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.8 python3.8-venv python3.8-dev
python3.8 -m venv "$ORAN_DEBUG_ROOT/python38-venv"
source "$ORAN_DEBUG_ROOT/python38-venv/bin/activate"
```

Se o pacote `python3.8` não estiver disponível, mantenha o Python 3.12/3.13 do Ubuntu para o ns-3 e não force a instalação de uma versão antiga no sistema.

## 2. FlexRIC

```bash
cd "$ORAN_DEBUG_ROOT"
git clone https://gitlab.eurecom.fr/mosaic5g/flexric.git flexric
cd flexric
git checkout oie-ric-taap-xapps
```

Para repetir o ambiente de referência conhecido, o commit utilizado foi:

```text
76cee3821f54b3429a2a0c58eadb79d7f274b3ab
```

Opcionalmente, fixe-o após o checkout da branch:

```bash
git checkout 76cee3821f54b3429a2a0c58eadb79d7f274b3ab
```

### 2.1 Correção dos xApps de exemplo

Ative `examples` para gerar o RIC e desative o subdiretório de xApps de exemplo que pode falhar no build:

```bash
sed -i 's/# add_subdirectory(examples)/add_subdirectory(examples)/g' CMakeLists.txt
sed -i 's/add_subdirectory(xApp)/# add_subdirectory(xApp)/g' examples/CMakeLists.txt
git diff -- CMakeLists.txt examples/CMakeLists.txt
```

Se o `git diff` não mostrar a alteração esperada, pare e revise o arquivo antes de compilar; não aplique `sed` em outro checkout.

### 2.2 Build do FlexRIC

```bash
cd "$ORAN_DEBUG_ROOT/flexric"
mkdir -p build
cd build
cmake .. \
  -DE2AP_VERSION=E2AP_V1 \
  -DKPM_VERSION=KPM_V3_00 \
  -G Ninja
ninja
sudo ninja install
sudo ldconfig
```

O simulador Orange-Nuclear usa E2AP v1.01 e KPM v3.00; não misture o RIC compilado com E2AP/KPM incompatíveis.

Valide sem iniciar o serviço:

```bash
test -x "$ORAN_DEBUG_ROOT/flexric/build/examples/ric/nearRT-RIC"
ldd "$ORAN_DEBUG_ROOT/flexric/build/examples/ric/nearRT-RIC"
```

## 3. ns-O-RAN-flexric, E2SIM e ns-3

```bash
cd "$ORAN_DEBUG_ROOT"
git clone --recurse-submodules \
  https://github.com/Orange-OpenSource/ns-O-RAN-flexric.git \
  ns-O-RAN-flexric
cd ns-O-RAN-flexric
git submodule update --init --recursive
```

O commit de referência do projeto Orange foi:

```text
78cacdadb493c941f1a15efde22c5da4ee574426
```

Para reproduzir esse estado:

```bash
git checkout 78cacdadb493c941f1a15efde22c5da4ee574426
git submodule update --init --recursive
```

> Se o checkout por commit falhar por divergência do repositório remoto, permaneça na branch oficial e registre o commit retornado por `git rev-parse HEAD`.

### 3.1 Build do E2SIM

O E2SIM é necessário para a terminação E2 e para a comunicação com o FlexRIC:

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/e2sim-kpmv3/e2sim"
mkdir -p build
sudo ./build_e2sim.sh 2
```

Para logs mais detalhados durante debug, use nível 3:

```bash
sudo ./build_e2sim.sh 3
```

Níveis documentados:

| Nível | Uso |
|---:|---|
| 0 | somente mensagens incondicionais |
| 1 | erros de codificação e falhas |
| 2 | informações gerais; padrão recomendado |
| 3 | debug completo e impressão ASN.1 |

### 3.2 Configuração do ns-3/mmWave

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran"
./ns3 clean
./ns3 configure \
  --build-profile=debug \
  --disable-examples \
  --disable-tests \
  --enable-modules=nr,mmwave,oran-interface,lte,energy
```

O módulo `energy` é obrigatório para os headers e modelos de energia usados pelo cenário Orange-Nuclear.

Compile primeiro com uma thread, para diagnosticar erros claramente:

```bash
./ns3 build -j1
```

Depois, se o build estiver correto:

```bash
./ns3 build -j"$(nproc)"
```

Valide os alvos:

```bash
./ns3 show targets
test -x ./ns3
```

## 4. Execução em dois terminais

Use dois terminais no mesmo `ORAN_DEBUG_ROOT`.

### Terminal 1 — FlexRIC

```bash
cd "$ORAN_DEBUG_ROOT/flexric/build/examples/ric"
./nearRT-RIC
```

Aguarde a inicialização do nearRT-RIC e as mensagens de SCTP.

### Terminal 2 — ns-3

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran"
./ns3 run scratch/scenario-zero-with_parallel_loging
```

Para passar parâmetros ao cenário:

```bash
./ns3 run "scratch/scenario-zero-with_parallel_loging --e2TermIp=127.0.0.1 --simTime=100"
```

Se aparecer `Connection refused`, confirme primeiro que o RIC do mesmo runtime está ativo no Terminal 1. Não pare serviços de outro ambiente.

## 5. Checklist final

```bash
/caminho/para/orange-nuclear-debug/scripts/check_environment.sh "$ORAN_DEBUG_ROOT"
test -x "$ORAN_DEBUG_ROOT/flexric/build/examples/ric/nearRT-RIC"
test -x "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran/ns3"
test -x "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/e2sim-kpmv3/e2sim/build_e2sim.sh"
git -C "$ORAN_DEBUG_ROOT/flexric" rev-parse HEAD
git -C "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric" rev-parse HEAD
git -C "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric" submodule status
```

O runtime está pronto quando o RIC, o wrapper `ns3`, o E2SIM e os submódulos podem ser localizados e o build termina sem erro.
