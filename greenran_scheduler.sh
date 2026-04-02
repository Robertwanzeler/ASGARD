#!/bin/bash
# GreenRAN Scheduler - Executa simulação 4x por dia com retreinamento ML
# ==========================================
# Cron: 0 0,6,12,18 * * * /home/robert/orange_nuclear/greenran_scheduler.sh
#
# Fluxo:
#   1. Inicia cenário completo (ns-3 + orchestrator + dashboard)
#   2. Após 5 minutos, retreina ML com dados do banco
#   3. Após 10 minutos, para todos os processos
#   4. Logs salvos em /tmp/greenran_logs/
# ==========================================

BASE_DIR="/home/robert/orange_nuclear"
LOG_DIR="/tmp/greenran_logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/run_$TIMESTAMP.log"
SIM_DURATION=600        # 10 minutos em segundos
RETRAIN_DELAY=300       # 5 minutos para retreinar

# Cores para o terminal
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Criar diretório de logs
mkdir -p $LOG_DIR

echo -e "${BLUE}==========================================${NC}" | tee -a $LOG_FILE
echo -e "${GREEN}  GreenRAN Scheduler - $(date)${NC}" | tee -a $LOG_FILE
echo -e "${BLUE}==========================================${NC}" | tee -a $LOG_FILE
echo -e "  Duração da simulação: ${SIM_DURATION}s (10 min)" | tee -a $LOG_FILE
echo -e "  Retreinamento ML: após ${RETRAIN_DELAY}s (5 min)" | tee -a $LOG_FILE
echo -e "  Log: $LOG_FILE" | tee -a $LOG_FILE
echo -e "${BLUE}==========================================${NC}" | tee -a $LOG_FILE

# 1. Iniciar cenário completo
echo -e "\n${BLUE}[1/3] Iniciando cenário GreenRAN...${NC}" | tee -a $LOG_FILE
cd $BASE_DIR
./run_greenran_v2.sh >> $LOG_FILE 2>&1 &

# Aguardar um pouco para o cenário inicializar
sleep 10

# 2. Aguardar 5 minutos e retreinar ML
echo -e "\n${YELLOW}[2/3] Aguardando ${RETRAIN_DELAY}s para retreinar ML...${NC}" | tee -a $LOG_FILE
sleep $RETRAIN_DELAY

echo -e "\n${GREEN}[2/3] Retreinando ML com dados do banco...${NC}" | tee -a $LOG_FILE
python3 $BASE_DIR/train_ml_model.py --output $BASE_DIR/models >> $LOG_FILE 2>&1

if [ $? -eq 0 ]; then
    echo -e "${GREEN}  ✓ Retreinamento concluído${NC}" | tee -a $LOG_FILE

    # Mostrar accuracy do novo modelo
    if [ -f "$BASE_DIR/models/training_report.json" ]; then
        ACCURACY=$(python3 -c "import json; r=json.load(open('$BASE_DIR/models/training_report.json')); print(f\"{r['classifier']['random_forest_accuracy']:.2%}\")")
        CLASSES=$(python3 -c "import json; r=json.load(open('$BASE_DIR/models/training_report.json')); print(r['classifier']['classes'])")
        echo -e "${GREEN}  Accuracy: $ACCURACY${NC}" | tee -a $LOG_FILE
        echo -e "${GREEN}  Classes: $CLASSES${NC}" | tee -a $LOG_FILE
    fi
else
    echo -e "${RED}  ✗ Erro no retreinamento${NC}" | tee -a $LOG_FILE
fi

# 3. Aguardar tempo restante e parar
REMAINING=$((SIM_DURATION - RETRAIN_DELAY - 10))
echo -e "\n${YELLOW}[3/3] Aguardando ${REMAINING}s restantes...${NC}" | tee -a $LOG_FILE
sleep $REMAINING

echo -e "\n${BLUE}[3/3] Parando todos os processos...${NC}" | tee -a $LOG_FILE
cd $BASE_DIR
./stop_all.sh >> $LOG_FILE 2>&1

# Estatísticas finais
echo -e "\n${BLUE}==========================================${NC}" | tee -a $LOG_FILE
echo -e "${GREEN}  Execução finalizada - $(date)${NC}" | tee -a $LOG_FILE
echo -e "${BLUE}==========================================${NC}" | tee -a $LOG_FILE

# Contar decisões no banco
DECISIONS=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('/tmp/rapp_data_lake.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM decisions_history WHERE timestamp >= strftime(\"%s\",\"now\") - 600')
    print(c.fetchone()[0])
    conn.close()
except:
    print('0')
")
echo -e "  Decisões registradas: $DECISIONS" | tee -a $LOG_FILE

# Verificar classes do modelo
CLASSES=$(python3 -c "
import joblib
try:
    le = joblib.load('/home/robert/orange_nuclear/models/label_encoder.joblib')
    print(list(le.classes_))
except:
    print('N/A')
")
echo -e "  Classes do modelo: $CLASSES" | tee -a $LOG_FILE

echo -e "${BLUE}==========================================${NC}" | tee -a $LOG_FILE
echo -e "  Próxima execução: 6 horas" | tee -a $LOG_FILE
echo -e "${BLUE}==========================================${NC}\n" | tee -a $LOG_FILE
