import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.run_tasam_online_controlled import (
    advance_rollout,
    migrate_legacy_candidate_shadow_state,
    normalize_state_contract,
    publish_manifests,
)


class TasamOnlineControlledTests(unittest.TestCase):
    def test_legacy_replay_row_is_augmented_to_active_checkpoint_contract(self):
        row = {
            "scenario_stage": "camera_blocked",
            "next_scenario_stage": "allowed_recovery",
            "decision": {"decision": "BLOCKED"},
            "global_state": {"state_vector": [0.0] * 10},
            "next_global_state": {"state_vector": [0.0] * 10},
            "du_states": [{"state_vector": [0.0] * 10} for _ in range(3)],
            "next_du_states": [{"state_vector": [0.0] * 10} for _ in range(3)],
        }

        self.assertTrue(normalize_state_contract(row, 13, 13))
        self.assertEqual(row["global_state"]["state_vector"][-3:], [0.0, 0.0, 1.0])
        self.assertEqual(row["next_global_state"]["state_vector"][-3:], [1.0, 0.0, 0.0])
        self.assertTrue(all(len(du["state_vector"]) == 13 for du in row["du_states"]))
        self.assertTrue(all(len(du["state_vector"]) == 13 for du in row["next_du_states"]))

    def test_promotion_publishes_consistent_full_gate_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate_0001" / "tasam_selective"
            candidate.mkdir(parents=True)
            (candidate / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
            (candidate / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({"du_count": 3}), encoding="utf-8"
            )
            (candidate / "tasam_marl_summary.json").write_text(
                json.dumps({"completed_epochs": 3, "final_metrics": {}}),
                encoding="utf-8",
            )

            args = SimpleNamespace(
                promotion_window=30,
                rollout_manifest=root / "online_rollout.json",
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
            )
            state = {
                "stage": "canary_50",
                "rollout_fraction": 0.50,
                "active_checkpoint": str(root / "old"),
                "candidate_checkpoint": str(candidate),
                "candidate_promoted": False,
                "stage_started_decisions": 100,
            }

            advance_rollout(args, state, decision_count=130, guard={"rollback": False})

            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["active_checkpoint"], str(candidate))
            self.assertEqual(state["candidate_checkpoint"], "")

            rollout = json.loads(args.rollout_manifest.read_text(encoding="utf-8"))
            self.assertEqual(rollout["rollout"]["stage"], "full")
            self.assertEqual(rollout["rollout"]["checkpoint"], str(candidate))

            gate = json.loads(args.control_gate.read_text(encoding="utf-8"))
            self.assertEqual(gate["gate"]["status"], "full")
            self.assertTrue(gate["gate"]["allow_control_trial"])
            self.assertEqual(gate["gate"]["runtime_readiness"], "online_guarded")

    def test_full_rollout_keeps_active_policy_during_candidate_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate_0002" / "tasam_selective"
            candidate.mkdir(parents=True)
            (candidate / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
            (candidate / "tasam_marl_checkpoint_meta.json").write_text(
                json.dumps({"du_count": 3}), encoding="utf-8"
            )
            (candidate / "tasam_marl_summary.json").write_text(
                json.dumps({"completed_epochs": 3, "final_metrics": {}}), encoding="utf-8"
            )
            active = root / "active" / "tasam_selective"
            active.mkdir(parents=True)
            for name, payload in {
                "tasam_marl_actors.pt": b"active",
                "tasam_marl_checkpoint_meta.json": json.dumps({"du_count": 3}),
                "tasam_marl_summary.json": json.dumps({"completed_epochs": 3}),
            }.items():
                (active / name).write_bytes(payload if isinstance(payload, bytes) else payload.encode())

            args = SimpleNamespace(
                promotion_window=30,
                rollout_manifest=root / "online_rollout.json",
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
            )
            state = {
                "stage": "full",
                "rollout_fraction": 1.0,
                "active_checkpoint": str(active),
                "candidate_checkpoint": str(candidate),
                "candidate_promoted": False,
            }

            advance_rollout(args, state, decision_count=100, guard={"rollback": False})
            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["active_checkpoint"], str(active))
            self.assertEqual(state["candidate_checkpoint"], str(candidate))
            self.assertEqual(state["candidate_shadow_started_decisions"], 100)
            rollout = json.loads(args.rollout_manifest.read_text(encoding="utf-8"))
            self.assertEqual(rollout["rollout"]["checkpoint"], str(active))
            self.assertEqual(rollout["rollout"]["candidate"], str(candidate))
            self.assertEqual(rollout["rollout"]["fraction"], 1.0)

            advance_rollout(args, state, decision_count=130, guard={"rollback": False})
            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["active_checkpoint"], str(candidate))
            self.assertEqual(state["candidate_checkpoint"], "")
            promoted = json.loads(args.eval_manifest.read_text(encoding="utf-8"))
            self.assertEqual(promoted["best_run"]["run_dir"], str(candidate.resolve()))

    def test_legacy_shadow_state_migrates_without_resetting_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(promotion_window=30)
            state = {
                "stage": "shadow",
                "rollout_fraction": 0.0,
                "active_checkpoint": str(root / "candidate_0001"),
                "candidate_checkpoint": str(root / "candidate_0002"),
                "candidate_promoted": False,
                "stage_started_decisions": 1009,
                "updates_completed": 2,
            }

            self.assertTrue(migrate_legacy_candidate_shadow_state(args, state, 1027))
            self.assertEqual(state["stage"], "full")
            self.assertEqual(state["rollout_fraction"], 1.0)
            self.assertEqual(state["candidate_shadow_started_decisions"], 1009)
            self.assertEqual(state["candidate_shadow_until_decisions"], 1039)
            self.assertEqual(state["updates_completed"], 2)

    def test_active_manifest_is_not_replaced_by_pending_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "active"
            candidate = root / "candidate"
            for directory in (active, candidate):
                directory.mkdir()
                (directory / "tasam_marl_actors.pt").write_bytes(b"checkpoint")
                (directory / "tasam_marl_checkpoint_meta.json").write_text(
                    json.dumps({"du_count": 3}), encoding="utf-8"
                )
                (directory / "tasam_marl_summary.json").write_text("{}", encoding="utf-8")
            args = SimpleNamespace(
                updates_completed=2,
                eval_manifest=root / "online_eval_manifest.json",
                control_gate=root / "online_control_gate.json",
                candidate_eval_manifest=root / "online_candidate_eval_manifest.json",
                promotion_window=30,
            )
            state = {
                "stage": "full",
                "rollout_fraction": 1.0,
                "active_checkpoint": str(active),
                "candidate_checkpoint": str(candidate),
            }
            publish_manifests(args, state, active, "control_candidate")
            payload = json.loads(args.eval_manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["best_run"]["run_dir"], str(active.resolve()))


if __name__ == "__main__":
    unittest.main()
