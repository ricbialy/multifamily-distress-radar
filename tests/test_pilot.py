from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from distress_radar.models import (
    CodeCase,
    CollectionResult,
    PropertyCollectionResult,
    PropertyRecord,
)
from distress_radar.pilot import run_pilot


def property_record(folio: str, address: str, units: int) -> PropertyRecord:
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


class PilotTests(unittest.TestCase):
    def test_one_run_persists_workflow_and_generates_database_reports(self) -> None:
        csv_text = (
            ",MLS # Link,St,Area,Address,,Current Price,ZN,Year Built,"
            "Prop Type,Style of Property,Prop Type/Type of Building,"
            "Type of Property,Property SqFt,Waterfront Property (Y/N),"
            "Sale Price,#Bays\n"
            '1,A123,A,41,100 Test Ave,,"$2,500,000",3901,1970,COM/Sale,,'
            "Commercial/Residential Income,Income/MultiFamily,12000,,,,\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
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

            recommendations = json.loads(
                (output / "recommendations.json").read_text()
            )
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
        }
        self.assertEqual({path.name for path in result.output_files}, required)
        self.assertGreaterEqual(summary["canonical_properties"], 2)
        self.assertGreaterEqual(summary["recommendations"], 2)
        listed = next(item for item in recommendations if "mls" in item["discovery_channels"])
        off_market = next(
            item for item in recommendations if "off_market" in item["discovery_channels"]
        )
        self.assertEqual(
            listed["recommended_action"], "contact_broker_for_documents"
        )
        self.assertEqual(
            off_market["recommended_action"], "human_municipal_review"
        )
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
