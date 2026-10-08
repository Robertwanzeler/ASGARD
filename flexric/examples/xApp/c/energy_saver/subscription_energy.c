#include "subscription_energy.h"
#include "../../../../src/util/time_now_us.h"
#include "../../../../src/util/alg_ds/ds/lock_guard/lock_guard.h"
#include "../../../../src/util/alg_ds/alg/defer.h"
#include "../../../../src/util/e.h"
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <string.h>
#include <stdbool.h>
#include <pthread.h>
#include <stdint.h>

static ue_kpm_state_t g_ue_kpm[MAX_UE_KPM_STATE];
static pthread_mutex_t g_ue_kpm_mtx = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t mtx;
static int g_camera_idx = 0;
static bool g_send_control = false;

#define THRESHOLD_LOW_TRAFFIC_KBPS 5000
#define THRESHOLD_HIGH_LATENCY_US  80000  // 80ms (GREENRAN: ajustado)

static uint64_t now_ms_kpm(void)
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return (uint64_t)ts.tv_sec * 1000ULL + (uint64_t)(ts.tv_nsec / 1000000ULL);
}

static ue_kpm_state_t* get_or_create_ue_state(uint64_t ue_id)
{
  ue_kpm_state_t* free_slot = NULL;

  for (int i = 0; i < MAX_UE_KPM_STATE; ++i) {
    if (g_ue_kpm[i].ue_id == ue_id)
      return &g_ue_kpm[i];
    if (g_ue_kpm[i].ue_id == 0 && free_slot == NULL)
      free_slot = &g_ue_kpm[i];
  }

  if (free_slot != NULL) {
    free_slot->ue_id = ue_id;
    free_slot->last_seen = now_ms_kpm();
    return free_slot;
  }

  return NULL;
}

static bool ue_metrics_ready(const ue_kpm_state_t* s)
{
  return s != NULL &&
         s->has_volume &&
         s->has_pkts &&
         s->has_bitrate &&
         s->has_delay;
}

static void reset_ue_metric_flags(ue_kpm_state_t* s)
{
  if (s == NULL) return;
  s->has_volume = false;
  s->has_pkts = false;
  s->has_bitrate = false;
  s->has_delay = false;
}

static void upsert_ue_metric_i(uint64_t ue_id, const char* meas_name, long value)
{
  if (meas_name == NULL || ue_id == 0)
    return;

  pthread_mutex_lock(&g_ue_kpm_mtx);

  ue_kpm_state_t* s = get_or_create_ue_state(ue_id);
  if (s == NULL) {
    pthread_mutex_unlock(&g_ue_kpm_mtx);
    return;
  }

  s->last_seen = now_ms_kpm();

  if (strstr(meas_name, "DRB.PdcpSduVolumeDl") != NULL) {
    s->volume_dl = value;
    s->has_volume = true;
  } else if (strstr(meas_name, "Tot.PdcpSduNbrDl") != NULL) {
    s->pacotes_dl = value;
    s->has_pkts = true;
  } else if (strstr(meas_name, "DRB.PdcpSduBitRateDl") != NULL) {
    s->bitrate_dl_kbps = value;
    s->has_bitrate = true;
  } else if (strstr(meas_name, "DRB.PdcpSduDelayDl") != NULL) {
    s->delay_dl_us = value;
    s->has_delay = true;
  }

  pthread_mutex_unlock(&g_ue_kpm_mtx);
}

