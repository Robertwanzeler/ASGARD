# Debug do ns-3

Todos os comandos abaixo devem ser executados em `ns3-base`, ou em `/workspace/ns3` quando estiver usando Docker.

## GDB

Compile em modo debug e execute o alvo com o suporte nativo do wrapper `ns3`:

```bash
./ns3 configure --build-profile=debug --enable-examples --enable-tests
./ns3 build
./ns3 run hello-simulator --gdb
```

Ao entrar no GDB:

```gdb
break main
run
next
step
print nome_da_variavel
backtrace
continue
quit
```

Para um programa próprio, coloque o arquivo C++ em `scratch/` e execute:

```bash
./ns3 run meu-programa --gdb
```

## Debug de falhas de compilação

Use uma única thread para obter uma mensagem de erro mais legível:

```bash
./ns3 build -j1
```

Veja a configuração e os alvos disponíveis:

```bash
./ns3 show config
./ns3 show targets
```

Após alterar módulos ou dependências:

```bash
./ns3 clean
./ns3 configure --force-refresh --build-profile=debug \
  --enable-examples --enable-tests
./ns3 build -j1
```

## Logs do simulador

Ative os logs de um componente sem recompilar:

```bash
NS_LOG='Simulator=level_all|prefix_time' ./ns3 run hello-simulator
```

Salve a saída para análise:

```bash
./ns3 run hello-simulator > simulation.log 2>&1
```

## Valgrind

```bash
./ns3 run hello-simulator --valgrind
```

Para guardar a saída:

```bash
valgrind --leak-check=full --track-origins=yes \
  --log-file=valgrind-hello-simulator.txt \
  ./build/examples/tutorial/hello-simulator
```

O caminho do executável pode ser conferido com `./ns3 show targets`.

## Sanitizers

Para erros de memória e comportamento indefinido, configure o suporte a sanitizers:

```bash
./ns3 configure --build-profile=debug --enable-sanitizers \
  --enable-examples --enable-tests
./ns3 build
./ns3 run hello-simulator
```

## Testes

Execute toda a suíte:

```bash
./test.py
```

Execute somente um conjunto de testes:

```bash
./test.py -s core
```

Consulte as opções disponíveis com:

```bash
./test.py --help
```
