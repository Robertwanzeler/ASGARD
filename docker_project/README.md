# ns-3 em Docker

Ambiente isolado para instalar, compilar e depurar o ns-3 com GDB e Valgrind.

## Uso

Na raiz do repositório:

```bash
cd docker_project
make build
make run
make configure
make build-ns3
make shell
```

Dentro do contêiner:

```bash
cd /workspace/ns3
./ns3 run hello-simulator
./ns3 run hello-simulator --gdb
./ns3 run hello-simulator --valgrind
```

O checkout local de `ns3-base` é montado em `/workspace/ns3`, portanto as alterações e os artefatos de build ficam disponíveis no host. O material adicional de debug fica em `/workspace/debug`.

## Limpeza

```bash
make stop
make clean
```