bool check_energy_saving_energy(long bitrate_dl_kbps, long delay_dl_us)
{
  if (delay_dl_us >= THRESHOLD_HIGH_LATENCY_US) {
    printf("\n");
    printf("========================================\n");
    printf("[XAPP-ENERGY] *** INTERVENCAO ***\n");
    printf("[XAPP-ENERGY] diagnostico = LATENCIA_ALTA\n");
    printf("[XAPP-ENERGY] Latencia: %ld us > %d us (SLA)\n", delay_dl_us, THRESHOLD_HIGH_LATENCY_US);
    printf("[XAPP-ENERGY] Acao = BLOQUEAR ENERGY SAVER\n");
    printf("[XAPP-ENERGY] Motivo: Camera/UE em estado critico\n");
    printf("[XAPP-ENERGY] Prioridade: MANTER CELULA ATIVA\n");
    printf("========================================\n");
    printf("\n");
    return false;  // Nao faca energy saving
  }

  if (bitrate_dl_kbps < THRESHOLD_LOW_TRAFFIC_KBPS && delay_dl_us < THRESHOLD_HIGH_LATENCY_US) {
    printf("\n");
    printf("========================================\n");
    printf("[XAPP-ENERGY] *** INTERVENCAO ***\n");
    printf("[XAPP-ENERGY] diagnostico = BAIXA_CARGA_POSSIVEL_POWEROFF\n");
    printf("[XAPP-ENERGY] Bitrate: %ld kbps < %d kbps\n", bitrate_dl_kbps, THRESHOLD_LOW_TRAFFIC_KBPS);
    printf("[XAPP-ENERGY] Latencia: %ld us < %d us (OK)\n", delay_dl_us, THRESHOLD_HIGH_LATENCY_US);
    printf("[XAPP-ENERGY] Acao = PERMITIR ENERGY SAVER\n");
    printf("[XAPP-ENERGY] Motivo: Baixo trafego, economia de energia possivel\n");
    printf("========================================\n");
    printf("\n");
    return true;
  }

  printf("\n");
  printf("[XAPP-ENERGY] diagnostico = CARGA_NORMAL\n");
  printf("[XAPP-ENERGY] Latencia: %ld us < %d us (OK)\n", delay_dl_us, THRESHOLD_HIGH_LATENCY_US);
  printf("[XAPP-ENERGY] Acao = MANTER_NORMAL\n");
  printf("\n");
  return false;
}

static
void log_gnb_ue_id(ue_id_e2sm_t ue_id)
{
  if (ue_id.gnb.gnb_cu_ue_f1ap_lst != NULL) {
    for (size_t i = 0; i < ue_id.gnb.gnb_cu_ue_f1ap_lst_len; i++) {
      printf("[XAPP-ENERGY] UE ID type = gNB-CU, gnb_cu_ue_f1ap = %u\n", ue_id.gnb.gnb_cu_ue_f1ap_lst[i]);
    }
  } else {
    printf("[XAPP-ENERGY] UE ID type = gNB, amf_ue_ngap_id = %lu\n", ue_id.gnb.amf_ue_ngap_id);
  }
  if (ue_id.gnb.ran_ue_id != NULL) {
    printf("[XAPP-ENERGY] ran_ue_id = %lx\n", *ue_id.gnb.ran_ue_id);
  }
}

static
void log_du_ue_id(ue_id_e2sm_t ue_id)
{
  printf("[XAPP-ENERGY] UE ID type = gNB-DU, gnb_cu_ue_f1ap = %u\n", ue_id.gnb_du.gnb_cu_ue_f1ap);
  if (ue_id.gnb_du.ran_ue_id != NULL) {
    printf("[XAPP-ENERGY] ran_ue_id = %lx\n", *ue_id.gnb_du.ran_ue_id);
  }
}

static
void log_cuup_ue_id(ue_id_e2sm_t ue_id)
{
  printf("[XAPP-ENERGY] UE ID type = gNB-CU-UP, gnb_cu_cp_ue_e1ap = %u\n", ue_id.gnb_cu_up.gnb_cu_cp_ue_e1ap);
  if (ue_id.gnb_cu_up.ran_ue_id != NULL) {
    printf("[XAPP-ENERGY] ran_ue_id = %lx\n", *ue_id.gnb_cu_up.ran_ue_id);
  }
}

typedef void (*log_ue_id)(ue_id_e2sm_t ue_id);

static
log_ue_id log_ue_id_e2sm[END_UE_ID_E2SM] = {
    log_gnb_ue_id,
    log_du_ue_id,
    log_cuup_ue_id,
    NULL,
    NULL,
    NULL,
    NULL,
};

