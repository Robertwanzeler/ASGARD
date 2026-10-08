/*
 * GreenRAN O-RAN - xApp ENERGY SAVER (Pure Actuator)
 * 
 * ARQUITETURA: Atuador Puro Controlado pelo rApp
 * 
 * Responsabilidade:
 * - Executar comandos recebidos do rApp via JSON
 * - Monitorar watchdog (se rApp falhar → FULL_POWER)
 * - Reportar status de execução para rApp
 * 
 * NÃO FAZ:
 * - Não decide quando economizar (rApp decide)
 * - Não analisa métricas (rApp analisa)
 * - Não calcula tendências (rApp calcula)
 * 
 * Interface:
 * - Lê comandos: /tmp/xapp_intents/energy_command.json
 * - Reporta status: /tmp/xapp_intents/energy_saver.txt
 * 
 * Protocolo JSON (rApp → xApp):
 * {
 *   "action": "FULL_POWER|REDUCE_POWER|CONDITIONAL_REDUCE|POWER_DOWN|POWER_DOWN_ECO|MAINTAIN",
 *   "power_level": 0-100,
 *   "timestamp": unix_timestamp,
 *   "ttl_seconds": 5,
 *   "reason": "string"
 * }
 * 
 * Watchdog:
 * - Se não receber comando em TTL segundos → FULL_POWER (fail-safe)
 * - Quando rApp voltar, enviará novo comando automaticamente
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
 * CONFIGURAÇÕES
 ******************************************************************************/
#define CONTROL_LOOP_INTERVAL_MS    500     // 500ms loop principal
#define COMMAND_FILE_PATH           "/tmp/xapp_intents/energy_command.json"
#define STATUS_FILE_PATH            "/tmp/xapp_intents/energy_saver.txt"
#define SOCKET_FILE_PATH            "energy_saver.sock"

static const char* energy_socket_path(void)
{
    const char* configured = getenv("GREENRAN_ENERGY_SOCKET_PATH");
    return (configured && configured[0]) ? configured : SOCKET_FILE_PATH;
}
#define COMMAND_POLL_INTERVAL_MS    200     // 200ms polling para comandos
#define DEFAULT_TTL_SECONDS         5       // Watchdog timeout

/*******************************************************************************
 * AÇÕES DO ATUADOR
 ******************************************************************************/
typedef enum {
    ACTION_FULL_POWER = 0,          // RU=1, mmWave=1, 100%
    ACTION_CONDITIONAL_REDUCE = 1,  // RU=1, mmWave=1, 60%
    ACTION_POWER_DOWN = 2,          // RU=1, mmWave=1, 50%
    ACTION_POWER_DOWN_ECO = 3,      // RU=1, mmWave=1, 25%
    ACTION_MAINTAIN = 4,            // Manter estado atual
    ACTION_NONE = 5                 // Nenhum comando recebido
} energy_action_e;

/*******************************************************************************
 * ESTADO GLOBAL DO ATUADOR
 ******************************************************************************/
static struct {
    energy_action_e current_action;
    energy_action_e previous_action;
    int ru_count;               // RUs ativos (1 ou 2)
    int mmwave_count;           // mmWave ativo (0 ou 1)
    int power_level;            // Nível de potência 0-100%
    
    time_t last_command_time;   // Timestamp do último comando válido
    int ttl_seconds;            // Timeout do watchdog
    bool watchdog_triggered;    // Se watchdog foi ativado
    
    bool running;
    FILE* status_file;
    
    uint64_t last_command_timestamp;  // Timestamp do comando (para deduplicação)
    
    // Estatísticas
    int commands_received;
    int watchdog_triggers;
    int full_power_count;
    int conditional_reduce_count;
    int power_down_count;
    int power_down_eco_count;
    int maintain_count;
} g_xapp_state = {0};

static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;

/*******************************************************************************
 * FUNÇÕES AUXILIARES
 ******************************************************************************/
static const char* action_to_string(energy_action_e action)
{
    switch (action) {
        case ACTION_FULL_POWER:          return "FULL_POWER";
        case ACTION_CONDITIONAL_REDUCE:  return "CONDITIONAL_REDUCE";
        case ACTION_POWER_DOWN:          return "POWER_DOWN";
        case ACTION_POWER_DOWN_ECO:      return "POWER_DOWN_ECO";
        case ACTION_MAINTAIN:            return "MAINTAIN";
        case ACTION_NONE:                return "NO_COMMAND";
        default:                         return "UNKNOWN";
    }
}

