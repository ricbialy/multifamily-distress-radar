from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.models import (
    CodeCase,
    CollectionResult,
    OfficialRecord,
    PropertyCollectionResult,
    PropertyRecord,
)
from distress_radar.municipal_severity import (
    active_violations,
    classify_municipal_case,
)
from distress_radar.pilot import (
    _acquisition_rank_reason,
    _active_clerk_records,
    _apply_disposition_action,
    _material_content_hash,
    _municipal_rank_reason,
    run_pilot,
)
from distress_radar.recommendations.features import (
    InvestmentCriteria,
    acquisition_attractiveness,
    calculate_evidence_scores,
    municipal_review_urgency,
)

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
    closed_date: str | None = None,
) -> CodeCase:
    return CodeCase(
        city_slug="hialeah_fl",
        source_name="hialeah_tyler_energov",
        source_record_id=record_id,
        case_number=f"CE-{record_id}",
        case_type=case_type,
        status=status,
        opened_date=opened_date,
        closed_date=closed_date,
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
            case(
                case_type="Courtesy Warning", status="Open", description="Grass warning"
            ),
            as_of=GENERATED_AT,
        )
        lien = classify_municipal_case(
            case(
                case_type="Code Enforcement",
                status="Intent to Lien",
                description="Intent to lien",
            ),
            as_of=GENERATED_AT,
        )
        special_master = classify_municipal_case(
            case(
                case_type="Special Master",
                status="Hearing",
                description="Special master hearing",
            ),
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

        self.assertEqual(classification.category, "unknown_hazard")
        self.assertNotEqual(classification.substantive_hazard, "unsafe_life_safety")

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

    def test_enforcement_stage_is_separate_from_substantive_hazard(self) -> None:
        graffiti = classify_municipal_case(
            case(
                case_type="Graffiti",
                status="Special Master Order Sent",
                description="Graffiti on exterior wall",
            ),
            as_of=GENERATED_AT,
        )
        minimum_housing = classify_municipal_case(
            case(
                case_type="Minimum Housing",
                status="Notice Of Violation Sent",
                description="Mold and uninhabitable sleeping room",
            ),
            as_of=GENERATED_AT,
        )

        self.assertEqual(graffiti.enforcement_stage, "special_master")
        self.assertEqual(graffiti.substantive_hazard, "cosmetic")
        self.assertEqual(minimum_housing.enforcement_stage, "nov")
        self.assertEqual(minimum_housing.substantive_hazard, "minimum_housing")
        self.assertGreater(
            municipal_review_urgency((minimum_housing,)),
            municipal_review_urgency((graffiti,)),
        )

    def test_configured_hialeah_hearing_and_itl_statuses_are_recognized(self) -> None:
        expectations = {
            "Notice of Hearing Sent": "hearing",
            "ITL NOH Sent": "itl",
            "ITL Appealed": "itl",
            "Intent to Lien Sent": "itl",
            "Lien": "lien",
        }
        for status, expected in expectations.items():
            with self.subTest(status=status):
                result = classify_municipal_case(
                    case(case_type="Signs", status=status, description="Sign"),
                    as_of=GENERATED_AT,
                )
                self.assertEqual(result.enforcement_stage, expected)

    def test_resolved_violation_rows_are_filtered(self) -> None:
        rows = (
            {"CodeStatus": "In Violation", "ResolveDate": None},
            {"CodeStatus": "Complied", "ResolveDate": "2026-07-01"},
            {"Status": "Closed"},
        )
        self.assertEqual(active_violations(rows), (rows[0],))

    def test_closed_case_has_no_current_review_urgency(self) -> None:
        closed = classify_municipal_case(
            case(
                status="Closed",
                closed_date="2026-07-01",
                violations=({"CodeStatus": "Complied"},),
            ),
            as_of=GENERATED_AT,
        )
        self.assertFalse(closed.currently_active)
        self.assertEqual(municipal_review_urgency((closed,)), 0)


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
                "noi": 100_000,
                "expenses": 30_000,
            },
            municipal=(),
            official_records=({"signal_type": "recorded_liens"},),
            tax_records=({"amount_due": 12_000, "status": "unpaid"},),
            missing_fields=("rent_roll", "T12"),
            investment_criteria=InvestmentCriteria(0.04, 0.06),
        )

        self.assertGreater(scores.dimensions.owner_motivation, 0)
        self.assertGreater(scores.dimensions.market_pressure, 0)
        self.assertGreater(scores.dimensions.economics, 0)
        self.assertIn("price_per_unit", scores.metrics)
        self.assertEqual(scores.metrics["price_per_unit"], 70_000)
        self.assertTrue(scores.components["owner_motivation"])
        self.assertTrue(scores.components["economics"])
        self.assertTrue(scores.components["data_completeness"])

    def test_absentee_and_long_ownership_are_context_not_confirmed_motivation(
        self,
    ) -> None:
        scores = calculate_evidence_scores(
            county={
                "absentee_owner": True,
                "ownership_duration_years": 30,
                "verified_units": 20,
            },
            listing={},
            municipal=(),
            official_records=(),
            tax_records=(),
            missing_fields=(),
        )
        self.assertEqual(scores.dimensions.owner_motivation, 0)
        self.assertTrue(
            all("+0" in item for item in scores.components["owner_motivation"])
        )

    def test_bad_economics_and_assessed_value_do_not_create_positive_points(
        self,
    ) -> None:
        scores = calculate_evidence_scores(
            county={"verified_units": 10, "assessed_value": 10_000_000},
            listing={
                "list_price": 5_000_000,
                "noi": 100_000,
                "expenses": None,
            },
            municipal=(),
            official_records=(),
            tax_records=(),
            missing_fields=("expenses",),
        )
        self.assertIsNone(scores.dimensions.economics)
        self.assertEqual(scores.metrics["economics_status"], "unknown")
        self.assertNotIn("asking_to_assessed_ratio", scores.metrics)
        self.assertNotIn("reported_noi_cap_rate", scores.metrics)

    def test_only_price_reductions_create_market_pressure(self) -> None:
        increases = calculate_evidence_scores(
            county={},
            listing={"price_reduction_count": 0, "price_change_count": 4},
            municipal=(),
            official_records=(),
            tax_records=(),
            missing_fields=(),
        )
        reductions = calculate_evidence_scores(
            county={},
            listing={"price_reduction_count": 2, "price_change_count": 4},
            municipal=(),
            official_records=(),
            tax_records=(),
            missing_fields=(),
        )
        self.assertEqual(increases.dimensions.market_pressure, 0)
        self.assertGreater(reductions.dimensions.market_pressure, 0)

    def test_resolved_taxes_and_clerk_releases_score_zero(self) -> None:
        records = tuple(
            {"amount_due": 10_000, "status": status}
            for status in ("redeemed", "released", "cancelled", "paid", "satisfied")
        )
        scores = calculate_evidence_scores(
            county={},
            listing={},
            municipal=(),
            official_records=({"signal_type": "release"},),
            tax_records=records,
            missing_fields=(),
        )
        self.assertEqual(scores.dimensions.owner_motivation, 0)

    def test_acquisition_attractiveness_does_not_reward_property_risk(self) -> None:
        safe = calculate_evidence_scores(
            county={"verified_units": 20},
            listing={"list_price": 2_000_000},
            municipal=(),
            official_records=(),
            tax_records=(),
            missing_fields=(),
        )
        risky = replace(
            safe,
            dimensions=replace(safe.dimensions, property_risk=100),
        )
        self.assertEqual(
            acquisition_attractiveness(safe.dimensions),
            acquisition_attractiveness(risky.dimensions),
        )

    def test_rank_explanations_name_the_component_that_caused_ordering(self) -> None:
        acquisition = {
            "scores": {
                "economics": 20,
                "market_pressure": 0,
                "owner_motivation": 0,
                "data_confidence": 0,
                "data_completeness": 0,
            }
        }
        next_acquisition = {
            "scores": {
                "economics": 10,
                "market_pressure": 0,
                "owner_motivation": 0,
                "data_confidence": 0,
                "data_completeness": 0,
            }
        }
        self.assertIn(
            "economics",
            _acquisition_rank_reason(acquisition, next_acquisition),
        )
        self.assertIn(
            "stable canonical",
            _acquisition_rank_reason(acquisition, acquisition),
        )
        self.assertIn("Final item", _acquisition_rank_reason(acquisition, None))

        def municipal(urgency: float, hazard: str, age_days: int) -> dict[str, object]:
            return {
                "municipal_review_urgency_score": urgency,
                "municipal_cases": [
                    {"substantive_hazard": hazard, "age_days": age_days}
                ],
            }

        high = municipal(90, "minimum_housing", 20)
        low = municipal(80, "unsafe_life_safety", 100)
        self.assertIn("urgency", _municipal_rank_reason(high, low))
        self.assertIn(
            "substantive-hazard",
            _municipal_rank_reason(
                municipal(90, "unsafe_life_safety", 20),
                municipal(90, "cosmetic", 100),
            ),
        )
        self.assertIn(
            "active-case age",
            _municipal_rank_reason(
                municipal(90, "permit", 100),
                municipal(90, "permit", 20),
            ),
        )
        self.assertIn(
            "stable canonical",
            _municipal_rank_reason(
                municipal(90, "permit", 20),
                municipal(90, "permit", 20),
            ),
        )


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

    def test_approval_is_hash_bound_and_cannot_bypass_hard_gates(self) -> None:
        self.assertEqual(
            _apply_disposition_action(
                disposition="approved_for_contact",
                baseline_content_hash="old",
                current_content_hash="new",
                default_action="watch",
                listing_present=False,
                identity_verified=True,
                in_scope=True,
                serious_municipal=False,
                underwriting_complete=True,
            ),
            "watch",
        )
        for gate_action, flags in (
            ("verify_identity", {"identity_verified": False}),
            ("excluded", {"in_scope": False}),
            ("human_municipal_review", {"serious_municipal": True}),
            ("request_documents", {"underwriting_complete": False}),
        ):
            with self.subTest(gate=gate_action):
                arguments = {
                    "identity_verified": True,
                    "in_scope": True,
                    "serious_municipal": False,
                    "underwriting_complete": True,
                    **flags,
                }
                self.assertEqual(
                    _apply_disposition_action(
                        disposition="approved_for_contact",
                        baseline_content_hash="same",
                        current_content_hash="same",
                        default_action=gate_action,
                        listing_present=False,
                        **arguments,
                    ),
                    gate_action,
                )

    def test_material_hash_excludes_clock_only_fields(self) -> None:
        first = {
            "listing": {"remarks": "No known roof issue."},
            "county": {
                "owner": "OWNER LLC",
                "last_sale_date": "2000-01-01",
                "ownership_duration_years": 26.5,
                "fetched_at": "2026-07-24T12:00:00Z",
            },
            "municipal": [
                {
                    "case_number": "C-1",
                    "status": "Open",
                    "age_days": 100,
                    "severity_score": 50,
                    "severity_reasons": ["100 days old"],
                }
            ],
            "generated_at": "2026-07-24T12:00:00Z",
            "data_freshness": 90,
        }
        second = {
            "listing": first["listing"],
            "county": {
                **first["county"],
                "ownership_duration_years": 26.6,
                "fetched_at": "2026-07-25T12:00:00Z",
            },
            "municipal": [
                {
                    **first["municipal"][0],
                    "age_days": 101,
                    "severity_reasons": ["101 days old"],
                }
            ],
            "generated_at": "2026-07-25T12:00:00Z",
            "data_freshness": 89,
        }
        self.assertEqual(
            _material_content_hash(first),
            _material_content_hash(second),
        )
        changed_remarks = {
            **second,
            "listing": {
                "remarks": "Roof replacement is required before closing.",
            },
        }
        self.assertNotEqual(
            _material_content_hash(second),
            _material_content_hash(changed_remarks),
        )

    def test_disposition_cannot_invent_a_broker_contact_path(self) -> None:
        self.assertEqual(
            _apply_disposition_action(
                disposition="request_documents",
                baseline_content_hash="same",
                current_content_hash="same",
                default_action="request_documents",
                listing_present=True,
                identity_verified=True,
                in_scope=True,
                serious_municipal=False,
                underwriting_complete=False,
            ),
            "request_documents",
        )


