from __future__ import annotations

import json
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
    _disposition_supports_action,
    _evidence_value_supports_action,
    _g5_is_valid,
    _g10_is_valid,
    _manifest_chronology,
    _manifest_commit_shas,
    _manifests_match_commit,
    _repository_commit_sha,
    _run_tests,
    verify_real_pilot,
)


class PilotVerificationTests(unittest.TestCase):
    def test_all_six_authoritative_manifests_must_match_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commit_sha = "b" * 40
            directories = (
                "first",
                "second",
                "next-day",
                "controlled-baseline",
                "controlled",
                "failed-source",
            )
            for directory in directories:
                run_directory = root / directory
                run_directory.mkdir()
                (run_directory / "run_manifest.json").write_text(
                    json.dumps(
                        {
                            "source_commit_sha": commit_sha,
                            "started_at": "2026-07-27T12:00:00+00:00",
                            "effective_at": "2026-07-28T12:00:00+00:00",
                            "completed_at": "2026-07-27T12:01:00+00:00",
                        }
                    )
                    + "\n"
                )
            manifests = _manifest_commit_shas(root)
            self.assertTrue(_manifests_match_commit(manifests, commit_sha))
            chronology = _manifest_chronology(root)
            self.assertTrue(all(run["valid"] for run in chronology.values()))
            manifests["failed-source"] = "c" * 40
            self.assertFalse(_manifests_match_commit(manifests, commit_sha))
            manifests["failed-source"] = commit_sha
            del manifests["controlled"]
            self.assertFalse(_manifests_match_commit(manifests, commit_sha))
            manifests["controlled"] = commit_sha
            manifests["extra"] = commit_sha
            self.assertFalse(_manifests_match_commit(manifests, commit_sha))
            del manifests["extra"]
            manifests["failed-source"] = None
            self.assertFalse(_manifests_match_commit(manifests, commit_sha))
            manifests["failed-source"] = []
            self.assertFalse(_manifests_match_commit(manifests, commit_sha))
            failed_manifest = root / "failed-source/run_manifest.json"
            failed_payload = json.loads(failed_manifest.read_text())
            failed_payload["completed_at"] = "2026-07-27T11:59:00+00:00"
            failed_manifest.write_text(json.dumps(failed_payload) + "\n")
            chronology = _manifest_chronology(root)
            self.assertFalse(chronology["failed-source"]["valid"])

    def test_authoritative_commit_requires_clean_full_sha(self) -> None:
        clean = SimpleNamespace(returncode=0, stdout="", stderr="")
        revision = SimpleNamespace(
            returncode=0,
            stdout="a" * 40 + "\n",
            stderr="",
        )
        with patch(
            "distress_radar.pilot_verification.subprocess.run",
            side_effect=(clean, revision),
        ) as run:
            self.assertEqual(_repository_commit_sha(Path("/repository")), "a" * 40)
        self.assertEqual(run.call_count, 2)

        dirty = SimpleNamespace(
            returncode=0,
            stdout=" M src/distress_radar/pilot.py\n",
            stderr="",
        )
        with patch(
            "distress_radar.pilot_verification.subprocess.run",
            return_value=dirty,
        ), self.assertRaisesRegex(ValueError, "clean committed working tree"):
            _repository_commit_sha(Path("/repository"))

        invalid_revision = SimpleNamespace(
            returncode=0,
            stdout="short\n",
            stderr="",
        )
        with patch(
            "distress_radar.pilot_verification.subprocess.run",
            side_effect=(clean, invalid_revision),
        ), self.assertRaisesRegex(RuntimeError, "full commit SHA"):
            _repository_commit_sha(Path("/repository"))

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

    def test_verification_harness_refuses_to_overwrite_acceptance_database(
        self,
    ) -> None:
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
            self.assertIn("A12057467,W,1440 SW 4th St", destination.read_text())

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
                (
                    "s1",
                    "p1",
                    "off_market_live_code_case",
                    "active",
                    "confirmed",
                    "run-2",
                ),
                (
                    "s2",
                    "p1",
                    "off_market_live_code_case",
                    "active",
                    "confirmed",
                    "run-2",
                ),
                (
                    "s3",
                    "p2",
                    "off_market_live_code_case",
                    "active",
                    "last_known",
                    "run-1",
                ),
                (
                    "s4",
                    "p3",
                    "off_market_live_code_case",
                    "resolved",
                    "resolved",
                    "run-2",
                ),
            ],
        )
        self.assertEqual(_active_intersection_count(connection, "run-2"), 1)
        self.assertTrue(_g5_is_valid(1, 0))
        self.assertTrue(_g5_is_valid(1, 10))
        self.assertFalse(_g5_is_valid(0, 0))
        self.assertFalse(_g5_is_valid(1, 11))

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
                "human_municipal_review",
                "municipal_code_case",
                {
                    "case_number": "C-2",
                    "currently_active": True,
                    "enforcement_stage": "lien",
                    "substantive_hazard": "cosmetic",
                },
            )
        )
        self.assertFalse(
            _evidence_value_supports_action(
                "human_municipal_review",
                "municipal_code_case",
                {
                    "case_number": "C-3",
                    "currently_active": True,
                    "enforcement_stage": "warning",
                    "substantive_hazard": "cosmetic",
                },
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
        self.assertTrue(
            _evidence_value_supports_action(
                "human_violation_review",
                "municipal_code_case",
                {
                    "case_number": "C-4",
                    "currently_active": True,
                    "enforcement_stage": "warning",
                    "substantive_hazard": "unknown_hazard",
                },
            )
        )
        self.assertTrue(
            _evidence_value_supports_action(
                "order_municipal_search",
                "municipal_code_case",
                None,
                value_type="unknown",
                metadata={"reason": "municipal_source_not_run"},
            )
        )
        self.assertTrue(
            _evidence_value_supports_action(
                "order_municipal_search",
                "municipal_code_case",
                {
                    "case_number": "C-5",
                    "currently_active": True,
                    "enrichment_state": "never_enriched",
                },
            )
        )
        self.assertFalse(
            _evidence_value_supports_action(
                "order_municipal_search",
                "municipal_code_case",
                None,
                value_type="unknown",
                metadata={},
            )
        )

    def test_g7_validates_hash_bound_active_dispositions(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE human_dispositions (
                disposition_id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                disposition TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                notes TEXT,
                baseline_content_hash TEXT NOT NULL,
                active INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO human_dispositions VALUES
            ('d1','p1','dismiss','2026-07-24T12:00:00Z',NULL,'hash-1',1)
            """
        )
        self.assertTrue(
            _disposition_supports_action(
                connection,
                property_id="p1",
                content_hash="hash-1",
                action="dismiss",
            )
        )
        self.assertFalse(
            _disposition_supports_action(
                connection,
                property_id="p1",
                content_hash="changed-hash",
                action="dismiss",
            )
        )

    def test_g10_requires_exact_target_and_zero_unrelated_changes(self) -> None:
        self.assertTrue(
            _g10_is_valid(controlled_isolated=True, status_change_count=1)
        )
        self.assertFalse(
            _g10_is_valid(controlled_isolated=False, status_change_count=1)
        )
        self.assertFalse(
            _g10_is_valid(controlled_isolated=True, status_change_count=2)
        )
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
