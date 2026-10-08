# ASGARD — Guia de instalação e debug

Guia operacional para instalar e depurar o ambiente de simulação **ASGARD/GreenRAN** (ex-Orange-Nuclear):

```text
FlexRIC + E2SIM + ns-O-RAN-flexric + ns-3/mmWave/LENA
```

O foco é Ubuntu 24.04 em um servidor ou runtime separado.

> Este guia foi o README original do repositório local de desenvolvimento e foi movido para `docs/`. A visão geral do projeto está no [README principal](../README.md).

## Comece aqui

1. Leia o [Manual de instalação](../MANUAL_INSTALACAO_ORAN.md).
2. Use um diretório de runtime exclusivo, diferente de qualquer simulação em execução.
3. Execute o [check_environment.sh](../scripts/check_environment.sh) antes e depois da instalação.
4. Consulte o [Guia de debug](../GUIA_DEBUG_NS3_FLEXRIC.md) somente quando o build ou a execução apresentar erro.

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

O stack de terceiros está vendido como diretórios comuns neste repositório (snapshot de trabalho):

- `ns-O-RAN-flexric/` — fork ns-3.42 O-RAN (inclui `mmwave-LENA-oran`, `e2sim-kpmv3`, `contrib/oran-interface`, `src/nr`), com branches de trabalho GreenRAN preservadas no backup local de submódulos
- `flexric/` — FlexRIC 2.0.0 (EURECOM) com xApps custom (branch `greenran-xapps-tasam`)
- `ns3-base/` — **não incluído**: ns-3 upstream vanilla, sem customização. Re-clonável com:
  `git clone https://gitlab.com/nsnam/ns-3-dev.git ns3-base`

Os créditos e licenças de cada componente permanecem nos repositórios originais (Orange-OpenSource, MinaYonan123, EURECOM/mosaic5g, nsnam).