static const char* action_to_color(energy_action_e action)
{
    switch (action) {
        case ACTION_FULL_POWER:          return "\033[1;31m";  // Vermelho
        case ACTION_CONDITIONAL_REDUCE:  return "\033[1;33m";  // Amarelo
        case ACTION_POWER_DOWN:          return "\033[1;32m";  // Verde
        case ACTION_POWER_DOWN_ECO:      return "\033[1;92m";  // Verde claro
        case ACTION_MAINTAIN:            return "\033[1;36m";  // Ciano
        case ACTION_NONE:                return "\033[0;90m";  // Cinza
        default:                         return "\033[0m";
    }
}

#define COLOR_RESET "\033[0m"

static int g_sockfd = -1;

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
    const char* socket_path = energy_socket_path();
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

    printf("[XAPP-ENERGY] Socket server listening on %s\n", socket_path);
    return sockfd;
}

/*******************************************************************************
 * PARSING DO COMANDO JSON
 ******************************************************************************/
typedef struct {
    energy_action_e action;
    int power_level;
    int ru_count;
    int mmwave_count;
    uint64_t timestamp;
    int ttl_seconds;
    char reason[256];
    bool valid;
} energy_command_t;

static energy_command_t parse_command_buffer(const char* buffer)
{
    energy_command_t cmd = {0};
    cmd.valid = false;
    cmd.action = ACTION_NONE;

    // Parse manual do JSON (sem dependências externas)
    char* ptr;
    
    // action
    ptr = strstr(buffer, "\"action\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            char* start = strchr(colon, '"');
            if (start) {
                start++;
                char* end = strchr(start, '"');
                if (end) {
                    char action_str[32];
                    int len = end - start;
                    if (len < 32) {
                        strncpy(action_str, start, len);
                        action_str[len] = '\0';
                        
                        if (strcmp(action_str, "FULL_POWER") == 0)
                            cmd.action = ACTION_FULL_POWER;
                        else if (strcmp(action_str, "REDUCE_POWER") == 0)
                            cmd.action = ACTION_CONDITIONAL_REDUCE;
                        else if (strcmp(action_str, "CONDITIONAL_REDUCE") == 0)
                            cmd.action = ACTION_CONDITIONAL_REDUCE;
                        else if (strcmp(action_str, "POWER_DOWN") == 0)
                            cmd.action = ACTION_POWER_DOWN;
                        else if (strcmp(action_str, "POWER_DOWN_ECO") == 0)
                            cmd.action = ACTION_POWER_DOWN_ECO;
                        else if (strcmp(action_str, "MAINTAIN") == 0)
                            cmd.action = ACTION_MAINTAIN;
                        else
                            cmd.action = ACTION_NONE;
                    }
                }
            }
        }
    }
    
    // power_level
    ptr = strstr(buffer, "\"power_level\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            cmd.power_level = atoi(colon + 1);
        }
    }
    
    // ru_count
    ptr = strstr(buffer, "\"ru_count\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            cmd.ru_count = atoi(colon + 1);
        }
    }
    
    // mmwave_count
    ptr = strstr(buffer, "\"mmwave_count\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            cmd.mmwave_count = atoi(colon + 1);
        }
    }
    
    // timestamp
    ptr = strstr(buffer, "\"timestamp\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            cmd.timestamp = strtoull(colon + 1, NULL, 10);
        }
    }
    
    // ttl_seconds
    ptr = strstr(buffer, "\"ttl_seconds\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            cmd.ttl_seconds = atoi(colon + 1);
        }
    }
    
    // reason
    ptr = strstr(buffer, "\"reason\"");
    if (ptr) {
        char* colon = strchr(ptr, ':');
        if (colon) {
            char* start = strchr(colon, '"');
            if (start) {
                start++;
                char* end = strchr(start, '"');
                if (end) {
                    int len = end - start;
                    if (len < 256) {
                        strncpy(cmd.reason, start, len);
                        cmd.reason[len] = '\0';
                    }
                }
            }
        }
    }
    
    // Validar comando
    if (cmd.action != ACTION_NONE && cmd.timestamp > 0) {
        cmd.valid = true;
    }
    
    // Defaults
    if (cmd.ttl_seconds <= 0) {
        cmd.ttl_seconds = DEFAULT_TTL_SECONDS;
    }
    
    return cmd;
}

