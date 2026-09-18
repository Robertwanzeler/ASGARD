import hashlib
import json
from pathlib import Path

import torch

from scripts.build_tasam_clean_curriculum_checkpoint import COMPONENTS, build
from scripts.run_tasam_online_controlled import checkpoint_quality


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_clean_checkpoint_reinitializes_policy_and_keeps_only_category_head(tmp_path):
    source = Path(
        "/run/media/robert/GREENRAN/GreenRAN_results/"
        "tasam_seed47_state_aligned_curriculum_checkpoint_20260903"
    )
    if not (source / "tasam_marl_category_head.pt").is_file():
        return

    output = tmp_path / "clean_checkpoint"
    result = build(
        type("Args", (), {
            "source_checkpoint": source,
            "output_checkpoint": output,
            "seed": 47,
        })()
    )
    assert result["initialization"]["initialization_source"] == "clean_curriculum_only"
    assert result["initialization"]["replay_copied"] is False
    assert result["initialization"]["historical_trace_copied"] is False
    assert all((output / name).is_file() for name in COMPONENTS)
    assert not (output / "resume_checkpoint.pt").exists()
    assert not (output / "epoch_history.jsonl").exists()

    source_head = torch.load(source / "tasam_marl_category_head.pt", map_location="cpu", weights_only=False)
    output_head = torch.load(output / "tasam_marl_category_head.pt", map_location="cpu", weights_only=False)
    assert source_head.keys() == output_head.keys()
    for key in source_head:
        assert torch.equal(source_head[key], output_head[key])

    metadata = json.loads((output / "tasam_marl_checkpoint_meta.json").read_text())
    assert metadata["initialization_source"] == "clean_curriculum_only"
    assert metadata["trace_jsonl"] == ""
    assert metadata["replay_source"] == "none"
    assert metadata["historical_replay_enabled"] is False
    assert metadata["initialization"]["component_sha256"]["tasam_marl_category_head.pt"] == _sha256(
        output / "tasam_marl_category_head.pt"
    )
    quality, available = checkpoint_quality(output)
    assert quality == (0.0, 0.0, 0.0, 0.0, 0.0)
    assert available is False
