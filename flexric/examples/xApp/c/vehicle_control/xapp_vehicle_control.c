/*
 * xApp-VehicleSafety (Fase 1): proteção de SLA veicular como xApp real.
 *
 * Contrato documentado em docs/APP3_VEICULAR_ARQUITETURA.md: o xApp reage
 * perto do rádio aplicando EXATAMENTE a mesma política de SLA do framework
 * Python (src/vehicle_policy_runtime.py), publicando o mesmo arquivo de
 * intenção consumido pelo rApp (read_vehicle_intent).  A fonte de dados da
 * decisão é o export nativo extended_metrics.json (proveniência pdcp_real) —
 * a assinatura E2-KPM e o reforço de fatia (E2SM-RC) são a Fase 2.
 *
 * Formato do intent é byte-compatível com src/xapp_vehicle_control.py:
 *   xApp=vehicle_control
 *   TIMESTAMP=<unix>
 *   STATE=... ACTION=... VIOLATION=... REASON=... CONFIDENCE=%.4f ...
 *
 * Sem -c/-p (FlexRIC), roda standalone (usado nos testes golden).
 */
#include <ctype.h>
#include <errno.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "../../../../src/xApp/e42_xapp_api.h"

#define MAX_VEHICLE_UES 64
#define MAX_STRING 128
#define METRICS_MAX_BYTES (8u * 1024u * 1024u)
#define INTENT_HEADER "xApp=vehicle_control\n"
#define STATUS_SCHEMA "greenran.vehicle_safety_status.v1"

typedef struct {
  char imsi[MAX_STRING];
  char vehicle_role[MAX_STRING];
  char autonomy_state[MAX_STRING];
  char risk_state[MAX_STRING];
  char vehicle_id[MAX_STRING];
  double latency_ms;
  double packet_loss_percent;
  double speed_mps;
  double sample_window_s;
  long tx_pdus;
  long rx_pdus;
  int has_loss_value;
  int has_latency_avg;
  double latency_avg_us;
  double latency_us;
  int latency_is_proxy;
  char provenance[MAX_STRING];
} vehicle_entry_t;

typedef struct {
  int available;
  int stale;
  double age_seconds; /* < 0.0 => desconhecido (equivalente a None) */
  int total_vehicles;
  int ego_present;
  int high_risk_vehicles;
  int medium_risk_vehicles;
  int degraded_autonomy_vehicles;
  double max_latency_ms;
  double max_packet_loss_percent;
  double max_speed_mps;
  long min_tx_pdus;
  double min_sample_window_s;
  char reason[MAX_STRING];
  vehicle_entry_t entries[MAX_VEHICLE_UES];
  int entry_count;
} vehicle_metrics_t;

typedef struct {
  const char* severity; /* none | unknown | critical | warning | normal */
  const char* violation;
  const char* action;
  char reason[MAX_STRING];
  double confidence;
  int sla_violated;
  int guard_active;
  int warmup;
  int economic_replay_eligible;
} vehicle_policy_t;

typedef struct {
  const char* metrics_path;
  const char* intent_path;
  const char* status_path;
  double interval;
  double stale_after;
  long cycles; /* 0 = infinito */
  int expected_imsis[64];
  int expected_imsi_count;
  int e2_enabled;
  int strict_real;
} xapp_config_t;

static volatile sig_atomic_t running = 1;

static void stop_handler(int signal_number)
{
  (void)signal_number;
  running = 0;
}

static void crash_handler(int signal_number)
{
  dprintf(STDERR_FILENO, "[VehicleSafety] fatal signal=%d\n", signal_number);
  _exit(128 + signal_number);
}

static void lower_ascii(char* buffer)
{
  for (; *buffer; ++buffer) *buffer = (char)tolower((unsigned char)*buffer);
}

static int env_flag(const char* name)
{
  const char* value = getenv(name);
  if (value == NULL) return 0;
  return strcasecmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
         strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0;
}

/*******************************************************************************
 * Scanner JSON mínimo (somente o necessário para ue_metrics).  Sem alocação:
 * strings são copiadas para buffers do chamador; objetos/arrays desconhecidos
 * são saltados com acompanhamento de aninhamento e escapes.
 ******************************************************************************/

