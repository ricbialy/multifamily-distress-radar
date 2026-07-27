from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_real_pilot_02 import (
    GENERATED_AT,
    TargetedCodeCollector,
    TargetedPropertyCollector,
    case,
)

from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.models import (
    CollectionResult,
    OfficialRecord,
    OfficialRecordCollectionResult,
    PropertyCollectionResult,
    TaxDelinquency,
)
from distress_radar.municipal_severity import classify_municipal_case
from distress_radar.orchestration.refresh import run_fixture_demo
from distress_radar.pilot import run_pilot
from distress_radar.pilot_verification import _evidence_value_supports_action
from distress_radar.recommendations.features import (
    acquisition_attractiveness,
    calculate_evidence_scores,
)

CSV_TEXT = (
    "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
    "A123,100 Test Ave,A,2400000,0,0,12,unmotivated seller\n"
)


class OverlapCodeCollector(TargetedCodeCollector):
    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        return CollectionResult((case(parcel="0400000000001"),), (), statuses)

    def enrich_records(
        self, records: tuple, *, include_violations: bool
    ) -> CollectionResult:
        return CollectionResult(records, (), ("Notice of Violation",))


class EmptyCodeCollector(OverlapCodeCollector):
    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        return CollectionResult((), (), statuses)


class FailedCodeCollector(OverlapCodeCollector):
    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        raise RuntimeError("case list unavailable")


class FailedEnrichmentCollector(OverlapCodeCollector):
    def enrich_records(
        self, records: tuple, *, include_violations: bool
    ) -> CollectionResult:
        raise RuntimeError("case detail unavailable")


class BareCodeCollector:
    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        bare = replace(
            case(parcel="0400000000001"),
            case_type="Code Enforcement",
            description=None,
            project_name=None,
            violations=(),
            violation_count=0,
        )
        return CollectionResult((bare,), (), statuses)


class EmptySuccessfulEnrichmentCollector(BareCodeCollector):
    def enrich_records(
        self, records: tuple, *, include_violations: bool
    ) -> CollectionResult:
        return CollectionResult(records, (), ("Notice of Violation",))


class FailedPropertyCollector(TargetedPropertyCollector):
    def lookup_address(self, address: str) -> PropertyCollectionResult:
        raise RuntimeError("county unavailable")

    def lookup_exact_folio(self, folio: str) -> PropertyCollectionResult:
        raise RuntimeError("county unavailable")

    def collect(self) -> PropertyCollectionResult:
        raise RuntimeError("county unavailable")


