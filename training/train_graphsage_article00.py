#!/usr/bin/env python3
"""
Temporal GraphSAGE trainer for the isolated article00 track.

This trainer stays outside the GreenRAN runtime. Its sole purpose is to
reproduce the article00 method experimentally:

- temporal graph over time steps;
- GraphSAGE aggregation over consecutive timestamps;
- MSE reconstruction of the temporal feature vectors;
- adjacency reconstruction from correlation + threshold;
- post-processing with known A-P and A-K edges;
- conflict labeling for direct, indirect, and implicit conflicts.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ModuleNotFoundError as exc:
    raise SystemExit(
        "torch is required for train_graphsage_article00.py; run it with ./drlexp/.venv/bin/python or an "
        "equivalent environment that has torch installed"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runs" / "article00" / "training"
DEFAULT_EPOCHS = "50,100,200,400,600,800,1000"
DEFAULT_THRESHOLDS = "0.2,0.5,0.9"
DEFAULT_FEATURE_ORDER = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "K1", "K2", "K3", "K4")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the isolated temporal GraphSAGE track for article00.")
    parser.add_argument(
        "--dataset-dir",
        required=True,
        help="directory containing timeseries.csv, article00_metadata.json and article00_graph_reference.json",
    )
    parser.add_argument(
        "--output-dir",
        help="training output dir; defaults to runs/article00/training/<dataset-dir-name>",
    )
    parser.add_argument("--epochs", default=DEFAULT_EPOCHS, help="checkpoint epochs, for example 50,100,200,400,600")
    parser.add_argument(
        "--thresholds",
        default=DEFAULT_THRESHOLDS,
        help="correlation thresholds used for evaluation, for example 0.2,0.5,0.9",
    )
    parser.add_argument(
        "--selection-threshold",
        type=float,
        default=0.5,
        help="threshold used to choose the best checkpoint in the training summary",
    )
    parser.add_argument("--hidden-dim", type=int, default=16, help="hidden dimension for the temporal GraphSAGE stack")
    parser.add_argument("--embed-dim", type=int, default=16, help="latent embedding dimension")
    parser.add_argument("--dropout", type=float, default=0.10, help="dropout rate")
    parser.add_argument("--learning-rate", type=float, default=0.001, help="optimizer learning rate")
    parser.add_argument("--weight-decay", type=float, default=1e-4, help="optimizer weight decay")
    parser.add_argument(
        "--temporal-radius",
        type=int,
        default=1,
        help="temporal neighborhood radius; 1 reproduces the current immediate-neighbor support",
    )
    parser.add_argument(
        "--temporal-decay",
        type=float,
        default=1.0,
        help="decay factor for farther temporal neighbors when temporal-radius > 1",
    )
    parser.add_argument(
        "--fp-penalty-weight",
        type=float,
        default=0.0,
        help="optional weight for the false-positive correlation penalty; 0 disables experiment C",
    )
    parser.add_argument(
        "--fp-penalty-margin",
        type=float,
        default=0.10,
        help="allowed absolute correlation for non-edge P-K pairs before the false-positive penalty activates",
    )
    parser.add_argument(
        "--tp-reward-weight",
        type=float,
        default=0.0,
        help="optional weight for rewarding true P-K edges to keep high correlation; 0 disables this term",
    )
    parser.add_argument(
        "--tp-reward-margin",
        type=float,
        default=0.5,
        help="minimum absolute correlation targeted for true P-K edges before the true-positive reward loss activates",
    )
    parser.add_argument(
        "--hard-positive-weight",
        type=float,
        default=0.0,
        help="optional weight for forcing true P-K edges above a target threshold; 0 disables this targeted loss",
    )
    parser.add_argument(
        "--hard-positive-threshold",
        type=float,
        default=0.5,
        help="target absolute correlation threshold for the hard-positive true-edge loss",
    )
    parser.add_argument(
        "--hard-positive-focus-kpis",
        default="",
        help="optional comma-separated KPI ids to focus the hard-positive loss on, for example K2",
    )
    parser.add_argument(
        "--hard-negative-weight",
        type=float,
        default=0.0,
        help="optional weight for forcing false P-K edges below a target threshold; 0 disables this targeted loss",
    )
    parser.add_argument(
        "--hard-negative-threshold",
        type=float,
        default=0.5,
        help="maximum absolute correlation threshold tolerated for the hard-negative non-edge loss",
    )
    parser.add_argument(
        "--hard-negative-focus-kpis",
        default="",
        help="optional comma-separated KPI ids to focus the hard-negative loss on, for example K2",
    )
    parser.add_argument(
        "--selection-mode",
        choices=("threshold_f1", "composite"),
        default="threshold_f1",
        help="how to choose the best checkpoint: legacy threshold_f1 tuple or weighted composite score",
    )
    parser.add_argument("--selection-weight-parameter", type=float, default=1.0)
    parser.add_argument("--selection-weight-indirect", type=float, default=1.0)
    parser.add_argument("--selection-weight-implicit", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    return parser.parse_args()


def parse_epoch_list(raw: str) -> list[int]:
    values = sorted({int(piece.strip()) for piece in raw.split(",") if piece.strip()})
    if not values:
        raise SystemExit("no epochs requested")
    return values


def parse_threshold_list(raw: str) -> list[float]:
    values = sorted({float(piece.strip()) for piece in raw.split(",") if piece.strip()})
    if not values:
        raise SystemExit("no thresholds requested")
    return values


def parse_csv_list(raw: str) -> list[str]:
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def mean_stdev(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), 0.0
    return float(statistics.mean(values)), float(statistics.pstdev(values))


def feature_order_from_metadata(metadata: dict[str, Any]) -> list[str]:
    order = metadata.get("feature_order") or list(DEFAULT_FEATURE_ORDER)
    if not order:
        raise SystemExit("dataset metadata must define a non-empty feature_order")
    parameters = [feature for feature in order if feature.startswith("P")]
    kpis = [feature for feature in order if feature.startswith("K")]
    if not parameters or not kpis:
        raise SystemExit("dataset metadata must contain at least one parameter feature (P*) and one KPI feature (K*)")
    return list(order)


def build_feature_tensor(rows: list[dict[str, str]], feature_order: list[str]) -> torch.Tensor:
    matrix = []
    for row in rows:
        matrix.append([safe_float(row.get(feature)) for feature in feature_order])
    if not matrix:
        raise SystemExit("empty article00 dataset")
    return torch.tensor(matrix, dtype=torch.float32)


def normalize_features(features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    mean = features.mean(dim=0, keepdim=True)
    std = features.std(dim=0, keepdim=True)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    return (features - mean) / std, mean, std


def build_temporal_support(num_steps: int, radius: int = 1, decay: float = 1.0) -> torch.Tensor:
    support = torch.zeros((num_steps, num_steps), dtype=torch.float32)
    radius = max(1, radius)
    decay = max(0.0, decay)
    for distance in range(1, radius + 1):
        weight = decay ** (distance - 1)
        for index in range(num_steps - distance):
            support[index, index + distance] = weight
            support[index + distance, index] = weight
    return support


def split_feature_groups(feature_order: list[str]) -> tuple[list[str], list[str]]:
    parameters = [feature for feature in feature_order if feature.startswith("P")]
    kpis = [feature for feature in feature_order if feature.startswith("K")]
    return parameters, kpis


def known_edge_sets(graph: dict[str, Any]) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    app_parameter = set()
    app_kpi = set()
    parameter_kpi = set()
    for edge in graph.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        relation = edge.get("relation")
        if relation == "controls":
            app_parameter.add((source, target))
        elif relation == "monitors":
            app_kpi.add((source, target))
        elif relation == "influences":
            parameter_kpi.add((source, target))
    return app_parameter, app_kpi, parameter_kpi


def edge_index_by_threshold(
    correlation: list[list[float]],
    feature_order: list[str],
    threshold: float | None,
) -> set[tuple[str, str]]:
    parameters, kpis = split_feature_groups(feature_order)
    feature_index = {name: idx for idx, name in enumerate(feature_order)}
    edges = set()
    for parameter in parameters:
        for kpi in kpis:
            score = abs(correlation[feature_index[parameter]][feature_index[kpi]])
            if threshold is None or score >= threshold:
                edges.add((parameter, kpi))
    return edges


def full_graph_edges(
    app_parameter_edges: set[tuple[str, str]],
    app_kpi_edges: set[tuple[str, str]],
    parameter_kpi_edges: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    return set(app_parameter_edges) | set(app_kpi_edges) | set(parameter_kpi_edges)


def compute_binary_metrics(
    predicted: set[tuple[str, str]],
    truth: set[tuple[str, str]],
    universe: set[tuple[str, str]],
) -> dict[str, Any]:
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    tn = len(universe - (predicted | truth))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "predicted_edges": len(predicted),
        "target_edges": len(truth),
    }


def conflict_pair_universe(
    app_parameter_edges: set[tuple[str, str]],
    app_kpi_edges: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    apps = sorted(({source for source, _ in app_parameter_edges}) | ({source for source, _ in app_kpi_edges}))
    universe = set()
    for left in range(len(apps)):
        for right in range(left + 1, len(apps)):
            universe.add((apps[left], apps[right]))
    return universe


def sorted_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def label_conflicts(
    app_parameter_edges: set[tuple[str, str]],
    app_kpi_edges: set[tuple[str, str]],
    parameter_kpi_edges: set[tuple[str, str]],
) -> dict[str, set[tuple[str, str]]]:
    app_to_parameters: dict[str, set[str]] = {}
    app_to_kpis: dict[str, set[str]] = {}
    parameter_to_kpis: dict[str, set[str]] = {}

    for app, parameter in app_parameter_edges:
        app_to_parameters.setdefault(app, set()).add(parameter)
    for app, kpi in app_kpi_edges:
        app_to_kpis.setdefault(app, set()).add(kpi)
    for parameter, kpi in parameter_kpi_edges:
        parameter_to_kpis.setdefault(parameter, set()).add(kpi)

    apps = sorted(set(app_to_parameters) | set(app_to_kpis))
    direct: set[tuple[str, str]] = set()
    indirect: set[tuple[str, str]] = set()
    implicit: set[tuple[str, str]] = set()

    for left in range(len(apps)):
        for right in range(left + 1, len(apps)):
            app_left = apps[left]
            app_right = apps[right]
            shared_parameters = app_to_parameters.get(app_left, set()) & app_to_parameters.get(app_right, set())
            if shared_parameters:
                direct.add((app_left, app_right))

            left_kpis = set()
            for parameter in app_to_parameters.get(app_left, set()):
                left_kpis.update(parameter_to_kpis.get(parameter, set()))
            right_kpis = set()
            for parameter in app_to_parameters.get(app_right, set()):
                right_kpis.update(parameter_to_kpis.get(parameter, set()))
            if left_kpis & right_kpis:
                indirect.add((app_left, app_right))

    for app_left in apps:
        for parameter in app_to_parameters.get(app_left, set()):
            for kpi in parameter_to_kpis.get(parameter, set()):
                for app_right in apps:
                    if app_right == app_left:
                        continue
                    if kpi not in app_to_kpis.get(app_right, set()):
                        continue
                    for _target_parameter in app_to_parameters.get(app_right, set()):
                        implicit.add(sorted_pair(app_left, app_right))
                        break

    return {"direct": direct, "indirect": indirect, "implicit": implicit}


def conflict_metrics(
    predicted: dict[str, set[tuple[str, str]]],
    truth: dict[str, set[tuple[str, str]]],
    universe: set[tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    return {
        label: compute_binary_metrics(predicted.get(label, set()), truth.get(label, set()), universe)
        for label in ("direct", "indirect", "implicit")
    }


def correlation_from_reconstruction(reconstructed_raw: torch.Tensor) -> list[list[float]]:
    centered = reconstructed_raw - reconstructed_raw.mean(dim=0, keepdim=True)
    covariance = centered.t() @ centered / max(reconstructed_raw.shape[0] - 1, 1)
    std = reconstructed_raw.std(dim=0, keepdim=True)
    denominator = std.t() @ std
    denominator = torch.where(denominator < 1e-8, torch.ones_like(denominator), denominator)
    correlation = covariance / denominator
    correlation = torch.nan_to_num(correlation, nan=0.0, posinf=0.0, neginf=0.0)
    return correlation.detach().cpu().tolist()


def correlation_tensor_from_reconstruction(reconstructed: torch.Tensor) -> torch.Tensor:
    centered = reconstructed - reconstructed.mean(dim=0, keepdim=True)
    covariance = centered.t() @ centered / max(reconstructed.shape[0] - 1, 1)
    std = reconstructed.std(dim=0, keepdim=True)
    denominator = std.t() @ std
    denominator = torch.where(denominator < 1e-8, torch.ones_like(denominator), denominator)
    correlation = covariance / denominator
    return torch.nan_to_num(correlation, nan=0.0, posinf=0.0, neginf=0.0)


def build_non_edge_index_tensor(
    feature_order: list[str],
    parameter_kpi_truth: set[tuple[str, str]],
    device: torch.device,
    focus_kpis: set[str] | None = None,
) -> torch.Tensor:
    feature_index = {name: idx for idx, name in enumerate(feature_order)}
    candidate_pairs = sorted(
        (parameter, kpi)
        for parameter, kpi in (candidate_parameter_kpi_universe(feature_order) - parameter_kpi_truth)
        if not focus_kpis or kpi in focus_kpis
    )
    if not candidate_pairs:
        return torch.empty((0, 2), dtype=torch.long, device=device)
    return torch.tensor(
        [[feature_index[parameter], feature_index[kpi]] for parameter, kpi in candidate_pairs],
        dtype=torch.long,
        device=device,
    )


def build_true_edge_index_tensor(
    feature_order: list[str],
    parameter_kpi_truth: set[tuple[str, str]],
    device: torch.device,
    focus_kpis: set[str] | None = None,
) -> torch.Tensor:
    feature_index = {name: idx for idx, name in enumerate(feature_order)}
    candidate_pairs = sorted(
        (parameter, kpi)
        for parameter, kpi in parameter_kpi_truth
        if not focus_kpis or kpi in focus_kpis
    )
    if not candidate_pairs:
        return torch.empty((0, 2), dtype=torch.long, device=device)
    return torch.tensor(
        [[feature_index[parameter], feature_index[kpi]] for parameter, kpi in candidate_pairs],
        dtype=torch.long,
        device=device,
    )


def false_positive_penalty_loss(
    reconstructed: torch.Tensor,
    non_edge_indices: torch.Tensor,
    margin: float,
) -> torch.Tensor:
    if non_edge_indices.numel() == 0:
        return reconstructed.new_tensor(0.0)
    correlation = correlation_tensor_from_reconstruction(reconstructed)
    pair_scores = correlation[non_edge_indices[:, 0], non_edge_indices[:, 1]].abs()
    penalty = F.relu(pair_scores - margin)
    return penalty.mean()


def true_positive_reward_loss(
    reconstructed: torch.Tensor,
    true_edge_indices: torch.Tensor,
    margin: float,
) -> torch.Tensor:
    if true_edge_indices.numel() == 0:
        return reconstructed.new_tensor(0.0)
    correlation = correlation_tensor_from_reconstruction(reconstructed)
    pair_scores = correlation[true_edge_indices[:, 0], true_edge_indices[:, 1]].abs()
    reward_gap = F.relu(margin - pair_scores)
    return reward_gap.mean()


def hard_positive_threshold_loss(
    reconstructed: torch.Tensor,
    true_edge_indices: torch.Tensor,
    threshold: float,
) -> torch.Tensor:
    if true_edge_indices.numel() == 0:
        return reconstructed.new_tensor(0.0)
    correlation = correlation_tensor_from_reconstruction(reconstructed)
    pair_scores = correlation[true_edge_indices[:, 0], true_edge_indices[:, 1]].abs()
    threshold_gap = F.relu(threshold - pair_scores)
    return threshold_gap.mean()


def hard_negative_threshold_loss(
    reconstructed: torch.Tensor,
    non_edge_indices: torch.Tensor,
    threshold: float,
) -> torch.Tensor:
    if non_edge_indices.numel() == 0:
        return reconstructed.new_tensor(0.0)
    correlation = correlation_tensor_from_reconstruction(reconstructed)
    pair_scores = correlation[non_edge_indices[:, 0], non_edge_indices[:, 1]].abs()
    threshold_gap = F.relu(pair_scores - threshold)
    return threshold_gap.mean()


def selection_score(
    args: argparse.Namespace,
    selected_metrics: dict[str, Any],
) -> tuple[float, float, float, float]:
    parameter_f1 = float(selected_metrics["parameter_kpi_metrics"]["f1"])
    indirect_f1 = float(selected_metrics["conflict_metrics"]["indirect"]["f1"])
    implicit_f1 = float(selected_metrics["conflict_metrics"]["implicit"]["f1"])
    if args.selection_mode == "composite":
        weighted = (
            args.selection_weight_parameter * parameter_f1
            + args.selection_weight_indirect * indirect_f1
            + args.selection_weight_implicit * implicit_f1
        )
        return (weighted, parameter_f1, indirect_f1, implicit_f1)
    return (parameter_f1, implicit_f1, indirect_f1, 0.0)


class DenseGraphSAGELayer(nn.Module):
    def __init__(self, in_dim: int, out_dim: int) -> None:
        super().__init__()
        self.self_linear = nn.Linear(in_dim, out_dim)
        self.neigh_linear = nn.Linear(in_dim, out_dim)

    def forward(self, x: torch.Tensor, support: torch.Tensor) -> torch.Tensor:
        degree = support.sum(dim=1, keepdim=True).clamp(min=1.0)
        neighborhood = support @ x / degree
        return self.self_linear(x) + self.neigh_linear(neighborhood)


class TemporalGraphSAGEAutoencoder(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int, embed_dim: int, dropout: float) -> None:
        super().__init__()
        self.encoder1 = DenseGraphSAGELayer(in_dim, hidden_dim)
        self.encoder2 = DenseGraphSAGELayer(hidden_dim, embed_dim)
        self.decoder = DenseGraphSAGELayer(embed_dim, in_dim)
        self.dropout = dropout

    def forward(self, x: torch.Tensor, support: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = self.encoder1(x, support)
        hidden = F.relu(hidden)
        hidden = F.dropout(hidden, p=self.dropout, training=self.training)
        embedding = self.encoder2(hidden, support)
        embedding = F.relu(embedding)
        embedding = F.dropout(embedding, p=self.dropout, training=self.training)
        reconstruction = self.decoder(embedding, support)
        return embedding, reconstruction


def candidate_parameter_kpi_universe(feature_order: list[str]) -> set[tuple[str, str]]:
    parameters, kpis = split_feature_groups(feature_order)
    return {(parameter, kpi) for parameter in parameters for kpi in kpis}


def serializable_edges(edges: set[tuple[str, str]]) -> list[list[str]]:
    return [list(edge) for edge in sorted(edges)]


def save_csv_matrix(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def evaluate_checkpoint(
    reconstructed_raw: torch.Tensor,
    feature_order: list[str],
    app_parameter_truth: set[tuple[str, str]],
    app_kpi_truth: set[tuple[str, str]],
    parameter_kpi_truth: set[tuple[str, str]],
    thresholds: list[float],
) -> dict[str, Any]:
    correlation = correlation_from_reconstruction(reconstructed_raw)
    pk_universe = candidate_parameter_kpi_universe(feature_order)
    pair_universe = conflict_pair_universe(app_parameter_truth, app_kpi_truth)
    truth_full = full_graph_edges(app_parameter_truth, app_kpi_truth, parameter_kpi_truth)
    truth_labels = label_conflicts(app_parameter_truth, app_kpi_truth, parameter_kpi_truth)

    threshold_payload: dict[str, Any] = {}
    for threshold in [None, *thresholds]:
        label = "no_threshold" if threshold is None else f"{threshold:.2f}"
        parameter_kpi_pred = edge_index_by_threshold(correlation, feature_order, threshold)
        full_pred = full_graph_edges(app_parameter_truth, app_kpi_truth, parameter_kpi_pred)
        predicted_labels = label_conflicts(app_parameter_truth, app_kpi_truth, parameter_kpi_pred)
        threshold_payload[label] = {
            "threshold": threshold,
            "parameter_kpi_metrics": compute_binary_metrics(parameter_kpi_pred, parameter_kpi_truth, pk_universe),
            "full_graph_metrics": compute_binary_metrics(full_pred, truth_full, truth_full | pk_universe),
            "conflict_metrics": conflict_metrics(predicted_labels, truth_labels, pair_universe),
            "predicted_parameter_kpi_edges": serializable_edges(parameter_kpi_pred),
            "predicted_conflicts": {
                key: serializable_edges(value) for key, value in predicted_labels.items()
            },
        }

    return {
        "correlation_matrix": correlation,
        "threshold_metrics": threshold_payload,
        "truth_conflicts": {key: serializable_edges(value) for key, value in truth_labels.items()},
        "truth_parameter_kpi_edges": serializable_edges(parameter_kpi_truth),
    }


def main() -> None:
    args = parse_args()
    set_seed(args.seed)

    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.exists():
        raise SystemExit(f"dataset dir not found: {dataset_dir}")

    timeseries_path = dataset_dir / "timeseries.csv"
    metadata_path = dataset_dir / "article00_metadata.json"
    graph_path = dataset_dir / "article00_graph_reference.json"

    if not timeseries_path.exists() or not metadata_path.exists() or not graph_path.exists():
        raise SystemExit(
            "article00 dataset dir must contain timeseries.csv, article00_metadata.json, and article00_graph_reference.json"
        )

    output_dir = Path(args.output_dir) if args.output_dir else (DEFAULT_OUTPUT_ROOT / dataset_dir.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv_rows(timeseries_path)
    metadata = load_json(metadata_path)
    graph = load_json(graph_path)
    feature_order = feature_order_from_metadata(metadata)
    raw_features = build_feature_tensor(rows, feature_order)
    features, feature_mean, feature_std = normalize_features(raw_features)
    support = build_temporal_support(features.shape[0], radius=args.temporal_radius, decay=args.temporal_decay)
    epochs = parse_epoch_list(args.epochs)
    thresholds = parse_threshold_list(args.thresholds)
    hard_positive_focus_kpis = set(parse_csv_list(args.hard_positive_focus_kpis))
    hard_negative_focus_kpis = set(parse_csv_list(args.hard_negative_focus_kpis))
    app_parameter_truth, app_kpi_truth, parameter_kpi_truth = known_edge_sets(graph)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    features = features.to(device)
    support = support.to(device)
    non_edge_indices = build_non_edge_index_tensor(feature_order, parameter_kpi_truth, device)
    hard_negative_indices = build_non_edge_index_tensor(
        feature_order,
        parameter_kpi_truth,
        device,
        focus_kpis=hard_negative_focus_kpis if hard_negative_focus_kpis else None,
    )
    true_edge_indices = build_true_edge_index_tensor(feature_order, parameter_kpi_truth, device)
    hard_positive_indices = build_true_edge_index_tensor(
        feature_order,
        parameter_kpi_truth,
        device,
        focus_kpis=hard_positive_focus_kpis if hard_positive_focus_kpis else None,
    )

    model = TemporalGraphSAGEAutoencoder(
        in_dim=features.shape[1],
        hidden_dim=args.hidden_dim,
        embed_dim=args.embed_dim,
        dropout=args.dropout,
    ).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
    )
    criterion = nn.MSELoss()
    max_epoch = max(epochs)
    checkpoints = set(epochs)
    history = []
    best_record = None
    started_at = time.time()

    for epoch in range(1, max_epoch + 1):
        model.train()
        optimizer.zero_grad()
        embedding, reconstruction = model(features, support)
        reconstruction_loss = criterion(reconstruction, features)
        fp_penalty = false_positive_penalty_loss(reconstruction, non_edge_indices, args.fp_penalty_margin)
        tp_reward = true_positive_reward_loss(reconstruction, true_edge_indices, args.tp_reward_margin)
        hard_positive = hard_positive_threshold_loss(
            reconstruction,
            hard_positive_indices,
            args.hard_positive_threshold,
        )
        hard_negative = hard_negative_threshold_loss(
            reconstruction,
            hard_negative_indices,
            args.hard_negative_threshold,
        )
        total_loss = (
            reconstruction_loss
            + (args.fp_penalty_weight * fp_penalty)
            + (args.tp_reward_weight * tp_reward)
            + (args.hard_positive_weight * hard_positive)
            + (args.hard_negative_weight * hard_negative)
        )
        total_loss.backward()
        optimizer.step()

        if epoch not in checkpoints:
            continue

        model.eval()
        with torch.no_grad():
            embedding, reconstruction = model(features, support)
            reconstruction_cpu = reconstruction.detach().cpu()
            reconstructed_raw = (reconstruction_cpu * feature_std) + feature_mean
            evaluation = evaluate_checkpoint(
                reconstructed_raw,
                feature_order,
                app_parameter_truth,
                app_kpi_truth,
                parameter_kpi_truth,
                thresholds,
            )
            selected_label = f"{args.selection_threshold:.2f}"
            selected_metrics = evaluation["threshold_metrics"].get(selected_label)
            if selected_metrics is None:
                raise SystemExit(
                    f"selection threshold {args.selection_threshold} is not present in the requested thresholds {thresholds}"
                )
            snapshot = {
                "epoch": epoch,
                "loss": round(float(total_loss.item()), 6),
                "reconstruction_loss": round(float(reconstruction_loss.item()), 6),
                "fp_penalty_loss": round(float(fp_penalty.item()), 6),
                "tp_reward_loss": round(float(tp_reward.item()), 6),
                "hard_positive_loss": round(float(hard_positive.item()), 6),
                "hard_negative_loss": round(float(hard_negative.item()), 6),
                "selection_threshold": args.selection_threshold,
                "selected_parameter_kpi_f1": selected_metrics["parameter_kpi_metrics"]["f1"],
                "selected_indirect_f1": selected_metrics["conflict_metrics"]["indirect"]["f1"],
                "selected_implicit_f1": selected_metrics["conflict_metrics"]["implicit"]["f1"],
                "selection_mode": args.selection_mode,
                "threshold_metrics": evaluation["threshold_metrics"],
            }
            history.append(snapshot)
            torch.save(
                {
                    "state_dict": model.state_dict(),
                    "feature_order": feature_order,
                    "hidden_dim": args.hidden_dim,
                    "embed_dim": args.embed_dim,
                    "dropout": args.dropout,
                    "epoch": epoch,
                    "temporal_radius": args.temporal_radius,
                    "temporal_decay": args.temporal_decay,
                },
                checkpoint_dir / f"epoch_{epoch}.pt",
            )

            candidate_score = selection_score(args, selected_metrics)
            if best_record is None or candidate_score > best_record["score"]:
                best_record = {
                    "score": candidate_score,
                    "epoch": epoch,
                    "loss": round(float(total_loss.item()), 6),
                    "reconstruction_loss": round(float(reconstruction_loss.item()), 6),
                    "fp_penalty_loss": round(float(fp_penalty.item()), 6),
                    "tp_reward_loss": round(float(tp_reward.item()), 6),
                    "hard_positive_loss": round(float(hard_positive.item()), 6),
                    "hard_negative_loss": round(float(hard_negative.item()), 6),
                    "embedding": embedding.detach().cpu(),
                    "reconstruction": reconstruction_cpu,
                    "reconstructed_raw": reconstructed_raw,
                    "evaluation": evaluation,
                }

    if best_record is None:
        raise SystemExit("no checkpoint was evaluated")

    best_label = f"{args.selection_threshold:.2f}"
    best_threshold_payload = best_record["evaluation"]["threshold_metrics"][best_label]
    latent_path = output_dir / "latent_embeddings.csv"
    reconstructed_path = output_dir / "reconstructed_features.csv"
    correlation_path = output_dir / "correlation_matrix.json"
    adjacency_path = output_dir / "reconstructed_adjacency.json"
    conflicts_path = output_dir / "conflict_labels.json"
    summary_path = output_dir / "training_summary.json"

    latent_rows = []
    best_embedding = best_record["embedding"]
    for time_index, vector in enumerate(best_embedding.tolist()):
        latent_rows.append([time_index, *[round(float(value), 6) for value in vector]])
    save_csv_matrix(latent_path, ["time_index", *[f"z{index}" for index in range(best_embedding.shape[1])]], latent_rows)

    reconstructed_rows = []
    reconstructed_raw = best_record["reconstructed_raw"]
    for time_index, vector in enumerate(reconstructed_raw.tolist()):
        reconstructed_rows.append([time_index, *[round(float(value), 6) for value in vector]])
    save_csv_matrix(reconstructed_path, ["time_index", *feature_order], reconstructed_rows)

    best_no_threshold = best_record["evaluation"]["threshold_metrics"]["no_threshold"]
    predicted_parameter_kpi_edges = {
        tuple(edge) for edge in best_threshold_payload["predicted_parameter_kpi_edges"]
    }
    full_predicted_edges = full_graph_edges(app_parameter_truth, app_kpi_truth, predicted_parameter_kpi_edges)
    full_truth_edges = full_graph_edges(app_parameter_truth, app_kpi_truth, parameter_kpi_truth)

    with correlation_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.article00_correlation_matrix.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "best_epoch": best_record["epoch"],
                "feature_order": feature_order,
                "selection_threshold": args.selection_threshold,
                "matrix": best_record["evaluation"]["correlation_matrix"],
            },
            handle,
            indent=2,
        )

    with adjacency_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.article00_reconstructed_adjacency.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "best_epoch": best_record["epoch"],
                "selection_threshold": args.selection_threshold,
                "parameter_kpi_truth_edges": serializable_edges(parameter_kpi_truth),
                "parameter_kpi_predicted_edges": serializable_edges(predicted_parameter_kpi_edges),
                "full_truth_edges": serializable_edges(full_truth_edges),
                "full_predicted_edges": serializable_edges(full_predicted_edges),
                "no_threshold_metrics": best_no_threshold,
                "selected_threshold_metrics": best_threshold_payload,
            },
            handle,
            indent=2,
        )

    with conflicts_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "greenran.article00_conflict_labels.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "best_epoch": best_record["epoch"],
                "truth": best_record["evaluation"]["truth_conflicts"],
                "predicted": best_threshold_payload["predicted_conflicts"],
                "metrics": best_threshold_payload["conflict_metrics"],
            },
            handle,
            indent=2,
        )

    summary = {
        "schema": "greenran.article00_training_summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_temporal_graphsage",
        "dataset_dir": str(dataset_dir),
        "timeseries_path": str(timeseries_path),
        "metadata_path": str(metadata_path),
        "graph_reference_path": str(graph_path),
        "output_dir": str(output_dir),
        "device": str(device),
        "samples": len(rows),
        "feature_order": feature_order,
        "parameter_count": len([feature for feature in feature_order if feature.startswith("P")]),
        "kpi_count": len([feature for feature in feature_order if feature.startswith("K")]),
        "temporal_edges": int(max(len(rows) - 1, 0)),
        "node_count": len(graph.get("nodes", [])),
        "edge_count": len(graph.get("edges", [])),
        "epochs_requested": epochs,
        "thresholds_requested": thresholds,
        "selection_threshold": args.selection_threshold,
        "hidden_dim": args.hidden_dim,
        "embed_dim": args.embed_dim,
        "dropout": args.dropout,
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "temporal_radius": args.temporal_radius,
        "temporal_decay": args.temporal_decay,
        "fp_penalty_weight": args.fp_penalty_weight,
        "fp_penalty_margin": args.fp_penalty_margin,
        "tp_reward_weight": args.tp_reward_weight,
        "tp_reward_margin": args.tp_reward_margin,
        "hard_positive_weight": args.hard_positive_weight,
        "hard_positive_threshold": args.hard_positive_threshold,
        "hard_positive_focus_kpis": sorted(hard_positive_focus_kpis),
        "hard_negative_weight": args.hard_negative_weight,
        "hard_negative_threshold": args.hard_negative_threshold,
        "hard_negative_focus_kpis": sorted(hard_negative_focus_kpis),
        "selection_mode": args.selection_mode,
        "selection_weight_parameter": args.selection_weight_parameter,
        "selection_weight_indirect": args.selection_weight_indirect,
        "selection_weight_implicit": args.selection_weight_implicit,
        "seed": args.seed,
        "elapsed_seconds": round(time.time() - started_at, 3),
        "best_epoch": best_record["epoch"],
        "best_loss": best_record["loss"],
        "best_reconstruction_loss": best_record["reconstruction_loss"],
        "best_fp_penalty_loss": best_record["fp_penalty_loss"],
        "best_tp_reward_loss": best_record["tp_reward_loss"],
        "best_hard_positive_loss": best_record["hard_positive_loss"],
        "best_hard_negative_loss": best_record["hard_negative_loss"],
        "best_parameter_kpi_metrics": best_threshold_payload["parameter_kpi_metrics"],
        "best_full_graph_metrics": best_threshold_payload["full_graph_metrics"],
        "best_conflict_metrics": best_threshold_payload["conflict_metrics"],
        "history": history,
        "artifacts": {
            "latent_embeddings_csv": str(latent_path),
            "reconstructed_features_csv": str(reconstructed_path),
            "correlation_matrix_json": str(correlation_path),
            "reconstructed_adjacency_json": str(adjacency_path),
            "conflict_labels_json": str(conflicts_path),
        },
        "notes": [
            "Experimental isolated implementation of the article00 temporal GraphSAGE track.",
            "No GreenRAN runtime integration is performed here.",
        ],
    }
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    print(f"Article00 temporal training summary written to: {summary_path}")


if __name__ == "__main__":
    main()