static const char* json_skip_ws(const char* p, const char* end)
{
  while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) ++p;
  return p;
}

static const char* json_skip_string(const char* p, const char* end)
{
  ++p; /* abre aspas */
  while (p < end && *p != '"') {
    if (*p == '\\' && p + 1 < end) ++p;
    ++p;
  }
  return (p < end) ? p + 1 : end;
}

static const char* json_skip_value(const char* p, const char* end)
{
  p = json_skip_ws(p, end);
  if (p >= end) return end;
  if (*p == '"') return json_skip_string(p, end);
  if (*p == '{' || *p == '[') {
    int depth = 0;
    while (p < end) {
      if (*p == '"') {
        p = json_skip_string(p, end);
        continue;
      }
      if (*p == '{' || *p == '[') ++depth;
      else if (*p == '}' || *p == ']') {
        --depth;
        if (depth == 0) return p + 1;
      }
      ++p;
    }
    return end;
  }
  while (p < end && *p != ',' && *p != '}' && *p != ']' && *p != ' ' &&
         *p != '\n' && *p != '\r' && *p != '\t')
    ++p;
  return p;
}

/* Copia o conteúdo de uma string JSON (com escapes simples) para out. */
static const char* json_copy_string(const char* p, const char* end, char* out,
                                    size_t out_size)
{
  size_t used = 0;
  if (p >= end || *p != '"') return p;
  ++p;
  while (p < end && *p != '"') {
    char c = *p;
    if (c == '\\' && p + 1 < end) {
      ++p;
      switch (*p) {
        case 'n': c = '\n'; break;
        case 't': c = '\t'; break;
        case 'r': c = '\r'; break;
        default: c = *p; break;
      }
    }
    if (used + 1 < out_size) out[used++] = c;
    ++p;
  }
  out[used] = '\0';
  return (p < end) ? p + 1 : end;
}

/* Avança até a ASpas que abre o valor string da chave; NULL se ausente. */
static const char* json_string_field(const char* object, const char* end,
                                     const char* key, char* out,
                                     size_t out_size)
{
  char pattern[MAX_STRING];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);
  const char* p = object;
  while ((p = strstr(p, pattern)) != NULL && p < end) {
    const char* cursor = p + strlen(pattern);
    cursor = json_skip_ws(cursor, end);
    if (cursor < end && *cursor == ':') {
      cursor = json_skip_ws(cursor + 1, end);
      return json_copy_string(cursor, end, out, out_size);
    }
    p += strlen(pattern);
  }
  if (out_size) out[0] = '\0';
  return NULL;
}

static const char* json_number_field(const char* object, const char* end,
                                     const char* key, double* out)
{
  char pattern[MAX_STRING];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);
  const char* p = object;
  while ((p = strstr(p, pattern)) != NULL && p < end) {
    const char* cursor = p + strlen(pattern);
    cursor = json_skip_ws(cursor, end);
    if (cursor < end && *cursor == ':') {
      cursor = json_skip_ws(cursor + 1, end);
      char* stop = NULL;
      double value = strtod(cursor, &stop);
      if (stop != cursor) {
        *out = value;
        return cursor;
      }
      return NULL; /* null/true/objeto — presente mas não numérico */
    }
    p += strlen(pattern);
  }
  return NULL;
}

static const char* json_bool_field(const char* object, const char* end,
                                   const char* key, int* out)
{
  char pattern[MAX_STRING];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);
  const char* p = object;
  while ((p = strstr(p, pattern)) != NULL && p < end) {
    const char* cursor = p + strlen(pattern);
    cursor = json_skip_ws(cursor, end);
    if (cursor < end && *cursor == ':') {
      cursor = json_skip_ws(cursor + 1, end);
      if (cursor + 4 <= end && strncmp(cursor, "true", 4) == 0) {
        *out = 1;
        return cursor;
      }
      if (cursor + 5 <= end && strncmp(cursor, "false", 5) == 0) {
        *out = 0;
        return cursor;
      }
      return NULL;
    }
    p += strlen(pattern);
  }
  return NULL;
}

