/* Dedicated TA-SAM actuator: versioned GreenRAN bundles -> E2SM-RC. */
#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_parameter_value.h"
#include "../../../../src/sm/rc_sm/rc_sm_id.h"

#include <assert.h>
#include <execinfo.h>
#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define CONTROL_SCHEMA "greenran.control.bundle.v2"
#define CONTROL_SCHEMA_V3 "greenran.control.bundle.v3"
#define CONTROL_SCHEMA_V4 "greenran.control.bundle.v4"
#define ACK_SCHEMA "greenran.control.ack.v2"
#define DEFAULT_SOCKET "/tmp/sockets/tasam_control.sock"
#define MAX_REQUEST 131072
#define STYLE_SCHEDULER 2
#define STYLE_MOBILITY 3
#define STYLE_ENERGY 300
#define ACTION_PREPARE 1
#define ACTION_COMMIT 2
#define ACTION_CLEAR 3
#define ACTION_SET_POWER 2
#define ACTION_HANDOVER 1

enum {
  PARAM_MIN_DL_BP = 1,
  PARAM_MIN_UL_BP = 2,
  PARAM_WEIGHT_BP = 3,
  PARAM_TRANSACTION = 4,
  PARAM_EXPECTED_UES = 5,
  PARAM_TTL_MS = 6,
  PARAM_CELL_ID = 7,
  PARAM_POWER_PERCENT = 8,
  PARAM_MAX_DISCRETIONARY_DL_SYMBOLS_BP = 9,
  PARAM_SLEEP_COMMIT = 10,
};

static volatile sig_atomic_t running = 1;
/*
 * The ns-3 manifest is authoritative for the logical-cell to E2-node
 * mapping.  FlexRIC's global_e2_node_id representation is not guaranteed to
 * use the logical cell id directly (the current scenario advertises
 * cell_id=2 with ns3_node_id=3, for example), so registration order and the
 * old 2..4 shortcut are not safe identity checks.
 */
static uint32_t native_node_id_by_cell[5] = {0};
static char last_drain_sleep_transaction[128] = "";

static int64_t logical_cell_from_node(const global_e2_node_id_t* node);

static void crash_handler(int signal_number)
{
  void* frames[32];
  int count = backtrace(frames, 32);
  dprintf(STDERR_FILENO, "[TA-SAM actuator] fatal signal=%d frames=%d\n",
          signal_number, count);
  backtrace_symbols_fd(frames, count, STDERR_FILENO);
  _exit(128 + signal_number);
}

static void write_status(const char* path, const char* state, const char* reason,
                         const e2_node_arr_xapp_t* observed_nodes,
                         const char* socket_path)
{
  if (path == NULL || path[0] == '\0') return;
  FILE* file = fopen(path, "w");
  if (file == NULL) return;
  bool mapped[5] = {false, false, false, false, false};
  size_t nodes = observed_nodes == NULL ? 0 : observed_nodes->len;
  if (observed_nodes != NULL && observed_nodes->n != NULL) {
    for (size_t i = 0; i < observed_nodes->len; ++i) {
      if (observed_nodes->n[i].id.nb_id.nb_id == 0) continue;
      int64_t cell = logical_cell_from_node(&observed_nodes->n[i].id);
      if (cell >= 2 && cell <= 4) mapped[cell] = true;
    }
  }
  fprintf(file, "{\"schema\":\"greenran.tasam_actuator_status.v2\","
                "\"state\":\"%s\",\"reason\":\"%s\",\"ready\":%s,"
                "\"nodes\":%lu,\"observed_nodes\":[",
          state == NULL ? "unknown" : state,
          reason == NULL ? "" : reason,
          state != NULL && strcmp(state, "ready") == 0 ? "true" : "false",
          (unsigned long)nodes);
  bool first = true;
  if (observed_nodes != NULL && observed_nodes->n != NULL) {
    for (size_t i = 0; i < observed_nodes->len; ++i) {
      uint32_t raw = observed_nodes->n[i].id.nb_id.nb_id;
      if (raw == 0) continue;
      if (!first) fputc(',', file);
      fprintf(file, "{\"raw_node_id\":%u,\"logical_cell_id\":%lld}",
              raw, (long long)logical_cell_from_node(&observed_nodes->n[i].id));
      first = false;
    }
  }
  fprintf(file, "],\"mapped_cells\":[");
  first = true;
  for (int cell = 2; cell <= 4; ++cell) {
    if (!mapped[cell]) continue;
    if (!first) fputc(',', file);
    fprintf(file, "%d", cell);
    first = false;
  }
  fprintf(file, "],\"missing_cells\":[");
  first = true;
  for (int cell = 2; cell <= 4; ++cell) {
    if (mapped[cell]) continue;
    if (!first) fputc(',', file);
    fprintf(file, "%d", cell);
    first = false;
  }
  fprintf(file, "],\"socket_path\":\"%s\"}\n",
          socket_path == NULL ? "" : socket_path);
  fclose(file);
}

