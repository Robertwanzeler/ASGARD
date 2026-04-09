#!/usr/bin/env python3
import sqlite3
import os

DB_PATH = "/tmp/rapp_data_lake.db"

def cleanup():
    if not os.path.exists(DB_PATH):
        print(f"Banco de dados não encontrado em {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"--- Iniciando limpeza do Dataset: {DB_PATH} ---")
    
    # 1. Tamanho inicial
    size_init = os.path.getsize(DB_PATH) / (1024*1024)
    print(f"Tamanho inicial: {size_init:.2f} MB")

    # 2. Remover métricas com latência zero (dados inúteis)
    cursor.execute("DELETE FROM metrics_history WHERE latency_us = 0")
    print(f"Removidas {cursor.rowcount} métricas zeradas de metrics_history.")
    
    cursor.execute("DELETE FROM extended_metrics WHERE global_avg_latency_us = 0")
    print(f"Removidas {cursor.rowcount} métricas zeradas de extended_metrics.")

    # 3. Limpar decisões redundantes (Manter apenas a primeira de uma série idêntica)
    # Lógica: Se a decisão e o motivo são iguais ao registro anterior, é redundante.
    cursor.execute("""
        DELETE FROM decisions_history 
        WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, decision, reason, 
                       LAG(decision) OVER (ORDER BY timestamp) as prev_dec,
                       LAG(reason) OVER (ORDER BY timestamp) as prev_reason
                FROM decisions_history
            ) WHERE decision != prev_dec OR reason != prev_reason OR prev_dec IS NULL
        )
    """)
    print(f"Removidas {cursor.rowcount} decisões redundantes (repetitivas).")

    # 4. Otimizar o banco de dados
    print("Otimizando espaço em disco (VACUUM)...")
    conn.execute("VACUUM")
    
    conn.commit()
    conn.close()
    
    size_final = os.path.getsize(DB_PATH) / (1024*1024)
    print(f"Tamanho final: {size_final:.2f} MB")
    print(f"Economia de espaço: {size_init - size_final:.2f} MB")
    print("--- Limpeza concluída! ---")

if __name__ == "__main__":
    cleanup()