/*******************************************************************************
 * Coleta de métricas (espelho de get_vehicle_metrics, caminho estrito nativo).
 ******************************************************************************/

static void metrics_defaults(vehicle_metrics_t* metrics)
{
  memset(metrics, 0, sizeof(*metrics));
  metrics->age_seconds = -1.0;
  metrics->min_tx_pdus = 0;
  metrics->min_sample_window_s = 0.0;
}

static void collect_entry(vehicle_entry_t* entry, const char* object,
                          const char* end, const char* imsi)
{
  memset(entry, 0, sizeof(*entry));
  snprintf(entry->imsi, sizeof(entry->imsi), "%s", imsi);
  json_string_field(object, end, "vehicle_role", entry->vehicle_role,
                    sizeof(entry->vehicle_role));
  json_string_field(object, end, "autonomy_state", entry->autonomy_state,
                    sizeof(entry->autonomy_state));
  json_string_field(object, end, "risk_state", entry->risk_state,
                    sizeof(entry->risk_state));
  json_string_field(object, end, "vehicle_id", entry->vehicle_id,
                    sizeof(entry->vehicle_id));
  json_string_field(object, end, "pdcp_provenance", entry->provenance,
                    sizeof(entry->provenance));
  double value = 0.0;
  if (json_number_field(object, end, "packet_loss_percent", &value) != NULL) {
    entry->packet_loss_percent = value;
    entry->has_loss_value = 1;
  }
  if (json_number_field(object, end, "latency_avg_us", &value) != NULL) {
    entry->latency_avg_us = value;
    entry->has_latency_avg = 1;
  }
  if (json_number_field(object, end, "latency_us", &value) != NULL)
    entry->latency_us = value;
  if (json_number_field(object, end, "speed_mps", &value) != NULL)
    entry->speed_mps = value;
  if (json_number_field(object, end, "sample_window_s", &value) != NULL)
    entry->sample_window_s = value;
  if (json_number_field(object, end, "tx_pdus", &value) != NULL)
    entry->tx_pdus = (long)value;
  if (json_number_field(object, end, "rx_pdus", &value) != NULL)
    entry->rx_pdus = (long)value;
  int flag = 0;
  if (json_bool_field(object, end, "latency_is_proxy", &flag) != NULL)
    entry->latency_is_proxy = flag;

  /* float(latency_avg_us or latency_us or 0) / 1000.0 */
  double chosen = 0.0;
  if (entry->has_latency_avg && entry->latency_avg_us != 0.0)
    chosen = entry->latency_avg_us;
  else if (entry->latency_us != 0.0)
    chosen = entry->latency_us;
  entry->latency_ms = chosen / 1000.0;
}

static int imsi_expected(const xapp_config_t* config, const char* imsi)
{
  for (int i = 0; i < config->expected_imsi_count; ++i)
    if (config->expected_imsis[i] == atoi(imsi)) return 1;
  return 0;
}

