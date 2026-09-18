import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collection_event_alternator import (  # noqa: E402
    PROFILES,
    VEHICLE_ALLOWED_STABLE,
    VEHICLE_WARNING,
)
from rapp_judge import RAppJudge  # noqa: E402
from vehicle_policy_runtime import evaluate_vehicle_policy  # noqa: E402


def _observation(vehicle):
    vehicle = {
        **vehicle,
        "max_latency_ms": max(
            float(vehicle.get("ego_latency_ms", 0.0)),
            float(vehicle.get("traffic_latency_ms", 0.0)),
        ),
        "max_packet_loss_percent": max(
            float(vehicle.get("ego_packet_loss_percent", 0.0)),
            float(vehicle.get("traffic_packet_loss_percent", 0.0)),
        ),
    }
    return {
        "camera_metrics": {
            "active_cameras": 3,
            "throughput_ready": True,
            "throughput_mbps": 32.0,
            "latency_ms": 18.0,
        },
        "vehicle_metrics": {
            "available": True,
            "total_vehicles": 5,
            "ego_present": True,
            **vehicle,
        },
        "app2_metrics": {
            "delivery_success_percent": 99.1,
            "packet_loss_percent": 1.2,
            "avg_latency_ms": 118.0,
        },
        "network_health": {"cvar_us": 50000.0},
    }


def test_allowed_profile_values_are_below_warning_thresholds():
    assert VEHICLE_ALLOWED_STABLE["ego_latency_ms"] < 10.0
    assert VEHICLE_ALLOWED_STABLE["ego_packet_loss_percent"] < 0.5
    for stage_name in ("allowed_bootstrap", "allowed_stable", "allowed_recovery"):
        stage = next(stage for stage in PROFILES["tasam_training_balanced_v3"] if stage.name == stage_name)
        assert stage.vehicle["ego_latency_ms"] < 10.0
        assert stage.vehicle["ego_packet_loss_percent"] < 0.5


def test_vehicle_conditional_profile_is_not_critical():
    assert 10.0 <= VEHICLE_WARNING["ego_latency_ms"] < 20.0
    assert 0.5 <= VEHICLE_WARNING["ego_packet_loss_percent"] < 1.0
    assert evaluate_vehicle_policy({
        "available": True,
        "total_vehicles": 5,
        "ego_present": True,
        **VEHICLE_WARNING,
    })["severity"] == "warning"
    observed = RAppJudge.derive_observed_outcome(_observation(VEHICLE_WARNING))
    assert observed["correct_verdict"] == "CONDITIONAL"
    assert observed["critical_violation"] is False


def test_allowed_profile_observes_allowed_category():
    observed = RAppJudge.derive_observed_outcome(_observation(VEHICLE_ALLOWED_STABLE))
    assert observed["correct_verdict"] == "ALLOWED"
