from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from distress_radar.domain.owner import CanonicalOwner
from distress_radar.domain.property import CanonicalProperty
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.models import (
    CodeCase,
    CollectionResult,
    PropertyCollectionResult,
    PropertyRecord,
)
from distress_radar.pilot import _save_owner, run_pilot


def property_record(folio: str, address: str, units: int | None) -> PropertyRecord:
    return PropertyRecord(
        city_slug="hialeah_fl",
        source_name="miami_dade_property_point_view",
        folio=folio,
        address=address,
        city="HIALEAH",
        zip_code="33010",
        owner_name="TEST OWNER LLC",
        owner_name_2=None,
        mailing_address_1=None,
        mailing_address_2=None,
        mailing_city=None,
        mailing_state=None,
        mailing_zip=None,
        dor_code="0303",
        dor_description="MULTIFAMILY 10 UNITS PLUS",
        unit_count=units,
        year_built=1970,
        floor_count=2,
        building_area=12_000,
        lot_size=15_000,
        assessed_value=1_000_000,
        last_sale_date="2020-01-01",
        last_sale_price=900_000,
        latitude=25.8,
        longitude=-80.2,
        source_url="https://example.test/county",
        fetched_at="2026-07-24T12:00:00+00:00",
    )


class FakePropertyCollector:
    def __init__(self) -> None:
        self.matrix = property_record("0400000000001", "100 TEST AVE", 12)
        self.off_market = property_record("0400000000002", "200 TEST AVE", 20)

    def lookup_address(self, address: str) -> PropertyCollectionResult:
        return PropertyCollectionResult((self.matrix,), ())

    def lookup_exact_folio(self, folio: str) -> PropertyCollectionResult:
        return PropertyCollectionResult((self.matrix,), ())

    def collect(self) -> PropertyCollectionResult:
        return PropertyCollectionResult((self.matrix, self.off_market), ())


class MismatchPropertyCollector(FakePropertyCollector):
    def __init__(self) -> None:
        super().__init__()
        self.matrix = property_record("0400000000001", "999 OTHER AVE", 12)


class UnknownUnitsPropertyCollector(FakePropertyCollector):
    def __init__(self) -> None:
        super().__init__()
        self.matrix = property_record("0400000000001", "100 TEST AVE", None)


class FolioFallbackPropertyCollector(FakePropertyCollector):
    def __init__(self) -> None:
        super().__init__()
        self.exact_folios: list[str] = []

    def lookup_address(self, address: str) -> PropertyCollectionResult:
        return PropertyCollectionResult((), ())

    def lookup_exact_folio(self, folio: str) -> PropertyCollectionResult:
        self.exact_folios.append(folio)
        return PropertyCollectionResult((self.matrix,), ())


class FakeCodeCollector:
    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        case = CodeCase(
            city_slug="hialeah_fl",
            source_name="hialeah_tyler_energov",
            source_record_id="case-1",
            case_number="CE-1",
            case_type="Unsafe Structure",
            status="Notice of Violation",
            opened_date="2026-01-01",
            closed_date=None,
            address="200 TEST AVE",
            parcel_number="0400000000002",
            description="Unsafe structure review",
            project_name=None,
            assigned_to=None,
            violation_count=1,
            violations=(),
            source_url="https://example.test/case-1",
            fetched_at="2026-07-24T12:00:00+00:00",
        )
        return CollectionResult((case,), (), statuses)


class IntentToLienCodeCollector:
    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        case = CodeCase(
            city_slug="hialeah_fl",
            source_name="hialeah_tyler_energov",
            source_record_id="case-itl",
            case_number="CE-ITL",
            case_type="Code Enforcement",
            status="Intent to Lien",
            opened_date="2026-07-24",
            closed_date=None,
            address="200 TEST AVE",
            parcel_number="0400000000002",
            description="Administrative enforcement case",
            project_name=None,
            assigned_to=None,
            violation_count=0,
            violations=(),
            source_url="https://example.test/case-itl",
            fetched_at="2026-07-24T12:00:00+00:00",
        )
        return CollectionResult((case,), (), statuses)