static void collect_metrics(const xapp_config_t* config,
                            vehicle_metrics_t* metrics)
{
  metrics_defaults(metrics);

  FILE* handle = fopen(config->metrics_path, "r");
  if (handle == NULL) {
    snprintf(metrics->reason, sizeof(metrics->reason), "metrics_file_missing");
    return;
  }
  struct stat info = {0};
  if (stat(config->metrics_path, &info) == 0)
    metrics->age_seconds = difftime(time(NULL), info.st_mtime);
  metrics->stale = metrics->age_seconds >= 0.0 &&
                   metrics->age_seconds > config->stale_after;

  char* buffer = malloc(METRICS_MAX_BYTES);
  if (buffer == NULL) {
    fclose(handle);
    snprintf(metrics->reason, sizeof(metrics->reason), "metrics_alloc_failed");
    return;
  }
  size_t size = fread(buffer, 1, METRICS_MAX_BYTES - 1, handle);
  fclose(handle);
  buffer[size] = '\0';
  const char* end = buffer + size;

  const char* cursor = strstr(buffer, "\"ue_metrics\"");
  if (cursor == NULL) {
    free(buffer);
    snprintf(metrics->reason, sizeof(metrics->reason), "ue_metrics_missing");
    return;
  }
  cursor = strchr(cursor, '{');
  if (cursor == NULL) {
    free(buffer);
    snprintf(metrics->reason, sizeof(metrics->reason), "ue_metrics_malformed");
    return;
  }
  ++cursor; /* entra no objeto ue_metrics */

  int invalid_real_vehicle = 0;
  while (cursor < end && *cursor != '}' && running) {
    cursor = json_skip_ws(cursor, end);
    if (cursor >= end || *cursor == '}' || *cursor == '\0') break;
    char imsi[MAX_STRING] = {0};
    cursor = json_copy_string(cursor, end, imsi, sizeof(imsi));
    cursor = json_skip_ws(cursor, end);
    if (cursor >= end || *cursor != ':') break;
    cursor = json_skip_ws(cursor + 1, end);
    if (cursor >= end) break;

    vehicle_entry_t candidate;
    memset(&candidate, 0, sizeof(candidate));
    int is_vehicle = 0;
    if (*cursor == '{') {
      const char* object_end = json_skip_value(cursor, end);
      char device_type[MAX_STRING] = {0};
      json_string_field(cursor, object_end, "device_type", device_type,
                        sizeof(device_type));
      is_vehicle = strcmp(device_type, "vehicle") == 0;
      if (is_vehicle) collect_entry(&candidate, cursor, object_end, imsi);
      cursor = object_end;
    } else {
      cursor = json_skip_value(cursor, end);
    }
    if (is_vehicle && metrics->entry_count < MAX_VEHICLE_UES) {
      if (config->strict_real &&
          (strcmp(candidate.provenance, "pdcp_real") != 0 ||
           !candidate.has_loss_value || candidate.latency_is_proxy)) {
        invalid_real_vehicle = 1;
      } else {
        metrics->entries[metrics->entry_count++] = candidate;
      }
    }
    cursor = json_skip_ws(cursor, end);
    if (cursor < end && *cursor == ',') ++cursor;
  }
  free(buffer);

  if (config->strict_real) {
    int observed = 0;
    for (int i = 0; i < metrics->entry_count; ++i)
      if (imsi_expected(config, metrics->entries[i].imsi)) ++observed;
    if (invalid_real_vehicle ||
        observed != config->expected_imsi_count) {
      snprintf(metrics->reason, sizeof(metrics->reason),
               "real_pdcp_vehicle_coverage_incomplete");
      metrics->entry_count = 0;
      return;
    }
  }

  if (metrics->entry_count == 0) {
    snprintf(metrics->reason, sizeof(metrics->reason), "no_vehicle_entries");
    return;
  }

  metrics->available = 1;
  metrics->total_vehicles = metrics->entry_count;
  for (int i = 0; i < metrics->entry_count; ++i) {
    const vehicle_entry_t* entry = &metrics->entries[i];
    char state[MAX_STRING];
    snprintf(state, sizeof(state), "%s", entry->risk_state);
    lower_ascii(state);
    if (strcmp(state, "high") == 0 || strcmp(state, "critical") == 0)
      ++metrics->high_risk_vehicles;
    if (strcmp(state, "medium") == 0 || strcmp(state, "warning") == 0)
      ++metrics->medium_risk_vehicles;
    snprintf(state, sizeof(state), "%s", entry->autonomy_state);
    lower_ascii(state);
    if (strcmp(state, "normal") != 0 && strcmp(state, "unknown") != 0)
      ++metrics->degraded_autonomy_vehicles;
    if (strcmp(entry->vehicle_role, "ego") == 0) metrics->ego_present = 1;
    if (entry->latency_ms > metrics->max_latency_ms)
      metrics->max_latency_ms = entry->latency_ms;
    if (entry->packet_loss_percent > metrics->max_packet_loss_percent)
      metrics->max_packet_loss_percent = entry->packet_loss_percent;
    if (entry->speed_mps > metrics->max_speed_mps)
      metrics->max_speed_mps = entry->speed_mps;
    if (i == 0 || entry->tx_pdus < metrics->min_tx_pdus)
      metrics->min_tx_pdus = entry->tx_pdus;
    if (i == 0 || entry->sample_window_s < metrics->min_sample_window_s)
      metrics->min_sample_window_s = entry->sample_window_s;
  }
}

