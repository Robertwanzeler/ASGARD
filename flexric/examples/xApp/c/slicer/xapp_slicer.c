/*
 * GreenRAN O-RAN - xApp SLICER
 * 
 * Responsabilidade: Controle de alocação de PRBs por fatia
 * - Monitora SLA das câmaras (latência < 100ms)
 * - Reserva recursos para priorizar câmeras (App1-Vigilância)
 * - Quando CRITICAL: alocar mais PRBs para câmeras
 * 
 * Interface para rApp: /tmp/xapp_intents/slicer.txt
 * 
 * Regras de Decisão:
 * - Latência > 100ms → CRITICAL (SLA violado)
 * - Latência 50-100ms → WARNING
 * - Latência < 50ms E packets < 5 → IDLE
 * - Caso contrário → NORMAL
 * 
 * Ações de Controle:
 * - CRITICAL: Alocar PRBs adicionais para câmeras
 * - WARNING: Aumentar reserva de recursos
 * - NORMAL: Manter alocação padrão
 * - IDLE: Liberar recursos excedentes
 */

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_struct.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_list.h"
#include "../../../../src/sm/rc_sm/rc_sm_id.h"
#include "../../../../src/util/time_now_us.h"
#include "../../../../src/util/alg_ds/ds/lock_guard/lock_guard.h"
#include "../../../../src/util/e.h"
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <sys/socket.h>
#include <sys/un.h>

/*******************************************************************************
 * RUNTIME
 ******************************************************************************/
static uint64_t get_max_runtime_seconds(void)
{
    const char* raw = getenv("GREENRAN_XAPP_MAX_RUNTIME_S");
    if (raw == NULL || raw[0] == '\0') {
        return 0;  // 0 = sem limite
    }

    char* endptr = NULL;
    unsigned long long value = strtoull(raw, &endptr, 10);
    if (endptr == raw || (endptr && *endptr != '\0')) {
        return 0;
    }
    return (uint64_t)value;
}

/*******************************************************************************
 * CONFIGURAÇÕES - Thresholds SLA
 ******************************************************************************/
#define CONTROL_LOOP_INTERVAL_MS      200     // 200ms para Near-RT RIC
#define SLA_LATENCY_THRESHOLD_US     80000   // 80ms - SLA máximo (CRITICAL se >= 80ms)
#define CAMERA_MIN_PACKETS              5     // Mínimo pacotes para ativar
#define KPM_REPORT_PERIOD_MS         100
#define METRICS_FILE_PATH        "/tmp/xapp_metrics/metrics.json"
#define METRICS_POLL_INTERVAL_MS      100     // 100ms polling para métricas

/*******************************************************************************
 * ESTADOS DO SLICER (SEM WARNING - apenas NORMAL/CRITICAL/IDLE)
 ******************************************************************************/
typedef enum {
    STATE_NORMAL = 0,
    STATE_CRITICAL = 1,
    STATE_IDLE = 2
} slicer_state_e;

/*******************************************************************************
 * AÇÕES DE CONTROLE DO SLICER
 ******************************************************************************/
typedef enum {
    ACTION_NONE = 0,
    ACTION_ALLOCATE_PRB = 1,      // Alocar PRBs para câmeras
    ACTION_INCREASE_RESERVE = 2,   // Aumentar reserva
    ACTION_RELEASE_RESOURCES = 3,   // Liberar recursos
    ACTION_MAINTAIN = 4            // Manter alocação atual
} slicer_action_e;

static const char* state_to_string(slicer_state_e state)
{
    switch (state) {
        case STATE_NORMAL:  return "NORMAL";
        case STATE_CRITICAL: return "CRITICAL";
        case STATE_IDLE:    return "IDLE";
        default:           return "UNKNOWN";
    }
}

static const char* action_to_string(slicer_action_e action)
{
    switch (action) {
        case ACTION_NONE:           return "NONE";
        case ACTION_ALLOCATE_PRB:   return "ALLOCATE_PRB";
        case ACTION_INCREASE_RESERVE: return "INCREASE_RESERVE";
        case ACTION_RELEASE_RESOURCES: return "RELEASE_RESOURCES";
        case ACTION_MAINTAIN:      return "MAINTAIN";
        default:                  return "UNKNOWN";
    }
}

