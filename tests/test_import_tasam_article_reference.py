import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from import_tasam_article_reference import build_reference_payload  # noqa: E402


REPORT = """# Relatório de Metodologia — TA-SAM vs SAC
| Número de DUs | 6 |
| Número de UEs | 200 |
| RBs por DU | 100 |
| Largura de banda | 20 MHz |
| Distância entre sites | 500 m |
| Mobilidade | 10–20 m/s |
| eMBB | 7,3 Mbps | 80% | 1024 B | ~67 |
| mMTC | 37 kbps | 5% | 64 B | ~67 |
| URLLC | 370 kbps | 50% | 32 B | ~67 |
static constexpr double INIT_ALPHA = 1.0;
static constexpr double TARGET_ENTROPY = -1.0;
static constexpr double TARGET_THPT_MMTC = 5e6;
static constexpr double ZETA = 5.0;
static constexpr double QOS_MIN = 0.1;
static constexpr uint32_t RHO_SCHEDULE_STEPS = 1200;
Time simTime = Seconds(1200);
"""

SUMMARY = """# comment
window,sac_steps,sac_alpha,sac_q0,sac_q1,sac_q2,ta_steps,ta_alpha,ta_q0,ta_q1,ta_q2,delta_q0
1-200,200,0.9930,0.1492,0.002587,0.0334,200,0.9997,0.1475,0.002592,0.0335,-1.1%
201-400,200,0.9075,0.1479,0.002689,0.0039,200,1.1003,0.1773,0.002695,0.0046,+19.9%
metric,value
sac_q0_last100,0.1571
ta_q0_last100,0.1996
delta_q0_last100,+27.0%
sac_q1_last100,0.002684
ta_q1_last100,0.002665
sac_q2_last100,0.0034
ta_q2_last100,0.0043
sac_alpha_final,0.5772
ta_alpha_final,1.5251
sac_rho_final,0.1023
ta_rho_final,0.1211
metric,value
sac_q0_global,0.1607
ta_q0_global,0.1799
delta_q0_global,+12.0%
sac_q1_global,0.002658
ta_q1_global,0.002660
sac_q2_global,0.0089
ta_q2_global,0.0098
metric,sac,ta_sam
rb_slice0_embb,200,223
rb_slice1_mmtc,201,253
rb_slice2_urllc,199,124
rb_total,600,600
metric,sac,ta_sam
catastrophic_forgetting,sim (0.186->0.155),nao (0.206->0.193)
peak_q0,0.1865 (steps 601-800),0.2066 (steps 601-800)
final_q0_trend,declinio,estavel
delta_peak_to_final,-14.3%,-6.7%
exploration_trend,alpha_decresceu (1.0->0.58),alpha_aumentou (1.0->1.53)
"""

TAIL = """step,alpha,rho,q0_avg,q1_avg,q2_avg,sl0_rb,sl1_rb,sl2_rb
963,1.4865,0.1590,0.1602,0.002358,0.0042,243,219,138
964,1.4869,0.1586,0.1764,0.003520,0.0022,202,277,121
"""


class ImportTasamArticleReferenceTests(unittest.TestCase):
    def test_build_reference_payload_parses_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "resultados_experimentos_tasam.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("resultados_experimentos_tasam/relatorio_metodologia_tasam.md", REPORT)
                archive.writestr("resultados_experimentos_tasam/resultados_experimentos_tasam.csv", SUMMARY)
                archive.writestr("resultados_experimentos_tasam/tasam_ultimos100.csv", TAIL)
                archive.writestr("resultados_experimentos_tasam/sac_ultimos100.csv", TAIL)

            payload = build_reference_payload(zip_path)

            self.assertEqual(payload["schema"], "greenran.tasam_external_article_reference.v1")
            self.assertEqual(payload["scenario"]["du_count"], 6)
            self.assertEqual(payload["scenario"]["sim_steps"], 1200)
            self.assertEqual(payload["training_method"]["final_tuning"]["alpha_init"], 1.0)
            self.assertAlmostEqual(payload["results"]["last100"]["delta_q0_pct"], 27.0, places=5)
            self.assertAlmostEqual(payload["results"]["last100"]["tasam_alpha_final"], 1.5251, places=5)
            self.assertAlmostEqual(payload["results"]["global"]["tasam_q2"], 0.0098, places=5)
            self.assertTrue(payload["derived"]["tasam_reduces_forgetting"])
            self.assertEqual(payload["tails"]["tasam"]["step_range"]["start"], 963)


if __name__ == "__main__":
    unittest.main()