static size_t expected_node_count(void)
{
  const char* raw = getenv("GREENRAN_TASAM_EXPECTED_E2_NODES");
  if (raw == NULL || raw[0] == '\0') return 3;
  char* tail = NULL;
  unsigned long value = strtoul(raw, &tail, 10);
  if (tail == raw || value == 0 || value > 64) return 3;
  return (size_t)value;
}

static bool native_node_manifest_ready(void)
{
  const char* path = getenv("GREENRAN_E2_NODE_MANIFEST");
  if (path == NULL || path[0] == '\0') return false;
  FILE* file = fopen(path, "r");
  if (file == NULL) return false;
  char buffer[16384];
  size_t used = fread(buffer, 1, sizeof(buffer) - 1, file);
  fclose(file);
  buffer[used] = '\0';
  if (strstr(buffer, "greenran.ns3.e2_node_manifest.v1") == NULL) return false;
  if (strstr(buffer, "\"cell_id\":2") == NULL ||
      strstr(buffer, "\"cell_id\":3") == NULL ||
      strstr(buffer, "\"cell_id\":4") == NULL) return false;
  for (int cell = 2; cell <= 4; ++cell) {
    char cell_key[32];
    snprintf(cell_key, sizeof(cell_key), "\"cell_id\":%d", cell);
    const char* cell_entry = strstr(buffer, cell_key);
    const char* node_key = cell_entry == NULL
        ? NULL : strstr(cell_entry, "\"ns3_node_id\"");
    if (node_key == NULL) return false;
    char* tail = NULL;
    unsigned long node_id = strtoul(strchr(node_key, ':') + 1, &tail, 10);
    if (tail == strchr(node_key, ':') + 1 || node_id == 0 || node_id > UINT32_MAX)
      return false;
    native_node_id_by_cell[cell] = (uint32_t)node_id;
  }
  const char* cursor = buffer;
  size_t supported = 0;
  while ((cursor = strstr(cursor, "\"rc_control_supported\":true")) != NULL) {
    ++supported;
    cursor += 2;
  }
  return supported >= 3;
}

static bool refresh_e2_nodes(e2_node_arr_xapp_t* nodes, size_t expected)
{
  e2_node_arr_xapp_t fresh = e2_nodes_xapp_api();
  // Keep partial RIC snapshots for diagnostics.  Discarding them made a
  // two-node registration report as observed_nodes=0, hiding the exact DU
  // that blocked the topology gate.
  bool enough = fresh.len >= expected;
  free_e2_node_arr_xapp(nodes);
  *nodes = fresh;
  return enough;
}

// Scan the WHOLE node snapshot for the three managed cells.  The RIC cache
// legitimately contains extra agents (LTE anchor, E2DU) and can expose
// transient zeroed entries while a node registers, so readiness must be
// order-agnostic and tolerate unrelated or torn entries instead of failing
// on the first unexpected slot.
static bool snapshot_covers_managed_cells(const e2_node_arr_xapp_t* nodes)
{
  if (nodes == NULL || nodes->n == NULL) return false;
  bool seen_cell[5] = {false, false, false, false, false};
  for (size_t i = 0; i < nodes->len; ++i) {
    if (nodes->n[i].id.nb_id.nb_id == 0) continue;
    int cell = (int)logical_cell_from_node(&nodes->n[i].id);
    if (cell < 2 || cell > 4) continue;
    seen_cell[cell] = true;
  }
  return seen_cell[2] && seen_cell[3] && seen_cell[4];
}

static bool local_e2_nodes_ready(const e2_node_arr_xapp_t* nodes, size_t expected)
{
  (void)expected;
  if (nodes == NULL || nodes->n == NULL) return false;
  bool seen_cell[5] = {false, false, false, false, false};
  for (size_t i = 0; i < nodes->len; ++i) {
    if (nodes->n[i].id.nb_id.nb_id == 0) continue;
    uint32_t raw = nodes->n[i].id.nb_id.nb_id;
    int cell = (int)logical_cell_from_node(&nodes->n[i].id);
    fprintf(stderr, "[TA-SAM actuator] E2 node raw=%u logical_cell=%d\n",
            raw, cell);
    if (cell >= 2 && cell <= 4) seen_cell[cell] = true;
  }
  return seen_cell[2] && seen_cell[3] && seen_cell[4];
}