/*******************************************************************************
 * ESTADO GLOBAL DO SLICER
 ******************************************************************************/
static struct {
    slicer_state_e global_state;
    slicer_state_e previous_state;
    slicer_action_e current_action;
    uint64_t last_state_change;
    uint32_t total_violations;
    uint32_t total_recoveries;
    uint32_t total_actions;
    bool running;
    FILE* intent_file;
    double worst_latency_us;
    double p95_latency_us;       // P95 - métrica mais robusta
    int active_cameras;
    int critical_cameras;
    int prb_allocation_level;  // 0-100%
} g_xapp_state = {0};

static global_e2_node_id_t* g_target_node = NULL;
static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_json_mtx = PTHREAD_MUTEX_INITIALIZER;

static int g_sockfd = -1;

#define SLICER_SOCKET_PATH "slicer.sock"

static const char* slicer_socket_path(void)
{
    const char* configured = getenv("GREENRAN_SLICER_SOCKET_PATH");
    return (configured && configured[0]) ? configured : SLICER_SOCKET_PATH;
}

static int init_socket(void)
{
    int sockfd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sockfd == -1) {
        perror("socket");
        return -1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    const char* socket_path = slicer_socket_path();
    strncpy(addr.sun_path, socket_path, sizeof(addr.sun_path) - 1);

    unlink(socket_path);
    if (bind(sockfd, (struct sockaddr*)&addr, sizeof(addr)) == -1) {
        perror("bind");
        close(sockfd);
        return -1;
    }

    if (listen(sockfd, 5) == -1) {
        perror("listen");
        close(sockfd);
        return -1;
    }

    printf("[XAPP-SLICER] Socket server listening on %s\n", socket_path);
    return sockfd;
}

static void* socket_server_thread(void* arg)
{
    (void)arg;
    while (g_xapp_state.running) {
        struct sockaddr_un addr;
        socklen_t addr_len = sizeof(addr);
        int client_fd = accept(g_sockfd, (struct sockaddr*)&addr, &addr_len);
        if (client_fd == -1) continue;

        pthread_mutex_lock(&g_mtx);
        char buffer[1024];
        snprintf(buffer, sizeof(buffer), 
                 "{\"state\": \"%s\", \"action\": \"%s\", \"latency_us\": %.2f}",
                 state_to_string(g_xapp_state.global_state),
                 action_to_string(g_xapp_state.current_action),
                 g_xapp_state.p95_latency_us);
        pthread_mutex_unlock(&g_mtx);

        send(client_fd, buffer, strlen(buffer), 0);
        close(client_fd);
    }
    return NULL;
}

/*******************************************************************************
 * INTERFACE PARA rApp
 ******************************************************************************/
#define INTENT_FILE_PATH "/tmp/xapp_intents/slicer.txt"

static const char* state_to_color(slicer_state_e state)
{
    switch (state) {
        case STATE_CRITICAL: return "\033[1;31m";  // Vermelho brilhante
        case STATE_NORMAL:   return "\033[1;32m";  // Verde brilhante
        case STATE_IDLE:     return "\033[0;36m";   // Ciano
        default:             return "\033[0m";
    }
}

#define COLOR_RESET "\033[0m"

