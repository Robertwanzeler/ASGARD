import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from src.rapp_marl_shadow import MARLShadowRuntimeEvaluator, build_shadow_comparison


class TestMARLShadow(unittest.TestCase):
    def test_shadow_evaluator_produces_non_actuating_recommendation(self):
        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow', 'stability_window': 2})
        marl_state = {
            'topology_id': 'greenran_fixed_marl_v1',
            'du_states': [
                {'du_id': 'du1', 'primary_slice': 'eMBB', 'state_vector': [0.8, 0.2, 0.1, 0.3, 0.8, 0.1, 0.1, 0.7, 0.6, 0.8]},
                {'du_id': 'du2', 'primary_slice': 'mMTC', 'state_vector': [0.4, 0.6, 0.2, 0.3, 0.2, 0.7, 0.1, 0.5, 0.4, 0.7]},
                {'du_id': 'du3', 'primary_slice': 'URLLC', 'state_vector': [0.3, 0.2, 0.9, 0.4, 0.1, 0.2, 0.7, 0.6, 0.5, 0.6]},
            ],
            'slice_state': {
                'eMBB': {'qos_pressure': 0.8},
                'mMTC': {'qos_pressure': 0.6},
                'URLLC': {'qos_pressure': 0.9},
            },
        }
        resource_snapshot = {
            'usable_budget': 0.95,
            'resource_budget': 1.0,
            'd_ran': 0.9,
            'd_ai': 0.4,
            'r_ran': 0.7,
            'r_ai': 0.25,
        }
        out = evaluator.evaluate(marl_state, resource_snapshot=resource_snapshot)
        self.assertTrue(out['enabled'])
        self.assertTrue(out['available'])
        self.assertEqual(out['mode'], 'shadow_only')
        self.assertEqual(len(out['du_recommendations']), 3)
        self.assertIn('shadow_r_ran', out)
        self.assertIn('delta_r_ran_vs_live', out)
        self.assertIn('comparison', out)
        self.assertIn('advisor', out)
        self.assertEqual(out['advisor']['mode'], 'shadow')
        self.assertIn('resource_advice', out['advisor'])
        self.assertIn('energy_advice', out['advisor'])
        self.assertIn('du_contributions', out['advisor'])
        self.assertFalse(out['advisor']['would_influence'])
        self.assertIn('score_delta', out['comparison'])
        self.assertIn('recommend_shadow', out['comparison'])

    def test_shadow_advisor_marks_recommendation_stable_after_window(self):
        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow', 'stability_window': 2, 'min_confidence': 0.0})
        marl_state = {
            'topology_id': 'greenran_fixed_marl_v1',
            'du_states': [
                {'du_id': 'du1', 'primary_slice': 'eMBB', 'state_vector': [0.8, 0.2, 0.1, 0.3, 0.8, 0.1, 0.1, 0.7, 0.6, 0.8]},
                {'du_id': 'du2', 'primary_slice': 'mMTC', 'state_vector': [0.4, 0.6, 0.2, 0.3, 0.2, 0.7, 0.1, 0.5, 0.4, 0.7]},
                {'du_id': 'du3', 'primary_slice': 'URLLC', 'state_vector': [0.3, 0.2, 0.9, 0.4, 0.1, 0.2, 0.7, 0.6, 0.5, 0.6]},
            ],
            'slice_state': {
                'eMBB': {'qos_pressure': 0.8},
                'mMTC': {'qos_pressure': 0.6},
                'URLLC': {'qos_pressure': 0.9},
            },
        }
        resource_snapshot = {
            'usable_budget': 0.95,
            'resource_budget': 1.0,
            'd_ran': 0.9,
            'd_ai': 0.4,
            'r_ran': 0.7,
            'r_ai': 0.25,
        }
        first = evaluator.evaluate(marl_state, resource_snapshot=resource_snapshot)
        second = evaluator.evaluate(marl_state, resource_snapshot=resource_snapshot)
        self.assertFalse(first['advisor']['stability']['stable'])
        self.assertTrue(second['advisor']['stability']['stable'])
        self.assertEqual(second['advisor']['stability']['window'], 2)

    def test_build_shadow_comparison_detects_score_improvement(self):
        resource_snapshot = {
            'usable_budget': 0.95,
            'resource_budget': 1.0,
            'd_ran': 0.9,
            'd_ai': 0.15,
            'r_ran': 0.62,
            'r_ai': 0.33,
        }
        marl_shadow = {
            'available': True,
            'source': 'checkpoint',
            'checkpoint_readiness': 'control_candidate',
            'shadow_r_ran': 0.78,
            'shadow_r_ai': 0.17,
        }
        comparison = build_shadow_comparison(resource_snapshot, marl_shadow)
        self.assertGreater(comparison['score_delta'], 0.0)
        self.assertGreaterEqual(comparison['shadow_ran_completion_est'], comparison['live_ran_completion_est'])
        self.assertIn('recommend_shadow', comparison)

    def test_causal_score_includes_calibrated_energy_and_resources(self):
        comparison = build_shadow_comparison(
            {
                'usable_budget': 1.0,
                'd_ran': 0.5,
                'd_ai': 0.3,
                'r_ran': 0.6,
                'r_ai': 0.4,
                'live_power_percent': 100,
                'live_ru_count': 1,
                'live_mmwave_count': 1,
            },
            {
                'available': True,
                'source': 'checkpoint',
                'checkpoint_readiness': 'control_candidate',
                'shadow_r_ran': 0.5,
                'shadow_r_ai': 0.3,
                'shadow_power_percent': 60,
            },
        )
        self.assertTrue(comparison['energy_valid'])
        self.assertTrue(comparison['resource_valid'])
        self.assertGreater(comparison['energy_saving_fraction'], 0.0)
        self.assertGreater(comparison['resource_saving_fraction'], 0.0)
        self.assertGreater(comparison['causal_score_delta'], comparison['score_delta'])

    def test_causal_score_fails_closed_without_energy_observation(self):
        comparison = build_shadow_comparison(
            {'usable_budget': 1.0, 'r_ran': 0.6, 'r_ai': 0.4, 'd_ran': 0.5, 'd_ai': 0.3},
            {
                'available': True,
                'source': 'checkpoint',
                'checkpoint_readiness': 'control_candidate',
                'shadow_r_ran': 0.5,
                'shadow_r_ai': 0.3,
                'shadow_power_percent': 60,
            },
        )
        self.assertFalse(comparison['energy_valid'])
        self.assertEqual(comparison['causal_score_delta'], 0.0)

    def test_economic_allocation_head_reads_batched_output_after_batch_strip(self):
        torch = __import__('torch')

        class EconomicHead:
            def __call__(self, states, temporal=None):
                return torch.tensor([[0.40, 0.60, 0.72]], dtype=torch.float32)

        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow'})
        evaluator._torch = torch
        evaluator._allocation_head = EconomicHead()
        evaluator.checkpoint_meta = {'global_state_dim': 2}

        advice = evaluator._allocation_head_advice(
            {'global_state': {'state_vector': [0.1, 0.2]}},
            {},
        )

        self.assertTrue(advice['total_budget_head_enabled'])
        self.assertEqual(advice['predicted_total_budget_fraction'], 0.72)

    def test_online_economic_bootstrap_explores_only_safe_power_states(self):
        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow'})
        with mock.patch.dict(os.environ, {'GREENRAN_TASAM_ECONOMIC_BOOTSTRAP_POWER': '25'}, clear=False):
            conditional = evaluator._power_advice({}, 'CONDITIONAL', {})
            blocked = evaluator._power_advice({}, 'BLOCKED', {})
        self.assertEqual(conditional['power_percent'], 25.0)
        self.assertEqual(conditional['source'], 'economic_bootstrap_exploration')
        self.assertEqual(blocked['power_percent'], 100.0)
        self.assertEqual(blocked['source'], 'safety_envelope')

    def test_causal_exploration_is_seed_stable_and_respects_safety_bands(self):
        state = {'global_state': {'state_vector': [0.21, 0.34, 0.55]}}
        actor = {
            'enabled': True,
            'power_percent': 100.0,
            'source': 'global_energy_infra_actor',
            'global_budget': {
                'power_percent_by_cell': {'2': 100.0, '3': 100.0, '4': 100.0},
            },
        }
        with mock.patch.dict(os.environ, {
            'GREENRAN_TASAM_CAUSAL_EXPLORATION': '1',
            'GREENRAN_TASAM_EXPLORATION_SEED': '43',
        }, clear=False):
            first = MARLShadowRuntimeEvaluator({'mode': 'shadow'})._apply_causal_economic_exploration(
                actor, 'ALLOWED', state
            )
            second = MARLShadowRuntimeEvaluator({'mode': 'shadow'})._apply_causal_economic_exploration(
                actor, 'ALLOWED', state
            )
            conditional = MARLShadowRuntimeEvaluator({'mode': 'shadow'})._apply_causal_economic_exploration(
                actor, 'CONDITIONAL', state
            )
        assert first == second
        assert first['action_origin'] in {'checkpoint_actor', 'causal_epsilon_exploration'}
        assert all(25.0 <= value <= 100.0 and value % 5 == 0
                   for value in first['power_percent_by_cell'].values())
        assert all(60.0 <= value <= 100.0 and value % 5 == 0
                   for value in conditional['power_percent_by_cell'].values())

    def test_armd_envelope_bounds_tasam_inside_policy_floors(self):
        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow'})
        shadow = {
            'available': True,
            'source': 'checkpoint',
            'shadow_r_ran': 0.10,
            'shadow_r_ai': 0.80,
            'advisor': {'resource_advice': {'suggested_r_ran': 0.10, 'suggested_r_ai': 0.80}},
        }
        armd = {
            'available': True,
            'valid': True,
            'proposal_id': 'armd:test',
            'verdict': 'CONDITIONAL',
            'resource_allocation': {
                'usable_budget': 0.90,
                'floor_total_ran': 0.20,
                'floor_total_ai': 0.30,
                'r_ran': 0.20,
                'r_ai': 0.70,
            },
        }
        out = evaluator.apply_armd_policy_envelope(
            shadow,
            armd,
            {'usable_budget': 0.90, 'r_ran': 0.30, 'r_ai': 0.60, 'd_ran': 0.5, 'd_ai': 0.5},
        )
        self.assertTrue(out['armd_policy_envelope']['applied'])
        self.assertGreaterEqual(out['shadow_r_ran'], 0.20)
        self.assertGreaterEqual(out['shadow_r_ai'], 0.30)
        self.assertLessEqual(out['shadow_r_ran'] + out['shadow_r_ai'], 0.90 + 1e-6)
        self.assertTrue(out['advisor']['resource_advice']['armd_envelope_applied'])

    def test_armd_critical_veto_fixes_tasam_proposal(self):
        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow'})
        out = evaluator.apply_armd_policy_envelope(
            {'available': True, 'shadow_r_ran': 0.20, 'shadow_r_ai': 0.80, 'advisor': {}},
            {
                'available': True,
                'valid': True,
                'proposal_id': 'armd:critical',
                'verdict': 'BLOCKED',
                'safety_veto': True,
                'resource_allocation': {
                    'usable_budget': 1.0,
                    'floor_total_ran': 0.50,
                    'floor_total_ai': 0.15,
                    'r_ran': 0.85,
                    'r_ai': 0.15,
                },
            },
            {'usable_budget': 1.0, 'r_ran': 0.50, 'r_ai': 0.50, 'd_ran': 0.5, 'd_ai': 0.5},
        )
        self.assertEqual(out['shadow_r_ran'], 0.85)
        self.assertEqual(out['shadow_r_ai'], 0.15)
        self.assertEqual(out['advisor']['energy_advice']['decision'], 'BLOCKED')
        self.assertEqual(out['advisor']['energy_advice']['action'], 'FULL_POWER')

    def test_armd_envelope_preserves_full_shadow_result_for_runtime(self):
        evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow'})
        shadow = {
            'enabled': True,
            'available': True,
            'valid': True,
            'source': 'checkpoint',
            'policy_id': 'ta_sam:test',
            'shadow_r_ran': 0.55,
            'shadow_r_ai': 0.35,
            'advisor': {
                'enabled': True,
                'mode': 'assistant_only_control',
                'source': 'checkpoint',
                'valid': True,
                'resource_advice': {'enabled': True},
            },
        }
        armd = {
            'available': True,
            'valid': True,
            'proposal_id': 'armd:runtime',
            'verdict': 'CONDITIONAL',
            'resource_allocation': {
                'usable_budget': 0.90,
                'floor_total_ran': 0.20,
                'floor_total_ai': 0.20,
            },
        }
        out = evaluator.apply_armd_policy_envelope(
            shadow, armd,
            {'usable_budget': 0.90, 'r_ran': 0.50, 'r_ai': 0.40, 'd_ran': 0.5, 'd_ai': 0.5},
        )
        self.assertTrue(out['enabled'])
        self.assertEqual(out['source'], 'checkpoint')
        self.assertTrue(out['advisor']['enabled'])
        self.assertEqual(out['advisor']['source'], 'checkpoint')
        self.assertTrue(out['advisor']['armd_policy_envelope']['applied'])

    def test_shadow_evaluator_loads_article_sac_checkpoint_format(self):
        torch = __import__('torch')
        nn = torch.nn

        class ArticleActor(nn.Module):
            def __init__(self):
                super().__init__()
                self.backbone = nn.Sequential(
                    nn.Linear(10, 8),
                    nn.Tanh(),
                    nn.Linear(8, 6),
                    nn.Tanh(),
                )
                self.concentration_head = nn.Linear(6, 3)

            def forward(self, x):
                raw = self.concentration_head(self.backbone(x))
                alpha = torch.nn.functional.softplus(raw) + 1e-3
                return alpha / torch.clamp(alpha.sum(dim=-1, keepdim=True), min=1e-9)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / 'article_checkpoint'
            run_dir.mkdir(parents=True, exist_ok=True)

            actors = nn.ModuleList([ArticleActor() for _ in range(3)])
            torch.save(actors.state_dict(), run_dir / 'tasam_marl_actors.pt')
            (run_dir / 'tasam_marl_checkpoint_meta.json').write_text(
                json.dumps(
                    {
                        'du_count': 3,
                        'du_state_dim': 10,
                        'actor_hidden_dims': [8, 6],
                        'activation': 'tanh',
                        'action_layout': ['eMBB', 'mMTC', 'URLLC'],
                        'trainer_backend': 'article_sac',
                    }
                ),
                encoding='utf-8',
            )
            eval_manifest = tmp_path / 'tasam_eval.json'
            eval_manifest.write_text(
                json.dumps(
                    {
                        'best_run': {
                            'run_dir': str(run_dir),
                            'readiness': 'shadow_ready',
                            'promote_shadow': True,
                        }
                    }
                ),
                encoding='utf-8',
            )
            gate_manifest = tmp_path / 'gate.json'
            gate_manifest.write_text(json.dumps({'gate': {'status': 'shadow_only'}}), encoding='utf-8')

            marl_state = {
                'topology_id': 'greenran_fixed_marl_v1',
                'du_states': [
                    {'du_id': 'du1', 'primary_slice': 'eMBB', 'state_vector': [0.8, 0.2, 0.1, 0.3, 0.8, 0.1, 0.1, 0.7, 0.6, 0.8]},
                    {'du_id': 'du2', 'primary_slice': 'mMTC', 'state_vector': [0.4, 0.6, 0.2, 0.3, 0.2, 0.7, 0.1, 0.5, 0.4, 0.7]},
                    {'du_id': 'du3', 'primary_slice': 'URLLC', 'state_vector': [0.3, 0.2, 0.9, 0.4, 0.1, 0.2, 0.7, 0.6, 0.5, 0.6]},
                ],
                'slice_state': {
                    'eMBB': {'qos_pressure': 0.8},
                    'mMTC': {'qos_pressure': 0.6},
                    'URLLC': {'qos_pressure': 0.9},
                },
            }
            resource_snapshot = {
                'usable_budget': 0.95,
                'resource_budget': 1.0,
                'd_ran': 0.9,
                'd_ai': 0.4,
                'r_ran': 0.7,
                'r_ai': 0.25,
            }

            with mock.patch.dict(
                os.environ,
                {
                    'GREENRAN_TASAM_EVAL_MANIFEST': str(eval_manifest),
                    'GREENRAN_MARL_CONTROL_GATE_MANIFEST': str(gate_manifest),
                },
                clear=False,
            ):
                evaluator = MARLShadowRuntimeEvaluator({'mode': 'shadow'})
                out = evaluator.evaluate(marl_state, resource_snapshot=resource_snapshot)

            self.assertEqual(out['source'], 'checkpoint')
            self.assertEqual(out['checkpoint_run_dir'], str(run_dir))
            self.assertTrue(out['available'])
            self.assertTrue(out['policy_id'].endswith(':article_checkpoint'))
            self.assertEqual(out['checkpoint_error'], '')

    def test_explicit_checkpoint_bootstraps_without_a_valid_manifest(self):
        torch = __import__('torch')
        nn = torch.nn

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / 'explicit_checkpoint'
            run_dir.mkdir(parents=True, exist_ok=True)
            class LegacyActor(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.net = nn.Sequential(
                        nn.Linear(10, 8), nn.ReLU(), nn.Linear(8, 3), nn.Sigmoid()
                    )

            actors = nn.ModuleList([LegacyActor() for _ in range(3)])
            torch.save(actors.state_dict(), run_dir / 'tasam_marl_actors.pt')
            (run_dir / 'tasam_marl_checkpoint_meta.json').write_text(
                json.dumps({
                    'du_count': 3,
                    'du_state_dim': 10,
                    'actor_hidden_dims': [8],
                    'activation': 'relu',
                    'action_layout': ['eMBB', 'mMTC', 'URLLC'],
                }),
                encoding='utf-8',
            )
            missing_manifest = tmp_path / 'missing_eval.json'
            gate_manifest = tmp_path / 'gate.json'
            gate_manifest.write_text(json.dumps({'gate': {'status': 'control_trial'}}), encoding='utf-8')

            with mock.patch.dict(
                os.environ,
                {
                    'GREENRAN_TASAM_CHECKPOINT': str(run_dir),
                    'GREENRAN_TASAM_CHECKPOINT_READINESS': 'control_candidate',
                    'GREENRAN_TASAM_EVAL_MANIFEST': str(missing_manifest),
                    'GREENRAN_MARL_CONTROL_GATE_MANIFEST': str(gate_manifest),
                    'GREENRAN_TASAM_REQUIRE_CHECKPOINT': '1',
                },
                clear=False,
            ):
                evaluator = MARLShadowRuntimeEvaluator({'mode': 'assistant_only_control'})

            self.assertTrue(evaluator.checkpoint_loaded)
            self.assertEqual(evaluator.checkpoint_source, 'checkpoint')
            self.assertEqual(evaluator.checkpoint_run_dir, str(run_dir))

    def test_required_checkpoint_never_falls_back_to_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            missing_manifest = tmp_path / 'missing_eval.json'
            missing_checkpoint = tmp_path / 'missing_checkpoint'
            with mock.patch.dict(
                os.environ,
                {
                    'GREENRAN_TASAM_CHECKPOINT': str(missing_checkpoint),
                    'GREENRAN_TASAM_EVAL_MANIFEST': str(missing_manifest),
                    'GREENRAN_MARL_CONTROL_GATE_MANIFEST': str(tmp_path / 'gate.json'),
                    'GREENRAN_TASAM_REQUIRE_CHECKPOINT': '1',
                },
                clear=False,
            ):
                evaluator = MARLShadowRuntimeEvaluator({'mode': 'assistant_only_control'})
                out = evaluator.evaluate({
                    'du_states': [{
                        'du_id': 'du1',
                        'state_vector': [0.2] * 10,
                    }],
                    'slice_state': {},
                }, resource_snapshot={'usable_budget': 1.0})

            self.assertFalse(evaluator.checkpoint_loaded)
            self.assertFalse(out['available'])
            self.assertEqual(out['source'], 'unavailable')
            self.assertTrue(out['required_checkpoint'])


if __name__ == '__main__':
    unittest.main()
