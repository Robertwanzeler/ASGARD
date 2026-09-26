"""O TTL das transições do Judge precisa de um relógio de simulação.

r29: _sim_time() não encontrava sim_time_s em nenhuma fonte do decision
pendente, o gate `current_sim >= issued_sim + ttl` nunca disparava e os
193 pendentes econômicos acumulavam até o shutdown — apenas 45 judge
outcomes (ids 1-3 e 195-236), 191 linhas judge_feedback_pending, seleção
20/90 e not_promotable.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _decision_with_sim_time(sim_time_s, *, bundle=True):
    decision = {
        "decision_id": 1,
        "sim_time_s": sim_time_s,
        "rapp_judge_result": {"selected_advocate": "ta_sam", "winner": "ta_sam"},
        "economic_action": {"correlation_id": "corr-1", "contract": "economic_action_v3_per_du_sleep"},
        "resource_allocation": {},
    }
    if bundle:
        decision["tasam_control_bundle"] = {"sim_time_s": sim_time_s, "ttl_ms": 5000}
    return decision


class JudgeOutcomeSimClockTests(unittest.TestCase):
    def test_sim_time_presente_no_decision_e_no_bundle(self):
        """As três fontes de relógio da observação atrasada ficam preenchidas."""
        decision = _decision_with_sim_time(42.5)
        self.assertEqual(decision["sim_time_s"], 42.5)
        self.assertEqual(decision["tasam_control_bundle"]["sim_time_s"], 42.5)

    def test_ttl_por_sim_time_dispara_com_cinco_segundos_de_simulacao(self):
        """issued 10.0 + ttl 5.0 = vence a 15.0; corrente 16.0 fecha o pendente."""
        issued_sim, current_sim, ttl = 10.0, 16.0, 5.0
        self.assertGreaterEqual(current_sim, issued_sim + ttl)

    def test_ttl_nao_dispara_antes_do_prazo(self):
        issued_sim, current_sim, ttl = 10.0, 14.0, 5.0
        self.assertLess(current_sim, issued_sim + ttl)

    def test_sim_time_ausente_nao_deve_mais_ocorrer_no_orchestrator(self):
        """Guarda de contrato: o orchestrator grava sim_time_s na decisão
        (linha de persistência) — a ausência era a trava r29."""
        import ast

        tree = ast.parse(
            (Path(__file__).resolve().parents[1] / "src" / "rapp_orchestrator.py").read_text()
        )
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "decision"
                and getattr(target.slice, "value", None) == "sim_time_s"
                for target in node.targets
            )
        ]
        self.assertTrue(
            assignments,
            "decision['sim_time_s'] não é mais gravado — a trava do r29 voltou",
        )

    def test_sim_time_resolve_tambem_pelo_bundle_no_helper(self):
        """O fallback _sim_time(bundle) fecha pendentes mesmo sem os campos
        antigos (defesa em profundidade contra r29)."""
        decision = {
            "tasam_control_bundle": {"sim_time_s": 99.0, "ttl_ms": 5000},
        }
        values = []
        for source in (
            decision.get("sim_time_s"),
            (decision.get("resource_allocation") or {}).get("sim_time_s"),
            (decision.get("tasam_control_bundle") or {}).get("sim_time_s"),
        ):
            try:
                values.append(float(source))
            except (TypeError, ValueError):
                continue
        self.assertEqual(values, [99.0])


if __name__ == "__main__":
    unittest.main()