static void write_intent_to_file(slicer_state_e state, slicer_action_e action, double p95_latency_us)
{
    if (g_xapp_state.intent_file) {
        rewind(g_xapp_state.intent_file);
        int fd = fileno(g_xapp_state.intent_file);
        if (fd >= 0) {
            ftruncate(fd, 0);
        }
        time_t now = time(NULL);
        
        const char* intent = state_to_string(state);
        const char* act_str = action_to_string(action);
        
        double latency_ms = p95_latency_us / 1000.0;
        const char* problem_desc = "";
        if (state == STATE_CRITICAL) {
            problem_desc = "PROBLEMA: Latencia P95 >= 80ms (SLA VIOLADO!)";
        } else if (state == STATE_NORMAL) {
            problem_desc = "OK: Latencia P95 < 80ms";
        } else if (state == STATE_IDLE) {
            problem_desc = "OK: Nenhuma camera ativa";
        }
        
        fprintf(g_xapp_state.intent_file, 
                "================================================================\n"
                "                    xApp SLICER - Intent\n"
                "================================================================\n"
                "\n"
                "STATUS:\n"
                "  Estado: %s\n"
                "  %s\n"
                "\n"
                "METRICAS:\n"
                "  Latencia P95: %.0f us (%.1f ms)\n"
                "  Cameras ativas: %d\n"
                "  Cameras criticas: %d\n"
                "\n"
                "ACAO:\n"
                "  Comando: %s\n"
                "  Alocacao PRB: %d%%\n"
                "\n"
                "================================================================\n"
                "\n"
                "XAPP=slicer\n"
                "INTENT=%s\n"
                "STATE=%s\n"
                "ACTION=%s\n"
                "TIMESTAMP=%ld\n"
                "P95_LATENCY_US=%.0f\n"
                "ACTIVE_CAMERAS=%d\n"
                "CRITICAL_CAMERAS=%d\n"
                "PRB_ALLOCATION=%d%%\n"
                "ALLOWED=pending\n",
                intent,
                problem_desc,
                p95_latency_us,
                latency_ms,
                g_xapp_state.active_cameras,
                g_xapp_state.critical_cameras,
                act_str,
                g_xapp_state.prb_allocation_level,
                intent,
                intent,
                act_str,
                (long)now,
                g_xapp_state.worst_latency_us,
                g_xapp_state.active_cameras,
                g_xapp_state.critical_cameras,
                g_xapp_state.prb_allocation_level);
        fflush(g_xapp_state.intent_file);
    }
}

static void init_intent_file(void)
{
    system("mkdir -p /tmp/xapp_intents");
    g_xapp_state.intent_file = fopen(INTENT_FILE_PATH, "w");
    if (!g_xapp_state.intent_file) {
        fprintf(stderr, "[XAPP-SLICER] WRN: Não foi possível criar %s\n", INTENT_FILE_PATH);
    }
}

static void close_intent_file(void)
{
    if (g_xapp_state.intent_file) {
        fclose(g_xapp_state.intent_file);
        g_xapp_state.intent_file = NULL;
    }
}

/*******************************************************************************
 * JSON PARSING
 ******************************************************************************/

static int parse_json_file(const char* filepath, 
                          double* worst_latency,
                          double* p95_latency,
                          int* active_cameras,
                          int* critical_cameras)
{
    FILE* f = fopen(filepath, "r");
    if (!f) {
        return -1;
    }
    
    pthread_mutex_lock(&g_json_mtx);
    
    char buffer[65536];
    size_t bytes_read = fread(buffer, 1, sizeof(buffer) - 1, f);
    buffer[bytes_read] = '\0';
    
    fclose(f);
    pthread_mutex_unlock(&g_json_mtx);
    
    *worst_latency = 0;
    *p95_latency = 0;
    *active_cameras = 0;
    *critical_cameras = 0;
    
    char* ptr = buffer;
    while (*ptr) {
        if (strstr(ptr, "\"global_worst_latency_us\"") != NULL) {
            char* colon = strchr(ptr, ':');
            if (colon) {
                *worst_latency = atof(colon + 1);
            }
        }
        if (strstr(ptr, "\"latency_p95_us\"") != NULL) {
            char* colon = strchr(ptr, ':');
            if (colon) {
                *p95_latency = atof(colon + 1);
            }
        }
        if (strstr(ptr, "\"active_cameras\"") != NULL) {
            char* colon = strchr(ptr, ':');
            if (colon) {
                *active_cameras = atoi(colon + 1);
            }
        }
        if (strstr(ptr, "\"critical_cameras\"") != NULL) {
            char* colon = strchr(ptr, ':');
            if (colon) {
                *critical_cameras = atoi(colon + 1);
            }
        }
        ptr++;
    }
    
    return 0;
}

/*******************************************************************************
 * DECISÃO DE ESTADO E AÇÃO
 ******************************************************************************/