static bool ensure_directory(char* path)
{
  if (path == NULL || path[0] == '\0') return false;
  for (char* cursor = path + 1; *cursor != '\0'; ++cursor) {
    if (*cursor != '/') continue;
    *cursor = '\0';
    if (mkdir(path, 0750) != 0 && errno != EEXIST) return false;
    *cursor = '/';
  }
  return mkdir(path, 0750) == 0 || errno == EEXIST;
}

static void stop_handler(int signal_number)
{
  (void)signal_number;
  running = 0;
}

static ue_id_e2sm_t make_ue_id(uint64_t imsi)
{
  ue_id_e2sm_t ue = {0};
  ue.type = GNB_UE_ID_E2SM;
  ue.gnb.ran_ue_id = calloc(1, sizeof(uint64_t));
  assert(ue.gnb.ran_ue_id != NULL);
  *ue.gnb.ran_ue_id = imsi;
  return ue;
}

static e2sm_rc_ctrl_hdr_t make_header(uint64_t imsi, uint32_t style, uint16_t action)
{
  e2sm_rc_ctrl_hdr_t header = {0};
  header.format = FORMAT_1_E2SM_RC_CTRL_HDR;
  ue_id_e2sm_t ue = make_ue_id(imsi);
  header.frmt_1.ue_id = cp_ue_id_e2sm(&ue);
  free_ue_id_e2sm(&ue);
  header.frmt_1.ric_style_type = style;
  header.frmt_1.ctrl_act_id = action;
  return header;
}

static e2sm_rc_ctrl_msg_t make_integer_message(const uint32_t* ids,
                                               const int64_t* values,
                                               size_t count)
{
  e2sm_rc_ctrl_msg_t message = {0};
  message.format = FORMAT_1_E2SM_RC_CTRL_MSG;
  message.frmt_1.sz_ran_param = count;
  message.frmt_1.ran_param = calloc(count, sizeof(seq_ran_param_t));
  assert(message.frmt_1.ran_param != NULL);
  for (size_t i = 0; i < count; ++i) {
    seq_ran_param_t* parameter = &message.frmt_1.ran_param[i];
    parameter->ran_param_id = ids[i];
    parameter->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
    parameter->ran_param_val.flag_false = calloc(1, sizeof(ran_parameter_value_t));
    assert(parameter->ran_param_val.flag_false != NULL);
    parameter->ran_param_val.flag_false->type = INTEGER_RAN_PARAMETER_VALUE;
    parameter->ran_param_val.flag_false->int_ran = values[i];
  }
  return message;
}

static sm_ans_xapp_t send_integer_control(global_e2_node_id_t* node,
                                          uint64_t imsi,
                                          uint32_t style,
                                          uint16_t action,
                                          uint64_t transaction,
                                          const uint32_t* ids,
                                          const int64_t* values,
                                          size_t count)
{
  fprintf(stderr, "[TA-SAM actuator] RC control tx begin imsi=%lu style=%u action=%u transaction=%lu\n",
          (unsigned long)imsi, (unsigned)style, (unsigned)action,
          (unsigned long)transaction);
  fflush(stderr);
  rc_ctrl_req_data_t control = {0};
  control.hdr = make_header(imsi, style, action);
  control.msg = make_integer_message(ids, values, count);
  fprintf(stderr, "[TA-SAM actuator] RC params");
  for (size_t i = 0; i < count; ++i) {
    fprintf(stderr, " id=%u value=%lld", ids[i], (long long)values[i]);
  }
  fputc('\n', stderr);
  // The E2SM-RC CallProcessID encoder shipped in this FlexRIC build is not
  // implemented for the active ASN/RC combination and returns an invalid
  // buffer.  GreenRAN already carries the authoritative transaction in the
  // RC parameters (PARAM_TRANSACTION); leave the optional E2 CallProcessID
  // unset so the request reaches the RIC/agent instead of crashing the xApp.
  (void)transaction;
  control.proc_id = NULL;
  sm_ans_xapp_t answer = control_sm_xapp_api(node, SM_RC_ID, &control);
  fprintf(stderr, "[TA-SAM actuator] RC control tx end success=%d\n",
          answer.success ? 1 : 0);
  fflush(stderr);
  free_rc_ctrl_req_data(&control);
  return answer;
}

