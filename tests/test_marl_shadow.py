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


if __name__ == '__main__':
    unittest.main()