static slicer_state_e evaluate_state(double worst_latency_us, double p95_latency_us, int active_cameras)
{
    // Usar P95 se disponível (mais robusto), senão fallback para worst
    double latency_us = (p95_latency_us > 0) ? p95_latency_us : worst_latency_us;
    
    if (latency_us >= SLA_LATENCY_THRESHOLD_US) {
        return STATE_CRITICAL;
    }
    
    if (active_cameras == 0) {
        return STATE_IDLE;
    }
    
    return STATE_NORMAL;
}

static slicer_action_e evaluate_action(slicer_state_e state, int* prb_level)
{
    switch (state) {
        case STATE_CRITICAL:
            // Alocar PRBs máximos para câmeras
            *prb_level = 100;
            return ACTION_ALLOCATE_PRB;
            
        case STATE_IDLE:
            // Liberar recursos excedentes
            *prb_level = 25;
            return ACTION_RELEASE_RESOURCES;
            
        case STATE_NORMAL:
        default:
            // Manter alocação padrão
            *prb_level = 50;
            return ACTION_MAINTAIN;
    }
}

/*******************************************************************************
 * AÇÃO DE CONTROLE - Alocação de PRBs
 ******************************************************************************/
static void send_prb_allocation_control(slicer_action_e action, int prb_level)
{
    // Esta função simula o envio de comando de alocação de PRBs
    // No cenário real, enviariamos via E2/RIC interface
    
    switch (action) {
        case ACTION_ALLOCATE_PRB:
            printf("\033[1;36m      - Details: \033[0mAlocar PRBs MÁXIMOS (%d%%) para câmeras\n", prb_level);
            printf("\033[1;36m      - Details: \033[0mGarantir latência < 100ms para App1-Vigilância\n");
            break;
            
        case ACTION_INCREASE_RESERVE:
            printf("\033[1;36m      - Details: \033[0mAumentar reserva de PRBs (%d%%)\n", prb_level);
            printf("\033[1;36m      - Details: \033[0mMonitorar e ajustar conforme necessário\n");
            break;
            
        case ACTION_RELEASE_RESOURCES:
            printf("\033[1;36m      - Details: \033[0mLiberar recursos excedentes (%d%%)\n", prb_level);
            printf("\033[1;36m      - Details: \033[0mPermitir que ENERGY SAVER atue\n");
            break;
            
        case ACTION_MAINTAIN:
            printf("\033[1;36m      - Details: \033[0mManter alocação atual (%d%%)\n", prb_level);
            break;
            
        default:
            break;
    }
}

/*******************************************************************************
 * CALLBACK JSON (monitoramento)
 ******************************************************************************/
