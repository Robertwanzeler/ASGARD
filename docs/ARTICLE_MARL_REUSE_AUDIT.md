# Article MARL Reuse Audit

## Objective

Adapt GreenRAN to the SAC + SAM + MARL architecture from the O-RAN article
without changing the fixed scenario:

- 12 ns-3 UEs
- 3 camera UEs
- 9 background UEs
- up to 5 vehicles
- ARMD-GreenRAN remains permanent

## Keep

These parts still serve the article-aligned path directly.

- `config/greenran_fixed_scenario.json`
- `docs/GREENRAN_FIXED_SCENARIO_BASELINE.md`
- `src/greenran_paths.py`
- `src/carla_ns3_mapper.py`
- `src/rapp_orchestrator.py`
- `src/rapp_data_lake.py`
- `src/rapp_dashboard.py`
- `templates/ops_mobile.html`
- `scripts/collection_event_alternator.py`
- `scripts/run_fixed_awac_controlled_collection.py`
- `scripts/watch_live_collection.py`
- `scripts/export_sac_workload_trace.py`
- `src/rapp_sac_resource_model.py`
- `src/xapp_vehicle_control.py`
- `src/vehicle_policy_runtime.py`
- `ns-O-RAN-flexric/mmwave-LENA-oran` pressure-profile changes
- `flexric` xApp changes

Rationale:

- they preserve the scenario;
- they already produce real conflict windows;
- they are useful for MARL state/reward instrumentation;
- they do not lock us into the old A3C path.

## Keep As Transition

These parts are useful only as bridge or benchmark while the article-faithful
trainer is implemented.

- `src/rapp_rl_policy.py`
- `drlexp/src/drl/caora_sac_environment.py`
- `drlexp/training/train_sac.py`
- `docs/SAC_MIGRATION_PLAN.md`
- `docs/SAC_REAL_COLLECTION_PLAYBOOK.md`
- `docs/AWAC_CHECKPOINT_STATUS.md`
- `scripts/prepare_awac_retrain_handoff.py`
- `scripts/run_awac_refresh_cycle.py`
- `scripts/show_latest_collection_checkpoint.py`
- `runs/sac_bootstrap/*`
- `src/rapp_drl_predictor.py`
- `drlexp/training/train_a3c.py`
- `drlexp/training/train_sbilstm.py`
- `drlexp/config/drl_config.yaml`
- `drlexp/models/a3c/*`
- `drlexp/models/sbilstm/*`

Rationale:

- the current SAC/AWAC path is not article-faithful because it is single-agent,
  offline, and has no SAM;
- the legacy A3C/SBiLSTM path still serves as historical baseline and fallback;
- both should remain until MARL + SAM is working end to end.

## Remove Now

These items no longer serve the forward path.

- `ns-O-RAN-flexric-broken`
- tracked `__pycache__` / `.pyc` artifacts
- `scripts/watch_awac_retrain_handoff.sh`

Rationale:

- `ns-O-RAN-flexric-broken` is referenced only in `.gitmodules`;
- the handoff watcher became redundant after the probe-refresh logic moved into
  `prepare_awac_retrain_handoff.py`;
- bytecode artifacts should not be versioned.

## Remove Later

Only remove these after MARL + SAM is validated.

- `src/rapp_drl_predictor.py`
- `drlexp/training/train_a3c.py`
- `drlexp/training/train_sbilstm.py`
- `drlexp/config/drl_config.yaml`
- `drlexp/models/a3c/*`
- `drlexp/models/sbilstm/*`
- A3C/SBiLSTM-only dashboards, plots, and docs

## Next Build Target

The next implementation target should be:

1. logical DU partitioning for the fixed scenario;
2. per-DU / per-slice state export;
3. MARL SAC trainer with global critic;
4. selective SAM and dynamic rho scheduling;
5. shadow-mode integration with ARMD unchanged.