/*******************************************************************************
 * Política (espelho 1:1 de evaluate_vehicle_policy).
 ******************************************************************************/

static void evaluate_policy(const vehicle_metrics_t* metrics,
                            vehicle_policy_t* policy)
{
  memset(policy, 0, sizeof(*policy));
  policy->confidence = 0.0;
  if (!metrics->available) {
    policy->severity = "none";
    return;
  }

  if (metrics->min_tx_pdus < 100 || metrics->min_sample_window_s < 1.0) {
    policy->severity = "unknown";
    policy->violation = "VEHICLE_WARMUP";
    policy->action = "MONITOR";
    snprintf(policy->reason, sizeof(policy->reason),
             "janela veicular insuficiente para decisão SLA "
             "(min_tx_pdus=%ld, min_window_s=%.3f)",
             metrics->min_tx_pdus, metrics->min_sample_window_s);
    policy->guard_active = 1;
    policy->warmup = 1;
    return;
  }

  char critical[MAX_STRING] = {0};
  char warning[MAX_STRING] = {0};
  if (metrics->stale) {
    if (metrics->age_seconds >= 0.0)
      snprintf(warning, sizeof(warning),
               "snapshot de veículo antigo (%.0fs)", metrics->age_seconds);
    else
      snprintf(warning, sizeof(warning), "snapshot de veículo antigo");
  }
  if (metrics->high_risk_vehicles > 0)
    snprintf(critical, sizeof(critical), "veículos em risco alto=%d",
             metrics->high_risk_vehicles);
  if (critical[0] == '\0' && metrics->degraded_autonomy_vehicles > 0)
    snprintf(critical, sizeof(critical),
             "autonomia degradada em %d veículo(s)",
             metrics->degraded_autonomy_vehicles);
  if (metrics->ego_present && metrics->max_latency_ms >= 20.0)
    snprintf(critical, sizeof(critical), "latência veicular %.0fms >= 20ms",
             metrics->max_latency_ms);
  else if (metrics->ego_present && metrics->max_latency_ms >= 10.0 &&
           warning[0] == '\0')
    snprintf(warning, sizeof(warning), "latência veicular %.0fms >= 10ms",
             metrics->max_latency_ms);
  if (metrics->max_packet_loss_percent >= 1.0)
    snprintf(critical, sizeof(critical),
             "packet loss veicular %.1f%% >= 1%%",
             metrics->max_packet_loss_percent);
  else if (metrics->max_packet_loss_percent >= 0.5 && warning[0] == '\0')
    snprintf(warning, sizeof(warning), "packet loss veicular %.1f%% >= 0.5%%",
             metrics->max_packet_loss_percent);

  if (critical[0] != '\0') {
    policy->severity = "critical";
    policy->violation = "VEHICLE_CRITICAL";
    policy->action = "FULL_POWER";
    snprintf(policy->reason, sizeof(policy->reason), "%s", critical);
    policy->confidence = 0.95;
    policy->sla_violated = 1;
    return;
  }
  if (warning[0] == '\0' && metrics->medium_risk_vehicles > 0)
    snprintf(warning, sizeof(warning), "veículos em risco médio=%d",
             metrics->medium_risk_vehicles);
  if (warning[0] != '\0') {
    policy->severity = "warning";
    policy->violation = "VEHICLE_WARNING";
    policy->action = "FULL_POWER_GUARD";
    snprintf(policy->reason, sizeof(policy->reason), "%s", warning);
    policy->confidence = 0.8;
    policy->guard_active = 1;
    return;
  }
  policy->severity = "normal";
  policy->action = "MONITOR";
  snprintf(policy->reason, sizeof(policy->reason), "cenário veicular saudável");
  policy->confidence = 0.7;
}

/*******************************************************************************
 * Publicação (intent byte-compatível + status JSON).
 ******************************************************************************/

