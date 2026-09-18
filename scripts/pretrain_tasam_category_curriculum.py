#!/usr/bin/env python3
"""Pre-train only the TA-SAM observed-category head from the nine-stage curriculum.

The generated records are deliberately *not* MARL transitions.  They teach the
category head the shape of the authorised GreenRAN states before a live run,
while the actors and critics keep learning only from observed network feedback.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "drlexp" / "src"))

import torch
import torch.nn.functional as F

from collection_event_alternator import PROFILES
from greenran_marl_topology import build_du_state_snapshot
from rapp_judge import expected_verdict_for_stage
from rapp_sac_resource_model import infer_allocation_state
from drl.ta_sam_marl_sac import CATEGORY_ORDER, CATEGORY_TO_INDEX, OrdinalCategoryHead


SCHEMA = "greenran.tasam_category_curriculum.v1"
VERSION = "v1"
STATE_DIM = 13


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _one_hot(category: str) -> list[float]:
    return [1.0 if category == item else 0.0 for item in CATEGORY_ORDER]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resource_snapshot(category: str, rng: random.Random) -> dict[str, float | str | dict[str, float]]:
    """Build a feasible resource state without inventing a policy action."""
    bases = {
        "ALLOWED": (0.40, 0.30, 0.48, 0.36, 0.96, 0.96, 0.62),
        "CONDITIONAL": (0.56, 0.44, 0.55, 0.42, 0.88, 0.87, 0.78),
        "BLOCKED": (0.78, 0.66, 0.72, 0.62, 0.76, 0.76, 0.92),
    }
    d_ran, d_ai, r_ran, r_ai, ran_done, ai_done, utilization = bases[category]

    def vary(value: float, width: float) -> float:
        return _clamp(value + rng.uniform(-width, width))

    return {
        "d_ran": vary(d_ran, 0.025),
        "d_ai": vary(d_ai, 0.025),
        "r_ran": vary(r_ran, 0.02),
        "r_ai": vary(r_ai, 0.02),
        "resource_budget": 1.0,
        "usable_budget": 0.90,
        "ran_completion_ratio": vary(ran_done, 0.015),
        "ai_completion_ratio": vary(ai_done, 0.015),
        "utilization_ratio": vary(utilization, 0.02),
        "allocation_state": category,
        "ai_components": {"app2_pressure": 0.5, "vehicle_pressure": 0.5},
    }


def _stage_metrics(stage: Any, rng: random.Random) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Translate an alternator stage into the same metric families used at runtime."""
    app1 = copy.deepcopy(stage.app1)
    app2 = copy.deepcopy(stage.app2)
    vehicle = copy.deepcopy(stage.vehicle)

    # The jitters stay well inside each stage's intended band. They make the
    # head learn regions rather than a single hard-coded vector.
    app1["throughput_mbps"] = max(0.0, float(app1.get("throughput_mbps", 0.0)) + rng.uniform(-0.20, 0.20))
    app1["latency_ms"] = max(0.0, float(app1.get("latency_ms", 0.0)) + rng.uniform(-1.0, 1.0))
    app2["avg_latency_ms"] = max(0.0, float(app2.get("avg_latency_ms", 0.0)) + rng.uniform(-8.0, 8.0))
    vehicle["ego_latency_ms"] = max(0.0, float(vehicle.get("ego_latency_ms", 0.0)) + rng.uniform(-0.25, 0.25))
    vehicle["traffic_latency_ms"] = max(0.0, float(vehicle.get("traffic_latency_ms", 0.0)) + rng.uniform(-0.25, 0.25))
    vehicle["ego_packet_loss_percent"] = max(0.0, float(vehicle.get("ego_packet_loss_percent", 0.0)) + rng.uniform(-0.03, 0.03))

    sensor_total = max(1, int(app2.get("total_sensors", 1) or 1))
    connected = max(0, min(sensor_total, int(app2.get("connected_sensors", sensor_total) or sensor_total)))
    camera = {
        "active_cameras": int(app1.get("active_cameras", 3) or 3),
        "throughput_mbps": float(app1.get("throughput_mbps", 0.0) or 0.0),
        "latency_ms": float(app1.get("latency_ms", 0.0) or 0.0),
    }
    sensors = {
        "active_sensors": sensor_total,
        "total_sensors": sensor_total,
        "connected_ratio": connected / sensor_total,
        "connected_sensors": connected,
        "error_sensors": int(app2.get("error_sensors", 0) or 0),
        "low_battery_sensors": int(app2.get("low_battery_sensors", 0) or 0),
        "error_ratio": int(app2.get("error_sensors", 0) or 0) / sensor_total,
        "delivery_success_percent": float(app2.get("delivery_success_percent", 100.0) or 100.0),
        "packet_loss_percent": float(app2.get("packet_loss_percent", 0.0) or 0.0),
        "avg_latency_ms": float(app2.get("avg_latency_ms", 0.0) or 0.0),
        "avg_battery_percent": float(app2.get("avg_battery_percent", 100.0) or 100.0),
    }
    vehicles = {
        "total_vehicles": int(vehicle.get("total_vehicles", 5) or 5),
        "high_risk_vehicles": int(vehicle.get("high_risk_vehicles", 0) or 0),
        "max_latency_ms": max(float(vehicle.get("ego_latency_ms", 0.0) or 0.0), float(vehicle.get("traffic_latency_ms", 0.0) or 0.0)),
        "max_packet_loss_percent": max(float(vehicle.get("ego_packet_loss_percent", 0.0) or 0.0), float(vehicle.get("traffic_packet_loss_percent", 0.0) or 0.0)),
    }
    network = {"p95_us": max(camera["latency_ms"] * 1000.0, vehicles["max_latency_ms"] * 1000.0)}
    return camera, sensors, vehicles, network


