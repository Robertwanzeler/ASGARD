import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from status_tasam_offline_growth import (  # noqa: E402
    compute_status_payload,
    render_status,
)


def _write_db(db_path: Path, ext: int, dec: int, glob: int, du: int) -> None:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("create table extended_metrics (id integer primary key)")
    cur.execute("create table decisions_history (id integer primary key)")
    cur.execute("create table marl_global_state_history (id integer primary key)")
    cur.execute("create table marl_du_state_history (id integer primary key)")
    cur.executemany("insert into extended_metrics default values", [()] * ext)
    cur.executemany("insert into decisions_history default values", [()] * dec)
    cur.executemany("insert into marl_global_state_history default values", [()] * glob)
    cur.executemany("insert into marl_du_state_history default values", [()] * du)
    conn.commit()
    conn.close()


def _write_round(root: Path, idx: int, transitions: int, *, complete: bool = True, db_counts=(10, 11, 11, 33)) -> Path:
    round_dir = root / f"round_{idx:04d}"
    trace_dir = round_dir / "state" / "ns3_traces"
    export_dir = round_dir / "export"
    trace_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

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

    _write_db(round_dir / "state" / "rapp_data_lake.db", *db_counts)

    if complete:
        trace_jsonl = export_dir / "tasam_article_trace.jsonl"
        trace_jsonl.write_text(
            "".join('{"idx": %d}\n' % item for item in range(transitions)),
            encoding="utf-8",
        )
        (export_dir / "tasam_article_export_summary.json").write_text(
            json.dumps(
                {
                    "written_transitions": transitions,
                    "candidate_snapshots": transitions + 1,
                    "decision_counts": {"BLOCKED": transitions if idx == 1 else 0, "ALLOWED": 0 if idx == 1 else transitions},
                    "stage_counts": {"baseline_healthy": transitions},
                    "collection_event_profile": "drl_balanced_blocked_v1" if idx == 1 else "drl_balanced_borderline_v1",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return round_dir


class TestStatusTASAMOfflineGrowth(unittest.TestCase):
    def test_payload_for_completed_collection_uses_consolidated_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_round(root, 1, 248, db_counts=(249, 250, 250, 750))
            _write_round(root, 2, 216, db_counts=(218, 219, 219, 648))
            (root / "tasam_article_trace.jsonl").write_text(
                "".join('{"idx": %d}\n' % item for item in range(464)),
                encoding="utf-8",
            )
            (root / "offline_growth_summary.json").write_text(
                json.dumps(
                    {
                        "target_transitions": 500,
                        "collection_phase": "borderline_phase",
                        "active_collection_event_profile": "drl_balanced_borderline_v1",
                        "decision_targets": {"BLOCKED": 1000, "ALLOWED": 1000, "CONDITIONAL": 1000},
                        "decision_counts_current": {"BLOCKED": 248, "ALLOWED": 216, "CONDITIONAL": 0},
                        "decision_targets_remaining": {"BLOCKED": 752, "ALLOWED": 784, "CONDITIONAL": 1000},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "offline_collection_summary.json").write_text(
                json.dumps(
                    {
                        "written_transitions_total": 464,
                        "decision_counts_total": {"BLOCKED": 248, "ALLOWED": 216, "CONDITIONAL": 0},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = compute_status_payload(root, ps_entries=[])

            self.assertFalse(payload["collecting"])
            self.assertEqual(payload["phase"], "parada")
            self.assertEqual(payload["consolidated_transitions"], 464)
            self.assertEqual(payload["remaining_transitions"], 36)
            self.assertEqual(payload["latest_complete_round"]["round_id"], "round_0002")
            self.assertIsNone(payload["active_round"])
            text = render_status(payload)
            self.assertIn("consolidado:     464", text)
            self.assertIn("rodada ativa:    --", text)
            self.assertIn("fase controle:   borderline_phase", text)
            self.assertIn("BLOCKED:         248 / 1.000", text)

    def test_payload_detects_incomplete_active_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_round(root, 1, 248, db_counts=(249, 250, 250, 750))
            _write_round(root, 2, 0, complete=False, db_counts=(48, 49, 49, 147))
            (root / "tasam_article_trace.jsonl").write_text(
                "".join('{"idx": %d}\n' % item for item in range(248)),
                encoding="utf-8",
            )
            (root / "offline_growth_summary.json").write_text(
                json.dumps({"target_transitions": 500}, indent=2) + "\n",
                encoding="utf-8",
            )

            ps_entries = [
                {
                    "pid": "1234",
                    "etimes": "30",
                    "cpu": "99.0",
                    "mem": "1.2",
                    "args": "ns3.42-Energy_saving_with_cell_utilization_scenario-default",
                },
                {
                    "pid": "1235",
                    "etimes": "28",
                    "cpu": "0.1",
                    "mem": "0.2",
                    "args": "python3 run_tasam_article_offline_growth.py --output-root " + str(root),
                },
            ]

            payload = compute_status_payload(root, ps_entries=ps_entries)

            self.assertTrue(payload["collecting"])
            self.assertEqual(payload["phase"], "coletando")
            self.assertEqual(payload["active_round"]["round_id"], "round_0002")
            self.assertEqual(payload["reference_round"]["round_id"], "round_0002")
            self.assertEqual(payload["consolidated_transitions"], 248)
            self.assertEqual(payload["remaining_transitions"], 252)
            text = render_status(payload)
            self.assertIn("status:          ATIVA", text)
            self.assertIn("round_0002", text)

    def test_payload_uses_effective_counts_when_reference_root_is_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_round(root, 1, 220, db_counts=(221, 222, 222, 666))
            (root / "tasam_article_trace.jsonl").write_text(
                "".join('{"idx": %d}\n' % item for item in range(220)),
                encoding="utf-8",
            )
            (root / "offline_growth_summary.json").write_text(
                json.dumps(
                    {
                        "target_transitions": 3000,
                        "collection_phase": "allowed_phase",
                        "active_collection_event_profile": "drl_allowed_only_v1",
                        "reference_root": "/tmp/reference_run",
                        "reference_transitions": 2800,
                        "reference_decision_counts": {"BLOCKED": 1300, "ALLOWED": 7, "CONDITIONAL": 1000},
                        "local_transitions_current": 220,
                        "current_transitions": 3020,
                        "decision_targets": {"BLOCKED": 1000, "ALLOWED": 1000, "CONDITIONAL": 1000},
                        "local_decision_counts_current": {"BLOCKED": 0, "ALLOWED": 220, "CONDITIONAL": 0},
                        "decision_counts_current": {"BLOCKED": 1300, "ALLOWED": 227, "CONDITIONAL": 1000},
                        "effective_decision_counts_current": {"BLOCKED": 1300, "ALLOWED": 227, "CONDITIONAL": 1000},
                        "decision_targets_remaining": {"BLOCKED": 0, "ALLOWED": 773, "CONDITIONAL": 0},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "offline_collection_summary.json").write_text(
                json.dumps(
                    {
                        "written_transitions_total": 220,
                        "decision_counts_total": {"BLOCKED": 0, "ALLOWED": 220, "CONDITIONAL": 0},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = compute_status_payload(root, ps_entries=[])

            self.assertEqual(payload["consolidated_transitions"], 3020)
            self.assertEqual(payload["local_consolidated_transitions"], 220)
            self.assertEqual(payload["reference_transitions"], 2800)
            self.assertEqual(payload["decision_counts"]["ALLOWED"], 227)
            text = render_status(payload)
            self.assertIn("fase controle:   allowed_phase", text)
            self.assertIn("perfil:          drl_allowed_only_v1", text)
            self.assertIn("run local:       220", text)
            self.assertIn("referencia:      2.800", text)
            self.assertIn("ALLOWED:         227 / 1.000  falta=773", text)


if __name__ == "__main__":
    unittest.main()
