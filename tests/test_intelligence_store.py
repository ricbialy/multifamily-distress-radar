import sqlite3
import tempfile
import unittest
from pathlib import Path

from distress_radar.domain.evidence import EvidenceItem, FreshnessStatus, ValueType
from distress_radar.domain.property import CanonicalProperty
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.sources.base import SourceHealthState


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


if __name__ == "__main__":
    unittest.main()