def build_curriculum_records(samples_per_stage: int = 64, seed: int = 47) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return stratified train/validation records based on the real v3 stages."""
    if samples_per_stage < 5:
        raise ValueError("samples_per_stage must be at least 5 for a stratified validation split")
    stages = PROFILES["tasam_training_balanced_v3"]
    rng = random.Random(int(seed))
    previous_explicit = os.environ.get("GREENRAN_TASAM_EXPLICIT_STATE_FEATURE")
    os.environ["GREENRAN_TASAM_EXPLICIT_STATE_FEATURE"] = "1"
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    try:
        for stage in stages:
            category = expected_verdict_for_stage(stage.name)
            if category not in CATEGORY_TO_INDEX:
                raise ValueError(f"curriculum stage without category: {stage.name}")
            for index in range(samples_per_stage):
                camera, sensors, vehicles, network = _stage_metrics(stage, rng)
                observed_state = infer_allocation_state(
                    camera,
                    sensors,
                    vehicles,
                    network,
                    {},
                )
                if observed_state == "CRITICAL":
                    observed_state = "BLOCKED"
                if observed_state != category:
                    raise ValueError(
                        f"curriculum stage does not match runtime thresholds: "
                        f"{stage.name} expected {category}, inferred {observed_state}"
                    )
                snapshot = build_du_state_snapshot(
                    camera,
                    sensors,
                    vehicles,
                    network,
                    _resource_snapshot(category, rng),
                    operating_state=observed_state,
                )
                global_state = [float(value) for value in (snapshot.get("global_state") or {}).get("state_vector", [])]
                du_states = [
                    [float(value) for value in (du or {}).get("state_vector", [])]
                    for du in snapshot.get("du_states", []) or []
                ]
                if len(global_state) != STATE_DIM or len(du_states) != 3 or any(len(state) != STATE_DIM for state in du_states):
                    raise ValueError("curriculum state is incompatible with the 3-DU / 13-D runtime contract")
                if global_state[-3:] != _one_hot(category):
                    raise ValueError(f"curriculum state tail is inconsistent for {stage.name}: {global_state[-3:]}")
                record = {
                    "stage_name": stage.name,
                    "category": category,
                    "global_state": global_state,
                    "du_states": du_states,
                    "state_category_consistent": True,
                    "state_category_source": "current_network_metrics",
                }
                # Every fifth sample is held out; each stage remains present
                # in both splits and all classes stay balanced.
                (validation if index % 5 == 0 else train).append(record)
    finally:
        if previous_explicit is None:
            os.environ.pop("GREENRAN_TASAM_EXPLICIT_STATE_FEATURE", None)
        else:
            os.environ["GREENRAN_TASAM_EXPLICIT_STATE_FEATURE"] = previous_explicit
    return train, validation


def _tensor(records: list[dict[str, Any]]) -> tuple[torch.Tensor, torch.Tensor]:
    states = torch.tensor([record["global_state"] for record in records], dtype=torch.float32)
    labels = torch.tensor([CATEGORY_TO_INDEX[record["category"]] for record in records], dtype=torch.long)
    return states, labels


def _metrics(model: OrdinalCategoryHead, records: list[dict[str, Any]], class_weights: torch.Tensor) -> dict[str, Any]:
    states, targets = _tensor(records)
    model.eval()
    with torch.no_grad():
        logits = model(states)
        predictions = logits.argmax(dim=-1)
        loss = F.cross_entropy(logits, targets, weight=class_weights)
    confusion = {f"{predicted}->{observed}": 0 for predicted in CATEGORY_ORDER for observed in CATEGORY_ORDER}
    for predicted, observed in zip(predictions.tolist(), targets.tolist()):
        confusion[f"{CATEGORY_ORDER[predicted]}->{CATEGORY_ORDER[observed]}"] += 1
    by_class: dict[str, dict[str, float]] = {}
    for category in CATEGORY_ORDER:
        tp = confusion[f"{category}->{category}"]
        predicted_total = sum(confusion[f"{category}->{observed}"] for observed in CATEGORY_ORDER)
        observed_total = sum(confusion[f"{predicted}->{category}"] for predicted in CATEGORY_ORDER)
        precision = tp / max(predicted_total, 1)
        recall = tp / max(observed_total, 1)
        f1 = 2.0 * precision * recall / max(precision + recall, 1e-12)
        by_class[category] = {"precision": precision, "recall": recall, "f1": f1, "support": observed_total}
    return {
        "samples": len(records),
        "accuracy": float((predictions == targets).float().mean().item()),
        "loss": float(loss.item()),
        "confusion": confusion,
        "per_category": by_class,
    }


def train_category_head(
    initial_state: dict[str, Any],
    train: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    *,
    hidden_dim: int,
    learning_rate: float,
    conditional_weight: float,
    epochs: int,
    batch_size: int,
    seed: int,
) -> tuple[OrdinalCategoryHead, dict[str, Any]]:
    torch.manual_seed(int(seed))
    model = OrdinalCategoryHead(STATE_DIM, hidden_dim=hidden_dim, activation="tanh")
    model.load_state_dict(initial_state)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(learning_rate))
    class_weights = torch.tensor([1.0, float(conditional_weight), 1.0], dtype=torch.float32)
    states, labels = _tensor(train)
    generator = torch.Generator().manual_seed(int(seed))
    best_state = copy.deepcopy(model.state_dict())
    best_validation = _metrics(model, validation, class_weights)
    history: list[dict[str, float]] = []
    for epoch in range(1, int(epochs) + 1):
        model.train()
        ordering = torch.randperm(len(train), generator=generator)
        epoch_losses: list[float] = []
        for start in range(0, len(train), max(1, int(batch_size))):
            indices = ordering[start:start + max(1, int(batch_size))]
            logits = model(states[indices])
            loss = F.cross_entropy(logits, labels[indices], weight=class_weights)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()
            epoch_losses.append(float(loss.item()))
        validation_metrics = _metrics(model, validation, class_weights)
        history.append({"epoch": epoch, "train_loss": sum(epoch_losses) / max(len(epoch_losses), 1), "validation_accuracy": validation_metrics["accuracy"], "validation_loss": validation_metrics["loss"]})
        if (validation_metrics["accuracy"], -validation_metrics["loss"]) > (best_validation["accuracy"], -best_validation["loss"]):
            best_state = copy.deepcopy(model.state_dict())
            best_validation = validation_metrics
    model.load_state_dict(best_state)
    return model, {
        "optimizer": "Adam",
        "learning_rate": float(learning_rate),
        "conditional_class_weight": float(conditional_weight),
        "epochs": int(epochs),
        "batch_size": int(batch_size),
        "best_validation": _metrics(model, validation, class_weights),
        "final_train": _metrics(model, train, class_weights),
        "history": history,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def pretrain_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.input_checkpoint).resolve()
    output = Path(args.output_checkpoint).resolve()
    if not source.is_dir():
        raise ValueError(f"input checkpoint not found: {source}")
    if output.exists():
        raise ValueError(f"output checkpoint already exists: {output}")
    metadata = _read_json(source / "tasam_marl_checkpoint_meta.json")
    expected = {"du_count": 3, "du_state_dim": STATE_DIM, "global_state_dim": STATE_DIM}
    if {key: metadata.get(key) for key in expected} != expected:
        raise ValueError(f"incompatible checkpoint dimensions: {metadata}")
    source_head = source / "tasam_marl_category_head.pt"
    if not source_head.is_file():
        raise ValueError(f"category head missing: {source_head}")
    initial_state = torch.load(source_head, map_location="cpu", weights_only=False)
    train, validation = build_curriculum_records(args.samples_per_stage, args.seed)
    model, metrics = train_category_head(
        initial_state,
        train,
        validation,
        hidden_dim=int(metadata.get("category_head_hidden_dim", 64) or 64),
        learning_rate=args.learning_rate,
        conditional_weight=args.conditional_weight,
        epochs=args.epochs,
        batch_size=args.batch_size,
        seed=args.seed,
    )
    validation_metrics = metrics["best_validation"]
    if validation_metrics["per_category"]["CONDITIONAL"]["recall"] <= 0.0:
        raise ValueError("curriculum category head did not learn CONDITIONAL")

    shutil.copytree(source, output)
    torch.save(model.state_dict(), output / "tasam_marl_category_head.pt")
    stage_counts = Counter(record["stage_name"] for record in train + validation)
    category_counts = Counter(record["category"] for record in train + validation)
    curriculum = {
        "schema": SCHEMA,
        "version": VERSION,
        "source": "synthetic_curriculum",
        "seed": int(args.seed),
        "profile": "tasam_training_balanced_v3",
        "state_contract": {"du_count": 3, "du_state_dim": STATE_DIM, "global_state_dim": STATE_DIM},
        "samples": {"total": len(train) + len(validation), "train": len(train), "validation": len(validation), "per_stage": dict(sorted(stage_counts.items())), "per_category": dict(sorted(category_counts.items()))},
        "labels": {stage.name: expected_verdict_for_stage(stage.name) for stage in PROFILES["tasam_training_balanced_v3"]},
        "threshold_source": "tasam_training_balanced_v3 + rapp_judge.STAGE_EXPECTED_VERDICTS",
        "training": metrics,
        "online_replay_included": False,
        "online_reward_contract": {"correct": 1.0, "under_severity": -1.0, "over_severity_or_two_levels_or_invalid": -2.0, "formula": "min(tasam_continuous_reward, tasam_training_category_credit)"},
    }
    metadata["category_head_pretraining"] = curriculum
    metadata["category_head_pretrained"] = True
    metadata["category_head_training_source"] = "synthetic_curriculum"
    (output / "tasam_marl_checkpoint_meta.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary_path = output / "tasam_marl_summary.json"
    summary = _read_json(summary_path)
    summary["category_curriculum"] = curriculum
    summary["category_head_pretrained"] = True
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "tasam_category_curriculum_summary.json").write_text(json.dumps(curriculum, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "tasam_category_curriculum_train.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in train), encoding="utf-8")
    (output / "tasam_category_curriculum_validation.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in validation), encoding="utf-8")

    protected_files = ("tasam_marl_actors.pt", "tasam_marl_global_actor.pt", "tasam_marl_critic1.pt", "tasam_marl_critic2.pt", "tasam_marl_target_critic1.pt", "tasam_marl_target_critic2.pt")
    unchanged = {name: _sha256(source / name) == _sha256(output / name) for name in protected_files}
    if not all(unchanged.values()):
        raise RuntimeError("curriculum pretraining changed an actor or critic")
    curriculum["policy_weights_preserved"] = unchanged
    (output / "tasam_category_curriculum_summary.json").write_text(json.dumps(curriculum, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"output_checkpoint": str(output), "curriculum": curriculum}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-checkpoint", required=True, type=Path)
    parser.add_argument("--output-checkpoint", required=True, type=Path)
    parser.add_argument("--samples-per-stage", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=48)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--conditional-weight", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=47)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = pretrain_checkpoint(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