class ClerkLifecycleTests(unittest.TestCase):
    def test_clerk_party_rows_deduplicate_and_release_original_instrument(self) -> None:
        base = OfficialRecord(
            "hialeah_fl",
            "miami_dade_clerk",
            "party-1",
            "2026-100",
            None,
            None,
            "0400000000001",
            "LIEN",
            "lien",
            "2026-01-01",
            None,
            "CITY",
            "OWNER",
            None,
            None,
            None,
            None,
            None,
            "https://example.test/clerk",
            GENERATED_AT,
        )
        duplicate = replace(base, source_record_id="party-2", first_party="OTHER")
        release = replace(
            base,
            source_record_id="release-party",
            instrument_id="2026-200",
            original_instrument_id="2026-100",
            document_type="SATISFACTION",
            signal_type="release",
        )
        self.assertEqual(_active_clerk_records((base, duplicate)), (base,))
        self.assertEqual(_active_clerk_records((base, duplicate, release)), ())


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
            municipal_queue = json.loads(
                (root / "output" / "municipal_review_queue.json").read_text()
            )

        self.assertEqual(code_collector.enriched_ids, ["case-1"])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["rank"], 1)
        self.assertGreater(queue[0]["scores"]["market_pressure"], 0)
        self.assertIsNone(queue[0]["scores"]["economics"])
        self.assertEqual(municipal_queue[0]["municipal_rank"], 1)
        self.assertEqual(
            municipal_queue[0]["recommended_action"],
            "human_municipal_review",
        )
        self.assertNotIn("evidence_id", brief.casefold())
        self.assertEqual(trace["recommendation"]["rank"], 1)
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

    def test_next_day_unchanged_run_has_no_false_transitions_or_alerts(self) -> None:
        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            database = root / "pilot.sqlite"
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            second = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "second",
                municipality="hialeah",
                generated_at="2026-07-25T12:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                changed = connection.execute(
                    """
                    SELECT COUNT(*) FROM recommendations
                    WHERE pilot_run_id=? AND change_type='materially_changed'
                    """,
                    (second.run_id,),
                ).fetchone()[0]
                alerts = connection.execute(
                    "SELECT COUNT(*) FROM opportunity_alerts WHERE pilot_run_id=?",
                    (second.run_id,),
                ).fetchone()[0]
                transitions = connection.execute(
                    """
                    SELECT COUNT(*) FROM signal_observations
                    WHERE pilot_run_id=? AND status IN ('resolved','active')
                    """,
                    (second.run_id,),
                ).fetchone()[0]
            self.assertEqual(changed, 0)
            self.assertEqual(alerts, 0)
            self.assertEqual(transitions, 0)

    def test_source_failure_preserves_last_known_without_current_scoring(self) -> None:
        class FailedCodeCollector(TargetedCodeCollector):
            def collect(
                self, statuses: tuple[str, ...], **kwargs: object
            ) -> CollectionResult:
                raise RuntimeError("source unavailable")

        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            database = root / "pilot.sqlite"
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            failed = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "failed",
                municipality="hialeah",
                generated_at="2026-07-25T12:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=FailedCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                connection.row_factory = sqlite3.Row
                signal = connection.execute(
                    """
                    SELECT status,confirmation_status FROM property_signals
                    WHERE signal_type='off_market_live_code_case'
                    """
                ).fetchone()
                current = connection.execute(
                    """
                    SELECT explanation_json FROM recommendations
                    WHERE pilot_run_id=? AND property_id=(
                        SELECT property_id FROM canonical_properties
                        WHERE folio='0400000000002'
                    )
                    """,
                    (failed.run_id,),
                ).fetchone()
                alerts = connection.execute(
                    "SELECT COUNT(*) FROM opportunity_alerts WHERE pilot_run_id=?",
                    (failed.run_id,),
                ).fetchone()[0]
            self.assertEqual(
                dict(signal), {"status": "active", "confirmation_status": "last_known"}
            )
            self.assertEqual(
                json.loads(current["explanation_json"])["municipal_cases"], []
            )
            self.assertEqual(alerts, 0)

    def test_inventory_failure_cannot_resolve_code_intersection(self) -> None:
        class FailedInventoryCollector(TargetedPropertyCollector):
            def collect(self) -> PropertyCollectionResult:
                raise RuntimeError("inventory unavailable")

        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            database = root / "pilot.sqlite"
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            second = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "failed",
                municipality="hialeah",
                generated_at="2026-07-25T12:00:00+00:00",
                property_collector=FailedInventoryCollector(),
                code_collector=TargetedCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                signal = connection.execute(
                    """
                    SELECT status,confirmation_status FROM property_signals
                    WHERE signal_type='off_market_live_code_case'
                    """
                ).fetchone()
                resolutions = connection.execute(
                    """
                    SELECT COUNT(*) FROM signal_observations
                    WHERE pilot_run_id=? AND status='resolved'
                    """,
                    (second.run_id,),
                ).fetchone()[0]
                county_coverage = connection.execute(
                    """
                    SELECT c.state,c.error_message,s.pilot_run_id
                    FROM property_source_coverage c
                    JOIN source_runs s ON s.run_id=c.run_id
                    WHERE c.property_id=(
                        SELECT property_id FROM canonical_properties
                        WHERE folio='0400000000002'
                    )
                      AND c.source_name='miami_dade_property_point_view'
                    """
                ).fetchone()
        self.assertEqual(signal, ("active", "last_known"))
        self.assertEqual(resolutions, 0)
        self.assertEqual(
            county_coverage,
            ("unknown_failed", "inventory unavailable", second.run_id),
        )

    def test_failure_after_resolution_does_not_resurrect_old_material_content(
        self,
    ) -> None:
        class EmptyCodeCollector(TargetedCodeCollector):
            def collect(
                self, statuses: tuple[str, ...], **kwargs: object
            ) -> CollectionResult:
                return CollectionResult((), (), statuses)

        class FailedCodeCollector(TargetedCodeCollector):
            def collect(
                self, statuses: tuple[str, ...], **kwargs: object
            ) -> CollectionResult:
                raise RuntimeError("source unavailable")

        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            database = root / "pilot.sqlite"
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            resolved = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "resolved",
                municipality="hialeah",
                generated_at="2026-07-25T12:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=EmptyCodeCollector(),
            )
            failed = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "failed",
                municipality="hialeah",
                generated_at="2026-07-26T12:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=FailedCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                property_id = connection.execute(
                    """
                    SELECT property_id FROM canonical_properties
                    WHERE folio='0400000000002'
                    """
                ).fetchone()[0]
                hashes = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT content_hash FROM recommendations
                        WHERE property_id=? AND pilot_run_id IN (?,?)
                        ORDER BY generated_at
                        """,
                        (property_id, resolved.run_id, failed.run_id),
                    ).fetchall()
                ]
                failed_alerts = connection.execute(
                    """
                    SELECT COUNT(*) FROM opportunity_alerts WHERE pilot_run_id=?
                    """,
                    (failed.run_id,),
                ).fetchone()[0]
        self.assertEqual(len(set(hashes)), 1)
        self.assertEqual(failed_alerts, 0)

    def test_healthy_disappearance_resolves_once_and_removes_current_case(self) -> None:
        class EmptyCodeCollector(TargetedCodeCollector):
            def collect(
                self, statuses: tuple[str, ...], **kwargs: object
            ) -> CollectionResult:
                return CollectionResult((), (), statuses)

        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            database = root / "pilot.sqlite"
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "first",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=TargetedCodeCollector(),
            )
            disappeared = run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "second",
                municipality="hialeah",
                generated_at="2026-07-25T12:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=EmptyCodeCollector(),
            )
            run_pilot(
                matrix_path=matrix,
                database_path=database,
                output_dir=root / "third",
                municipality="hialeah",
                generated_at="2026-07-26T12:00:00+00:00",
                property_collector=TargetedPropertyCollector(),
                code_collector=EmptyCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                resolutions = connection.execute(
                    """
                    SELECT COUNT(*) FROM signal_observations WHERE status='resolved'
                    """
                ).fetchone()[0]
                explanation = json.loads(
                    connection.execute(
                        """
                        SELECT explanation_json FROM recommendations
                        WHERE pilot_run_id=? AND property_id=(
                            SELECT property_id FROM canonical_properties
                            WHERE folio='0400000000002'
                        )
                        """,
                        (disappeared.run_id,),
                    ).fetchone()[0]
                )
            self.assertEqual(resolutions, 1)
            self.assertEqual(explanation["municipal_cases"], [])

    def test_closed_case_does_not_enter_current_queue(self) -> None:
        class ClosedCodeCollector(TargetedCodeCollector):
            def enrich_records(
                self, records: tuple[CodeCase, ...], *, include_violations: bool
            ) -> CollectionResult:
                closed = tuple(
                    case(
                        record_id=item.source_record_id,
                        status="Closed",
                        closed_date="2026-07-01",
                        violations=({"CodeStatus": "Complied"},),
                    )
                    for item in records
                )
                return CollectionResult(closed, (), ("Closed",))

        csv_text = (
            "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
            "A123,100 Test Ave,A,2400000,90,120,12,Price reduced\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = root / "Agent Single Line - COM.csv"
            matrix.write_text(csv_text, encoding="utf-8")
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=ClosedCodeCollector(),
            )
            queue = json.loads(
                (root / "output" / "municipal_review_queue.json").read_text()
            )
        self.assertEqual(queue, [])


if __name__ == "__main__":
    unittest.main()
