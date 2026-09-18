"""Validated local job contract for the autonomous GreenRAN dispatcher."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / "runs"
JOB_SCHEMA = "greenran.agent_campaign_job.v1"
JOB_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,95}$")
ALLOWED_KINDS = {
    "smoke",
    "performance_benchmark",
    "actuation_smoke",
    "shadow",
    "vehicle_feasibility",
    "online_economic",
    "causal_pair_frozen",
    "asgard_frozen_evaluation",
    "energy_calibration",
    "simulation_revaluation",
}
SEEDS = {45, 46, 47}
_OPTIMIZED_NS3_BINARY = PROJECT_ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build-v9-optimized/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-optimized"
NS3_BINARY = (
    _OPTIMIZED_NS3_BINARY
    if _OPTIMIZED_NS3_BINARY.is_file()
    else PROJECT_ROOT / "ns-O-RAN-flexric/mmwave-LENA-oran/build/scratch/ns3.42-Energy_saving_with_cell_utilization_scenario-default"
)
ONLINE_PROFILES = {
    "tasam_training_balanced_v3",
    "tasam_training_balanced_v4_v2x",
    "tasam_training_balanced_v4_v2x_gbr",
    "tasam_training_balanced_v4_v2x_gbr_priority",
    "tasam_training_economic_v4",
}
VEHICLE_SAFE_PROFILE = "tasam_training_economic_vehicle_safe_v1"
V2X_PROFILE = "tasam_training_balanced_v4_v2x"
V2X_GBR_PROFILE = "tasam_training_balanced_v4_v2x_gbr"
V2X_GBR_PRIORITY_PROFILE = "tasam_training_balanced_v4_v2x_gbr_priority"
ONLINE_PROFILES.add(VEHICLE_SAFE_PROFILE)
STARTUP_MIN_FREE_GIB = 15.8
RUNTIME_MIN_FREE_GIB = 10.0
ARTIFACT_BUDGET_GIB = 2.0


class JobValidationError(ValueError):
    pass


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    kind: str
    payload: dict[str, Any]
    command: list[str]
    campaign_path: Path


def _inside_project(raw: Any, label: str, *, require_exists: bool = False) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise JobValidationError(f"{label} ausente")
    candidate = Path(raw)
    path = (PROJECT_ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise JobValidationError(f"{label} fora do projeto local: {path}") from exc
    if "/run/media/" in str(path):
        raise JobValidationError(f"{label} no HD externo é proibido: {path}")
    if require_exists and not path.exists():
        raise JobValidationError(f"{label} ausente: {path}")
    return path


def _campaign_path(raw: Any, label: str = "campaign_dir") -> Path:
    path = _inside_project(raw, label)
    try:
        path.relative_to(RUNS_ROOT)
    except ValueError as exc:
        raise JobValidationError(f"{label} precisa ficar em {RUNS_ROOT}: {path}") from exc
    queue_root = RUNS_ROOT / "agent_jobs"
    if path == queue_root or queue_root in path.parents:
        raise JobValidationError(f"{label} não pode usar a fila do dispatcher: {path}")
    if path.exists() and any(path.iterdir()):
        raise JobValidationError(f"{label} já contém artefatos: {path}")
    return path


def _seed(payload: dict[str, Any]) -> int:
    value = payload.get("seed")
    if type(value) is not int or value not in SEEDS:
        raise JobValidationError(f"seed deve pertencer a {sorted(SEEDS)}")
    return value


def _checkpoint(payload: dict[str, Any], *, economic: bool = False) -> Path:
    path = _inside_project(payload.get("checkpoint"), "checkpoint", require_exists=True)
    metadata_path = path / "tasam_marl_checkpoint_meta.json"
    actors = path / "tasam_marl_actors.pt"
    if not metadata_path.is_file() or not actors.is_file():
        raise JobValidationError(f"checkpoint TA-SAM incompleto: {path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobValidationError(f"metadata do checkpoint inválido: {metadata_path}") from exc
    v10 = metadata.get("economic_action_contract") == "economic_action_v3_per_du_sleep"
    required = {
        "du_count": 3,
        "du_state_dim": 13,
        "global_state_dim": 13,
        "joint_action_dim": 14 if v10 else 12,
    }
    actual = {key: metadata.get(key) for key in required}
    if actual != required or metadata.get("uses_global_energy_infra_actor") is not True:
        raise JobValidationError(f"checkpoint incompatível: {actual}")
    if economic and v10 and metadata.get("global_action_dim") != 5:
        raise JobValidationError("checkpoint v10 precisa de global_action_dim=5")
    if economic and (
        metadata.get("allocation_head_output_dim") != 3
        or "total_budget_fraction" not in list(metadata.get("allocation_head_outputs") or [])
    ):
        raise JobValidationError("checkpoint econômico sem total_budget_fraction")
    if economic and v10 and metadata.get("economic_action_contract") != "economic_action_v3_per_du_sleep":
        raise JobValidationError("checkpoint v10 sem contrato econômico v3")
    return path


def _calibration(payload: dict[str, Any]) -> Path:
    path = _inside_project(payload.get("calibration"), "calibration", require_exists=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobValidationError(f"calibração inválida: {path}") from exc
    if not isinstance(data, dict) or not data.get("schema"):
        raise JobValidationError(f"calibração sem schema: {path}")
    return path


def _vehicle_profile_manifest(
    payload: dict[str, Any], *, expected_profile: str | None = None
) -> Path:
    path = _inside_project(payload.get("vehicle_profile_manifest"), "vehicle_profile_manifest", require_exists=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobValidationError(f"manifesto veicular inválido: {path}") from exc
    if (
        data.get("schema") != "greenran.autonomous_vehicle_feasibility.v1"
        or data.get("status") != "passed"
        or int(data.get("selected_interval_us") or 0) not in {4000, 6000, 8000, 12000, 16000}
    ):
        raise JobValidationError("manifesto veicular não foi aprovado pela linha de base protegida")
    if expected_profile is not None and data.get("profile") != expected_profile:
        raise JobValidationError(
            "manifesto veicular não corresponde ao perfil da campanha: "
            f"esperado={expected_profile} obtido={data.get('profile')}"
        )
    topology = data.get("topology") or {}
    if topology and (int(topology.get("ue_count", 20)) != 20 or int(topology.get("du_count", 3)) != 3):
        raise JobValidationError("manifesto veicular não corresponde à topologia canônica 20 UE/3 DU")
    return path


def _promoted_adaptation(payload: dict[str, Any], checkpoint: Path) -> Path:
    adaptation = _inside_project(payload.get("adaptation_dir"), "adaptation_dir", require_exists=True)
    state_path = adaptation / "online_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobValidationError(f"estado de adaptação inválido: {state_path}") from exc
    if state.get("active_checkpoint_promoted") is not True or int(state.get("promotion_count", 0) or 0) < 1:
        raise JobValidationError("checkpoint ASGARD ainda não foi promovido")
    promoted = Path(str(state.get("last_promoted_checkpoint") or "")).resolve()
    if promoted != checkpoint.resolve():
        raise JobValidationError("checkpoint não corresponde ao último checkpoint promovido")
    return adaptation


def _common_arm(
    kind: str, payload: dict[str, Any], *, decisions: int, wall_time: int,
    require_calibration: bool = False, mode: str = "combined_shadow",
    sim_time: float = 600.0, native_fidelity: bool = False,
) -> JobSpec:
    run_dir = _campaign_path(payload.get("campaign_dir"), "campaign_dir")
    checkpoint = _checkpoint(payload, economic=kind in {"online_economic", "actuation_smoke"})
    calibration = _calibration(payload) if require_calibration or payload.get("calibration") else None
    seed = _seed(payload)
    profile = payload.get("profile", "tasam_training_balanced_v3")
    if profile not in ONLINE_PROFILES:
        raise JobValidationError(f"profile não permitido: {profile}")
    vehicle_manifest = None
    if profile in {VEHICLE_SAFE_PROFILE, V2X_PROFILE, V2X_GBR_PROFILE, V2X_GBR_PRIORITY_PROFILE}:
        vehicle_manifest = _vehicle_profile_manifest(payload, expected_profile=profile)
    command = [
        sys.executable, str(PROJECT_ROOT / "scripts" / "run_tasam_online_arm.py"),
        "--mode", mode, "--run-dir", str(run_dir), "--checkpoint", str(checkpoint),
        "--seed", str(seed), "--profile", str(profile),
        "--wall-time", str(wall_time), "--sim-time", str(sim_time),
        "--decision-target", str(decisions),
        "--min-free-gib", str(STARTUP_MIN_FREE_GIB),
        "--artifact-budget-gib", str(ARTIFACT_BUDGET_GIB),
        "--artifact-min-free-gib", str(RUNTIME_MIN_FREE_GIB),
    ]
    if native_fidelity:
        command.extend(["--native-fidelity", "--performance-min-rtf", "0.016"])
    if calibration is not None:
        command.extend(["--energy-calibration", str(calibration)])
    if vehicle_manifest is not None:
        command.extend(["--vehicle-profile-manifest", str(vehicle_manifest)])
    return JobSpec(str(payload["job_id"]), kind, payload, command, run_dir)


def normalize_job(payload: Any) -> JobSpec:
    if not isinstance(payload, dict) or payload.get("schema") != JOB_SCHEMA:
        raise JobValidationError(f"schema obrigatório: {JOB_SCHEMA}")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise JobValidationError("job_id inválido")
    kind = payload.get("kind")
    if kind not in ALLOWED_KINDS:
        raise JobValidationError(f"kind não permitido: {kind}")
    # Copy only JSON values so runtime state cannot be smuggled into an object
    # received from an untrusted queue file.
    payload = json.loads(json.dumps(payload))
    # The CLI always serializes its boolean flag.  Only an affirmative
    # observe-only request is meaningful, and that mode is restricted to the
    # online economic launcher.
    if kind != "online_economic" and payload.get("observe_only") is True:
        raise JobValidationError("observe_only só é permitido em jobs online_economic")

    if kind == "smoke":
        return _common_arm(
            kind, payload, decisions=20, wall_time=3600,
            require_calibration=True, mode="combined_shadow", native_fidelity=True,
        )
    if kind == "performance_benchmark":
        return _common_arm(
            kind, payload, decisions=0, wall_time=1800, sim_time=10,
            require_calibration=True, mode="combined_shadow", native_fidelity=True,
        )
    if kind == "actuation_smoke":
        return _common_arm(
            kind, payload, decisions=300, wall_time=900,
            require_calibration=True, mode="combined_actuation_smoke", native_fidelity=True,
        )
    if kind == "shadow":
        return _common_arm(kind, payload, decisions=300, wall_time=10800, require_calibration=True, native_fidelity=True)
    if kind == "vehicle_feasibility":
        campaign = _campaign_path(payload.get("campaign_dir"))
        checkpoint = _checkpoint(payload)
        profile = payload.get("profile", VEHICLE_SAFE_PROFILE)
        if profile not in ONLINE_PROFILES:
            raise JobValidationError(f"perfil de viabilidade não permitido: {profile}")
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_tasam_vehicle_feasibility.py"),
            "--output-root", str(campaign), "--checkpoint", str(checkpoint),
            "--binary", str(NS3_BINARY), "--profile", profile,
            "--seed", str(_seed(payload)), "--decision-target", "150",
            "--wall-time", "43200",
            "--warmup-seconds", "30", "--window-seconds", "10", "--scored-windows", "30",
            "--min-tx-pdus", "500", "--min-free-gib", "10",
        ]
        return JobSpec(job_id, kind, payload, command, campaign)
    if kind == "online_economic":
        campaign = _campaign_path(payload.get("campaign_dir"))
        checkpoint = _checkpoint(payload, economic=True)
        calibration = _calibration(payload)
        checkpoint_metadata = json.loads(
            (checkpoint / "tasam_marl_checkpoint_meta.json").read_text(encoding="utf-8")
        )
        if checkpoint_metadata.get("economic_action_contract") == "economic_action_v3_per_du_sleep":
            calibration_data = json.loads(calibration.read_text(encoding="utf-8"))
            if calibration_data.get("schema") != "greenran.energy_calibration.v3":
                raise JobValidationError(
                    "checkpoint v10 exige calibração greenran.energy_calibration.v3"
                )
        seed = _seed(payload)
        profile = payload.get("profile", "tasam_training_balanced_v3")
        if profile not in ONLINE_PROFILES:
            raise JobValidationError(f"profile não permitido: {profile}")
        # A native-fidelity economic arm must start from a rApp-only vehicle
        # baseline that passed under this exact offered-load profile.  Without
        # this proof, a persistent HARD_VETO is a scenario/QoS defect rather
        # than useful DRL training data.
        vehicle_manifest = _vehicle_profile_manifest(payload, expected_profile=profile)
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_tasam_online_economic_campaign.py"),
            "--campaign-dir", str(campaign), "--checkpoint", str(checkpoint),
            "--calibration", str(calibration), "--seed", str(seed),
            "--profile", str(profile),
            "--wall-time", "43200", "--adaptation-wall-time", "43200",
            "--adaptation-decisions", "1200",
            "--adaptation-max-decisions", "1200",
            "--decisions", "300", "--shadow-min-decisions", "300",
            "--stage-window-decisions", "300", "--max-rollout-fraction", "0.50",
            "--min-economic-transitions", "180", "--min-applied-actions", "180",
            "--economic-update-min-transitions", "64",
            "--native-fidelity", "--performance-min-rtf", "0.016",
            "--require-vehicle-feasibility",
            "--vehicle-profile-manifest", str(vehicle_manifest),
        ]
        benchmark_manifest = payload.get("benchmark_manifest")
        if benchmark_manifest is not None:
            benchmark_path = _inside_project(
                benchmark_manifest, "benchmark_manifest", require_exists=True
            )
            command.extend(["--benchmark-manifest", str(benchmark_path)])
        min_free_gib = payload.get("min_free_gib", 10.0)
        if isinstance(min_free_gib, bool):
            raise JobValidationError("min_free_gib inválido")
        try:
            min_free_gib = float(min_free_gib)
        except (TypeError, ValueError) as exc:
            raise JobValidationError("min_free_gib inválido") from exc
        if min_free_gib < 10.0:
            raise JobValidationError("min_free_gib não pode ser inferior a 10 GiB")
        command.extend([
            "--min-free-gib", str(min_free_gib),
            "--startup-min-free-gib", "15.8",
        ])
        if profile == VEHICLE_SAFE_PROFILE:
            command.extend([
                "--adaptation-max-decisions", "3600", "--adaptation-wall-time", "9000",
                "--min-free-gib", "10",
            ])
        observe_only = payload.get("observe_only", False)
        if type(observe_only) is not bool:
            raise JobValidationError("observe_only deve ser booleano")
        if observe_only:
            command.append("--observe-only")
        warm_start = payload.get("warm_start", False)
        if type(warm_start) is not bool:
            raise JobValidationError("warm_start deve ser booleano")
        if warm_start:
            parent = _inside_project(
                payload.get("warm_start_parent") or payload.get("checkpoint"),
                "warm_start_parent", require_exists=True,
            )
            command.extend(["--warm-start", "--warm-start-parent", str(parent)])
            if payload.get("parent_was_promoted") is True:
                command.append("--parent-was-promoted")
        return JobSpec(job_id, kind, payload, command, campaign)
    if kind == "asgard_frozen_evaluation":
        campaign = _campaign_path(payload.get("campaign_dir"), "campaign_dir")
        checkpoint = _checkpoint(payload, economic=True)
        adaptation = _promoted_adaptation(payload, checkpoint)
        calibration = _calibration(payload)
        seed = _seed(payload)
        profile = payload.get("profile", "tasam_training_balanced_v3")
        if profile not in ONLINE_PROFILES:
            raise JobValidationError(f"profile não permitido: {profile}")
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_tasam_asgard_frozen_evaluation.py"),
            "--campaign-dir", str(campaign), "--adaptation-dir", str(adaptation),
            "--checkpoint", str(checkpoint), "--calibration", str(calibration),
            "--seed", str(seed), "--profile", str(profile),
            "--min-free-gib", "10", "--sim-time", "600", "--decisions", "300",
        ]
        return JobSpec(job_id, kind, payload, command, campaign)
    if kind == "causal_pair_frozen":
        campaign = _campaign_path(payload.get("campaign_dir"))
        checkpoint = _checkpoint(payload)
        shadow_db = _inside_project(payload.get("shadow_db"), "shadow_db", require_exists=True)
        gate = _inside_project(payload.get("control_gate"), "control_gate", require_exists=True)
        seed = _seed(payload)
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_tasam_causal_pilot.py"),
            "--execute", "--campaign-dir", str(campaign), "--checkpoint", str(checkpoint),
            "--shadow-db", str(shadow_db), "--control-gate", str(gate), "--seed", str(seed),
        ]
        return JobSpec(job_id, kind, payload, command, campaign)
    if kind == "energy_calibration":
        campaign = _campaign_path(payload.get("campaign_dir"), "campaign_dir")
        limit = payload.get("limit", 36)
        if type(limit) is not int or not 1 <= limit <= 36:
            raise JobValidationError("limit da calibração deve estar entre 1 e 36")
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_tasam_energy_calibration_sweep.py"),
            "--output-root", str(campaign), "--limit", str(limit),
        ]
        return JobSpec(job_id, kind, payload, command, campaign)

    # Simulation-only re-evaluation never starts ns-3 and uses readonly input
    # campaigns; the output is nevertheless a new directory under runs.
    campaign = _campaign_path(payload.get("campaign_dir"), "campaign_dir")
    baseline = _inside_project(payload.get("baseline"), "baseline", require_exists=True)
    treatment = _inside_project(payload.get("treatment"), "treatment", require_exists=True)
    calibration = _calibration(payload)
    seed = _seed(payload)
    output = campaign / "simulation_revaluation.json"
    command = [
        sys.executable, str(PROJECT_ROOT / "scripts" / "evaluate_tasam_causal_comparison.py"),
        "--baseline", str(baseline), "--treatment", str(treatment), "--seed", str(seed),
        "--energy-calibration", str(calibration), "--metric-scope", "simulation", "--output", str(output),
    ]
    return JobSpec(job_id, kind, payload, command, campaign)


def runtime_environment() -> dict[str, str]:
    """Provide a local-only, fail-closed environment to every dispatcher job."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GREENRAN_")}
    env.update({
        "GREENRAN_LOCAL_ONLY": "1",
        "GREENRAN_CGROUP_ENFORCE": "1",
        "GREENRAN_CGROUP_ALLOW_UNENFORCED": "0",
        "GREENRAN_REQUIRE_REAL_PDCP": "1",
        "GREENRAN_REAL_ONLY": "1",
        "GREENRAN_TASAM_EXPORT_ALLOW_PROXY": "0",
        "PYTHONPATH": os.pathsep.join((str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "drlexp" / "src"))),
    })
    return env


def new_job_id(kind: str) -> str:
    if kind not in ALLOWED_KINDS:
        raise JobValidationError(f"kind não permitido: {kind}")
    return f"{kind}-{int(time.time())}-{uuid.uuid4().hex[:10]}"


def queue_layout(root: Path) -> dict[str, Path]:
    return {name: root / name for name in ("queued", "running", "finished", "failed", "logs", "status")}


def prepare_queue(root: Path) -> dict[str, Path]:
    layout = queue_layout(root)
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return layout


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