static bool send_drain_handover(global_e2_node_id_t* source,
                                uint64_t imsi,
                                int64_t target_cell_id,
                                uint64_t transaction)
{
  if (source == NULL || imsi == 0 || target_cell_id < 2 || target_cell_id > 4)
    return false;
  uint32_t ids[] = {PARAM_CELL_ID, PARAM_TRANSACTION};
  int64_t values[] = {target_cell_id, (int64_t)transaction};
  return send_integer_control(source, imsi, STYLE_MOBILITY, ACTION_HANDOVER,
                              transaction, ids, values, 2).success;
}

static const char* find_key(const char* start, const char* end, const char* key)
{
  const char* found = strstr(start, key);
  return found != NULL && (end == NULL || found < end) ? found : NULL;
}

static bool parse_integer(const char* start, const char* end, const char* key, int64_t* value)
{
  const char* found = find_key(start, end, key);
  if (found == NULL) return false;
  const char* colon = strchr(found, ':');
  if (colon == NULL || (end != NULL && colon >= end)) return false;
  char* tail = NULL;
  errno = 0;
  long long parsed = strtoll(colon + 1, &tail, 10);
  if (errno != 0 || tail == colon + 1 || (end != NULL && tail > end)) return false;
  *value = (int64_t)parsed;
  return true;
}

static bool parse_string(const char* input, const char* key, char* output, size_t output_size)
{
  const char* found = strstr(input, key);
  if (found == NULL) return false;
  const char* colon = strchr(found, ':');
  const char* first = colon == NULL ? NULL : strchr(colon, '"');
  const char* last = first == NULL ? NULL : strchr(first + 1, '"');
  if (first == NULL || last == NULL || (size_t)(last - first) >= output_size) return false;
  size_t size = (size_t)(last - first - 1);
  memcpy(output, first + 1, size);
  output[size] = '\0';
  return true;
}

static int64_t logical_cell_from_node(const global_e2_node_id_t* node)
{
  if (node == NULL) return -1;
  uint32_t raw = node->nb_id.nb_id;
  for (int cell = 2; cell <= 4; ++cell) {
    uint32_t manifest_id = native_node_id_by_cell[cell];
    if (manifest_id != 0 &&
        (raw == manifest_id || ((raw >> 24) & 0xffU) == manifest_id))
      return cell;
  }
  /* FlexRIC encodes the logical node digit as an ASCII byte in nb_id. */
  uint32_t high_byte = (raw >> 24) & 0xffU;
  if (high_byte >= (uint32_t)'2' && high_byte <= (uint32_t)'4')
    return (int64_t)(high_byte - (uint32_t)'0');
  if (raw >= 2 && raw <= 4) return (int64_t)raw;
  return -1;
}

static global_e2_node_id_t* node_for_cell(e2_node_arr_xapp_t* nodes, int64_t cell_id)
{
  for (size_t i = 0; i < nodes->len; ++i) {
    if (logical_cell_from_node(&nodes->n[i].id) == cell_id) return &nodes->n[i].id;
  }
  // Never infer a DU from registration order.  A missing identity is a
  // fail-closed topology error and must be handled by the safety bundle.
  return NULL;
}

static bool send_failsafe(e2_node_arr_xapp_t* nodes, uint64_t transaction)
{
  bool ok = true;
  unsigned managed_nodes = 0;
  unsigned managed_mask = 0;
  for (size_t i = 0; i < nodes->len; ++i) {
    const int64_t logical_cell_id = logical_cell_from_node(&nodes->n[i].id);
    /* The MC registration snapshot may also contain an LTE/control node.
     * It is not a managed DU and must not make the three-DU safety bundle
     * fail. Unknown or duplicate managed identities remain fail-closed. */
    if (logical_cell_id < 2 || logical_cell_id > 4) continue;
    unsigned bit = 1U << (unsigned)(logical_cell_id - 2);
    if ((managed_mask & bit) != 0) return false;
    managed_mask |= bit;
    ++managed_nodes;
    uint32_t clear_ids[] = {PARAM_TRANSACTION};
    int64_t clear_values[] = {(int64_t)transaction};
    ok &= send_integer_control(&nodes->n[i].id, 1, STYLE_SCHEDULER, ACTION_CLEAR,
                               transaction,
                               clear_ids, clear_values, 1).success;
    uint32_t power_ids[] = {PARAM_CELL_ID, PARAM_POWER_PERCENT, PARAM_TRANSACTION, PARAM_TTL_MS};
    int64_t power_values[] = {logical_cell_id, 100,
                              (int64_t)transaction, 5000};
    ok &= send_integer_control(&nodes->n[i].id, 1, STYLE_ENERGY, ACTION_SET_POWER,
                               transaction,
                               power_ids, power_values, 4).success;
  }
  return ok && managed_nodes == 3 && managed_mask == 0x7U;
}