static void write_intent(const char* path, const vehicle_metrics_t* metrics,
                         const vehicle_policy_t* policy)
{
  FILE* file = fopen(path, "w");
  if (file == NULL) return;
  const char* state;
  if (strcmp(policy->severity, "critical") == 0) state = "CRITICAL";
  else if (strcmp(policy->severity, "warning") == 0) state = "WARNING";
  else if (metrics->available) state = "NORMAL";
  else state = "IDLE";
  const char* action = (policy->action != NULL && policy->action[0] != '\0')
                           ? policy->action
                           : "MONITOR";

  fputs(INTENT_HEADER, file);
  fprintf(file, "TIMESTAMP=%lld\n", (long long)time(NULL));
  fprintf(file, "STATE=%s\n", state);
  fprintf(file, "ACTION=%s\n", action);
  fprintf(file, "VIOLATION=%s\n", policy->violation ? policy->violation : "");
  fprintf(file, "REASON=%s\n", policy->reason);
  fprintf(file, "CONFIDENCE=%.4f\n", policy->confidence);
  fprintf(file, "TOTAL_VEHICLES=%d\n", metrics->total_vehicles);
  fprintf(file, "HIGH_RISK_VEHICLES=%d\n", metrics->high_risk_vehicles);
  fprintf(file, "MEDIUM_RISK_VEHICLES=%d\n", metrics->medium_risk_vehicles);
  fprintf(file, "DEGRADED_AUTONOMY_VEHICLES=%d\n",
          metrics->degraded_autonomy_vehicles);
  fprintf(file, "MAX_LATENCY_MS=%.4f\n", metrics->max_latency_ms);
  fprintf(file, "MAX_PACKET_LOSS_PERCENT=%.4f\n",
          metrics->max_packet_loss_percent);
  fprintf(file, "MAX_SPEED_MPS=%.4f\n", metrics->max_speed_mps);
  fprintf(file, "EGO_PRESENT=%s\n", metrics->ego_present ? "true" : "false");
  fprintf(file, "AVAILABLE=%s\n", metrics->available ? "true" : "false");
  fclose(file);
}

static void write_status(const char* path, const char* state,
                         const char* reason, const vehicle_metrics_t* metrics,
                         const xapp_config_t* config, const char* e2_state)
{
  if (path == NULL || path[0] == '\0') return;
  FILE* file = fopen(path, "w");
  if (file == NULL) return;
  fprintf(file,
          "{\"schema\":\"%s\",\"state\":\"%s\",\"reason\":\"%s\","
          "\"ue_total\":%d,\"high_risk\":%d,\"stale\":%s,"
          "\"strict_real\":%s,\"e2\":\"%s\",\"timestamp\":%lld}\n",
          STATUS_SCHEMA, state, reason ? reason : "",
          metrics ? metrics->total_vehicles : 0,
          metrics ? metrics->high_risk_vehicles : 0,
          (metrics && metrics->stale) ? "true" : "false",
          config->strict_real ? "true" : "false", e2_state,
          (long long)time(NULL));
  fclose(file);
}

/*******************************************************************************
 * Configuração e ciclo principal.
 ******************************************************************************/

static void parse_imsis(const char* spec, xapp_config_t* config)
{
  char buffer[MAX_STRING];
  snprintf(buffer, sizeof(buffer), "%s", spec);
  char* save = NULL;
  for (char* token = strtok_r(buffer, ",", &save);
       token != NULL && config->expected_imsi_count < 64;
       token = strtok_r(NULL, ",", &save)) {
    int value = atoi(token);
    if (value > 0) config->expected_imsis[config->expected_imsi_count++] = value;
  }
}

