#ifndef SUBSCRIPTION_ENERGY_H
#define SUBSCRIPTION_ENERGY_H

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

void subscription_energy_init(void);
kpm_sub_data_t gen_kpm_subs_energy(kpm_ran_function_def_t const* ran_func);
void sm_cb_kpm_energy(sm_ag_if_rd_t const* rd);
bool check_energy_saving_energy(long bitrate_dl_kbps, long delay_dl_us);

#endif