static energy_command_t parse_command_json(const char* filepath)
{
    FILE* f = fopen(filepath, "r");
    if (!f) {
        energy_command_t cmd = {0};
        cmd.action = ACTION_NONE;
        return cmd;
    }
    
    char buffer[4096];
    size_t bytes_read = fread(buffer, 1, sizeof(buffer) - 1, f);
    buffer[bytes_read] = '\0';
    fclose(f);
    
    return parse_command_buffer(buffer);
}

static energy_command_t recv_command_from_socket(int sockfd)
{
    struct sockaddr_un addr;
    socklen_t addr_len = sizeof(addr);
    
    // Set non-blocking for accept to prevent thread block
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = 100000; // 100ms
    setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    
    int client_fd = accept(sockfd, (struct sockaddr*)&addr, &addr_len);
    if (client_fd == -1) {
        energy_command_t cmd = {0};
        cmd.action = ACTION_NONE;
        return cmd;
    }

    char buffer[4096];
    ssize_t bytes_read = recv(client_fd, buffer, sizeof(buffer) - 1, 0);
    if (bytes_read <= 0) {
        close(client_fd);
        energy_command_t cmd = {0};
        cmd.action = ACTION_NONE;
        return cmd;
    }
    buffer[bytes_read] = '\0';
    energy_command_t cmd = parse_command_buffer(buffer);
    if (cmd.action != ACTION_NONE) {
        const char* ack = "{\"ack\":true,\"component\":\"energy_saver\"}";
        send(client_fd, ack, strlen(ack), 0);
    }
    close(client_fd);
    return cmd;
}


/*******************************************************************************
 * EXECUÇÃO DE COMANDO
 ******************************************************************************/
static void execute_action(energy_action_e action, int ru_count, int mmwave_count, int power_level)
{
    // Aplicar estado
    if (ru_count >= 0) {
        g_xapp_state.ru_count = ru_count;
    }
    if (mmwave_count >= 0) {
        g_xapp_state.mmwave_count = mmwave_count;
    }
    if (power_level >= 0) {
        g_xapp_state.power_level = power_level;
    }
    
    // Atualizar ação
    g_xapp_state.previous_action = g_xapp_state.current_action;
    g_xapp_state.current_action = action;
    
    // Log de execução
    printf("\033[1;36m      - Executing: \033[0m%s\n", action_to_string(action));
    printf("\033[1;36m      - State: \033[0mRU=%d, mmWave=%d, Power=%d%%\n",
           g_xapp_state.ru_count, g_xapp_state.mmwave_count, g_xapp_state.power_level);
}

/*******************************************************************************
 * WATCHDOG
 ******************************************************************************/
static bool check_watchdog(void)
{
    time_t now = time(NULL);
    time_t elapsed = now - g_xapp_state.last_command_time;
    
    if (elapsed > g_xapp_state.ttl_seconds) {
        // WATCHDOG TRIGGER!
        if (!g_xapp_state.watchdog_triggered) {
            printf("\n\033[1;31m[WATCHDOG] SEM COMANDO DO rApp POR %ld SEGUNDOS!\033[0m\n", elapsed);
            printf("\033[1;33m[WATCHDOG] Executando FULL_POWER (fail-safe)\033[0m\n");
            g_xapp_state.watchdog_triggers++;
            g_xapp_state.watchdog_triggered = true;
            
            // Executar FULL_POWER
            execute_action(ACTION_FULL_POWER, 1, 1, 100);
            g_xapp_state.full_power_count++;
        }
        return false;  // Watchdog ativo
    }
    
    // Se watchdog estava ativo e agora recebeu comando, resetar
    if (g_xapp_state.watchdog_triggered) {
        printf("\033[1;32m[WATCHDOG] rApp recuperou! Watchdog desativado\033[0m\n");
        g_xapp_state.watchdog_triggered = false;
    }
    
    return true;  // Normal
}