/* The sleep transition stays inside the same signed/validated v4 bundle as
 * power and scheduler policy.  Handovers are sent only once per sleep ID:
 * replaying a drain after the source is empty would otherwise turn a healthy
 * idempotent state into a spurious E2 rejection. */
static bool apply_drain_handovers(const char* request,
                                  e2_node_arr_xapp_t* nodes,
                                  uint64_t transaction,
                                  const char* sleep_transaction_id,
                                  int64_t source_cell_id,
                                  uint64_t* transaction_count)
{
  if (sleep_transaction_id == NULL || sleep_transaction_id[0] == '\0' ||
      source_cell_id < 2 || source_cell_id > 4) return false;
  if (strcmp(last_drain_sleep_transaction, sleep_transaction_id) == 0) return true;
  global_e2_node_id_t* source = node_for_cell(nodes, source_cell_id);
  if (source == NULL) return false;
  const char* plan = strstr(request, "\"handover_plan\"");
  if (plan == NULL) return false;
  bool sent = false;
  const char* item = find_key(plan, NULL, "\"imsi\"");
  while (item != NULL) {
    const char* target = find_key(item, NULL, "\"target_cell_id\"");
    int64_t imsi = 0;
    int64_t target_cell_id = 0;
    if (target == NULL || !parse_integer(item, NULL, "\"imsi\"", &imsi) ||
        !parse_integer(target, NULL, "\"target_cell_id\"", &target_cell_id) ||
        imsi < 1 || imsi > 20 || target_cell_id < 2 || target_cell_id > 4 ||
        target_cell_id == source_cell_id) return false;
    if (!send_drain_handover(source, (uint64_t)imsi, target_cell_id, transaction)) return false;
    if (transaction_count != NULL) ++*transaction_count;
    sent = true;
    item = find_key(target + 1, NULL, "\"imsi\"");
  }
  if (!sent) return false;
  snprintf(last_drain_sleep_transaction, sizeof(last_drain_sleep_transaction), "%s",
           sleep_transaction_id);
  return true;
}

