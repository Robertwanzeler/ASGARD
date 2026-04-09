#!/usr/bin/env python3
import sqlite3
import os

DB_PATH = "/tmp/rapp_data_lake.db"

def get_data():
    if not os.path.exists(DB_PATH):
        return None
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Tenta pegar worst_camera_latency se existir, senão usa o worst global
    try:
        cursor.execute("""
            SELECT global_worst_camera_latency_us/1000, total_active_cameras, total_active_ues, id
            FROM extended_metrics 
            ORDER BY id DESC LIMIT 50
        """)
    except:
        cursor.execute("""
            SELECT global_worst_latency_us/1000, total_active_cameras, total_active_ues, id
            FROM extended_metrics 
            ORDER BY id DESC LIMIT 50
        """)
    rows = cursor.fetchall()[::-1]
    conn.close()
    return rows

def plot_ascii():
    data = get_data()
    if not data:
        print("\033[1;33mAguardando dados no Data Lake...\033[0m")
        return

    # Estatísticas rápidas
    latencies = [row[0] for row in data]
    avg_lat = sum(latencies) / len(latencies)
    max_lat = max(latencies)
    sla_violations = len([l for l in latencies if l > 100])
    perf_pct = (1 - (sla_violations / len(latencies))) * 100

    print("\n" + "═"*65)
    print(f"   \033[1;32mGreenRAN\033[0m - Monitor de SLA das CÂMERAS (Últimas {len(data)} amostras)")
    print("═"*65)
    
    # Altura do gráfico
    height = 10
    top_scale = max(max_lat, 150)
    
    for i in range(height, -1, -1):
        level = i * (top_scale / height)
        
        if level <= 100 < level + (top_scale/height):
            prefix = f"\033[1;31m 100 -▶\033[0m"
            is_sla_line = True
        else:
            prefix = f"{int(level):4} | "
            is_sla_line = False
            
        line = prefix
        for lat, cams, ues, _ in data:
            char = " "
            if lat >= level:
                if is_sla_line: char = "\033[1;31m═\033[0m"
                elif lat > 100: char = "\033[1;31m█\033[0m"
                else: char = "\033[1;32m█\033[0m"
            elif is_sla_line: char = "\033[1;30m┈\033[0m"
            line += char
        print(line)
    
    print("     └" + "─" * len(data))
    
    # Rodapé de carga
    def get_load_line(label, values, color):
        line = f"{label} | "
        for v in values:
            if v == 0: line += " "
            elif v < 10: line += color + str(v) + "\033[0m"
            else: line += color + "█" + "\033[0m" # Usar um bloco para indicar alta densidade
        return line

    print(get_load_line(" CAM", [r[1] for r in data], "\033[1;36m"))
    # Calcular sensores: Total UEs - Cameras - Background(50)
    print(get_load_line(" SEN", [max(0, r[2] - r[1] - 50) if r[2] > 50 else r[2]-r[1] for r in data], "\033[1;33m"))
    print(get_load_line(" UEs", [r[2] for r in data], "\033[1;34m"))
    
    print("─"*65)
    print(f" \033[1mRESUMO DA SEGURANÇA (App1-Vigilância):\033[0m")
    status_color = "\033[1;32m" if perf_pct > 90 else "\033[1;31m"
    print(f"  ● Disponibilidade do Vídeo: {status_color}{perf_pct:.1f}%\033[0m")
    print(f"  ● Latência Câmera: Média \033[1m{avg_lat:.1f}ms\033[0m | Pico \033[1;31m{max_lat:.1f}ms\033[0m")
    print(f"  ● Carga: Câmeras \033[1;36m{data[-1][1]}\033[0m | Sensores \033[1;33m{max(0, data[-1][2]-data[-1][1]-50)}\033[0m | Total UEs \033[1;34m{data[-1][2]}\033[0m")
    print("═"*65 + "\n")

if __name__ == "__main__":
    plot_ascii()
