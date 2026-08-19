# Início rápido

```bash
cd docker_project
make build
make run
make configure
make build-ns3
make shell
```

No shell do contêiner:

```bash
cd /workspace/ns3
./ns3 run hello-simulator
./ns3 run hello-simulator --gdb
```
