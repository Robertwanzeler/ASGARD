import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from status_tasam_offline_live import compute_payload, render  # noqa: E402


def _write_round_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        create table extended_metrics (
            id integer primary key,
            datetime text,
            sim_time_s real,
            throughput_kbps real,
            latency_p95_us real,
            cvar_per_ue_us real,
            total_active_ues integer
        )
        """
    )
    cur.execute(
        """
        create table decisions_history (
            id integer primary key,
            datetime text,
            decision text,
            reason text
        )
        """
    )
    cur.execute("create table marl_global_state_history (id integer primary key)")
    cur.execute("create table marl_du_state_history (id integer primary key)")
    cur.execute(
        """
        insert into extended_metrics (datetime, sim_time_s, throughput_kbps, latency_p95_us, cvar_per_ue_us, total_active_ues)
        values ('2026-06-23 23:40:00', 9.0, 268449.66, 11248.9, 11249.0, 50)
        """
    )
    cur.executemany(
        "insert into decisions_history (datetime, decision, reason) values (?, ?, ?)",
        [
            ("2026-06-23 23:40:01", "CONDITIONAL", "margem protegida"),
            ("2026-06-23 23:40:02", "BLOCKED", "vehicle safety"),
            ("2026-06-23 23:40:03", "ALLOWED", "recovery"),
        ],
    )
    cur.executemany("insert into marl_global_state_history default values", [()] * 3)
    cur.executemany("insert into marl_du_state_history default values", [()] * 9)
    conn.commit()
    conn.close()


class TestStatusTASAMOfflineLive(unittest.TestCase):
    def test_payload_includes_consolidated_and_live_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "offline_collection_summary.json").write_text(
                json.dumps(
                    {
                        "written_transitions_total": 2097,
                        "decision_counts_total": {"BLOCKED": 1663, "CONDITIONAL": 433, "ALLOWED": 1},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            round_dir = root / "round_0004"
            trace_dir = round_dir / "state" / "ns3_traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "DlPdcpStats.txt",
                "DlRlcStats.txt",
                "UlPdcpStats.txt",
                "UlRlcStats.txt",
                "DlE2PdcpStats.txt",
                "DlE2RlcStats.txt",
                "UlE2PdcpStats.txt",
                "UlE2RlcStats.txt",
            ):
                (trace_dir / name).write_text("ok\n", encoding="utf-8")
            _write_round_db(round_dir / "state" / "rapp_data_lake.db")

            payload = compute_payload(root)

            self.assertEqual(payload["consolidated_transitions"], 2097)
            self.assertEqual(payload["live_round"]["round_id"], "round_0004")
            self.assertEqual(payload["live_round"]["decision_counts"]["BLOCKED"], 1)
            self.assertEqual(payload["live_round"]["decision_counts"]["CONDITIONAL"], 1)
            self.assertEqual(payload["live_round"]["decision_counts"]["ALLOWED"], 1)
            text = render(payload)
            self.assertIn("transicoes:      2.097", text)
            self.assertIn("round:           round_0004", text)
            self.assertIn("CONDITIONAL:     1", text)


if __name__ == "__main__":
    unittest.main()