/*******************************************************************************
 * THREAD DE MONITORAMENTO DE COMANDOS
 ******************************************************************************/
static void* command_monitor_thread(void* arg)
{
    (void)arg;
    
    int cycle = 0;
    
    while (g_xapp_state.running) {
        usleep(COMMAND_POLL_INTERVAL_MS * 1000);
        cycle++;
        
        // Ler comando do rApp (via socket agora)
        energy_command_t cmd = recv_command_from_socket(g_sockfd);
        
        if (cmd.valid) {
            pthread_mutex_lock(&g_mtx);
            
            // Verificar se é um comando novo (timestamp diferente)
            if (cmd.timestamp != g_xapp_state.last_command_timestamp) {
                g_xapp_state.last_command_timestamp = cmd.timestamp;
                g_xapp_state.last_command_time = time(NULL);
                g_xapp_state.ttl_seconds = cmd.ttl_seconds;
                g_xapp_state.commands_received++;
                
                // Log do comando recebido
                printf("\n\033[1;33m[ENERGY-ACTUATOR]\033[0m Novo comando recebido!\n");
                printf("  Ação: %s\n", action_to_string(cmd.action));
                printf("  Power: %d%%\n", cmd.power_level);
                printf("  RU: %d, mmWave: %d\n", cmd.ru_count, cmd.mmwave_count);
                printf("  Motivo: %s\n", cmd.reason);
                printf("  TTL: %ds\n", cmd.ttl_seconds);
                
                // Executar comando
                execute_action(cmd.action, cmd.ru_count, cmd.mmwave_count, cmd.power_level);
                
                // Atualizar estatísticas
                switch (cmd.action) {
                    case ACTION_FULL_POWER:         g_xapp_state.full_power_count++; break;
                    case ACTION_CONDITIONAL_REDUCE: g_xapp_state.conditional_reduce_count++; break;
                    case ACTION_POWER_DOWN:         g_xapp_state.power_down_count++; break;
                    case ACTION_POWER_DOWN_ECO:     g_xapp_state.power_down_eco_count++; break;
                    case ACTION_MAINTAIN:           g_xapp_state.maintain_count++; break;
                    default: break;
                }
            }
            
            pthread_mutex_unlock(&g_mtx);
        }
        
        // Verificar watchdog
        pthread_mutex_lock(&g_mtx);
        check_watchdog();
        pthread_mutex_unlock(&g_mtx);
    }
    
    return NULL;
}

/*******************************************************************************
 * ESCRITA DE STATUS
 ******************************************************************************/
static void write_status_file(void)
{
    if (g_xapp_state.status_file) {
        rewind(g_xapp_state.status_file);
        time_t now = time(NULL);
        
        const char* action_str = action_to_string(g_xapp_state.current_action);
        const char* watchdog_str = g_xapp_state.watchdog_triggered ? "YES" : "NO";
        time_t since_last_cmd = now - g_xapp_state.last_command_time;
        
        fprintf(g_xapp_state.status_file,
                "================================================================\n"
                "              xApp ENERGY SAVER (Pure Actuator)\n"
                "================================================================\n"
                "\n"
                "STATUS:\n"
                "  Ação Atual: %s\n"
                "  RU Ativos: %d\n"
                "  mmWave Ativo: %d\n"
                "  Power Level: %d%%\n"
                "\n"
                "WATCHDOG:\n"
                "  Ativado: %s\n"
                "  Último Comando: %ld segundos atrás\n"
                "  TTL: %d segundos\n"
                "\n"
                "ESTATÍSTICAS:\n"
                "  Comandos Recebidos: %d\n"
                "  Watchdog Triggers: %d\n"
                "  FULL_POWER: %d\n"
                "  CONDITIONAL_REDUCE: %d\n"
                "  POWER_DOWN: %d\n"
                "  POWER_DOWN_ECO: %d\n"
                "  MAINTAIN: %d\n"
                "\n"
                "================================================================\n"
                "\n"
                "XAPP=energy_saver\n"
                "ACTION=%s\n"
                "RU_COUNT=%d\n"
                "MMWAVE_COUNT=%d\n"
                "POWER_LEVEL=%d\n"
                "WATCHDOG_TRIGGERED=%s\n"
                "LAST_COMMAND_AGE=%ld\n"
                "COMMANDS_RECEIVED=%d\n"
                "WATCHDOG_TRIGGERS=%d\n"
                "TIMESTAMP=%ld\n",
                action_str,
                g_xapp_state.ru_count,
                g_xapp_state.mmwave_count,
                g_xapp_state.power_level,
                watchdog_str,
                (long)since_last_cmd,
                g_xapp_state.ttl_seconds,
                g_xapp_state.commands_received,
                g_xapp_state.watchdog_triggers,
                g_xapp_state.full_power_count,
                g_xapp_state.conditional_reduce_count,
                g_xapp_state.power_down_count,
                g_xapp_state.power_down_eco_count,
                g_xapp_state.maintain_count,
                action_str,
                g_xapp_state.ru_count,
                g_xapp_state.mmwave_count,
                g_xapp_state.power_level,
                watchdog_str,
                (long)since_last_cmd,
                g_xapp_state.commands_received,
                g_xapp_state.watchdog_triggers,
                (long)now);
        fflush(g_xapp_state.status_file);
    }
}

