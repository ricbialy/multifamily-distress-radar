from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.models import (
    CodeCase,
    CollectionResult,
    PropertyCollectionResult,
    PropertyRecord,
)
from distress_radar.municipal_severity import classify_municipal_case
from distress_radar.pilot import run_pilot
from distress_radar.recommendations.features import calculate_evidence_scores


GENERATED_AT = "2026-07-24T12:00:00+00:00"


def property_record(folio: str, address: str, units: int) -> PropertyRecord:
    return PropertyRecord(
        city_slug="hialeah_fl",
        source_name="miami_dade_property_point_view",
        folio=folio,
        address=address,
        city="HIALEAH",
        zip_code="33010",
        owner_name="REMOTE OWNER LLC",
        owner_name_2=None,
        mailing_address_1="500 OTHER ST",
        mailing_address_2=None,
        mailing_city="MIAMI",
        mailing_state="FL",
        mailing_zip="33130",
        dor_code="0303",
        dor_description="MULTIFAMILY 10 UNITS PLUS",
        unit_count=units,
        year_built=1965,
        floor_count=2,
        building_area=12_000,
        lot_size=15_000,
        assessed_value=1_000_000,
        last_sale_date="2000-01-01",
        last_sale_price=500_000,
        latitude=25.8,
        longitude=-80.2,
        source_url="https://example.test/county",
        fetched_at=GENERATED_AT,
    )


def case(
    *,
    record_id: str = "case-1",
    case_type: str = "Unsafe Structure",
    status: str = "Notice of Violation",
    opened_date: str = "2024-01-01",
    description: str = "Unsafe structure and life safety inspection",
    parcel: str = "0400000000002",
    violations: tuple[dict[str, object], ...] = (),
) -> CodeCase:
    return CodeCase(
        city_slug="hialeah_fl",
        source_name="hialeah_tyler_energov",
        source_record_id=record_id,
        case_number=f"CE-{record_id}",
        case_type=case_type,
        status=status,
        opened_date=opened_date,
        closed_date=None,
        address="200 TEST AVE",
        parcel_number=parcel,
        description=description,
        project_name=None,
        assigned_to=None,
        violation_count=len(violations),
        violations=violations,
        source_url=f"https://example.test/cases/{record_id}",
        fetched_at=GENERATED_AT,
    )


class TargetedPropertyCollector:
    def __init__(self) -> None:
        self.matrix = property_record("0400000000001", "100 TEST AVE", 12)
        self.off_market = property_record("0400000000002", "200 TEST AVE", 20)

    def lookup_address(self, address: str) -> PropertyCollectionResult:
        return PropertyCollectionResult((self.matrix,), ())

    def lookup_exact_folio(self, folio: str) -> PropertyCollectionResult:
        return PropertyCollectionResult((self.matrix,), ())

    def collect(self) -> PropertyCollectionResult:
        return PropertyCollectionResult((self.matrix, self.off_market), ())


class TargetedCodeCollector:
    def __init__(self) -> None:
        self.enriched_ids: list[str] = []

    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        records = (
            case(),
            case(
                record_id="not-in-target-inventory",
                case_type="Minor warning",
                status="Open",
                parcel="9999999999999",
            ),
        )
        return CollectionResult(records, (), statuses)

    def enrich_records(
        self, records: tuple[CodeCase, ...], *, include_violations: bool
    ) -> CollectionResult:
        self.enriched_ids = [record.source_record_id for record in records]
        enriched = tuple(
            case(
                record_id=record.source_record_id,
                violations=(
                    {
                        "ViolationType": "Unsafe Structure",
                        "Status": "Open",
                        "Description": "Life safety hazard",
                    },
                ),
            )
            for record in records
        )
        return CollectionResult(enriched, (), ("Notice of Violation",))


class MunicipalSeverityTests(unittest.TestCase):
    def test_taxonomy_orders_minor_lien_special_master_and_unsafe(self) -> None:
        minor = classify_municipal_case(
            case(case_type="Courtesy Warning", status="Open", description="Grass warning"),
            as_of=GENERATED_AT,
        )
        lien = classify_municipal_case(
            case(case_type="Code Enforcement", status="Intent to Lien", description="Intent to lien"),
            as_of=GENERATED_AT,
        )
        special_master = classify_municipal_case(
            case(case_type="Special Master", status="Hearing", description="Special master hearing"),
            as_of=GENERATED_AT,
        )
        unsafe = classify_municipal_case(case(), as_of=GENERATED_AT)

        self.assertEqual(minor.category, "minor_warning")
        self.assertLess(minor.score, lien.score)
        self.assertLess(lien.score, special_master.score)
        self.assertLess(special_master.score, unsafe.score)
        self.assertEqual(unsafe.category, "unsafe_or_life_safety")
        self.assertGreater(unsafe.age_days, 0)

    def test_municipal_severity_never_creates_owner_motivation(self) -> None:
        scores = calculate_evidence_scores(
            county={},
            listing={},
            municipal=(classify_municipal_case(case(), as_of=GENERATED_AT),),
            official_records=(),
            tax_records=(),
            missing_fields=("rent_roll",),
        )
        self.assertEqual(scores.dimensions.owner_motivation, 0)
        self.assertEqual(scores.components["owner_motivation"], ())
        self.assertGreater(scores.dimensions.property_risk, 0)

    def test_boilerplate_ordinance_text_does_not_inflate_swale_case(self) -> None:
        swale = case(
            case_type="Swale Alteration",
            status="Notice of Violation Extension",
            opened_date="2026-05-27",
            description="Failure to comply with swale maintenance standards.",
            violations=(
                {
                    "CodeDescription": "MAINTENANCE OF SWALE AREA",
                    "ViolationPriority": "Medium",
                    "RevisionCodeText": (
                        "If an unrelated emergency creates an unsafe condition, "
                        "the city may act immediately."
                    ),
                },
            ),
        )

        classification = classify_municipal_case(swale, as_of=GENERATED_AT)

        self.assertEqual(classification.category, "minor_warning")
        self.assertEqual(classification.score, 10)

    def test_case_age_is_stable_within_the_same_calendar_day(self) -> None:
        municipal_case = case(opened_date="2026-05-07T22:34:50Z")

        before_anniversary_time = classify_municipal_case(
            municipal_case, as_of="2026-07-24T22:32:00Z"
        )
        after_anniversary_time = classify_municipal_case(
            municipal_case, as_of="2026-07-24T22:38:00Z"
        )

        self.assertEqual(
            before_anniversary_time.age_days,
            after_anniversary_time.age_days,
        )