static void* json_monitor_thread(void* arg)
{
    (void)arg;
    
    int cycle = 0;
    
    while (g_xapp_state.running) {
        usleep(METRICS_POLL_INTERVAL_MS * 1000);
        cycle++;
        
        double worst_lat = 0;
        double p95_lat = 0;
        int active_cam = 0;
        int critical_cam = 0;
        
        int ret = parse_json_file(METRICS_FILE_PATH, &worst_lat, &p95_lat, &active_cam, &critical_cam);
        
        if (ret == 0) {
            pthread_mutex_lock(&g_mtx);
            
            slicer_state_e new_state = evaluate_state(worst_lat, p95_lat, active_cam);
            int new_prb_level = g_xapp_state.prb_allocation_level;
            slicer_action_e new_action = evaluate_action(new_state, &new_prb_level);
            
            // Formatacao vertical mais clara
            printf("\n\033[1;35m[SLICER]\033[0m Cycle: %d\n", cycle);
            printf("  P95 Latency:  %6.1f ms\n", p95_lat / 1000.0);
            printf("  Cameras:  %d (Critical: %d)\n", active_cam, critical_cam);
            printf("  PRB Resv: %d%%\n", new_prb_level);
            
            // Verificar mudança de estado
            if (new_state != g_xapp_state.global_state || new_action != g_xapp_state.current_action) {
                printf("\033[1;36m  => State Change: \033[0m%s -> %s\n",
                       state_to_string(g_xapp_state.global_state),
                       state_to_string(new_state));
                printf("\033[1;36m  => Executing Action: \033[1m%s\033[0m\n", action_to_string(new_action));
                
                // Executar ação de controle
                send_prb_allocation_control(new_action, new_prb_level);
                
                // Registrar violação
                if (new_state == STATE_CRITICAL && g_xapp_state.global_state != STATE_CRITICAL) {
                    g_xapp_state.total_violations++;
                    printf("[XAPP-SLICER-JSON] >>> SLA VIOLADO! Latência %.0f us > %d us\n",
                           worst_lat, SLA_LATENCY_THRESHOLD_US);
                }
                
                // Registrar recuperação
                if (new_state != STATE_CRITICAL && g_xapp_state.global_state == STATE_CRITICAL) {
                    g_xapp_state.total_recoveries++;
                    printf("[XAPP-SLICER-JSON] >>> RECUPERAÇÃO! SLA restaurado\n");
                }
                
                g_xapp_state.previous_state = g_xapp_state.global_state;
                g_xapp_state.global_state = new_state;
                g_xapp_state.current_action = new_action;
                g_xapp_state.prb_allocation_level = new_prb_level;
                g_xapp_state.last_state_change = time(NULL);
                
                g_xapp_state.total_actions++;
            }

            g_xapp_state.worst_latency_us = worst_lat;
            g_xapp_state.p95_latency_us = p95_lat;
            g_xapp_state.active_cameras = active_cam;
            g_xapp_state.critical_cameras = critical_cam;
            write_intent_to_file(g_xapp_state.global_state, g_xapp_state.current_action, g_xapp_state.p95_latency_us);
            
            pthread_mutex_unlock(&g_mtx);
        } else if (cycle % 10 == 0) {
            printf("[XAPP-SLICER-JSON] Aguardando arquivo de métricas... (cycle %d)\n", cycle);
        }
    }
    
    return NULL;
}

/*******************************************************************************
 * MAIN
 ******************************************************************************/