class PilotTests(unittest.TestCase):
    csv_text = (
        ",MLS # Link,St,Area,Address,,Current Price,ZN,Year Built,"
        "Prop Type,Style of Property,Prop Type/Type of Building,"
        "Type of Property,Property SqFt,Waterfront Property (Y/N),"
        "Sale Price,#Bays\n"
        '1,A123,A,41,100 Test Ave,,"$2,500,000",3901,1970,COM/Sale,,'
        "Commercial/Residential Income,Income/MultiFamily,12000,,,,\n"
    )

    def test_unknown_verified_units_fail_acquisition_scope_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(self.csv_text, encoding="utf-8")
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=UnknownUnitsPropertyCollector(),
                code_collector=FakeCodeCollector(),
            )
            recommendations = json.loads(
                (root / "output/recommendations.json").read_text()
            )
            acquisition_queue = json.loads(
                (root / "output/acquisition_queue.json").read_text()
            )
            manual_triage = json.loads(
                (root / "output/manual_triage_queue.json").read_text()
            )
            with (root / "output/manual_triage_queue.csv").open(
                encoding="utf-8"
            ) as handle:
                manual_triage_csv = tuple(csv.DictReader(handle))
            manual_triage_md = (
                root / "output/manual_triage_queue.md"
            ).read_text()
            with (root / "output/acquisition_queue.csv").open(
                encoding="utf-8"
            ) as handle:
                acquisition_csv = tuple(csv.DictReader(handle))
            acquisition_md = (root / "output/acquisition_queue.md").read_text()

        listed = next(
            item for item in recommendations if "mls" in item["discovery_channels"]
        )
        self.assertFalse(listed["in_scope"])
        self.assertFalse(listed["acquisition_qualified"])
        self.assertEqual(listed["recommended_action"], "manual_triage")
        self.assertIn("verified_unit_count", listed["missing_data"])
        self.assertFalse(
            any(item["property_id"] == listed["property_id"] for item in acquisition_queue)
        )
        self.assertFalse(
            any(row["property_id"] == listed["property_id"] for row in acquisition_csv)
        )
        self.assertNotIn(listed["property_id"], acquisition_md)
        self.assertTrue(
            any(item["property_id"] == listed["property_id"] for item in manual_triage)
        )
        self.assertTrue(
            any(
                row["property_id"] == listed["property_id"]
                for row in manual_triage_csv
            )
        )
        self.assertIn(listed["property_id"], manual_triage_md)

    def test_run_pilot_rejects_unsupported_municipality_for_direct_callers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(self.csv_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only Hialeah"):
                run_pilot(
                    matrix_path=matrix,
                    database_path=root / "pilot.sqlite",
                    output_dir=root / "output",
                    municipality="surfside",
                    generated_at="2026-07-24T12:00:00+00:00",
                    property_collector=FakePropertyCollector(),
                    code_collector=FakeCodeCollector(),
                )

    def test_submitted_folio_falls_back_to_authoritative_exact_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(
                "MLS # Link,St,Address,Folio Number,Current Price,Type of Property\n"
                "A123,A,100 Test Ave,04-0000-000-0001,2500000,"
                "Income/MultiFamily\n",
                encoding="utf-8",
            )
            collector = FolioFallbackPropertyCollector()
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=collector,
                code_collector=FakeCodeCollector(),
            )
            recommendations = json.loads(
                (root / "output/recommendations.json").read_text()
            )

        listed = next(
            item for item in recommendations if "mls" in item["discovery_channels"]
        )
        self.assertEqual(collector.exact_folios, ["0400000000001"])
        self.assertEqual(listed["folio"], "0400000000001")
        self.assertTrue(listed["identity_verified"])

    def test_submitted_folio_does_not_override_conflicting_municipality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(
                "MLS # Link,St,Address,City,State,Zip Code,Folio Number,"
                "Current Price,Type of Property\n"
                "A123,A,100 Test Ave,MIAMI,FL,33010,04-0000-000-0001,"
                "2500000,Income/MultiFamily\n",
                encoding="utf-8",
            )
            collector = FolioFallbackPropertyCollector()
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=collector,
                code_collector=FakeCodeCollector(),
            )
            recommendations = json.loads(
                (root / "output/recommendations.json").read_text()
            )

        listed = next(
            item for item in recommendations if "mls" in item["discovery_channels"]
        )
        self.assertEqual(collector.exact_folios, ["0400000000001"])
        self.assertFalse(listed["identity_verified"])
        self.assertEqual(listed["recommended_action"], "verify_identity")

    def test_ambiguous_owner_creates_no_owner_relationship_or_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "pilot.sqlite"
            with IntelligenceStore(database) as store:
                now = "2026-07-24T12:00:00+00:00"
                owners = (
                    CanonicalOwner("owner-llc", "Test Owner LLC", "llc"),
                    CanonicalOwner("owner-lp", "Test Owner LP", "lp"),
                )
                store.connection.executemany(
                    """
                    INSERT INTO canonical_owners (
                        owner_id,display_name,entity_type,created_at,updated_at
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        (
                            owner.owner_id,
                            owner.display_name,
                            owner.entity_type,
                            now,
                            now,
                        )
                        for owner in owners
                    ),
                )
                prop = CanonicalProperty(
                    property_id="property-owner-conflict",
                    folio="0400000000099",
                    address="900 TEST AVE, HIALEAH, FL 33010",
                    municipality="HIALEAH",
                    jurisdiction="Miami-Dade",
                )
                store.upsert_property(prop)
                record = property_record(
                    "0400000000099",
                    "900 TEST AVE",
                    12,
                )
                record = PropertyRecord(
                    **{
                        **record.__dict__,
                        "owner_name": "TEST OWNER",
                    }
                )

                resolution = _save_owner(store, prop, record)

                self.assertTrue(resolution.conflicting)
                self.assertIsNone(resolution.owner_id)
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM canonical_owners"
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM owner_aliases"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM property_ownership"
                    ).fetchone()[0],
                    0,
                )
                ambiguity = store.connection.execute(
                    """
                    SELECT value_type,metadata_json FROM evidence_items
                    WHERE property_id=? AND field_name='owner_identity_conflict'
                    """,
                    (prop.property_id,),
                ).fetchone()
                self.assertIsNotNone(ambiguity)
                self.assertEqual(ambiguity["value_type"], "unknown")
                self.assertEqual(
                    json.loads(ambiguity["metadata_json"])["candidate_owner_ids"],
                    ["owner-llc", "owner-lp"],
                )

            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(self.csv_text, encoding="utf-8")
            output = root / "output"
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=output,
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=FakeCodeCollector(),
            )
            recommendations = json.loads((output / "recommendations.json").read_text())
            manual_triage = json.loads(
                (output / "manual_triage_queue.json").read_text()
            )
            listed = next(
                item for item in recommendations if "mls" in item["discovery_channels"]
            )
            self.assertEqual(listed["recommended_action"], "manual_triage")
            self.assertIn("owner_identity_conflict", listed["missing_data"])
            self.assertTrue(
                any(
                    item["property_id"] == listed["property_id"]
                    for item in manual_triage
                )
            )
            with IntelligenceStore(database) as store:
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM property_ownership"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM owner_aliases"
                    ).fetchone()[0],
                    0,
                )

    def test_county_address_mismatch_requires_identity_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(self.csv_text, encoding="utf-8")
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=MismatchPropertyCollector(),
                code_collector=FakeCodeCollector(),
            )
            recommendations = json.loads(
                (root / "output/recommendations.json").read_text()
            )

        listed = next(
            item for item in recommendations if "mls" in item["discovery_channels"]
        )
        self.assertFalse(listed["identity_verified"])
        self.assertEqual(listed["recommended_action"], "verify_identity")

    def test_fresh_intent_to_lien_requires_municipal_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(self.csv_text, encoding="utf-8")
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=IntentToLienCodeCollector(),
            )
            recommendations = json.loads(
                (root / "output/recommendations.json").read_text()
            )

        off_market = next(
            item
            for item in recommendations
            if "off_market" in item["discovery_channels"]
        )
        self.assertIsNone(off_market["municipal_cases"][0]["severity_score"])
        self.assertEqual(
            off_market["municipal_cases"][0]["substantive_hazard"],
            "unknown_hazard",
        )
        self.assertEqual(off_market["recommended_action"], "human_municipal_review")

    def test_one_run_persists_workflow_and_generates_database_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(self.csv_text, encoding="utf-8")
            output = root / "output"
            db = root / "pilot.sqlite"
            result = run_pilot(
                matrix_path=matrix,
                database_path=db,
                output_dir=output,
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=FakeCodeCollector(),
            )

            recommendations = json.loads((output / "recommendations.json").read_text())
            summary = json.loads((output / "database_summary.json").read_text())

        required = {
            "run_manifest.json",
            "import_ledger.csv",
            "source_coverage.json",
            "database_summary.json",
            "recommendations.json",
            "recommendations.csv",
            "daily_brief.md",
            "top_candidate_trace.json",
            "top_candidate_trace.md",
            "acquisition_brief.md",
            "qualified_queue.json",
            "acquisition_queue.json",
            "acquisition_queue.csv",
            "acquisition_queue.md",
            "municipal_review_queue.json",
            "municipal_review_queue.csv",
            "municipal_review_queue.md",
            "manual_triage_queue.json",
            "manual_triage_queue.csv",
            "manual_triage_queue.md",
            "mls_example.json",
            "source_health_warnings.json",
            "evidence_state.json",
            "human_dispositions.json",
            "watch_trigger_events.json",
            "watch_validation_events.json",
            "change_summary.json",
            "opportunity_changes.json",
        }
        self.assertEqual({path.name for path in result.output_files}, required)
        self.assertGreaterEqual(summary["canonical_properties"], 2)
        self.assertGreaterEqual(summary["recommendations"], 2)
        listed = next(
            item for item in recommendations if "mls" in item["discovery_channels"]
        )
        off_market = next(
            item
            for item in recommendations
            if "off_market" in item["discovery_channels"]
        )
        self.assertEqual(listed["recommended_action"], "request_documents")
        self.assertEqual(off_market["recommended_action"], "human_municipal_review")
        self.assertTrue(listed["evidence_ids"])

    def test_repeat_change_and_failure_are_auditable(self) -> None:
        header = (
            ",MLS # Link,St,Area,Address,,Current Price,ZN,Year Built,"
            "Prop Type,Style of Property,Prop Type/Type of Building,"
            "Type of Property,Property SqFt,Waterfront Property (Y/N),"
            "Sale Price,#Bays\n"
        )
        active = (
            '1,A123,A,41,100 Test Ave,,"$2,500,000",3901,1970,COM/Sale,,'
            "Commercial/Residential Income,Income/MultiFamily,12000,,,,\n"
        )
        withdrawn = active.replace(",A,41,", ",W,41,", 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(header + active, encoding="utf-8")
            db = root / "pilot.sqlite"
            first = run_pilot(
                matrix_path=matrix,
                database_path=db,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at="2026-07-24T12:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=FakeCodeCollector(),
            )
            second = run_pilot(
                matrix_path=matrix,
                database_path=db,
                output_dir=root / "second",
                municipality="hialeah",
                generated_at="2026-07-24T13:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=FakeCodeCollector(),
            )
            matrix.write_text(header + withdrawn, encoding="utf-8")
            changed = run_pilot(
                matrix_path=matrix,
                database_path=db,
                output_dir=root / "changed",
                municipality="hialeah",
                generated_at="2026-07-24T14:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=FakeCodeCollector(),
            )
            failed = run_pilot(
                matrix_path=matrix,
                database_path=db,
                output_dir=root / "failed",
                municipality="hialeah",
                generated_at="2026-07-24T15:00:00+00:00",
                property_collector=FakePropertyCollector(),
                code_collector=FakeCodeCollector(),
                simulate_source_failure=True,
            )
            import sqlite3

            connection = sqlite3.connect(db)
            status_changes = connection.execute(
                """
                SELECT COUNT(*) FROM listing_changes
                WHERE change_type='status_change'
                """
            ).fetchone()[0]
            failed_coverage = connection.execute(
                """
                SELECT COUNT(*) FROM property_source_coverage
                WHERE source_name='hialeah_tyler_energov'
                  AND state='unknown_failed'
                """
            ).fetchone()[0]
            connection.close()
            failed_brief = (root / "failed" / "daily_brief.md").read_text()

        self.assertEqual(
            first.database_counts["canonical_properties"],
            second.database_counts["canonical_properties"],
        )
        self.assertEqual(
            first.database_counts["listing_snapshots"],
            second.database_counts["listing_snapshots"],
        )
        self.assertEqual(
            first.database_counts["property_signals"],
            second.database_counts["property_signals"],
        )
        self.assertEqual(status_changes, 1)
        self.assertGreaterEqual(failed_coverage, 1)
        self.assertIn("simulated source failure", failed_brief)
        self.assertGreaterEqual(
            failed.database_counts["recommendations"],
            changed.database_counts["recommendations"],
        )


if __name__ == "__main__":
    unittest.main()