class EvidenceScoringTests(unittest.TestCase):
    def test_scores_are_calculated_from_visible_components(self) -> None:
        scores = calculate_evidence_scores(
            county={
                "absentee_owner": True,
                "ownership_duration_years": 26,
                "assessed_value": 1_000_000,
                "verified_units": 20,
                "year_built": 1965,
                "building_area": 12_000,
            },
            listing={
                "status": "A",
                "list_price": 1_400_000,
                "dom": 120,
                "cdom": 180,
                "price_change_count": 2,
                "remarks": "Price reduced. Seller motivated.",
            },
            municipal=(),
            official_records=({"signal_type": "recorded_liens"},),
            tax_records=({"amount_due": 12_000, "status": "unpaid"},),
            missing_fields=("rent_roll", "T12"),
        )

        self.assertGreater(scores.dimensions.owner_motivation, 0)
        self.assertGreater(scores.dimensions.market_pressure, 0)
        self.assertGreater(scores.dimensions.economics, 0)
        self.assertIn("price_per_unit", scores.metrics)
        self.assertEqual(scores.metrics["price_per_unit"], 70_000)
        self.assertTrue(scores.components["owner_motivation"])
        self.assertTrue(scores.components["economics"])
        self.assertTrue(scores.components["data_completeness"])


class DispositionTests(unittest.TestCase):
    def test_dismissal_is_durable_until_material_evidence_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with IntelligenceStore(Path(temporary) / "pilot.sqlite") as store:
                prop = property_record("0400000000001", "100 TEST AVE", 12)
                from distress_radar.domain.property import CanonicalProperty

                store.upsert_property(
                    CanonicalProperty(
                        property_id="property-1",
                        folio=prop.folio,
                        address=prop.address or "",
                        municipality=prop.city or "",
                        jurisdiction="Miami-Dade",
                    )
                )
                store.record_disposition(
                    "property-1",
                    "dismiss",
                    GENERATED_AT,
                    baseline_content_hash="same-content",
                )

                self.assertEqual(
                    store.effective_disposition("property-1", "same-content"),
                    "dismiss",
                )
                self.assertIsNone(
                    store.effective_disposition("property-1", "new-content")
                )


class QualifiedQueueIntegrationTests(unittest.TestCase):
    def test_targeted_enrichment_and_ranked_analyst_queue(self) -> None:
        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        code_collector = TargetedCodeCollector()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            result = run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=code_collector,
            )
            brief = (root / "output" / "acquisition_brief.md").read_text()
            queue = json.loads((root / "output" / "qualified_queue.json").read_text())
            trace = json.loads(
                (root / "output" / "top_candidate_trace.json").read_text()
            )

        self.assertEqual(code_collector.enriched_ids, ["case-1"])
        self.assertLessEqual(len(queue), 10)
        self.assertEqual(queue[0]["rank"], 1)
        self.assertIn("score_components", queue[0])
        self.assertIn("ranked_above_next_reason", queue[0])
        self.assertIn("Exact scoring calculation", brief)
        self.assertIn("REMOTE OWNER LLC", brief)
        self.assertIn("contact_broker_for_documents", brief)
        self.assertIn("human_municipal_review", brief)
        self.assertNotIn("evidence_id", brief.casefold())
        self.assertEqual(trace["recommendation"]["rank"], 1)
        self.assertIn("pilot_run_id", trace["recommendation"])
        self.assertIn(
            "acquisition_brief.md", {path.name for path in result.output_files}
        )

    def test_unchanged_rerun_creates_zero_new_opportunity_alerts(self) -> None:
        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            database = root / "pilot.sqlite"
            first = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "second",
                municipality="hialeah",
                generated_at="2026-07-24T13:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                first_alerts = connection.execute(
                    "SELECT COUNT(*) FROM opportunity_alerts WHERE pilot_run_id=?",
                    (first.run_id,),
                ).fetchone()[0]
                second_alerts = connection.execute(
                    """
                    SELECT COUNT(*) FROM opportunity_alerts
                    WHERE pilot_run_id != ?
                    """,
                    (first.run_id,),
                ).fetchone()[0]

        self.assertGreater(first_alerts, 0)
        self.assertEqual(second_alerts, 0)


if __name__ == "__main__":
    unittest.main()