static
void log_kpm_measurements_energy(kpm_ind_msg_format_1_t const* msg_frm_1, ue_id_e2sm_t const* ue_id)
{
  assert(msg_frm_1->meas_info_lst_len > 0 && "Cannot correctly print measurements");

  // Track unique UEs by their amf_ue_ngap_id - persistent across all messages
  // This tracks ALL unique UEs seen during the entire simulation
  static uint64_t all_seen_ues[100] = {0};
  static int all_seen_count = 0;
  
  // Track which UEs we've already displayed (to avoid duplicates in output)
  static uint64_t displayed_ues[100] = {0};
  static int displayed_count = 0;
  
  // Get UE identifier
  uint64_t ue_identifier = 0;
  if (ue_id != NULL && ue_id->type == GNB_UE_ID_E2SM) {
    ue_identifier = ue_id->gnb.amf_ue_ngap_id;
  }
  
  if (ue_identifier == 0) {
    return; // Skip invalid UE IDs
  }
  
  // Check if we've already displayed this UE
  bool already_displayed = false;
  int ue_index = -1;
  for (int i = 0; i < displayed_count && i < 100; i++) {
    if (displayed_ues[i] == ue_identifier) {
      already_displayed = true;
      ue_index = i;
      break;
    }
  }
  
  // If not displayed yet and we have room, add it
  if (!already_displayed && displayed_count < 100 && ue_identifier > 0) {
    displayed_ues[displayed_count] = ue_identifier;
    ue_index = displayed_count;
    displayed_count++;
  } else if (!already_displayed && displayed_count >= 100) {
    // Too many UEs, skip
    return;
  } else if (already_displayed && ue_index < 0) {
    // Already displayed but index not found, skip
    return;
  }
  
  // Debug: print the index
  printf("[XAPP-ENERGY] DEBUG: ue_id=%lu, displayed_count=%d, ue_index=%d\n", 
         ue_identifier, displayed_count, ue_index);
  
  // If already displayed, we need to determine device type from stored index
  // Determine device type and number based on index in displayed list
  const char* device_type;
  int device_num;
  
  if (ue_index >= 0 && ue_index < 3) {
    // First 3 UEs are cameras
    device_type = "CAMERA";
    device_num = ue_index + 1;
  } else if (ue_index >= 3 && ue_index < 13) {
    // UEs 4-13 are regular UEs (10 total)
    device_type = "UE";
    device_num = ue_index - 2;  // UE1 = index 3, so 3-2=1
  } else if (ue_index >= 13) {
    // More than 13 devices - skip display
    return;
  } else {
    // Invalid index - skip
    return;
  }

  bool saw_interesting_metric = false;
  bool has_volume = false;
  bool has_pkts = false;
  bool has_bitrate = false;
  bool has_delay = false;

  long agg_volume = -1;
  long agg_pkts = -1;
  double agg_bitrate = -1.0;
  double agg_delay = -1.0;

  size_t lim = msg_frm_1->meas_data_lst_len < msg_frm_1->meas_info_lst_len ?
               msg_frm_1->meas_data_lst_len : msg_frm_1->meas_info_lst_len;

  for (size_t j = 0; j < lim; j++) {
    meas_data_lst_t const data_item = msg_frm_1->meas_data_lst[j];
    meas_type_t const meas_type = msg_frm_1->meas_info_lst[j].meas_type;

    if (meas_type.type != NAME_MEAS_TYPE)
      continue;

    if (data_item.meas_record_len == 0)
      continue;

    meas_record_lst_t const record_item = data_item.meas_record_lst[0];

    char meas_name[256] = {0};
    size_t n = meas_type.name.len < sizeof(meas_name) - 1 ? meas_type.name.len : sizeof(meas_name) - 1;
    memcpy(meas_name, meas_type.name.buf, n);
    meas_name[n] = '\0';

    if (strstr(meas_name, "PdcpSduVolumeDl") != NULL) {
      if (record_item.value == INTEGER_MEAS_VALUE)
        agg_volume = (long)record_item.int_val;
      else if (record_item.value == REAL_MEAS_VALUE)
        agg_volume = (long)record_item.real_val;

      has_volume = true;
      saw_interesting_metric = true;

    } else if (strstr(meas_name, "PdcpSduNbrDl") != NULL) {
      if (record_item.value == INTEGER_MEAS_VALUE)
        agg_pkts = (long)record_item.int_val;
      else if (record_item.value == REAL_MEAS_VALUE)
        agg_pkts = (long)record_item.real_val;

      has_pkts = true;
      saw_interesting_metric = true;

    } else if (strstr(meas_name, "PdcpSduBitRateDl") != NULL) {
      if (record_item.value == REAL_MEAS_VALUE)
        agg_bitrate = (double)record_item.real_val;
      else if (record_item.value == INTEGER_MEAS_VALUE)
        agg_bitrate = (double)record_item.int_val;

      has_bitrate = true;
      saw_interesting_metric = true;

    } else if (strstr(meas_name, "PdcpSduDelayDl") != NULL) {
      if (record_item.value == REAL_MEAS_VALUE)
        agg_delay = (double)record_item.real_val;
      else if (record_item.value == INTEGER_MEAS_VALUE)
        agg_delay = (double)record_item.int_val;

      has_delay = true;
      saw_interesting_metric = true;
    }
  }

  if (!saw_interesting_metric) {
    printf("[XAPP-ENERGY] %s %d -> conjunto KPM de interesse nao encontrado\n", device_type, device_num);
    return;
  }

  if (!(has_volume && has_pkts && has_bitrate && has_delay)) {
    printf("[XAPP-ENERGY] %s %d -> KPM_INCOMPLETO\n", device_type, device_num);
    return;
  }

  double pdcpBitrateDl = agg_bitrate;
  double pdcpDelayDl = agg_delay;
  int pdcpVolumeDl = (int)agg_volume;
  int pdcpNbrDl = (int)agg_pkts;

  printf("[XAPP-ENERGY] ===== %s %d =====\n", device_type, device_num);
  printf("[XAPP-ENERGY] volume_dl = %d\n", pdcpVolumeDl);
  printf("[XAPP-ENERGY] pacotes_dl = %d\n", pdcpNbrDl);
  printf("[XAPP-ENERGY] bitrate_dl_kbps = %.2f\n", pdcpBitrateDl);
  printf("[XAPP-ENERGY] delay_dl_us = %.2f\n", pdcpDelayDl);

  if (pdcpDelayDl >= 3000.0) {
    printf("[XAPP-ENERGY] problema_principal = LATENCIA_ALTA\n");
  } else if (pdcpNbrDl >= 0 && pdcpNbrDl <= 5) {
    printf("[XAPP-ENERGY] problema_principal = FALTA_DE_PACOTES\n");
  } else if (pdcpBitrateDl > 0.0 && pdcpBitrateDl < 1000.0) {
    printf("[XAPP-ENERGY] problema_principal = BITRATE_BAIXO\n");
  } else {
    printf("[XAPP-ENERGY] problema_principal = NENHUM\n");
  }

  g_send_control = check_energy_saving_energy(pdcpBitrateDl, pdcpDelayDl);
}

