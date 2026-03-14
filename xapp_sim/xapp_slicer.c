#include <stdio.h>
#include <unistd.h>
#include "ric_api.h" // API padrão do FlexRIC

// Callback disparado quando a RIC recebe dados do ns-3
void on_kpm_indication(ric_indication_t* ind) {
    // Simulando a extração da latência da fatia de segurança
    float latency = extract_latency_from_sm(ind); 

    printf("[Vigilância UFPA] Latência atual: %.2f ms\n", latency);

    if (latency > 100.0) {
        printf("!!! ALERTA: Latência crítica. Mitigando conflito...\n");
        // Comando para priorizar recursos (Conflict Mitigation)
        send_ran_control_priority(ind->nb_id, 1); 
    }
}

int main() {
    printf("Iniciando xApp Slicer Vigilance...\n");
    // Conecta à RIC que você abriu com ./nearRT-RIC
    ric_t* ric = ric_connect("127.0.0.1", 36421); 
    
    // Subscreve para métricas de KPM (Key Performance Metrics)
    subscribe_kpm(ric, on_kpm_indication);

    while(1) { sleep(1); }
    return 0;
}