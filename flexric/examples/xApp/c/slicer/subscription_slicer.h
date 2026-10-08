#ifndef SUBSCRIPTION_SLICER_H
#define SUBSCRIPTION_SLICER_H

#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_struct.h"
#include "../../../../src/sm/rc_sm/ie/ir/ran_param_list.h"

#define MAX_UE_KPM_STATE 256

typedef struct {
  uint64_t ue_id;
  bool has_volume;
  bool has_pkts;
  bool has_bitrate;
  bool has_delay;
  long volume_dl;
  long pacotes_dl;
  long bitrate_dl_kbps;
  long delay_dl_us;
  uint64_t last_seen;
} ue_kpm_state_t;

typedef struct {
  sm_ag_if_rd_t rd;
} kpm_data_t;

typedef void (*log_ue_id)(ue_id_e2sm_t ue_id);

extern pthread_mutex_t mtx;
extern ue_id_e2sm_t ue_id;
extern log_ue_id log_ue_id_e2sm[END_UE_ID_E2SM];

void subscription_slicer_init(void);
kpm_sub_data_t gen_kpm_subs_slicer(kpm_ran_function_def_t const* ran_func);
void sm_cb_kpm_slicer(sm_ag_if_rd_t const* rd);
bool check_congestion_slicer(long bitrate_dl_kbps, long delay_dl_us, long pacotes_dl);

#endif