void sm_cb_kpm_energy(sm_ag_if_rd_t const* rd)
{
  assert(rd != NULL);
  assert(rd->type == INDICATION_MSG_AGENT_IF_ANS_V0);
  assert(rd->ind.type == KPM_STATS_V3_0);

  kpm_ind_data_t const* ind = &rd->ind.kpm.ind;
  kpm_ric_ind_hdr_format_1_t const* hdr_frm_1 = &ind->hdr.kpm_ric_ind_hdr_format_1;
  kpm_ind_msg_format_3_t const* msg_frm_3 = &ind->msg.frm_3;

  int64_t const now = time_now_us();
  static int counter = 1;
  {
    lock_guard(&mtx);
    printf("[XAPP-ENERGY] \n%7d KPM ind_msg latency = %ld [us]\n", counter, now - hdr_frm_1->collectStartTime);

    for (size_t i = 0; i < msg_frm_3->ue_meas_report_lst_len; i++) {
      ue_id_e2sm_t const ue_id_e2sm = msg_frm_3->meas_report_per_ue[i].ue_meas_report_lst;
      ue_id_e2sm_e const type = ue_id_e2sm.type;
      log_ue_id_e2sm[type](ue_id_e2sm);

      log_kpm_measurements_energy(&msg_frm_3->meas_report_per_ue[i].ind_msg_format_1, &ue_id_e2sm);
    }
    counter++;
  }
}

void subscription_energy_init(void)
{
  pthread_mutexattr_t attr = {0};
  int rc = pthread_mutex_init(&mtx, &attr);
  assert(rc == 0);
}

static
label_info_lst_t fill_kpm_label(void)
{
  label_info_lst_t label_item = {0};

  label_item.noLabel = ecalloc(1, sizeof(enum_value_e));
  *label_item.noLabel = TRUE_ENUM_VALUE;

  return label_item;
}

static
test_info_lst_t filter_predicate(test_cond_type_e type, test_cond_e cond, int value)
{
  test_info_lst_t dst = {0};

  dst.test_cond_type = type;
  dst.S_NSSAI = TRUE_TEST_COND_TYPE;

  dst.test_cond = calloc(1, sizeof(test_cond_e));
  assert(dst.test_cond != NULL && "Memory exhausted");
  *dst.test_cond = cond;

  dst.test_cond_value = calloc(1, sizeof(test_cond_value_t));
  assert(dst.test_cond_value != NULL && "Memory exhausted");
  dst.test_cond_value->type = OCTET_STRING_TEST_COND_VALUE;

  dst.test_cond_value->octet_string_value = calloc(1, sizeof(byte_array_t));
  assert(dst.test_cond_value->octet_string_value != NULL && "Memory exhausted");
  const size_t len_nssai = 1;
  dst.test_cond_value->octet_string_value->len = len_nssai;
  dst.test_cond_value->octet_string_value->buf = calloc(len_nssai, sizeof(uint8_t));
  assert(dst.test_cond_value->octet_string_value->buf != NULL && "Memory exhausted");
  dst.test_cond_value->octet_string_value->buf[0] = value;

  return dst;
}

