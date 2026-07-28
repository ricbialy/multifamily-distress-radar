from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from distress_radar.cli import build_parser, main
from distress_radar.domain.property import CanonicalProperty
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.pilot import _migrate_pilot


class PilotDispositionCliTests(unittest.TestCase):
    def test_pilot_run_command_reports_complete_result(self) -> None:
        result = SimpleNamespace(
            run_id="run-1",
            matrix_sha256="sha",
            accepted_rows=20,
            rejected_rows=0,
            database_counts={"recommendations": 20},
            output_files=(Path("acquisition_brief.md"),),
        )
        output = StringIO()
        with patch("distress_radar.pilot.run_pilot", return_value=result):
            with redirect_stdout(output):
                main(
                    [
                        "pilot-run",
                        "--matrix",
                        "matrix.csv",
                        "--db",
                        "pilot.sqlite",
                        "--output-dir",
                        "outputs",
                    ]
                )
        self.assertEqual(json.loads(output.getvalue())["accepted_rows"], 20)

    def test_pilot_verify_command_reports_same_and_next_day_counts(self) -> None:
        result = SimpleNamespace(
            status="REAL-PILOT-02: PASS",
            gates=(SimpleNamespace(gate="G0", status="PASS", evidence="real input"),),
            first_run_counts={"recommendations": 20},
            second_run_counts={"recommendations": 40},
            next_day_run_counts={"recommendations": 60},
            controlled_change={"status_changes_detected": 1},
            source_statuses={"hialeah_tyler_energov": "healthy"},
            output_path=Path("acceptance_gates.json"),
        )
        output = StringIO()
        with patch(
            "distress_radar.pilot_verification.verify_real_pilot",
            return_value=result,
        ):
            with redirect_stdout(output):
                main(
                    [
                        "pilot-verify",
                        "--matrix",
                        "matrix.csv",
                        "--db",
                        "pilot.sqlite",
                        "--output-dir",
                        "outputs",
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "REAL-PILOT-02: PASS")
        self.assertEqual(payload["next_day_run_counts"]["recommendations"], 60)

    def test_parser_exposes_pilot_disposition_command(self) -> None:
        args = build_parser().parse_args(
            [
                "pilot-disposition",
                "--db",
                "pilot.sqlite",
                "--property-id",
                "property-1",
                "--disposition",
                "dismiss",
            ]
        )
        self.assertEqual(args.command, "pilot-disposition")
        self.assertEqual(args.disposition, "dismiss")

    def test_cli_records_disposition_against_latest_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "pilot.sqlite"
            with IntelligenceStore(database) as store:
                _migrate_pilot(store)
                store.upsert_property(
                    CanonicalProperty(
                        "property-1",
                        "0400000000001",
                        "100 TEST AVE",
                        "Hialeah",
                        "Miami-Dade",
                    )
                )
                store.connection.execute(
                    """
                    INSERT INTO recommendations (
                        recommendation_id,property_id,generated_at,action,
                        scores_json,explanation_json,pilot_run_id,content_hash,change_type
                    ) VALUES ('r1','property-1','2026-07-24','watch','{}','{}',NULL,'hash-1','new')
                    """
                )
                store.connection.commit()
            output = StringIO()
            with redirect_stdout(output):
                main(
                    [
                        "pilot-disposition",
                        "--db",
                        str(database),
                        "--property-id",
                        "property-1",
                        "--disposition",
                        "dismiss",
                        "--notes",
                        "Reviewed by analyst",
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["property_id"], "property-1")
            self.assertEqual(payload["baseline_content_hash"], "hash-1")
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    "SELECT disposition,notes FROM human_dispositions"
                ).fetchone()
            self.assertEqual(tuple(row), ("dismiss", "Reviewed by analyst"))


if __name__ == "__main__":
    unittest.main()