static bool apply_bundle(const char* request, e2_node_arr_xapp_t* nodes,
                         uint64_t* transaction_count,
                         char* cell_results, size_t cell_results_size)
{
  if (cell_results != NULL && cell_results_size > 0) {
    snprintf(cell_results, cell_results_size, "[]");
  }
  const bool schema_v2 =
    strstr(request, "\"schema\":\"" CONTROL_SCHEMA "\"") != NULL ||
    strstr(request, "\"schema\": \"" CONTROL_SCHEMA "\"") != NULL;
  const bool schema_v3 =
    strstr(request, "\"schema\":\"" CONTROL_SCHEMA_V3 "\"") != NULL ||
    strstr(request, "\"schema\": \"" CONTROL_SCHEMA_V3 "\"") != NULL;
  const bool schema_v4 =
    strstr(request, "\"schema\":\"" CONTROL_SCHEMA_V4 "\"") != NULL ||
    strstr(request, "\"schema\": \"" CONTROL_SCHEMA_V4 "\"") != NULL;
  const bool bootstrap =
    strstr(request, "\"bootstrap\":true") != NULL ||
    strstr(request, "\"bootstrap\": true") != NULL;
  if (!schema_v2 && !schema_v3 && !schema_v4) return false;
  int64_t sequence = 0;
  int64_t ttl_ms = 0;
  if (!parse_integer(request, NULL, "\"sequence\"", &sequence) || sequence <= 0 ||
      !parse_integer(request, NULL, "\"ttl_ms\"", &ttl_ms) || ttl_ms < 100 || ttl_ms > 60000)
    return false;

  if (strstr(request, "\"mode\":\"failsafe\"") != NULL ||
      strstr(request, "\"mode\": \"failsafe\"") != NULL) {
    *transaction_count += nodes->len * 2;
    return send_failsafe(nodes, (uint64_t)sequence);
  }

  bool all_ok = true;
  if (!local_e2_nodes_ready(nodes, 3)) return false;
  char sleep_phase[16] = "";
  char sleep_transaction_id[128] = "";
  int64_t sleep_source_cell = 0;
  const bool has_sleep_transition = schema_v4 &&
      parse_string(request, "\"phase\"", sleep_phase, sizeof(sleep_phase)) &&
      parse_string(request, "\"sleep_transaction_id\"", sleep_transaction_id,
                   sizeof(sleep_transaction_id)) &&
      parse_integer(request, NULL, "\"source_cell_id\"", &sleep_source_cell);
  if (schema_v4 && strstr(request, "\"sleep_transition\"") != NULL &&
      !has_sleep_transition) return false;
  if (has_sleep_transition && strcmp(sleep_phase, "drain") == 0) {
    all_ok &= apply_drain_handovers(request, nodes, (uint64_t)sequence,
                                    sleep_transaction_id, sleep_source_cell,
                                    transaction_count);
  }
  size_t cell_count = 0;
  unsigned cell_mask = 0;
  size_t cell_results_used = 1;
  if (cell_results != NULL && cell_results_size > 0) {
    cell_results[0] = '[';
    cell_results[1] = '\0';
  }
  const char* cell = strstr(request, "\"cell_id\"");
  while (cell != NULL) {
    const char* next_cell = strstr(cell + 9, "\"cell_id\"");
    int64_t cell_id = 0;
    int64_t power = 0;
    int64_t apply_power = 1;
    int64_t discretionary_bp = 10000;
    if (!parse_integer(cell, next_cell, "\"cell_id\"", &cell_id) ||
        !parse_integer(cell, next_cell, "\"tx_power_percent\"", &power) ||
        (parse_integer(cell, next_cell, "\"apply_power\"", &apply_power) &&
         (apply_power != 0 && apply_power != 1)) ||
        (schema_v4 && !parse_integer(cell, next_cell,
                                     "\"max_discretionary_dl_symbols_bp\"",
                                     &discretionary_bp)) ||
        discretionary_bp < 0 || discretionary_bp > 10000 ||
        power < 0 || power > 100 || (power != 0 && (power < 25 || power % 5 != 0))) return false;
    const bool sleep_commit_source = has_sleep_transition &&
        strcmp(sleep_phase, "commit") == 0 && cell_id == sleep_source_cell && power == 0;
    if (power == 0 && !sleep_commit_source) return false;
    global_e2_node_id_t* node = node_for_cell(nodes, cell_id);
    if (node == NULL) return false;

    size_t ue_count = 0;
    const char* ue = find_key(cell, next_cell, "\"imsi\"");
    while (ue != NULL) {
      const char* next_ue = find_key(ue + 6, next_cell, "\"imsi\"");
      int64_t imsi = 0, dl = 0, ul = 0, weight = 0;
      if (!parse_integer(ue, next_ue, "\"imsi\"", &imsi) || imsi < 1 || imsi > 20 ||
          !parse_integer(ue, next_ue, "\"min_dl_share_bp\"", &dl) || dl < 0 || dl > 10000 ||
          !parse_integer(ue, next_ue, "\"min_ul_share_bp\"", &ul) || ul < 0 || ul > 10000 ||
          !parse_integer(ue, next_ue, "\"surplus_weight_bp\"", &weight) || weight < 1 || weight > 10000)
        return false;
      uint32_t ids[] = {PARAM_MIN_DL_BP, PARAM_MIN_UL_BP, PARAM_WEIGHT_BP, PARAM_TRANSACTION};
      int64_t values[] = {dl, ul, weight, sequence};
      all_ok &= send_integer_control(node, (uint64_t)imsi, STYLE_SCHEDULER,
                                     ACTION_PREPARE, (uint64_t)sequence,
                                     ids, values, 4).success;
      ++ue_count;
      ++*transaction_count;
      ue = next_ue;
    }
    bool scheduler_ok = false;
    if (ue_count == 0) {
      // The first TA-SAM bootstrap intentionally preserves the checkpoint's
      // scheduler budget while establishing the 25% power state.  It is a
      // power-only transaction: do not clear scheduler state and do not
      // reject the bundle merely because native association is not mature.
      if (bootstrap && !sleep_commit_source) {
        scheduler_ok = true;
      } else if (!sleep_commit_source) {
        return false;
      }
      if (sleep_commit_source) {
      uint32_t clear_ids[] = {PARAM_TRANSACTION};
      int64_t clear_values[] = {sequence};
      scheduler_ok = send_integer_control(node, 1, STYLE_SCHEDULER, ACTION_CLEAR,
                                          (uint64_t)sequence,
                                          clear_ids, clear_values, 1).success;
      }
    } else {
      uint32_t commit_ids[] = {PARAM_TRANSACTION, PARAM_EXPECTED_UES, PARAM_TTL_MS,
                               PARAM_MAX_DISCRETIONARY_DL_SYMBOLS_BP};
      int64_t commit_values[] = {sequence, (int64_t)ue_count, ttl_ms, discretionary_bp};
      scheduler_ok = send_integer_control(node, 1, STYLE_SCHEDULER, ACTION_COMMIT,
                                          (uint64_t)sequence,
                                          commit_ids, commit_values, 4).success;
    }
    all_ok &= scheduler_ok;
    bool power_ok = true;
    if (apply_power == 1) {
      uint32_t power_ids[] = {PARAM_CELL_ID, PARAM_POWER_PERCENT, PARAM_TRANSACTION, PARAM_TTL_MS,
                              PARAM_SLEEP_COMMIT};
      int64_t power_values[] = {cell_id, power, sequence, ttl_ms, sleep_commit_source ? 1 : 0};
      power_ok = send_integer_control(node, 1, STYLE_ENERGY, ACTION_SET_POWER,
                                      (uint64_t)sequence,
                                      power_ids, power_values, 5).success;
    }
    all_ok &= power_ok;
    if (cell_results != NULL && cell_results_size > cell_results_used + 1) {
      int written = snprintf(
          cell_results + cell_results_used,
          cell_results_size - cell_results_used,
          "%s{\"cell_id\":%lld,\"scheduler_ack\":%s,\"power_ack\":%s,\"power_sent\":%s}",
          cell_results_used > 1 ? "," : "", (long long)cell_id,
          scheduler_ok ? "true" : "false", power_ok ? "true" : "false",
          apply_power == 1 ? "true" : "false");
      if (written > 0 && (size_t)written < cell_results_size - cell_results_used) {
        cell_results_used += (size_t)written;
      }
    }
    if (cell_id >= 2 && cell_id <= 4) cell_mask |= 1U << (unsigned)(cell_id - 2);
    *transaction_count += 1 + (apply_power == 1 ? 1 : 0);
    ++cell_count;
    cell = next_cell;
  }
  if (cell_results != NULL && cell_results_size > cell_results_used + 1) {
    cell_results[cell_results_used++] = ']';
    cell_results[cell_results_used] = '\0';
  }
  // A V3 native control sequence is atomic across the managed topology.  A
  // partial bundle is not an ACK for the campaign, even if one DU accepted it.
  const bool complete_cells = cell_count == 3 && cell_mask == 0x7U;
  if (!all_ok || !complete_cells) send_failsafe(nodes, (uint64_t)sequence);
  return all_ok && complete_cells;
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
  fr_args_t args = init_fr_args(argc, argv);
  init_xapp_api(&args);
  const char* status_path = getenv("GREENRAN_TASAM_ACTUATOR_STATUS_PATH");
  size_t expected_nodes = expected_node_count();
  e2_node_arr_xapp_t nodes = {0};
  const time_t deadline = time(NULL) + 120;
  const char* configured_socket = getenv("GREENRAN_TASAM_CONTROL_SOCKET_PATH");
  write_status(status_path, "waiting_for_e2_nodes", "startup", NULL, configured_socket);
  // The snapshot is frozen for the process lifetime (see the socket-handler
  // note below), so it must already cover cells 2, 3 and 4 before serving
  // any bundle.  Waiting for the mapped cells - not just a raw count -
  // avoids racing partial registrations and torn cache entries.
  while (running && time(NULL) <= deadline) {
    if (native_node_manifest_ready() &&
        refresh_e2_nodes(&nodes, expected_nodes) &&
        snapshot_covers_managed_cells(&nodes)) break;
    sleep(1);
  }
  if (!running || !snapshot_covers_managed_cells(&nodes)) {
    fprintf(stderr, "TA-SAM actuator: expected %lu E2 nodes, found %lu\n",
            (unsigned long)expected_nodes, (unsigned long)nodes.len);
    write_status(status_path, "failed", native_node_manifest_ready()
                 ? "e2_nodes_timeout" : "native_node_manifest_timeout",
                 &nodes, configured_socket);
    free_e2_node_arr_xapp(&nodes);
    try_stop_xapp_api();
    return EXIT_FAILURE;
  }
  write_status(status_path, "ready", "e2_nodes_registered", &nodes, configured_socket);

  const char* socket_path = configured_socket;
  if (socket_path == NULL || socket_path[0] == '\0') socket_path = DEFAULT_SOCKET;
  char parent[sizeof(((struct sockaddr_un*)0)->sun_path)] = {0};
  snprintf(parent, sizeof(parent), "%s", socket_path);
  char* slash = strrchr(parent, '/');
  if (slash != NULL) {
    *slash = '\0';
    if (!ensure_directory(parent)) return EXIT_FAILURE;
  }
  unlink(socket_path);
  int server = socket(AF_UNIX, SOCK_STREAM, 0);
  if (server < 0) return EXIT_FAILURE;
  struct sockaddr_un address = {0};
  address.sun_family = AF_UNIX;
  snprintf(address.sun_path, sizeof(address.sun_path), "%s", socket_path);
  if (bind(server, (struct sockaddr*)&address, sizeof(address)) != 0 || listen(server, 4) != 0)
    return EXIT_FAILURE;
  printf("TA-SAM actuator ready on %s\n", socket_path);
  fflush(stdout);

  while (running) {
    int client = accept(server, NULL, NULL);
    if (client < 0) {
      if (errno == EINTR) continue;
      break;
    }
    fprintf(stderr, "[TA-SAM actuator] client accepted\n");
    char* request = calloc(1, MAX_REQUEST);
    ssize_t size = recv(client, request, MAX_REQUEST - 1, 0);
    fprintf(stderr, "[TA-SAM actuator] request received bytes=%ld\n", (long)size);
    uint64_t transactions = 0;
    bool fallback = size > 0 &&
      (strstr(request, "\"mode\":\"failsafe\"") != NULL ||
       strstr(request, "\"mode\": \"failsafe\"") != NULL);
    // Do not rebuild/free the SDK's node snapshot from the socket handler.
    // The E2 SDK mutates its registry from its dispatch thread; rebuilding the
    // deep copy concurrently caused a use-after-free before the first RC
    // request.  Startup registration plus the synchronous RC result remains
    // the safe readiness boundary for each bundle.
    bool nodes_ready = local_e2_nodes_ready(&nodes, expected_nodes);
    fprintf(stderr, "[TA-SAM actuator] bundle apply begin nodes_ready=%d fallback=%d\n",
            nodes_ready ? 1 : 0, fallback ? 1 : 0);
    char cell_results[768] = "[]";
    bool applied = nodes_ready && size > 0 && apply_bundle(
        request, &nodes, &transactions, cell_results, sizeof(cell_results));
    fprintf(stderr, "[TA-SAM actuator] bundle apply end applied=%d transactions=%lu\n",
            applied ? 1 : 0, (unsigned long)transactions);
    char policy_id[128] = "unknown";
    int64_t sequence = -1;
    parse_string(request, "\"policy_id\"", policy_id, sizeof(policy_id));
    parse_integer(request, NULL, "\"sequence\"", &sequence);
    char response[1536];
    snprintf(response, sizeof(response),
             "{\"schema\":\"%s\",\"policy_id\":\"%s\",\"sequence\":%ld,"
             "\"ack\":%s,\"applied\":%s,\"e2_transactions\":%lu,"
             "\"observed_confirmations\":%lu,\"fallback\":%s,"
             "\"nodes_ready\":%s,\"expected_nodes\":%lu,\"cell_results\":%s}",
             ACK_SCHEMA, policy_id, (long)sequence, applied ? "true" : "false",
             applied ? "true" : "false", (unsigned long)transactions,
             applied ? (unsigned long)transactions : 0UL,
             fallback ? "true" : "false", nodes_ready ? "true" : "false",
             (unsigned long)expected_nodes, cell_results);
    send(client, response, strlen(response), 0);
    free(request);
    close(client);
  }
  send_failsafe(&nodes, UINT64_MAX - 1);
  write_status(status_path, "stopped", "signal", &nodes, socket_path);
  close(server);
  unlink(socket_path);
  free_e2_node_arr_xapp(&nodes);
  while (!try_stop_xapp_api()) usleep(1000);
  return EXIT_SUCCESS;
}
