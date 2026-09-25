# Guia de debug do Orange-Nuclear

Use sempre o runtime isolado em `ORAN_DEBUG_ROOT`. Antes de alterar qualquer coisa, confirme que o caminho não pertence a uma simulação em execução.

## 1. Diagnóstico inicial

```bash
./scripts/check_environment.sh "$ORAN_DEBUG_ROOT"
```

Verifique as versões:

```bash
git --version
g++ --version
cmake --version
ninja --version
python3 --version
```

Verifique commits e submódulos:

```bash
git -C "$ORAN_DEBUG_ROOT/flexric" rev-parse HEAD
git -C "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric" rev-parse HEAD
git -C "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric" submodule status
```

## 2. Ubuntu 24.04

O ns-3 básico usa Python 3. Para ferramentas antigas que exigem Python 3.8, use um venv no runtime separado. Não substitua `/usr/bin/python3`. Se o pacote legado não estiver disponível, use Ubuntu 22.04 ou uma VM/contêiner compatível.

## 3. FlexRIC

### `add_subdirectory(xApp)`

Confira a correção dentro do clone isolado:

```bash
cd "$ORAN_DEBUG_ROOT/flexric"
git diff -- CMakeLists.txt examples/CMakeLists.txt
```

O resultado deve mostrar `examples` habilitado e `xApp` comentado. Se as linhas esperadas não existirem, revise a branch/commit antes de aplicar qualquer correção.

### E2AP/KPM incompatíveis

O Orange-Nuclear exige:

```text
E2AP_V1
KPM_V3_00
```

Reconfigure somente o build do FlexRIC no runtime isolado:

```bash
cd "$ORAN_DEBUG_ROOT/flexric"
rm -rf build
mkdir build
cd build
cmake .. -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00 -G Ninja
ninja
```

### Erro de link ou biblioteca ausente

```bash
ldd "$ORAN_DEBUG_ROOT/flexric/build/examples/ric/nearRT-RIC"
sudo ldconfig
```

## 4. E2SIM

Se o E2SIM não foi compilado:

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/e2sim-kpmv3/e2sim"
mkdir -p build
sudo ./build_e2sim.sh 2
```

Para investigar mensagens E2AP/KPM:

```bash
sudo ./build_e2sim.sh 3
```

Nível 2 é informativo; nível 3 inclui debug detalhado e estruturas ASN.1.

## 5. Build do ns-3

### `ns3/energy-model-helper.h: No such file`

O módulo `energy` não foi incluído ou o comando foi executado em outro checkout:

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran"
./ns3 clean
./ns3 configure \
  --build-profile=debug \
  --disable-examples \
  --disable-tests \
  --enable-modules=nr,mmwave,oran-interface,lte,energy
./ns3 build -j1
```

### Build falha sem mensagem clara

```bash
./ns3 build -j1
./ns3 show config
./ns3 show targets
```

Após alterações de dependências ou módulos, use `--force-refresh`:

```bash
./ns3 configure --force-refresh \
  --build-profile=debug \
  --disable-examples \
  --disable-tests \
  --enable-modules=nr,mmwave,oran-interface,lte,energy
```

### Submódulo vazio

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric"
git submodule update --init --recursive
git submodule status
```

## 6. GDB

Compile em `debug` e execute o cenário pelo wrapper:

```bash
cd "$ORAN_DEBUG_ROOT/ns-O-RAN-flexric/mmwave-LENA-oran"
./ns3 run scratch/scenario-zero-with_parallel_loging --gdb
```

Comandos essenciais:

```gdb
break main
run
next
step
backtrace
info locals
print nome_da_variavel
continue
quit
```

## 7. Valgrind

```bash
./ns3 run scratch/scenario-zero-with_parallel_loging --valgrind
```

Para relatório detalhado:

```bash
valgrind --leak-check=full \
  --show-leak-kinds=all \
  --track-origins=yes \
  --log-file=valgrind-ns3.txt \
  ./ns3 run scratch/scenario-zero-with_parallel_loging
```

## 8. Logs

```bash
NS_LOG='Simulator=level_all|prefix_time' \
  ./ns3 run scratch/scenario-zero-with_parallel_loging
```

Para salvar a saída:

```bash
NS_LOG='level_info|prefix_time' \
  ./ns3 run scratch/scenario-zero-with_parallel_loging \
  > ns3-simulation.log 2>&1
rg -ni 'error|fatal|warning|assert|exception|sctp|refused' ns3-simulation.log
```

## 9. `Connection refused` ou SCTP sem conexão

Valide sem parar processos:

```bash
test -x "$ORAN_DEBUG_ROOT/flexric/build/examples/ric/nearRT-RIC"
pgrep -af nearRT-RIC || true
ss -ltnp 2>/dev/null || true
```

Ordem esperada:

1. O nearRT-RIC está iniciado no Terminal 1.
2. O ns-3 usa o mesmo runtime.
3. `e2TermIp` aponta para o host correto.
4. Nenhum outro runtime usa a mesma porta.

Não use `kill`, `pkill`, `docker rm` ou reinicialização global para corrigir conflito de porta.

## 10. Teste mínimo

```bash
./ns3 run hello-simulator
./ns3 run scratch/scenario-zero-with_parallel_loging
```

Só depois valide a integração com o FlexRIC.
