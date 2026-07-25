from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from distress_radar.cli import build_parser, main
from distress_radar.domain.property import CanonicalProperty
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.pilot import _migrate_pilot


class PilotDispositionCliTests(unittest.TestCase):
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
