from pathlib import Path

from src.tasam_sla_floor import (
    CONTRACT,
    load_state,
    new_state,
    observe_window,
    persist_state,
    project_power,
)


def _healthy(state, start, count):
    current = state
    observations = []
    for offset in range(count):
        current, observation = observe_window(
            current,
            window_id=start + offset,
            native_control_sequence=100 + start + offset,
            healthy=True,
        )
        observations.append(observation)
    return current, observations


def test_three_healthy_windows_descend_from_100_to_90_then_80():
    state, first = _healthy(new_state(), 1, 3)
    assert first[-1]["classification"] == "healthy_descend"
    assert state["floor_percent_by_cell"] == {"2": 90, "3": 90, "4": 90}
    state, second = _healthy(state, 4, 3)
    assert second[-1]["classification"] == "healthy_descend"
    assert state["floor_percent_by_cell"] == {"2": 80, "3": 80, "4": 80}


def test_unhealthy_window_restores_all_cells_and_duplicate_is_invalid():
    state, _ = _healthy(new_state(), 1, 3)
    state, observation = observe_window(
        state,
        window_id=4,
        native_control_sequence=104,
        healthy=False,
        reason="imsi_17_vehicle_loss",
    )
    assert observation["classification"] == "restore_full_power"
    assert state["floor_percent_by_cell"] == {"2": 100, "3": 100, "4": 100}


def test_invalid_evidence_is_classified_separately_and_restores_full_power():
    state, _ = _healthy(new_state(), 1, 3)
    state, observation = observe_window(
        state,
        window_id=4,
        native_control_sequence=104,
        healthy=False,
        metric_invalid=True,
        reason="proxy_or_missing_pdcp",
    )
    assert observation["classification"] == "metric_invalid"
    assert state["floor_percent_by_cell"] == {"2": 100, "3": 100, "4": 100}
    state, duplicate = observe_window(
        state, window_id=4, native_control_sequence=104, healthy=True
    )
    assert duplicate["classification"] == "metric_invalid"
    assert state["floor_percent_by_cell"] == {"2": 100, "3": 100, "4": 100}


def test_projection_respects_floor_upper_cap_and_probe():
    state, _ = _healthy(new_state(), 1, 3)
    selected, evidence = project_power({2: 40, 3: 100, 4: 92}, state)
    assert selected == {2: 90, 3: 90, 4: 92}
    assert evidence["action_kind"] == "sla_floor_probe"
    state, _ = _healthy(state, 4, 3)
    selected, evidence = project_power({2: 100, 3: 100, 4: 100}, state)
    assert selected == {2: 80, 3: 80, 4: 80}
    assert evidence["action_kind"] == "sla_floor_probe"


def test_state_survives_restart_without_counting_window_twice(tmp_path: Path):
    path = tmp_path / "sla_floor_state.json"
    state, _ = _healthy(new_state(), 1, 2)
    persist_state(path, state)
    resumed = load_state(path)
    resumed, observation = observe_window(
        resumed, window_id=3, native_control_sequence=103, healthy=True
    )
    assert observation["classification"] == "healthy_descend"
    resumed, duplicate = observe_window(
        resumed, window_id=3, native_control_sequence=103, healthy=True
    )
    assert duplicate["classification"] == "metric_invalid"
    assert resumed["floor_percent_by_cell"] == {"2": 100, "3": 100, "4": 100}
    assert resumed["contract"] == CONTRACT