static void init_status_file(void)
{
    system("mkdir -p /tmp/xapp_intents");
    g_xapp_state.status_file = fopen(STATUS_FILE_PATH, "w");
    if (!g_xapp_state.status_file) {
        fprintf(stderr, "[XAPP-ENERGY] WRN: Não foi possível criar %s\n", STATUS_FILE_PATH);
    }
}

static void close_status_file(void)
{
    if (g_xapp_state.status_file) {
        fclose(g_xapp_state.status_file);
        g_xapp_state.status_file = NULL;
    }
}

/*******************************************************************************
 * MAIN
 ******************************************************************************/
int main(int argc, char* argv[])
{
    fr_args_t args = init_fr_args(argc, argv);
    init_xapp_api(&args);
    
    printf("\n");
    printf("╔══════════════════════════════════════════════════════════════╗\n");
    printf("║       ENERGY SAVER xApp - Pure Actuator (GreenRAN)       ║\n");
    printf("╠══════════════════════════════════════════════════════════════╣\n");
    printf("║  ARQUITETURA: Atuador Puro Controlado pelo rApp          ║\n");
    printf("╠══════════════════════════════════════════════════════════════╣\n");
    printf("║  - Lê comandos JSON do rApp                              ║\n");
    printf("║  - Executa comandos sem decisão própria                   ║\n");
    printf("║  - Watchdog: %ds sem comando → FULL_POWER               ║\n", DEFAULT_TTL_SECONDS);
    printf("╠══════════════════════════════════════════════════════════════╣\n");
    printf("║  Comandos:                                                ║\n");
    printf("║    FULL_POWER         → RU=1, mmWave=1, 100%%             ║\n");
    printf("║    CONDITIONAL_REDUCE → RU=1, mmWave=1, 60%%              ║\n");
    printf("║    POWER_DOWN         → RU=1, mmWave=1, 50%%              ║\n");
    printf("║    POWER_DOWN_ECO     → RU=1, mmWave=1, 25%%              ║\n");
    printf("║    MAINTAIN     → Manter estado atual                     ║\n");
    printf("╚══════════════════════════════════════════════════════════════╝\n\n");
    
    // Inicializar estado
    g_xapp_state.current_action = ACTION_NONE;
    g_xapp_state.previous_action = ACTION_NONE;
    g_xapp_state.ru_count = 1;          // Começa em FULL_POWER (safe)
    g_xapp_state.mmwave_count = 1;
    g_xapp_state.power_level = 100;
    g_xapp_state.last_command_time = time(NULL);
    g_xapp_state.ttl_seconds = DEFAULT_TTL_SECONDS;
    g_xapp_state.watchdog_triggered = false;
    g_xapp_state.running = true;
    g_xapp_state.last_command_timestamp = 0;
    
    g_sockfd = init_socket();
    if (g_sockfd == -1) {
        fprintf(stderr, "[XAPP-ENERGY] ERRO: Falha ao inicializar socket\n");
        return 1;
    }
    
    init_status_file();
    
    system("mkdir -p /tmp/xapp_intents");
    
    // Criar thread de monitoramento de comandos
    pthread_t cmd_thread;
    if (pthread_create(&cmd_thread, NULL, command_monitor_thread, NULL) != 0) {
        fprintf(stderr, "[XAPP-ENERGY] ERRO: Falha ao criar thread\n");
        return 1;
    }
    
    printf("[XAPP-ENERGY] Thread de monitoramento de comandos iniciada\n");
    printf("[XAPP-ENERGY] Aguardando comandos do rApp...\n\n");
    
    uint64_t start_time = time_now_us();
    const uint64_t max_runtime_s = get_max_runtime_seconds();
    int cycle = 0;
    
    while (g_xapp_state.running) {
        usleep(CONTROL_LOOP_INTERVAL_MS * 1000);
        cycle++;
        uint64_t elapsed = (time_now_us() - start_time) / 1000000;
        
        pthread_mutex_lock(&g_mtx);
        
        energy_action_e action = g_xapp_state.current_action;
        int ru = g_xapp_state.ru_count;
        int mmwave = g_xapp_state.mmwave_count;
        int power = g_xapp_state.power_level;
        bool watchdog = g_xapp_state.watchdog_triggered;
        int cmds = g_xapp_state.commands_received;
        int wd_triggers = g_xapp_state.watchdog_triggers;
        time_t since_last = time(NULL) - g_xapp_state.last_command_time;
        
        pthread_mutex_unlock(&g_mtx);
        
        // Escrever status
        write_status_file();
        
        // Display a cada 5 ciclos ou quando watchdog ativa
        if (cycle % 5 == 0 || watchdog) {
            const char* action_color = action_to_color(action);
            
            printf("\n");
            printf("================================================================\n");
            printf("           xApp ENERGY SAVER (Pure Actuator)\n");
            printf("================================================================\n");
            printf("\n");
            printf("  Ação: %s[%s]%s\n", action_color, action_to_string(action), COLOR_RESET);
            printf("\n");
            printf("  Estado:\n");
            printf("    RUs ativos: %d\n", ru);
            printf("    mmWave ativo: %d\n", mmwave);
            printf("    Power Level: %d%%\n", power);
            printf("\n");
            printf("  Watchdog:\n");
            printf("    Último comando: %ld segundos atrás\n", (long)since_last);
            printf("    TTL: %d segundos\n", g_xapp_state.ttl_seconds);
            
            if (watchdog) {
                printf("    \033[1;31m>>> WATCHDOG ATIVO - rApp pode estar offline!\033[0m\n");
            } else {
                printf("    \033[1;32m<<< NORMAL - rApp conectado\033[0m\n");
            }
            
            printf("\n");
            printf("  Estatísticas:\n");
            printf("    Comandos recebidos: %d\n", cmds);
            printf("    Watchdog triggers: %d\n", wd_triggers);
            printf("================================================================\n");
        }
        
        // Timeout de execução
        if (max_runtime_s > 0 && elapsed >= max_runtime_s) {
            printf("[XAPP-ENERGY] Tempo máximo (%llus). Encerrando...\n",
                   (unsigned long long)max_runtime_s);
            g_xapp_state.running = false;
        }
    }
    
    printf("\n[XAPP-ENERGY] Finalizando...\n");
    
    pthread_join(cmd_thread, NULL);
    
    while (try_stop_xapp_api() == false) usleep(1000);
    
    close_status_file();
    if (g_sockfd >= 0) {
        close(g_sockfd);
        unlink(energy_socket_path());
        g_sockfd = -1;
    }
    
    printf("\n╔══════════════════════════════════════════════════════════════╗\n");
    printf("║       ENERGY SAVER xApp - FINALIZADO                    ║\n");
    printf("╠══════════════════════════════════════════════════════════════╣\n");
    printf("║  Comandos Recebidos: %d                                 ║\n", g_xapp_state.commands_received);
    printf("║  Watchdog Triggers: %d                                  ║\n", g_xapp_state.watchdog_triggers);
    printf("║  FULL_POWER: %d | REDUCE: %d | DOWN: %d | MAINT: %d  ║\n",
           g_xapp_state.full_power_count,
           g_xapp_state.conditional_reduce_count,
           g_xapp_state.power_down_count,
           g_xapp_state.maintain_count);
    printf("╚══════════════════════════════════════════════════════════════╝\n");
    
    return 0;
}