int main(int argc, char* argv[])
{
    fr_args_t args = init_fr_args(argc, argv);
    init_xapp_api(&args);
    
    printf("\n");
    printf("╔══════════════════════════════════════════════════════════╗\n");
    printf("║             SLICER xApp - GreenRAN O-RAN              ║\n");
    printf("╠══════════════════════════════════════════════════════════╣\n");
    printf("║  Função: Controle de alocação de PRBs por fatia     ║\n");
    printf("║  Prioriza: Câmeras (App1-Vigilância)               ║\n");
    printf("╠══════════════════════════════════════════════════════════╣\n");
    printf("║  Thresholds SLA:                                     ║\n");
    printf("║    CRITICAL: latência >= %d us (80ms)             ║\n", SLA_LATENCY_THRESHOLD_US);
    printf("║    IDLE: sem câmeras ativas                          ║\n");
    printf("╠══════════════════════════════════════════════════════════╣\n");
    printf("║  Ações de Controle:                                   ║\n");
    printf("║    CRITICAL: Alocar 100%% PRBs para câmeras          ║\n");
    printf("║    NORMAL:   Manter alocação 50%%                  ║\n");
    printf("║    IDLE:     Liberar recursos (25%%)               ║\n");
    printf("╚══════════════════════════════════════════════════════════╝\n\n");
    
    g_xapp_state.global_state = STATE_NORMAL;
    g_xapp_state.current_action = ACTION_MAINTAIN;
    g_xapp_state.prb_allocation_level = 50;
    g_xapp_state.running = true;
    init_intent_file();
    
    g_sockfd = init_socket();
    if (g_sockfd == -1) {
        fprintf(stderr, "[XAPP-SLICER] ERRO: Falha ao inicializar socket\n");
        return 1;
    }
    
    system("mkdir -p /tmp/xapp_metrics");
    
    pthread_t json_thread, socket_thread;
    if (pthread_create(&json_thread, NULL, json_monitor_thread, NULL) != 0) {
        fprintf(stderr, "[XAPP-SLICER] ERRO: Falha ao criar thread de monitoramento\n");
        return 1;
    }
    
    if (pthread_create(&socket_thread, NULL, socket_server_thread, NULL) != 0) {
        fprintf(stderr, "[XAPP-SLICER] ERRO: Falha ao criar thread de socket\n");
        return 1;
    }
    
    printf("[XAPP-SLICER] Thread de monitoramento e socket server iniciados\n");
    write_intent_to_file(g_xapp_state.global_state, g_xapp_state.current_action, 0.0);
    
    uint64_t start_time = time_now_us();
    const uint64_t max_runtime_s = get_max_runtime_seconds();
    int cycle = 0;
    
    while (g_xapp_state.running) {
        usleep(CONTROL_LOOP_INTERVAL_MS * 1000);
        cycle++;
        uint64_t elapsed = (time_now_us() - start_time) / 1000000;
        
        pthread_mutex_lock(&g_mtx);
        slicer_state_e current_state = g_xapp_state.global_state;
        slicer_action_e current_action = g_xapp_state.current_action;
        double worst_lat = g_xapp_state.worst_latency_us;
        int active = g_xapp_state.active_cameras;
        int critical = g_xapp_state.critical_cameras;
        int prb = g_xapp_state.prb_allocation_level;
        uint32_t violations = g_xapp_state.total_violations;
        uint32_t recoveries = g_xapp_state.total_recoveries;
        uint32_t actions = g_xapp_state.total_actions;
        pthread_mutex_unlock(&g_mtx);
        
        const char* state_color = state_to_color(current_state);
        
        printf("\n");
        printf("================================================================\n");
        printf("                     xApp SLICER\n");
        printf("================================================================\n");
        printf("\n");
        printf("  Estado: %s[%s]%s\n", state_color, state_to_string(current_state), COLOR_RESET);
        
        if (current_state == STATE_CRITICAL) {
            printf("  %s<- PROBLEMA: Latencia >= 80ms (SLA VIOLADO!)\033[0m\n", state_color);
        } else if (current_state == STATE_NORMAL) {
            printf("  %s<- OK: Latencia < 80ms\033[0m\n", state_color);
        } else {
            printf("  %s<- OK: Nenhuma camera ativa\033[0m\n", state_color);
        }
        
        printf("\n");
        printf("  Metricas:\n");
        printf("    Latencia: %.0f us (%.1f ms)\n", worst_lat, worst_lat / 1000.0);
        printf("    Cameras: ativas=%d, criticas=%d\n", active, critical);
        printf("\n");
        printf("  Acao: %s (PRB=%d%%)\n", action_to_string(current_action), prb);
        printf("\n");
        printf("  Estatisticas: Violacoes=%u, Recoveries=%u, Acoes=%u\n", violations, recoveries, actions);
        printf("================================================================\n");
        
        if (current_state == STATE_CRITICAL) {
            printf("\033[1;31m>>> ATENCAO: SLA VIOLADO! rApp deve BLOQUEAR Energy Saver\033[0m\n");
        }
        
        if (max_runtime_s > 0 && elapsed >= max_runtime_s) {
            printf("[XAPP-SLICER] Tempo máximo (%llus). Encerrando...\n",
                   (unsigned long long)max_runtime_s);
            g_xapp_state.running = false;
        }
    }
    
    printf("\n[XAPP-SLICER] Finalizando...\n");
    
    pthread_join(json_thread, NULL);
    
    while (try_stop_xapp_api() == false) usleep(1000);
    
    close_intent_file();
    if (g_sockfd >= 0) {
        close(g_sockfd);
        unlink(slicer_socket_path());
        g_sockfd = -1;
    }
    
    printf("\n╔══════════════════════════════════════════════════════════╗\n");
    printf("║             SLICER xApp - FINALIZADO                 ║\n");
    printf("╠══════════════════════════════════════════════════════════╣\n");
    printf("║  Violações SLA: %u                                  ║\n", g_xapp_state.total_violations);
    printf("║  Recuperações: %u                                   ║\n", g_xapp_state.total_recoveries);
    printf("║  Ações de controle: %u                               ║\n", g_xapp_state.total_actions);
    printf("╚══════════════════════════════════════════════════════════╝\n");
    
    return 0;
}