static
kpm_act_def_format_1_t fill_act_def_frm_1(ric_report_style_item_t const* report_item)
{
  assert(report_item != NULL);

  kpm_act_def_format_1_t ad_frm_1 = {0};

  size_t const sz = report_item->meas_info_for_action_lst_len;

  ad_frm_1.meas_info_lst_len = sz;
  ad_frm_1.meas_info_lst = calloc(sz, sizeof(meas_info_format_1_lst_t));
  assert(ad_frm_1.meas_info_lst != NULL && "Memory exhausted");

  for (size_t i = 0; i < sz; i++) {
    meas_info_format_1_lst_t* meas_item = &ad_frm_1.meas_info_lst[i];
    meas_item->meas_type.type = NAME_MEAS_TYPE;
    meas_item->meas_type.name = copy_byte_array(report_item->meas_info_for_action_lst[i].name);

    meas_item->label_info_lst_len = 1;
    meas_item->label_info_lst = ecalloc(1, sizeof(label_info_lst_t));
    meas_item->label_info_lst[0] = fill_kpm_label();
  }

  ad_frm_1.gran_period_ms = 100;
  ad_frm_1.cell_global_id = NULL;

#if defined KPM_V2_03 || defined KPM_V3_00
  ad_frm_1.meas_bin_range_info_lst_len = 0;
  ad_frm_1.meas_bin_info_lst = NULL;
#endif

  return ad_frm_1;
}

static
kpm_act_def_t fill_report_style_4(ric_report_style_item_t const* report_item)
{
  assert(report_item != NULL);
  assert(report_item->act_def_format_type == FORMAT_4_ACTION_DEFINITION);

  kpm_act_def_t act_def = {.type = FORMAT_4_ACTION_DEFINITION};

  act_def.frm_4.matching_cond_lst_len = 1;
  act_def.frm_4.matching_cond_lst = calloc(act_def.frm_4.matching_cond_lst_len, sizeof(matching_condition_format_4_lst_t));
  assert(act_def.frm_4.matching_cond_lst != NULL && "Memory exhausted");

  test_cond_type_e const type = S_NSSAI_TEST_COND_TYPE;
  test_cond_e const condition = EQUAL_TEST_COND;
  int const value = 1;
  act_def.frm_4.matching_cond_lst[0].test_info_lst = filter_predicate(type, condition, value);

  act_def.frm_4.action_def_format_1 = fill_act_def_frm_1(report_item);

  return act_def;
}

typedef kpm_act_def_t (*fill_kpm_act_def)(ric_report_style_item_t const* report_item);

static
fill_kpm_act_def get_kpm_act_def[END_RIC_SERVICE_REPORT] = {
    NULL,
    NULL,
    NULL,
    fill_report_style_4,
    NULL,
};

kpm_sub_data_t gen_kpm_subs_energy(kpm_ran_function_def_t const* ran_func)
{
  assert(ran_func != NULL);
  assert(ran_func->ric_event_trigger_style_list != NULL);

  kpm_sub_data_t kpm_sub = {0};

  assert(ran_func->ric_event_trigger_style_list[0].format_type == FORMAT_1_RIC_EVENT_TRIGGER);
  kpm_sub.ev_trg_def.type = FORMAT_1_RIC_EVENT_TRIGGER;
  kpm_sub.ev_trg_def.kpm_ric_event_trigger_format_1.report_period_ms = 100;

  kpm_sub.sz_ad = 1;
  kpm_sub.ad = calloc(kpm_sub.sz_ad, sizeof(kpm_act_def_t));
  assert(kpm_sub.ad != NULL && "Memory exhausted");

  ric_report_style_item_t* const report_item = &ran_func->ric_report_style_list[0];
  ric_service_report_e const report_style_type = report_item->report_style_type;
  *kpm_sub.ad = get_kpm_act_def[report_style_type](report_item);

  return kpm_sub;
}
