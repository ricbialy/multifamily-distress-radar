import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distress_radar.domain.evidence import EvidenceItem
from distress_radar.domain.property import CanonicalProperty
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.pilot import _migrate_pilot
from distress_radar.sources.base import CoverageState, SourceHealthState
from distress_radar.sources.mls.matrix_csv import MatrixCsvImporter
from distress_radar.watch_semantics import (
    WatchSpecification,
    WatchValidationOutcome,
)


class IntelligenceStoreTests(unittest.TestCase):
    def test_migration_creates_normalized_intelligence_tables(self) -> None:
        required = {
            "canonical_properties",
            "canonical_owners",
            "owner_aliases",
            "property_ownership",
            "source_runs",
            "source_health",
            "source_records",
            "evidence_items",
            "property_signals",
            "underwriting_runs",
            "recommendations",
            "human_decisions",
            "acquisition_outcomes",
            "ml_models",
            "ml_predictions",
            "listing_snapshots",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                rows = store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
        self.assertTrue(required.issubset({row[0] for row in rows}))

    def test_store_watch_queries_do_not_require_pilot_migration(self) -> None:
        prop = CanonicalProperty(
            property_id="property-standalone",
            folio="0123456789010",
            address="123 Main St",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.upsert_property(prop)
                outcome = store.validate_watch_specification(
                    prop.property_id,
                    None,
                    created_at="2026-07-24T12:00:00+00:00",
                    reviewed_content_hash="a" * 64,
                )
                signal_columns = {
                    row["name"]
                    for row in store.connection.execute(
                        "PRAGMA table_info(property_signals)"
                    )
                }
                observation_table = store.connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='signal_observations'
                    """
                ).fetchone()

        self.assertEqual(outcome.status, "legacy_incomplete_watch")
        self.assertTrue(
            {"pilot_run_id", "confirmation_status", "source_name"}.issubset(
                signal_columns
            )
        )
        self.assertIsNotNone(observation_table)

    def test_property_and_unknown_evidence_round_trip(self) -> None:
        prop = CanonicalProperty(
            property_id="property-1",
            folio="0123456789010",
            address="123 Main St",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        evidence = EvidenceItem.unknown(
            field="open_liens",
            source="miami_dade_clerk",
            source_record_id="run-1",
            fetched_at="2026-07-24T12:00:00+00:00",
            reason="authentication_required",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.upsert_property(prop)
                store.add_evidence("property-1", evidence)
                saved = store.property_evidence("property-1")
        self.assertEqual(len(saved), 1)
        self.assertIsNone(saved[0]["value"])
        self.assertEqual(saved[0]["value_type"], "unknown")

    def test_failed_source_run_persists_coverage_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                run_id = store.start_source_run("miami_dade_clerk")
                store.finish_source_run(
                    run_id=run_id,
                    state=SourceHealthState.AUTHENTICATION_REQUIRED,
                    records_examined=0,
                    records_changed=0,
                    error_message="credential missing",
                )
                warning = store.source_coverage_warnings()[0]
        self.assertEqual(warning["state"], "authentication_required")
        self.assertIn("credential missing", warning["error_message"])

    def test_property_source_coverage_uses_exact_truth_states(self) -> None:
        prop = CanonicalProperty(
            property_id="property-coverage",
            folio="0123456789010",
            address="123 Main St",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.upsert_property(prop)
                store.set_source_coverage(
                    property_id=prop.property_id,
                    source_name="miami_dade_property_point_view",
                    state=CoverageState.CONFIRMED_PRESENT,
                    query_scope="folio:0123456789010",
                    records_examined=1,
                    records_matched=1,
                )
                store.set_source_coverage(
                    property_id=prop.property_id,
                    source_name="hialeah_tyler_energov",
                    state=CoverageState.UNKNOWN_FAILED,
                    query_scope="folio:0123456789010",
                    records_examined=0,
                    records_matched=0,
                    error_message="timeout",
                )
                coverage = store.property_source_coverage(prop.property_id)
        self.assertEqual(
            {row["state"] for row in coverage},
            {"confirmed_present", "unknown_failed"},
        )
        failed = next(row for row in coverage if row["state"] == "unknown_failed")
        self.assertEqual(failed["error_message"], "timeout")

    def test_listing_snapshots_are_append_only_and_changes_persist(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "matrix_20.csv"
        importer = MatrixCsvImporter()
        first = importer.import_file(
            fixture, fetched_at="2026-07-23T12:00:00+00:00"
        )[0]
        second = importer.import_file(
            fixture, fetched_at="2026-07-24T12:00:00+00:00"
        )[0]
        second = dataclasses.replace(second, list_price=800_000)
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.save_listing_snapshot(first)
                changes = store.save_listing_snapshot(second)
                count = store.connection.execute(
                    "SELECT COUNT(*) FROM listing_snapshots"
                ).fetchone()[0]
                persisted = store.connection.execute(
                    "SELECT change_type FROM listing_changes"
                ).fetchall()
        self.assertEqual(count, 2)
        self.assertIn("price_change", {row[0] for row in persisted})
        self.assertIn("price_change", {change.change_type for change in changes})

    def test_unchanged_listing_is_not_duplicated_on_repeat_run(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "matrix_20.csv"
        importer = MatrixCsvImporter()
        first = importer.import_file(
            fixture, fetched_at="2026-07-23T12:00:00+00:00"
        )[0]
        repeated = dataclasses.replace(
            first, fetched_at="2026-07-24T12:00:00+00:00"
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.save_listing_snapshot(first)
                changes = store.save_listing_snapshot(repeated)
                snapshot_count = store.connection.execute(
                    "SELECT COUNT(*) FROM listing_snapshots"
                ).fetchone()[0]
                change_count = store.connection.execute(
                    "SELECT COUNT(*) FROM listing_changes"
                ).fetchone()[0]
        self.assertEqual(changes, ())
        self.assertEqual(snapshot_count, 1)
        self.assertEqual(change_count, 1)

    def test_delivery_source_url_does_not_create_material_snapshot(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "matrix_20.csv"
        importer = MatrixCsvImporter()
        first = importer.import_file(
            fixture, fetched_at="2026-07-23T12:00:00+00:00"
        )[0]
        repeated = dataclasses.replace(
            first,
            fetched_at="2026-07-24T12:00:00+00:00",
            source_url="email-attachment://renamed-export.csv",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.save_listing_snapshot(first)
                changes = store.save_listing_snapshot(repeated)
                snapshot_count = store.connection.execute(
                    "SELECT COUNT(*) FROM listing_snapshots"
                ).fetchone()[0]

        self.assertEqual(changes, ())
        self.assertEqual(snapshot_count, 1)

    def test_failed_watch_creation_rolls_back_all_disposition_writes(self) -> None:
        prop = CanonicalProperty(
            property_id="property-watch-rollback",
            folio="0123456789010",
            address="123 Main St",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        specification = WatchSpecification(
            watch_reason="Recheck after the scheduled analyst deadline",
            evidence_ids=("evidence-1",),
            trigger_type="scheduled_recheck",
            recheck_condition={
                "field": "current_time",
                "operator": "at_or_after",
                "value": "2026-07-25T12:00:00+00:00",
            },
            expected_next_action="manual_triage",
            creator="analyst:test-suite",
            recheck_at="2026-07-25T12:00:00+00:00",
        )
        validation = WatchValidationOutcome(
            status="valid",
            reason="test validation",
            evidence_states={"evidence-1": "current"},
            source_health={"matrix_csv": "healthy"},
            evidence=(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                _migrate_pilot(store)
                store.upsert_property(prop)
                prior_id = store.record_disposition(
                    prop.property_id,
                    "dismiss",
                    "2026-07-24T12:00:00+00:00",
                    baseline_content_hash="a" * 64,
                )
                with patch.object(
                    store,
                    "validate_watch_specification",
                    return_value=validation,
                ), self.assertRaisesRegex(
                    ValueError, "reviewed recommendation run"
                ):
                    store.record_disposition(
                        prop.property_id,
                        "watch",
                        "2026-07-24T13:00:00+00:00",
                        baseline_content_hash="b" * 64,
                        watch_specification=specification,
                    )
                store.connection.commit()
                rows = store.connection.execute(
                    """
                    SELECT disposition_id,disposition,active
                    FROM human_dispositions ORDER BY decided_at
                    """
                ).fetchall()

        self.assertEqual(
            [tuple(row) for row in rows],
            [(prior_id, "dismiss", 1)],
        )

    def test_human_decisions_and_outcomes_are_persisted_for_labels(self) -> None:
        prop = CanonicalProperty(
            property_id="property-1",
            folio="0123456789010",
            address="123 Main St",
            municipality="Hialeah",
            jurisdiction="Miami-Dade",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "radar.sqlite3") as store:
                store.upsert_property(prop)
                store.record_human_decision(
                    "property-1", "review_selected", "2026-07-24T12:00:00+00:00"
                )
                store.record_outcome(
                    "property-1",
                    "offer_made",
                    "2026-07-25T12:00:00+00:00",
                    amount=650_000,
                )
                labels = store.outcome_labels()
        self.assertEqual([item["outcome_type"] for item in labels], ["offer_made"])
        self.assertEqual(labels[0]["amount"], 650_000)


if __name__ == "__main__":
    unittest.main()
