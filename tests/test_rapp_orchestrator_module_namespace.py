import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rapp_orchestrator
from energy_calibration import sleep_state_power_by_cell_w, sleep_state_power_w


class RappOrchestratorModuleNamespaceTests(unittest.TestCase):
    """Nomes usados pelo ramo de energia precisam existir no namespace do módulo.

    A campanha r26 (asgard) crashou na 1ª decisão com
    ``NameError: name 'sleep_state_power_w' is not defined`` em
    _observe_one_pending_judge_outcome: o call site existia, mas o import do
    módulo só trazia sleep_state_power_by_cell_w.  Nenhum teste exercitava o
    ramo (só roda no treino econômico), então o suíte ficava verde até o
    braço de treino morrer em produção.
    """

    def test_sleep_state_power_w_esta_no_namespace_do_orchestrator(self):
        self.assertIs(rapp_orchestrator.sleep_state_power_w, sleep_state_power_w)

    def test_ambas_variantes_de_sleep_state_estao_vinculadas(self):
        for nome in ("sleep_state_power_w", "sleep_state_power_by_cell_w"):
            with self.subTest(nome=nome):
                funcao = getattr(rapp_orchestrator, nome, None)
                self.assertTrue(callable(funcao), f"{nome} ausente/nao chamavel")

    def test_sleep_state_power_w_funcao_importada_avalia_estado_calibrado(self):
        calibration = {
            "sleep_states": {"2": {"idle_w": 10.0, "dynamic_w": 5.0}},
        }
        self.assertEqual(
            sleep_state_power_w(calibration, active_cells=2, power_percent=100.0),
            15.0,
        )


if __name__ == "__main__":
    unittest.main()
