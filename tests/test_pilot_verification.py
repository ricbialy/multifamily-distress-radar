from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from distress_radar.pilot_verification import (
    _active_intersection_count,
    _controlled_change_is_isolated,
    _controlled_copy,
    _evidence_value_supports_action,
    _run_tests,
    verify_real_pilot,
)


class PilotVerificationTests(unittest.TestCase):
    def test_verification_harness_runs_tests_and_compile_check(self) -> None:
        test_process = SimpleNamespace(
            returncode=0,
            stdout="Ran 124 tests in 1.0s\n\nOK",
            stderr="",
        )
        compile_process = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch(
            "distress_radar.pilot_verification.subprocess.run",
            side_effect=(test_process, compile_process),
        ) as run:
            passed, count, output = _run_tests(Path("/repository"))
        self.assertTrue(passed)
        self.assertEqual(count, 124)
        self.assertIn("OK", output)
        self.assertEqual(run.call_count, 2)

    def test_verification_harness_refuses_to_overwrite_acceptance_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "existing.sqlite"
            database.touch()
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                verify_real_pilot(
                    matrix_path=root / "matrix.csv",
                    database_path=database,
                    output_dir=root / "outputs",
                    municipality="hialeah",
                )

    def test_controlled_copy_returns_the_exact_mutated_mls_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Agent Single Line - COM.csv"
            destination = root / "controlled" / source.name
            source.write_text(
                "MLS # Link,St,Address\n"
                "A12057467,A,1440 SW 4th St\n"
                "A12056557,A,934 Meridian Ave\n",
                encoding="utf-8",
            )

            description, mls_number = _controlled_copy(source, destination)

            self.assertEqual(mls_number, "A12057467")
            self.assertIn("A12057467", description)
            self.assertIn(
                "A12057467,W,1440 SW 4th St", destination.read_text()
            )

    def test_g5_counts_distinct_confirmed_current_run_intersections(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE property_signals (
                signal_id TEXT, property_id TEXT, signal_type TEXT,
                status TEXT, confirmation_status TEXT, pilot_run_id TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO property_signals VALUES (?,?,?,?,?,?)",
            [
                ("s1", "p1", "off_market_live_code_case", "active", "confirmed", "run-2"),
                ("s2", "p1", "off_market_live_code_case", "active", "confirmed", "run-2"),
                ("s3", "p2", "off_market_live_code_case", "active", "last_known", "run-1"),
                ("s4", "p3", "off_market_live_code_case", "resolved", "resolved", "run-2"),
            ],
        )
        self.assertEqual(_active_intersection_count(connection, "run-2"), 1)

    def test_g7_checks_evidence_values_not_only_identifiers(self) -> None:
        self.assertTrue(
            _evidence_value_supports_action(
                "human_municipal_review",
                "municipal_code_case",
                {
                    "case_number": "C-1",
                    "currently_active": True,
                    "enforcement_stage": "special_master",
                    "substantive_hazard": "permit",
                },
            )
        )
        self.assertFalse(
            _evidence_value_supports_action(
                "human_municipal_review",
                "municipal_code_case",
                {"case_number": "C-1", "currently_active": False},
            )
        )
        self.assertTrue(
            _evidence_value_supports_action(
                "verify_identity",
                "validated_address",
                None,
                value_type="unknown",
                metadata={"reason": "no_exact_county_match"},
            )
        )
        self.assertFalse(
            _evidence_value_supports_action(
                "verify_identity",
                "validated_address",
                None,
                value_type="unknown",
                metadata={},
            )
        )

    def test_g10_requires_exact_target_and_zero_unrelated_changes(self) -> None:
        changes = [
            {
                "mls_number": "A12057467",
                "change_type": "status_change",
                "before": "A",
                "after": "W",
            }
        ]
        self.assertTrue(
            _controlled_change_is_isolated(
                changes,
                target_mls="A12057467",
                materially_changed_property_ids={"property-target"},
                target_property_id="property-target",
            )
        )
        self.assertFalse(
            _controlled_change_is_isolated(
                changes
                + [
                    {
                        "mls_number": "A12056557",
                        "change_type": "price_change",
                        "before": 1,
                        "after": 2,
                    }
                ],
                target_mls="A12057467",
                materially_changed_property_ids={"property-target"},
                target_property_id="property-target",
            )
        )


if __name__ == "__main__":
    unittest.main()