def clerk_record() -> OfficialRecord:
    return OfficialRecord(
        "hialeah_fl",
        "miami_dade_clerk",
        "clerk-row-1",
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


class ClerkCollector:
    def collect(self, folios: list[str]) -> OfficialRecordCollectionResult:
        return OfficialRecordCollectionResult((clerk_record(),), ())


class FailedClerkCollector:
    def collect(self, folios: list[str]) -> OfficialRecordCollectionResult:
        raise RuntimeError("clerk unavailable")


class EmptyClerkCollector:
    def collect(self, folios: list[str]) -> OfficialRecordCollectionResult:
        return OfficialRecordCollectionResult((), ())


def tax_record() -> TaxDelinquency:
    return TaxDelinquency(
        "hialeah_fl",
        "authorized_tax_csv",
        "tax-row-1",
        "0400000000001",
        2025,
        10_000,
        "unpaid",
        "cert-1",
        "OWNER",
        "100 TEST AVE",
        "manual-import://tax.csv",
        GENERATED_AT,
    )


class TaxCollector:
    def collect(self) -> tuple[TaxDelinquency, ...]:
        return (tax_record(),)


class FailedTaxCollector:
    def collect(self) -> tuple[TaxDelinquency, ...]:
        raise RuntimeError("tax unavailable")


class EmptyTaxCollector:
    def collect(self) -> tuple[TaxDelinquency, ...]:
        return ()


class RealPilot02bAcceptanceTests(unittest.TestCase):
    def _matrix(self, root: Path, text: str = CSV_TEXT) -> Path:
        path = root / "Agent Single Line - COM.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def _run(
        self,
        root: Path,
        database: Path,
        output: str,
        timestamp: str,
        **collectors: object,
    ):
        return run_pilot(
            matrix_path=self._matrix(root),
            database_path=database,
            output_dir=root / output,
            municipality="hialeah",
            generated_at=timestamp,
            property_collector=collectors.get(
                "property_collector", TargetedPropertyCollector()
            ),
            code_collector=collectors.get("code_collector", OverlapCodeCollector()),
            clerk_collector=collectors.get("clerk_collector", ClerkCollector()),
            tax_collector=collectors.get("tax_collector", TaxCollector()),
        )

    def test_r1_honest_queue_requires_substantive_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._run(
                root,
                root / "pilot.sqlite",
                "output",
                GENERATED_AT,
                clerk_collector=EmptyClerkCollector(),
                tax_collector=EmptyTaxCollector(),
            )
            queue = json.loads((root / "output" / "acquisition_queue.json").read_text())
            municipal = json.loads(
                (root / "output" / "municipal_review_queue.json").read_text()
            )
        self.assertEqual(queue, [])
        self.assertEqual(len(municipal), 1)
        self.assertTrue(municipal[0]["municipal_cases_currently_confirmed"])

    def test_r2_rank_explanation_names_substantive_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = self._matrix(
                root,
                (
                    "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
                    "A123,100 Test Ave,A,2400000,90,0,12,unmotivated seller\n"
                ),
            )
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "output",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=EmptyCodeCollector(),
                clerk_collector=EmptyClerkCollector(),
                tax_collector=EmptyTaxCollector(),
            )
            queue = json.loads((root / "output" / "acquisition_queue.json").read_text())
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["scores"]["market_pressure"], 15)
        self.assertIn("market_pressure", queue[0]["ranked_above_next_reason"])
        self.assertNotIn("data_completeness", queue[0]["ranked_above_next_reason"])

    def test_r3_each_collector_outage_preserves_hash_and_disposition(self) -> None:
        scenarios = {
            "county": {"property_collector": FailedPropertyCollector()},
            "case_list": {"code_collector": FailedCodeCollector()},
            "enrichment": {"code_collector": FailedEnrichmentCollector()},
            "clerk": {"clerk_collector": FailedClerkCollector()},
            "tax": {"tax_collector": FailedTaxCollector()},
        }
        for label, failed_collectors in scenarios.items():
            with self.subTest(source=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                database = root / "pilot.sqlite"
                healthy = self._run(root, database, "healthy", GENERATED_AT)
                with sqlite3.connect(database) as connection:
                    property_id, content_hash = connection.execute(
                        """
                        SELECT property_id,content_hash FROM recommendations
                        WHERE pilot_run_id=? AND property_id=(
                            SELECT property_id FROM canonical_properties
                            WHERE folio='0400000000001'
                        )
                        """,
                        (healthy.run_id,),
                    ).fetchone()
                with IntelligenceStore(database) as store:
                    store.record_disposition(
                        property_id,
                        "dismiss",
                        GENERATED_AT,
                        baseline_content_hash=content_hash,
                    )
                failed = self._run(
                    root,
                    database,
                    "failed",
                    "2026-07-25T12:00:00+00:00",
                    **failed_collectors,
                )
                recovered = self._run(
                    root,
                    database,
                    "recovered",
                    "2026-07-26T12:00:00+00:00",
                )
                with sqlite3.connect(database) as connection:
                    rows = connection.execute(
                        """
                        SELECT pilot_run_id,content_hash,change_type,action
                        FROM recommendations WHERE property_id=?
                          AND pilot_run_id IN (?,?,?)
                        ORDER BY generated_at
                        """,
                        (property_id, healthy.run_id, failed.run_id, recovered.run_id),
                    ).fetchall()
                    false_alerts = connection.execute(
                        """
                        SELECT COUNT(*) FROM opportunity_alerts
                        WHERE pilot_run_id IN (?,?)
                        """,
                        (failed.run_id, recovered.run_id),
                    ).fetchone()[0]
                self.assertEqual(len({row[1] for row in rows}), 1)
                self.assertEqual(
                    [(row[2], row[3]) for row in rows[1:]],
                    [("unchanged", "dismiss"), ("unchanged", "dismiss")],
                )
                self.assertEqual(false_alerts, 0)
                warnings = json.loads(
                    (root / "failed" / "source_health_warnings.json").read_text()
                )
                self.assertTrue(warnings)

    def test_r4_healthy_disappearance_resolves_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "pilot.sqlite"
            self._run(root, database, "first", GENERATED_AT)
            self._run(
                root,
                database,
                "second",
                "2026-07-25T12:00:00+00:00",
                code_collector=EmptyCodeCollector(),
            )
            self._run(
                root,
                database,
                "third",
                "2026-07-26T12:00:00+00:00",
                code_collector=EmptyCodeCollector(),
            )
            with sqlite3.connect(database) as connection:
                resolutions = connection.execute(
                    "SELECT COUNT(*) FROM signal_observations WHERE status='resolved'"
                ).fetchone()[0]
            municipal = json.loads(
                (root / "second" / "municipal_review_queue.json").read_text()
            )
            changes = json.loads((root / "second" / "change_summary.json").read_text())
        self.assertEqual(resolutions, 1)
        self.assertEqual(municipal, [])
        self.assertEqual(changes["resolved"], 1)

    def test_r5_tokenized_hazard_ordering_and_adversarial_words(self) -> None:
        descriptions = {
            "cosmetic": "graffiti and sign on dumpster enclosure",
            "administrative": "administrative registration fee",
            "permit": "unpermitted construction",
            "recertification": "expired 40 year recertification",
            "minimum_housing": "minimum housing mold infestation",
            "unsafe_life_safety": "roof collapse with electrical hazard and blocked exit",
        }
        results = {
            label: classify_municipal_case(
                case(
                    case_type="Code Enforcement",
                    status="Notice of Violation",
                    description=description,
                    opened_date="2026-07-01",
                ),
                as_of=GENERATED_AT,
            )
            for label, description in descriptions.items()
        }
        self.assertEqual(
            [results[label].substantive_hazard for label in descriptions],
            list(descriptions),
        )
        self.assertEqual(
            sorted(result.score for result in results.values()),
            [results[label].score for label in descriptions],
        )
        for word in ("assigned", "designated", "design review", "grassy"):
            classified = classify_municipal_case(
                case(case_type="Code Enforcement", description=word),
                as_of=GENERATED_AT,
            )
            self.assertNotEqual(classified.substantive_hazard, "cosmetic")

    def test_r6_never_enriched_case_is_unknown_and_nonnumeric(self) -> None:
        for collector in (
            BareCodeCollector(),
            EmptySuccessfulEnrichmentCollector(),
        ):
            with self.subTest(collector=type(collector).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self._run(
                        root,
                        root / "pilot.sqlite",
                        "output",
                        GENERATED_AT,
                        code_collector=collector,
                    )
                    recommendations = json.loads(
                        (root / "output" / "recommendations.json").read_text()
                    )
                    target = next(
                        item
                        for item in recommendations
                        if item["folio"] == "0400000000001"
                    )
                    coverage = target["municipal_evidence_state"]
                self.assertIsNone(target["municipal_review_urgency_score"])
                self.assertEqual(
                    target["municipal_cases"][0]["substantive_hazard"],
                    "unknown_hazard",
                )
                self.assertIsNone(target["municipal_cases"][0]["severity_score"])
                self.assertEqual(coverage["case_detail"], "unknown_not_run")

    def test_r7_fifty_economics_combinations_enforce_order(self) -> None:
        for index in range(50):
            listing = {
                "list_price": 1_000_000 + index * 1_000,
                "dom": 90 + index,
                "cdom": 120 + index,
                "expenses": 30_000 + index,
            }
            common = {
                "county": {"verified_units": 12},
                "municipal": (),
                "official_records": ({"signal_type": "recorded_liens"},),
                "tax_records": (),
                "missing_fields": (),
            }
            good = calculate_evidence_scores(
                listing={**listing, "noi": 80_000}, **common
            )
            unknown = calculate_evidence_scores(
                listing={**listing, "noi": None}, **common
            )
            bad = calculate_evidence_scores(
                listing={**listing, "noi": -1 - index}, **common
            )
            self.assertEqual(good.metrics["economics_status"], "supported_good")
            self.assertEqual(unknown.metrics["economics_status"], "unknown")
            self.assertEqual(bad.metrics["economics_status"], "demonstrably_bad")
            self.assertGreater(
                acquisition_attractiveness(good.dimensions),
                acquisition_attractiveness(unknown.dimensions),
            )
            self.assertGreater(
                acquisition_attractiveness(unknown.dimensions),
                acquisition_attractiveness(bad.dimensions),
            )
        common_dimensions = unknown.dimensions
        low_positive = replace(common_dimensions, economics=1)
        negative = replace(common_dimensions, economics=-1)
        self.assertGreater(
            acquisition_attractiveness(low_positive),
            acquisition_attractiveness(common_dimensions),
        )
        self.assertGreater(
            acquisition_attractiveness(common_dimensions),
            acquisition_attractiveness(negative),
        )

    def test_r8_one_production_scoring_implementation(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        production = [
            repository / "src/distress_radar/pilot.py",
            repository / "src/distress_radar/orchestration/refresh.py",
            repository / "src/distress_radar/recommendations/features.py",
        ]
        definitions: list[tuple[str, str]] = []
        for path in production:
            tree = ast.parse(path.read_text())
            definitions.extend(
                (path.name, node.name)
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name
                in {
                    "calculate_evidence_scores",
                    "acquisition_attractiveness",
                    "municipal_review_urgency",
                }
            )
        self.assertEqual(
            definitions,
            [
                ("features.py", "acquisition_attractiveness"),
                ("features.py", "municipal_review_urgency"),
                ("features.py", "calculate_evidence_scores"),
            ],
        )
        refresh_text = production[1].read_text()
        self.assertNotIn("_economics_score", refresh_text)
        self.assertNotIn("_market_pressure_score", refresh_text)
        self.assertNotIn("score_owner_motivation", refresh_text)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix = self._matrix(
                root,
                (
                    "MLS Number,Address,Status,List Price,DOM,CDOM,Units,Remarks\n"
                    "A123,100 Test Ave,A,2400000,90,120,12,unmotivated seller\n"
                ),
            )
            off_market = root / "off_market.csv"
            off_market.write_text(
                (
                    "source_record_id,folio,address,city,state,zip_code,"
                    "owner_name,unpaid_taxes,lis_pendens,active_liens,"
                    "ownership_years,absentee,entity_status,"
                    "code_escalation,units,current_noi,stabilized_noi,"
                    "estimated_value,repairs,capex,source_url\n"
                ),
                encoding="utf-8",
            )
            demo = run_fixture_demo(
                matrix_path=matrix,
                off_market_path=off_market,
                output_dir=root / "demo",
                generated_at=GENERATED_AT,
                property_records=(TargetedPropertyCollector().matrix,),
            )
            run_pilot(
                matrix_path=matrix,
                database_path=root / "pilot.sqlite",
                output_dir=root / "pilot",
                municipality="hialeah",
                generated_at=GENERATED_AT,
                property_collector=TargetedPropertyCollector(),
                code_collector=EmptyCodeCollector(),
                clerk_collector=EmptyClerkCollector(),
                tax_collector=EmptyTaxCollector(),
            )
            demo_record = json.loads(demo.json_path.read_text())[0]
            pilot_record = json.loads(
                (root / "pilot" / "recommendations.json").read_text()
            )[0]
        self.assertEqual(
            demo_record["owner_motivation_score"],
            pilot_record["scores"]["owner_motivation"],
        )
        self.assertEqual(
            demo_record["economics_score"],
            pilot_record["scores"]["economics"],
        )
        self.assertEqual(
            demo_record["market_pressure_score"],
            pilot_record["scores"]["market_pressure"],
        )
        self.assertEqual(
            demo_record["recommendation_score"],
            pilot_record["acquisition_attractiveness_score"],
        )

    def test_r9_action_semantics_fail_closed(self) -> None:
        self.assertFalse(
            _evidence_value_supports_action(
                "unknown_action", "anything", {"uuid": "non-empty"}
            )
        )
        self.assertFalse(
            _evidence_value_supports_action(
                "investigate_owner", "official_record", {"signal_type": "release"}
            )
        )
        self.assertFalse(
            _evidence_value_supports_action(
                "contact_broker_for_documents",
                "matrix_listing",
                {"status": "A"},
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._run(root, root / "pilot.sqlite", "output", GENERATED_AT)
            recommendations = json.loads(
                (root / "output" / "recommendations.json").read_text()
            )
        for recommendation in recommendations:
            support = recommendation["action_support"]
            self.assertTrue(support["why_supported"])
            self.assertIn("missing_information", support)
            self.assertTrue(support["next_step"])
            self.assertTrue(support["completion_or_revisit_condition"])

    def test_r10_same_and_next_day_are_stable_and_risk_does_not_rank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "pilot.sqlite"
            clean_sources = {
                "clerk_collector": EmptyClerkCollector(),
                "tax_collector": EmptyTaxCollector(),
            }
            first = self._run(root, database, "first", GENERATED_AT, **clean_sources)
            same = self._run(
                root,
                database,
                "same",
                "2026-07-24T13:00:00+00:00",
                **clean_sources,
            )
            next_day = self._run(
                root,
                database,
                "next",
                "2026-07-25T12:00:00+00:00",
                **clean_sources,
            )
            with sqlite3.connect(database) as connection:
                for run_id in (same.run_id, next_day.run_id):
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM opportunity_alerts
                            WHERE pilot_run_id=?
                            """,
                            (run_id,),
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM recommendations
                            WHERE pilot_run_id=? AND change_type!='unchanged'
                            """,
                            (run_id,),
                        ).fetchone()[0],
                        0,
                    )
            queue = json.loads((root / "next" / "acquisition_queue.json").read_text())
            mls_example = json.loads((root / "next" / "mls_example.json").read_text())
        self.assertEqual(queue, [])
        self.assertIsNotNone(mls_example["record"])
        self.assertIn("not forced", mls_example["label"])
        self.assertNotEqual(first.run_id, next_day.run_id)


if __name__ == "__main__":
    unittest.main()
