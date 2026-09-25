# Orange-Nuclear — Guia de instalação e debug

Guia operacional para instalar e depurar o ambiente de simulação **Orange-Nuclear**:

```text
FlexRIC + E2SIM + ns-O-RAN-flexric + ns-3/mmWave/LENA
```

O foco é Ubuntu 24.04 em um servidor ou runtime separado. Este repositório contém documentação e verificações seguras; não contém os fontes, modelos ou resultados do simulador.

## Comece aqui

1. Leia o [Manual de instalação](MANUAL_INSTALACAO_ORAN.md).
2. Use um diretório de runtime exclusivo, diferente de qualquer simulação em execução.
3. Execute o [check_environment.sh](scripts/check_environment.sh) antes e depois da instalação.
4. Consulte o [Guia de debug](GUIA_DEBUG_NS3_FLEXRIC.md) somente quando o build ou a execução apresentar erro.

## Scripts seguros

Os scripts não instalam pacotes, não clonam repositórios e não iniciam/paralisam processos:

```bash
./scripts/check_environment.sh
./scripts/check_environment.sh "$HOME/orange_nuclear_runtime"
./scripts/show_install_commands.sh
```

O segundo script apenas imprime os comandos do manual para revisão e cópia manual.

## Runtime isolado

Os comandos do manual usam:

```bash
export ORAN_DEBUG_ROOT="$HOME/orange_nuclear_runtime"
```

Não use o checkout de outra simulação como `ORAN_DEBUG_ROOT`. O Git de debug não altera `/home/robert/orange_nuclear`, contêineres existentes ou processos em execução.

## Compatibilidade

- Ubuntu 24.04: alvo principal deste guia.
- Ubuntu 22.04: alternativa recomendada quando ferramentas legadas do stack exigirem versões antigas.
- O ns-3 atual usa C++, Python 3, CMake e Ninja/Make.
- Python 3.8 só deve ser instalado em ambiente virtual quando uma ferramenta legada — especialmente GUI — exigir essa versão; não substitua o Python do sistema.

## Licença e origem

O código de terceiros permanece nos repositórios originais. Este Git documenta o procedimento de instalação do stack Orange-Nuclear e não redistribui os fontes.
