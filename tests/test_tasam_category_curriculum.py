import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pretrain_tasam_category_curriculum.py"
SPEC = importlib.util.spec_from_file_location("tasam_category_curriculum", SCRIPT)
assert SPEC and SPEC.loader
curriculum = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = curriculum
SPEC.loader.exec_module(curriculum)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TasamCategoryCurriculumTests(unittest.TestCase):
    def test_records_cover_all_stages_with_consistent_13d_state_tail(self):
        train, validation = curriculum.build_curriculum_records(samples_per_stage=10, seed=47)
        self.assertEqual(len(train), 72)
        self.assertEqual(len(validation), 18)
        all_rows = train + validation
        self.assertEqual({row["stage_name"] for row in all_rows}, {
            "allowed_bootstrap", "allowed_stable", "camera_conditional", "camera_blocked",
            "vehicle_conditional", "vehicle_blocked", "app2_conditional", "app2_blocked",
            "allowed_recovery",
        })
        self.assertEqual({row["category"] for row in all_rows}, {"ALLOWED", "CONDITIONAL", "BLOCKED"})
        for row in all_rows:
            self.assertEqual(len(row["global_state"]), 13)
            self.assertEqual(len(row["du_states"]), 3)
            self.assertTrue(all(len(state) == 13 for state in row["du_states"]))
            self.assertEqual(row["global_state"][-3:], curriculum._one_hot(row["category"]))

    def test_pretraining_replaces_only_category_head_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            head = curriculum.OrdinalCategoryHead(13, hidden_dim=8, activation="tanh")
            torch.save(head.state_dict(), source / "tasam_marl_category_head.pt")
            for name in (
                "tasam_marl_actors.pt", "tasam_marl_global_actor.pt", "tasam_marl_critic1.pt", "tasam_marl_critic2.pt",
                "tasam_marl_target_critic1.pt", "tasam_marl_target_critic2.pt",
            ):
                (source / name).write_bytes((name + " unchanged").encode("utf-8"))
            (source / "tasam_marl_checkpoint_meta.json").write_text(json.dumps({
                "du_count": 3, "du_state_dim": 13, "global_state_dim": 13,
                "category_head_hidden_dim": 8,
            }), encoding="utf-8")
            (source / "tasam_marl_summary.json").write_text(json.dumps({"final_metrics": {}}), encoding="utf-8")
            source_hashes = {path.name: digest(path) for path in source.glob("tasam_marl_*.pt") if path.name != "tasam_marl_category_head.pt"}
            source_head_hash = digest(source / "tasam_marl_category_head.pt")
            output = root / "curriculum"
            result = curriculum.pretrain_checkpoint(Namespace(
                input_checkpoint=source,
                output_checkpoint=output,
                samples_per_stage=10,
                epochs=80,
                batch_size=24,
                learning_rate=0.001,
                conditional_weight=1.5,
                seed=47,
            ))
            self.assertEqual(result["curriculum"]["source"], "synthetic_curriculum")
            self.assertGreaterEqual(result["curriculum"]["training"]["best_validation"]["accuracy"], 0.90)
            self.assertGreater(result["curriculum"]["training"]["best_validation"]["per_category"]["CONDITIONAL"]["recall"], 0.0)
            for name, value in source_hashes.items():
                self.assertEqual(value, digest(output / name))
            self.assertEqual(source_head_hash, digest(source / "tasam_marl_category_head.pt"))
            meta = json.loads((output / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8"))
            self.assertTrue(meta["category_head_pretrained"])
            self.assertFalse(meta["category_head_pretraining"]["online_replay_included"])
