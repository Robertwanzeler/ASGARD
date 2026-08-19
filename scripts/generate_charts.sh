#!/bin/bash
# GreenRAN - Gerador de Gráficos
# Gera gráficos a partir do banco de dados
# Usage: ./scripts/generate_charts.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=========================================="
echo "  GreenRAN - Gerador de Gráficos"
echo "=========================================="
echo ""

echo "[1] Verificando banco de dados..."
if [ -f "/tmp/rapp_data_lake.db" ]; then
    echo "    ✓ Banco encontrado"
else
    echo "    ✗ Banco não encontrado em /tmp/rapp_data_lake.db"
    echo "    Execute a simulação primeiro com ./scripts/run_greenran_v2.sh"
    exit 1
fi

echo "[2] Gerando gráficos..."
python3 training/generate_charts.py

if [ $? -eq 0 ]; then
    echo "    ✓ Gráficos gerados com sucesso"
else
    echo "    ✗ Erro ao gerar gráficos"
    exit 1
fi

echo ""
echo "[3] Arquivos gerados:"
ls -la charts/

echo ""
echo "=========================================="
echo "  CONCLUÍDO!"
echo "=========================================="
echo ""
echo "Gráficos disponíveis em: $PROJECT_DIR/charts/"
echo ""
echo "Para visualizar, copie os arquivos do diretório charts/ para seu computador"
echo "ou abra directamente no servidor."