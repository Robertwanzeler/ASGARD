from __future__ import annotations

from src.rapp_orchestrator import DU_CELL_IDS, RappResourceOptimizer


def _optimizer() -> RappResourceOptimizer:
    optimizer = RappResourceOptimizer.__new__(RappResourceOptimizer)
    optimizer._causal_power_by_cell = {
        cell: {
            'power_percent': 100,
            'pending': False,
            'sequence': 0,
            'issued_sim_time_s': None,
            'last_scheduler_sim_time_s': None,
        }
        for cell in DU_CELL_IDS
    }
    return optimizer


def test_causal_coordinator_deduplicates_power_and_renews_scheduler(monkeypatch):
    monkeypatch.setenv('GREENRAN_TASAM_CAUSAL_COORDINATOR', '1')
    monkeypatch.setenv('GREENRAN_TASAM_SCHEDULER_RENEWAL_SECONDS', '4')
    optimizer = _optimizer()
    decision = {}
    first = optimizer._plan_causal_power_transaction(
        decision, {2: 75, 3: 75, 4: 75}, sim_time_s=1.0, failsafe=False,
    )
    assert first['changed_cells'] == [2, 3, 4]
    decision['tasam_control_sequence'] = 7
    optimizer._register_causal_power_transaction(decision, sim_time_s=1.0)
    held = optimizer._plan_causal_power_transaction(
        {}, {2: 60, 3: 60, 4: 60}, sim_time_s=2.0, failsafe=False,
    )
    assert held['send'] is False
    optimizer._close_causal_power_transaction(
        decision, economic_valid=True, critical_violation=False,
    )
    renewal = optimizer._plan_causal_power_transaction(
        {}, {2: 75, 3: 75, 4: 75}, sim_time_s=5.1, failsafe=False,
    )
    assert renewal['scheduler_renewal_only'] is True
    assert all(not item for item in renewal['apply_power_by_cell'].values())


def test_non_trainable_causal_result_releases_pending_du(monkeypatch):
    monkeypatch.setenv('GREENRAN_TASAM_CAUSAL_COORDINATOR', '1')
    optimizer = _optimizer()
    decision = {}
    plan = optimizer._plan_causal_power_transaction(
        decision, {2: 65, 3: 70, 4: 75}, sim_time_s=1.0, failsafe=False,
    )
    assert plan['changed_cells'] == [2, 3, 4]
    decision['tasam_control_sequence'] = 11
    optimizer._register_causal_power_transaction(decision, sim_time_s=1.0)
    assert all(optimizer._causal_power_by_cell[cell]['pending'] for cell in DU_CELL_IDS)

    optimizer._close_causal_power_transaction(
        decision, economic_valid=False, critical_violation=False,
    )

    assert all(not optimizer._causal_power_by_cell[cell]['pending'] for cell in DU_CELL_IDS)
    assert all(
        optimizer._causal_power_by_cell[cell]['power_percent'] == 100
        for cell in DU_CELL_IDS
    )


def test_safe_probe_forces_native_readback_at_current_power(monkeypatch):
    """The 100% probe must be observable before it can descend to 95%."""
    monkeypatch.setenv('GREENRAN_TASAM_CAUSAL_COORDINATOR', '1')
    monkeypatch.setenv(
        'GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT',
        'greenran.tasam.v2x.energy_staircase.v2_safe_probe',
    )
    monkeypatch.setenv('GREENRAN_TASAM_CAUSAL_EXPLORATION', '1')
    optimizer = _optimizer()
    decision = {'selected_assistant': 'ta_sam'}

    plan = optimizer._plan_causal_power_transaction(
        decision, {2: 100, 3: 100, 4: 100}, sim_time_s=1.0, failsafe=False,
    )

    assert plan['send'] is True
    assert plan['changed_cells'] == [2, 3, 4]
    assert plan['forced_power_readback_cells'] == [2, 3, 4]
    assert all(plan['apply_power_by_cell'].values())
    assert 'tasam_causal_exploration' not in decision


def test_safe_probe_waits_for_its_pending_native_readback(monkeypatch):
    monkeypatch.setenv('GREENRAN_TASAM_CAUSAL_COORDINATOR', '1')
    monkeypatch.setenv(
        'GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT',
        'greenran.tasam.v2x.energy_staircase.v2_safe_probe',
    )
    optimizer = _optimizer()
    for cell in DU_CELL_IDS:
        optimizer._causal_power_by_cell[cell]['pending'] = True
        optimizer._causal_power_by_cell[cell]['sequence'] = 9

    plan = optimizer._plan_causal_power_transaction(
        {'selected_assistant': 'ta_sam'}, {2: 100, 3: 100, 4: 100},
        sim_time_s=2.0, failsafe=False,
    )

    assert plan['changed_cells'] == []
    assert plan['forced_power_readback_cells'] == [2, 3, 4]
    assert not any(plan['apply_power_by_cell'].values())
    assert plan['blocked_cells'] == {
        2: 'awaiting_native_pdcp_causal_closure',
        3: 'awaiting_native_pdcp_causal_closure',
        4: 'awaiting_native_pdcp_causal_closure',
    }


def test_safe_probe_planning_does_not_clear_confirmed_state(monkeypatch):
    monkeypatch.setenv('GREENRAN_TASAM_ENERGY_STAIRCASE_CONTRACT',
                       'greenran.tasam.v2x.energy_staircase.v2_safe_probe')
    optimizer = _optimizer()
    optimizer._energy_staircase_state = {
        'contract': 'greenran.tasam.v2x.energy_staircase.v2_safe_probe',
        'healthy_required': 3,
        'trial_healthy_streak': 2,
        'next_selected_power_by_cell': {'2': 100, '3': 100, '4': 100},
        'last_processed_observation_sequence': 41,
    }
    optimizer._safe_power_floor_by_cell = lambda *_args: {2: 25, 3: 25, 4: 25}
    decision = {'selected_assistant': 'ta_sam'}
    selected, failsafe = optimizer._apply_energy_staircase(
        decision, {}, {}, {2: 25, 3: 25, 4: 25},
    )
    assert not failsafe
    assert selected == {2: 100, 3: 100, 4: 100}
    assert optimizer._energy_staircase_state['trial_healthy_streak'] == 2
