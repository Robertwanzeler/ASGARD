import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from scripts.run_tasam_online_arm import (
    _decision_target_watcher_command,
    _native_actuation_evidence,
    _reconcile_wall_status,
    build_environment,
    checkpoint_fingerprint,
    mode_contract,
)
from scripts.run_tasam_online_economic_campaign import _arm
from scripts.run_tasam_online_campaign import build_plan
from src.rapp_orchestrator import RappResourceOptimizer


class TestTasamOnlineCampaign(unittest.TestCase):
    def test_sigterm_is_cancelled_without_verified_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "wall_clock_status.json").write_text(
                '{"phase": "running", "schema": "greenran.wall_clock_run.v1"}',
                encoding="utf-8",
            )
            _reconcile_wall_status(
                run_dir, 143, "", completion_verified=False
            )
            status = json.loads((run_dir / "wall_clock_status.json").read_text())
        self.assertEqual(status["phase"], "cancelled")
        self.assertEqual(status["reconciliation_reason"], "external_termination")

    def test_verified_sigterm_can_finish_a_finite_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "wall_clock_status.json").write_text(
                '{"phase": "running", "schema": "greenran.wall_clock_run.v1"}',
                encoding="utf-8",
            )
            _reconcile_wall_status(
                run_dir, 143, "", completion_verified=True
            )
            status = json.loads((run_dir / "wall_clock_status.json").read_text())
        self.assertEqual(status["phase"], "finished")

    def test_native_ns3_recognizes_the_balanced_v3_online_profile(self):
        root = Path(__file__).resolve().parents[1]
        scenario = (
            root / "ns-O-RAN-flexric/mmwave-LENA-oran/scratch/"
            "Energy_saving_with_cell_utilization_scenario.cc"
        ).read_text(encoding="utf-8")
        self.assertIn('profileName == "tasam_training_balanced_v3"', scenario)
        self.assertIn('{"allowed_bootstrap", 12.0, 1.0, 1.0, 1.0}', scenario)

    def test_training_contract_is_full_control_without_armd_or_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment("train_no_armd", Path(tmp), 45, "tasam_training_balanced_v3", 600)
        self.assertEqual(env["GREENRAN_ARMD_MODE"], "off")
        self.assertEqual(env["GREENRAN_TASAM_ADVISOR_MODE"], "tasam_full_control")
        self.assertEqual(mode_contract("train_no_armd")["historical_replay"], False)
        self.assertEqual(env["GREENRAN_COLLECTION_EVENT_PROFILE"], "tasam_training_balanced_v3")
        self.assertEqual(env["NS_GLOBAL_VALUE"], "RngRun=45")

    def test_rapp_only_disables_both_assistants(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment("rapp_only", Path(tmp), 46, "tasam_training_balanced_v3", 600)
        self.assertEqual(env["GREENRAN_ARMD_MODE"], "off")
        self.assertEqual(env["GREENRAN_TASAM_ADVISOR_ENABLED"], "0")
        self.assertEqual(env["GREENRAN_CONTROL_TRIAL_ENABLED"], "0")

    def test_parallel_pair_environment_propagates_slot_ports_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment(
                "rapp_only_actuating",
                Path(tmp),
                43,
                "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max",
                9000,
                120,
                native_fidelity=True,
                infra_resource_profile="parallel_pair_v1",
                execution_slot_env={
                    "GREENRAN_V2X_EXECUTION_SLOT": "slot-b",
                    "GREENRAN_E2_TERM_PORT": "36431",
                    "GREENRAN_E2_XAPP_PORT": "36432",
                    "GREENRAN_E2_LOCAL_PORT": "38570",
                    "GREENRAN_PORT_OFFSET": "100",
                },
            )
        self.assertEqual(env["GREENRAN_INFRA_RESOURCE_PROFILE"], "parallel_pair_v1")
        self.assertEqual(env["GREENRAN_V2X_EXECUTION_SLOT"], "slot-b")
        self.assertEqual(env["GREENRAN_E2_TERM_PORT"], "36431")
        self.assertEqual(env["GREENRAN_E2_LOCAL_PORT"], "38570")

    def test_combined_contract_keeps_armd_context_and_frozen_tasam(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment("combined", Path(tmp), 47, "tasam_training_balanced_v3", 600)
        self.assertEqual(env["GREENRAN_ARMD_MODE"], "assist")
        self.assertEqual(env["GREENRAN_TASAM_ADVISOR_MODE"], "assistant_only_control")
        self.assertEqual(env["GREENRAN_ASSISTANT_DECISION_MODE"], "cooperative_hierarchy")
        self.assertEqual(env["GREENRAN_TASAM_TRUE_ONLINE_ENABLED"], "0")
        self.assertEqual(env["GREENRAN_TASAM_TRUE_ONLINE_EXTERNAL_CONTROLLER"], "0")
        self.assertEqual(env["GREENRAN_ONLINE_UPDATE_OWNER"], "frozen_checkpoint_evaluation")
        self.assertEqual(env["GREENRAN_TASAM_E2_CONTROL"], "1")

    def test_combined_shadow_keeps_live_allocation_and_disables_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment("combined_shadow", Path(tmp), 47, "tasam_training_balanced_v3", 120)
        contract = mode_contract("combined_shadow")
        self.assertEqual(contract["armd_mode"], "assist")
        self.assertEqual(env["GREENRAN_TASAM_ADVISOR_MODE"], "shadow")
        self.assertEqual(env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"], "0.0")
        self.assertEqual(env["GREENRAN_CONTROL_TRIAL_ENABLED"], "0")
        self.assertEqual(env["GREENRAN_TASAM_E2_CONTROL"], "0")

    def test_combined_online_starts_with_a_ten_percent_canary(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment("combined_online", Path(tmp), 47, "tasam_training_balanced_v3", 600)
        self.assertEqual(env["GREENRAN_TASAM_ADVISOR_MODE"], "assistant_only_control")
        self.assertEqual(env["GREENRAN_TASAM_ONLINE_ROLLOUT_FRACTION"], "0.10")
        self.assertEqual(env["GREENRAN_TASAM_FORCE_FULL_ROLLOUT"], "0")
        self.assertEqual(env["GREENRAN_TASAM_ECONOMIC_BOOTSTRAP_POWER"], "25")
        self.assertEqual(env["GREENRAN_ML_ENABLED"], "1")
        self.assertEqual(env["GREENRAN_ML_RETRAIN_ENABLED"], "true")
        self.assertEqual(env["GREENRAN_XAPP_MODE"], "socket")

    def test_actuation_smoke_forces_native_e2_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = build_environment(
                "combined_actuation_smoke", Path(tmp), 47,
                "tasam_training_balanced_v3", 360,
            )
        self.assertEqual(env["GREENRAN_XAPP_MODE"], "socket")
        self.assertEqual(env["GREENRAN_TASAM_E2_CONTROL"], "1")
        self.assertEqual(env["GREENRAN_TASAM_ACTUATION_SMOKE_PROBE"], "1")

    def test_actuation_smoke_probe_is_deterministic_ten_percent(self):
        RappResourceOptimizer._actuation_smoke_probe_count = 0
        with patch.dict("os.environ", {"GREENRAN_TASAM_ACTUATION_SMOKE_PROBE": "1"}, clear=False):
            allowed = [
                RappResourceOptimizer._online_rollout_allows({}, 0.10)
                for _ in range(20)
            ]
        self.assertEqual(allowed, [i in (0, 10) for i in range(20)])

    def test_campaign_socket_directory_stays_below_af_unix_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "tasam_asgard_adaptation_seed47_20260914_v8_fixed"
            env = build_environment(
                "combined_actuation_smoke", run_dir, 47,
                "tasam_training_balanced_v3", 900,
            )
        socket_dir = env["GREENRAN_SOCKET_DIR"]
        self.assertLess(len(socket_dir + "/tasam_control.sock"), 108)
        self.assertLess(len(socket_dir + "/energy_saver.sock"), 108)

    def test_native_environment_pins_seed_and_unique_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "native_seed47"
            env = build_environment(
                "combined_online", run_dir, 47,
                "tasam_training_balanced_v3", 900, native_fidelity=True,
            )
        self.assertEqual(env["GREENRAN_NS3_RNG_RUN"], "47")
        self.assertEqual(env["GREENRAN_CAMPAIGN_ID"], run_dir.name)
        self.assertEqual(env["GREENRAN_NATIVE_SOURCE_GENERATION"], f"{run_dir.name}:native-v5")
        self.assertEqual(env["GREENRAN_TASAM_FORCE_FULL_ROLLOUT"], "0")

    def test_actuation_evidence_requires_native_transaction_and_confirmation(self):
        import csv
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "arm_manifest.json").write_text(
                '{"native_evidence_version": "v4"}\n', encoding="utf-8"
            )
            trace = run / "ns3_energy" / "TasamControlObservations.csv"
            trace.parent.mkdir()
            with trace.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Time", "CellId", "SchedulerTransactionId", "PowerTransactionId", "ActiveUes", "TxPowerPercent", "TxPowerDbm", "NominalTxPowerDbm", "ObservationKind", "PolicyActive", "PolicyExpiryTime", "SourceGeneration", "AssociationEpoch", "ActiveDlSymbols", "ActiveDlSymbolCapacity", "CampaignId", "EvidenceVersion"])
                writer.writerow([1.0, 2, 7, 7, 20, 60, 27.8, 30.0])
            with sqlite3.connect(run / "rapp_data_lake.db") as conn:
                conn.execute("CREATE TABLE energy_commands (actuation_confirmed INTEGER)")
                conn.execute("INSERT INTO energy_commands VALUES (1)")
            evidence = _native_actuation_evidence(run)
            # A legacy one-cell row and an ACK are intentionally insufficient
            # for v9 native actuation evidence.
            self.assertFalse(evidence["valid"])

    def test_actuation_evidence_correlates_power_and_scheduler_ids(self):
        import csv
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "arm_manifest.json").write_text(
                '{"native_evidence_version": "v4"}\n', encoding="utf-8"
            )
            trace = run / "ns3_energy" / "TasamControlObservations.csv"
            trace.parent.mkdir()
            fields = [
                "Time", "CellId", "SchedulerTransactionId", "PowerTransactionId",
                "ActiveUes", "TxPowerPercent", "TxPowerDbm", "NominalTxPowerDbm",
                "ObservationKind", "PolicyActive", "PolicyExpiryTime", "SourceGeneration",
                "AssociationEpoch", "ActiveDlSymbols", "ActiveDlSymbolCapacity",
                "CampaignId", "EvidenceVersion", "NativeAllocationSource",
            ]
            with trace.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for cell in (2, 3, 4):
                    writer.writerow({
                        "Time": 10.0, "CellId": cell, "SchedulerTransactionId": 0,
                        "PowerTransactionId": 1, "ActiveUes": 20,
                        "TxPowerPercent": 50, "TxPowerDbm": 27.0,
                        "NominalTxPowerDbm": 30.0, "ObservationKind": "power_readback",
                        "PolicyActive": 0, "ActiveDlSymbols": 0,
                        "ActiveDlSymbolCapacity": 1, "EvidenceVersion": "v4",
                    })
                    writer.writerow({
                        "Time": 10.5, "CellId": cell, "SchedulerTransactionId": 1,
                        "PowerTransactionId": 1, "ActiveUes": 20,
                        "TxPowerPercent": 50, "TxPowerDbm": 27.0,
                        "NominalTxPowerDbm": 30.0, "ObservationKind": "state_snapshot",
                        "PolicyActive": 1, "ActiveDlSymbols": 8,
                        "ActiveDlSymbolCapacity": 10, "EvidenceVersion": "v4",
                    })
            with sqlite3.connect(run / "rapp_data_lake.db") as conn:
                conn.execute(
                    "CREATE TABLE energy_commands "
                    "(native_control_sequence INTEGER, actuation_confirmed INTEGER, "
                    "action_origin TEXT, application_status TEXT)"
                )
                conn.execute("INSERT INTO energy_commands VALUES (1, 1, 'ta_sam', 'invalid')")
            evidence = _native_actuation_evidence(run)
            self.assertTrue(evidence["valid"])
            self.assertEqual(evidence["nonzero_transactions"], [1])
            self.assertEqual(evidence["confirmed_native_sequences"], [1])

    def test_v5_actuation_evidence_keeps_coherent_context_when_transaction_repeats(self):
        import csv
        import json
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "v5_context"
            energy = run / "ns3_energy"
            energy.mkdir(parents=True)
            (run / "arm_manifest.json").write_text(
                json.dumps({"native_evidence_version": "v5"}) + "\n",
                encoding="utf-8",
            )
            (energy / "E2NodeManifest.json").write_text(
                json.dumps({
                    "schema": "greenran.ns3.e2_node_manifest.v1",
                    "campaign_id": run.name,
                    "evidence_version": "v5",
                    "source_generation": f"{run.name}:native-v5",
                    "nodes": [
                        {
                            "cell_id": cell,
                            "ns3_node_id": cell,
                            "e2_node_id": cell,
                            "control_protocol": "e2_rc",
                            "rc_control_supported": True,
                        }
                        for cell in (2, 3, 4)
                    ],
                }) + "\n",
                encoding="utf-8",
            )
            fields = [
                "Time", "CellId", "SchedulerTransactionId", "PowerTransactionId",
                "ActiveUes", "TxPowerPercent", "TxPowerDbm", "NominalTxPowerDbm",
                "ObservationKind", "PolicyActive", "PolicyExpiryTime", "SourceGeneration",
                "AssociationEpoch", "ActiveDlSymbols", "ActiveDlSymbolCapacity",
                "CampaignId", "EvidenceVersion", "NativeControlSequence", "DecisionId",
                "ActionCorrelationId",
            ]
            generation = f"{run.name}:native-v5"
            correlation = "economic:test:decision7"
            with (energy / "TasamControlObservations.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                # A repeated transaction contains a previous policy snapshot.
                # It must not contaminate the current coherent context.
                for cell in (2, 3, 4):
                    writer.writerow({
                        "Time": 3.5, "CellId": cell,
                        "SchedulerTransactionId": 3, "PowerTransactionId": 3,
                        "ActiveUes": 20, "TxPowerPercent": 45,
                        "TxPowerDbm": 26.5, "NominalTxPowerDbm": 30,
                        "ObservationKind": "state_snapshot", "PolicyActive": 1,
                        "ActiveDlSymbols": 100, "ActiveDlSymbolCapacity": 240,
                        "CampaignId": run.name, "EvidenceVersion": "v5",
                        "NativeControlSequence": 2, "DecisionId": 6,
                        "ActionCorrelationId": "economic:test:decision6",
                        "SourceGeneration": generation,
                    })
                for cell in (2, 3, 4):
                    writer.writerow({
                        "Time": 3.5, "CellId": cell,
                        "SchedulerTransactionId": 2, "PowerTransactionId": 3,
                        "ActiveUes": 20, "TxPowerPercent": 45,
                        "TxPowerDbm": 26.5, "NominalTxPowerDbm": 30,
                        "ObservationKind": "power_readback", "PolicyActive": 1,
                        "ActiveDlSymbols": 0, "ActiveDlSymbolCapacity": 240,
                        "CampaignId": run.name, "EvidenceVersion": "v5",
                        "NativeControlSequence": 3, "DecisionId": 7,
                        "ActionCorrelationId": correlation,
                        "SourceGeneration": generation,
                    })
                for cell in (2, 3, 4):
                    writer.writerow({
                        "Time": 4.0, "CellId": cell,
                        "SchedulerTransactionId": 3, "PowerTransactionId": 3,
                        "ActiveUes": 20, "TxPowerPercent": 45,
                        "TxPowerDbm": 26.5, "NominalTxPowerDbm": 30,
                        "ObservationKind": "state_snapshot", "PolicyActive": 1,
                        "ActiveDlSymbols": 100, "ActiveDlSymbolCapacity": 240,
                        "CampaignId": run.name, "EvidenceVersion": "v5",
                        "NativeControlSequence": 3, "DecisionId": 7,
                        "ActionCorrelationId": correlation,
                        "SourceGeneration": generation,
                    })
            with sqlite3.connect(run / "rapp_data_lake.db") as conn:
                conn.execute(
                    "CREATE TABLE energy_commands ("
                    "native_control_sequence INTEGER, actuation_confirmed INTEGER, "
                    "observed_power_percent REAL, native_observation_version TEXT, "
                    "action_origin TEXT)"
                )
                conn.execute(
                    "INSERT INTO energy_commands VALUES (3, 1, 45, 'v5', 'ta_sam')"
                )
            evidence = _native_actuation_evidence(run, strict_v5=True)
            self.assertTrue(evidence["valid"])
            self.assertEqual(evidence["native_valid_sequences"], [3])
            self.assertEqual(evidence["correlated_confirmation_count"], 1)


    def test_plan_has_two_final_arms_and_three_paired_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_plan(Path(tmp), [45, 46, 47], "tasam_training_balanced_v3", 600, Path(tmp) / "checkpoint")
        self.assertEqual(plan["seeds"], [45, 46, 47])
        self.assertEqual(plan["phase_2"]["arms"], ["rapp_only", "combined"])
        self.assertEqual(plan["profile"], "tasam_training_balanced_v3")
        self.assertEqual(plan["primary_seed"], 47)
        self.assertFalse(plan["energy"]["physical_wattmeter"])

    def test_checkpoint_fingerprint_detects_any_file_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp)
            (checkpoint / "actor.pt").write_bytes(b"frozen")
            before = checkpoint_fingerprint(checkpoint)
            self.assertEqual(before, checkpoint_fingerprint(checkpoint))
            (checkpoint / "actor.pt").write_bytes(b"mutated")
            self.assertNotEqual(before, checkpoint_fingerprint(checkpoint))

    def test_decision_target_watcher_is_state_scoped_and_wall_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            command = _decision_target_watcher_command(
                SimpleNamespace(run_dir=run_dir, decision_target=20)
            )
        self.assertIn("--state-dir", command)
        self.assertIn("--wall-only", command)
        self.assertIn("20", command)
        self.assertIn(str(run_dir / "rapp_data_lake.db"), command)

    def test_economic_arm_passes_the_versioned_runtime_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(
                seed=47, profile="tasam_training_balanced_v3", wall_time=900,
                sim_time=600, calibration=root / "energy-v2.json", decisions=300,
                min_free_gib=10, min_new_snapshots=60,
                min_trainable_transitions=180, epochs_per_update=2,
                controller_poll_seconds=10,
            )
            command = _arm("combined_shadow", root / "shadow", root / "checkpoint", args)
        index = command.index("--energy-calibration")
        self.assertEqual(command[index + 1], str(args.calibration))


if __name__ == "__main__":
    unittest.main()