/* Extrai os argumentos GreenRAN e devolve argv filtrado só com -c/-p/-x. */
static void parse_args(int argc, char** argv, xapp_config_t* config,
                       char*** filtered, int* filtered_argc,
                       char imsi_spec[MAX_STRING])
{
  static char* buffer[64];
  int used = 0;
  buffer[used++] = argv[0];
  *filtered = buffer;

  for (int i = 1; i < argc; ++i) {
    const char* argument = argv[i];
    if (strcmp(argument, "--metrics-path") == 0 && i + 1 < argc) {
      config->metrics_path = argv[++i];
    } else if (strcmp(argument, "--intent-path") == 0 && i + 1 < argc) {
      config->intent_path = argv[++i];
    } else if (strcmp(argument, "--status-path") == 0 && i + 1 < argc) {
      config->status_path = argv[++i];
    } else if (strcmp(argument, "--vehicle-imsis") == 0 && i + 1 < argc) {
      snprintf(imsi_spec, MAX_STRING, "%s", argv[++i]);
    } else if (strcmp(argument, "--interval") == 0 && i + 1 < argc) {
      config->interval = strtod(argv[++i], NULL);
    } else if (strcmp(argument, "--stale-after") == 0 && i + 1 < argc) {
      config->stale_after = strtod(argv[++i], NULL);
    } else if (strcmp(argument, "--cycles") == 0 && i + 1 < argc) {
      config->cycles = strtol(argv[++i], NULL, 10);
    } else if (strcmp(argument, "-c") == 0 && i + 1 < argc) {
      config->e2_enabled = 1;
      buffer[used++] = argv[i];
      buffer[used++] = argv[++i];
    } else if (strcmp(argument, "-p") == 0 && i + 1 < argc) {
      buffer[used++] = argv[i];
      buffer[used++] = argv[++i];
    } else if (strcmp(argument, "-x") == 0 && i + 1 < argc) {
      buffer[used++] = argv[i];
      buffer[used++] = argv[++i];
    } else if (strcmp(argument, "-h") == 0) {
      printf("uso: xapp_vehicle_control [--metrics-path P] [--intent-path P] "
             "[--status-path P] [--vehicle-imsis 16,17,18,19,20] "
             "[--interval S] [--stale-after S] [--cycles N] [-c conf] "
             "[-p libs] [-x porta]\n");
      exit(EXIT_SUCCESS);
    }
  }
  *filtered_argc = used;
}

int main(int argc, char** argv)
{
  setvbuf(stderr, NULL, _IONBF, 0);
  signal(SIGPIPE, SIG_IGN);
  signal(SIGABRT, crash_handler);
  signal(SIGSEGV, crash_handler);
  signal(SIGBUS, crash_handler);
  signal(SIGINT, stop_handler);
  signal(SIGTERM, stop_handler);

  xapp_config_t config = {0};
  config.metrics_path = "xapp_metrics/extended_metrics.json";
  config.intent_path = "xapp_intents/vehicle_control.txt";
  config.interval = 2.0;
  config.stale_after = 20.0;
  config.strict_real = env_flag("GREENRAN_REQUIRE_REAL_PDCP");
  char imsi_spec[MAX_STRING] = "16,17,18,19,20";
  char** filtered = NULL;
  int filtered_argc = 0;
  parse_args(argc, argv, &config, &filtered, &filtered_argc, imsi_spec);
  if (config.interval < 1.0) config.interval = 1.0;
  parse_imsis(imsi_spec, &config);

  const char* e2_state = "disabled";
  int e2_initialized = 0;
  if (config.e2_enabled) {
    fr_args_t args = init_fr_args(filtered_argc, filtered);
    init_xapp_api(&args);
    e2_initialized = 1;
    e2_state = "registered";
  }

  printf("[VehicleSafety] starting (strict_real=%d, e2=%s)\n",
         config.strict_real, e2_state);
  printf("[VehicleSafety] ready\n");

  vehicle_metrics_t metrics;
  vehicle_policy_t policy;
  metrics_defaults(&metrics);
  memset(&policy, 0, sizeof(policy));
  long cycle = 0;
  while (running && (config.cycles <= 0 || cycle < config.cycles)) {
    collect_metrics(&config, &metrics);
    evaluate_policy(&metrics, &policy);
    write_intent(config.intent_path, &metrics, &policy);
    write_status(config.status_path,
                 metrics.available ? "ready" : "degraded",
                 metrics.reason[0] != '\0' ? metrics.reason : "ok", &metrics,
                 &config, e2_state);
    ++cycle;
    for (double waited = 0.0;
         running && (config.cycles <= 0 || cycle < config.cycles) &&
         waited < config.interval;
         waited += 0.1)
      usleep(100000);
  }

  write_status(config.status_path, "stopped", "signal", &metrics, &config,
               e2_state);
  printf("[VehicleSafety] stopping\n");
  if (e2_initialized) {
    while (!try_stop_xapp_api()) usleep(1000);
  }
  return EXIT_SUCCESS;
}
