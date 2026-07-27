from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_real_pilot_02 import (
    GENERATED_AT,
    TargetedPropertyCollector,
    case,
)
from test_real_pilot_02b import (
    EmptyClerkCollector,
    EmptyCodeCollector,
    EmptyTaxCollector,
    FailedCodeCollector,
)

from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.models import CollectionResult
from distress_radar.pilot import run_pilot
from distress_radar.recommendations.features import (
    InvestmentCriteria,
    acquisition_attractiveness,
    calculate_evidence_scores,
)
from distress_radar.watch_semantics import WatchSpecification


CRITERIA = InvestmentCriteria(0.04, 0.06)


def _matrix(
    path: Path,
    *,
    noi: object,
    expenses: object,
    price: object = 1_000_000,
    status: str = "A",
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "MLS Number",
                "Address",
                "City",
                "Folio",
                "Status",
                "List Price",
                "DOM",
                "CDOM",
                "Units",
                "NOI",
                "Expenses",
                "Remarks",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "MLS Number": "E1",
                "Address": "100 Main St",
                "City": "Hialeah",
                "Folio": "0400000000001",
                "Status": status,
                "List Price": price,
                "DOM": 0,
                "CDOM": 0,
                "Units": 12,
                "NOI": noi,
                "Expenses": expenses,
                "Remarks": "",
            }
        )


def _run(
    root: Path,
    database: Path,
    output_name: str,
    generated_at: str,
    *,
    criteria: InvestmentCriteria | None,
    code_collector: object | None = None,
):
    return run_pilot(
        matrix_path=root / "matrix.csv",
        database_path=database,
        output_dir=root / output_name,
        municipality="hialeah",
        generated_at=generated_at,
        property_collector=TargetedPropertyCollector(),
        code_collector=code_collector or EmptyCodeCollector(),
        clerk_collector=EmptyClerkCollector(),
        tax_collector=EmptyTaxCollector(),
        source_commit_sha="test-real-pilot-02c",
        investment_criteria=criteria,
    )


class VariableCodeCollector:
    def __init__(self, records: tuple) -> None:
        self.records = records

    def collect(self, statuses: tuple[str, ...], **kwargs: object) -> CollectionResult:
        return CollectionResult(self.records, (), statuses)

    def enrich_records(
        self, records: tuple, *, include_violations: bool
    ) -> CollectionResult:
        enriched = tuple(
            replace(
                record,
                violation_count=1,
                violations=(
                    {
                        "ViolationType": "Minimum housing",
                        "Status": "Open",
                    },
                ),
            )
            for record in records
        )
        return CollectionResult(enriched, (), ("Notice of Violation",))


class RealPilot02cAcceptanceTests(unittest.TestCase):
    def test_r11_economics_threshold_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "pilot.sqlite"
            _matrix(root / "matrix.csv", noi=5_000, expenses=50_000)
            _run(root, database, "bad", GENERATED_AT, criteria=CRITERIA)
            record = json.loads((root / "bad/recommendations.json").read_text())[0]
            self.assertEqual(
                record["preliminary_metrics"]["economics_status"],
                "demonstrably_bad",
            )
            self.assertLess(record["scores"]["economics"], 0)
            self.assertFalse(record["acquisition_qualified"])
            for name in (
                "acquisition_queue.json",
                "acquisition_queue.csv",
                "acquisition_queue.md",
            ):
                self.assertNotIn(record["property_id"], (root / "bad" / name).read_text())

            no_criteria_database = root / "no-criteria.sqlite"
            _run(
                root,
                no_criteria_database,
                "no-criteria",
                "2026-07-24T12:01:00+00:00",
                criteria=None,
            )
            no_criteria = json.loads(
                (root / "no-criteria/recommendations.json").read_text()
            )[0]
            self.assertEqual(
                no_criteria["preliminary_metrics"]["investment_criteria"]["state"],
                "criteria_not_configured",
            )
            self.assertEqual(
                no_criteria["preliminary_metrics"]["economics_status"], "unknown"
            )
            self.assertFalse(no_criteria["acquisition_qualified"])

        common = dict(
            county={"verified_units": 12},
            municipal=(),
            official_records=(),
            tax_records=(),
            missing_fields=(),
            investment_criteria=CRITERIA,
        )
        rates = [
            0.0,
            0.001,
            0.0399,
            0.03996,
            0.04,
            0.0401,
            0.05,
            0.0599,
            0.05996,
            0.06,
            0.0601,
            0.08,
        ]
        expected = [
            "demonstrably_bad",
            "demonstrably_bad",
            "demonstrably_bad",
            "demonstrably_bad",
            "supported_neutral",
            "supported_neutral",
            "supported_neutral",
            "supported_neutral",
            "supported_neutral",
            "supported_good",
            "supported_good",
            "supported_good",
        ]
        results = []
        for price in (600_000, 1_000_000, 2_400_000, 5_000_000, 10_000_000):
            for rate, status in zip(rates, expected, strict=True):
                result = calculate_evidence_scores(
                    listing={
                        "list_price": price,
                        "noi": price * rate,
                        "expenses": 25_000,
                    },
                    **common,
                )
                self.assertEqual(result.metrics["economics_status"], status)
                results.append(result)
        self.assertEqual(len(results), 60)

        by_state = {
            status: calculate_evidence_scores(
                listing={
                    "list_price": 1_000_000,
                    "noi": noi,
                    "expenses": expenses,
                },
                **common,
            )
            for status, noi, expenses in (
                ("supported_good", 60_000, 20_000),
                ("supported_neutral", 40_000, 20_000),
                ("unknown", 60_000, None),
                ("demonstrably_bad", 39_000, 20_000),
            )
        }
        ordered = [
            acquisition_attractiveness(by_state[state].dimensions)
            for state in (
                "supported_good",
                "supported_neutral",
                "unknown",
                "demonstrably_bad",
            )
        ]
        self.assertEqual(ordered, sorted(ordered, reverse=True))
        self.assertEqual(len(set(ordered)), 4)
        for invalid in (
            (-0.01, 0.06),
            (0.06, 0.04),
            (4, 6),
            (float("nan"), 0.06),
            (0.04, float("inf")),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                InvestmentCriteria(*invalid)
        for listing in (
            {"list_price": 1_000_000, "noi": 60_000, "expenses": None},
            {"list_price": 1_000_000, "reported_cap_rate": 0.08},
            {"list_price": None, "noi": 60_000, "expenses": 20_000},
            {"list_price": 1_000_000, "noi": float("nan"), "expenses": 20_000},
        ):
            with self.subTest(unsupported=listing):
                unsupported = calculate_evidence_scores(listing=listing, **common)
                self.assertEqual(
                    unsupported.metrics["economics_status"], "unknown"
                )
                self.assertIsNone(unsupported.dimensions.economics)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "criteria-change.sqlite"
            _matrix(root / "matrix.csv", noi=60_000, expenses=20_000)
            first = _run(root, database, "first", GENERATED_AT, criteria=CRITERIA)
            first_record = json.loads(
                (root / "first/recommendations.json").read_text()
            )[0]
            with IntelligenceStore(database) as store:
                store.record_disposition(
                    first_record["property_id"],
                    "dismiss",
                    GENERATED_AT,
                    baseline_content_hash=first_record["content_hash"],
                )
            second = _run(
                root,
                database,
                "changed-criteria",
                "2026-07-24T12:05:00+00:00",
                criteria=InvestmentCriteria(0.07, 0.08),
            )
            second_record = json.loads(
                (root / "changed-criteria/recommendations.json").read_text()
            )[0]
            self.assertNotEqual(
                first_record["content_hash"], second_record["content_hash"]
            )
            self.assertEqual(second_record["change_type"], "materially_changed")
            self.assertNotEqual(second_record["recommended_action"], "dismiss")
            self.assertEqual(
                second_record["preliminary_metrics"]["economics_status"],
                "demonstrably_bad",
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM opportunity_alerts
                        WHERE pilot_run_id=? AND alert_type='materially_changed'
                        """,
                        (second.run_id,),
                    ).fetchone()[0],
                    1,
                )

    def test_r12_watch_semantic_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "pilot.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            first = _run(root, database, "first", GENERATED_AT, criteria=CRITERIA)
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r
                    JOIN evidence_items e ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='validated_address'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                property_id = str(row["property_id"])
                evidence_id = str(row["evidence_id"])
                with self.assertRaises(ValueError):
                    store.record_disposition(
                        property_id,
                        "watch",
                        GENERATED_AT,
                        baseline_content_hash=str(row["content_hash"]),
                        notes="watch",
                    )
                specification = WatchSpecification(
                    watch_reason="Recheck the verified listing after the analyst deadline",
                    evidence_ids=(evidence_id,),
                    trigger_type="scheduled_recheck",
                    recheck_condition={
                        "field": "current_time",
                        "operator": "at_or_after",
                        "value": "2026-07-24T13:00:00+00:00",
                    },
                    recheck_at="2026-07-24T13:00:00+00:00",
                    expected_next_action="manual_triage",
                    creator="analyst:test-suite",
                )
                invalid_specs = (
                    replace(specification, watch_reason="watch"),
                    replace(specification, evidence_ids=()),
                    replace(specification, evidence_ids=("missing-evidence",)),
                    replace(specification, trigger_type="unsupported"),
                    replace(specification, recheck_at=None),
                    replace(
                        specification,
                        recheck_at="2026-07-24T11:00:00+00:00",
                        recheck_condition={
                            "field": "current_time",
                            "operator": "at_or_after",
                            "value": "2026-07-24T11:00:00+00:00",
                        },
                    ),
                    replace(specification, recheck_condition={}),
                    replace(specification, expected_next_action="check_later"),
                    replace(specification, creator="system"),
                    WatchSpecification(
                        watch_reason="Wait for a supported listing price reduction",
                        evidence_ids=(evidence_id,),
                        trigger_type="listing_price_reduction",
                        recheck_condition={
                            "field": "list_price",
                            "operator": "decreases",
                        },
                        evidence_event_trigger={
                            "event_type": "listing_price_reduction",
                            "source_name": "matrix_csv",
                            "evidence_class": "matrix_listing",
                        },
                        expected_next_action="manual_triage",
                        creator="analyst:test-suite",
                    ),
                )
                for invalid_specification in invalid_specs:
                    with self.subTest(
                        invalid_watch=invalid_specification
                    ), self.assertRaises(ValueError):
                        store.record_disposition(
                            property_id,
                            "watch",
                            GENERATED_AT,
                            baseline_content_hash=str(row["content_hash"]),
                            watch_specification=invalid_specification,
                        )
                disposition_id = store.record_disposition(
                    property_id,
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=str(row["content_hash"]),
                    watch_specification=specification,
                )
                persisted = store.active_disposition(property_id)
                self.assertEqual(persisted["watch_validation_status"], "valid")
                self.assertEqual(persisted["disposition_id"], disposition_id)

            _run(
                root,
                database,
                "unchanged",
                "2026-07-24T12:30:00+00:00",
                criteria=CRITERIA,
            )
            unchanged = json.loads(
                (root / "unchanged/recommendations.json").read_text()
            )[0]
            self.assertEqual(unchanged["recommended_action"], "watch")
            self.assertEqual(
                unchanged["watch_semantics"]["validation_status"], "valid"
            )
            self.assertEqual(unchanged["watch_semantics"]["evidence_states"], {
                evidence_id: "current"
            })
            daily_brief = (root / "unchanged/daily_brief.md").read_text()
            self.assertIn("Validated watch workflows", daily_brief)
            self.assertIn("Recheck the verified listing", daily_brief)
            self.assertIn("manual_triage", daily_brief)

            _run(
                root,
                database,
                "due",
                "2026-07-24T13:01:00+00:00",
                criteria=CRITERIA,
            )
            due = json.loads((root / "due/recommendations.json").read_text())[0]
            self.assertEqual(due["recommended_action"], "manual_triage")
            self.assertEqual(due["watch_semantics"]["triggered_action"], "manual_triage")
            _run(
                root,
                database,
                "repeat",
                "2026-07-24T13:02:00+00:00",
                criteria=CRITERIA,
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM watch_trigger_events"
                    ).fetchone()[0],
                    1,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "degraded-recovery.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            original_case = replace(case(), fetched_at=GENERATED_AT)
            first = _run(
                root,
                database,
                "healthy",
                GENERATED_AT,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((original_case,)),
            )
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='municipal_code_case'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                watched_property = str(row["property_id"])
                store.record_disposition(
                    watched_property,
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=row["content_hash"],
                    watch_specification=WatchSpecification(
                        watch_reason="Reconsider only after municipal evidence changes",
                        evidence_ids=(row["evidence_id"],),
                        trigger_type="material_evidence_change",
                        recheck_condition={
                            "field": "content_hash",
                            "operator": "changes",
                        },
                        evidence_event_trigger={
                            "event_type": "material_evidence_change",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                        },
                        expected_next_action="human_municipal_review",
                        creator="analyst:test-suite",
                    ),
                )
            _run(
                root,
                database,
                "degraded",
                "2026-07-24T18:00:00+00:00",
                criteria=CRITERIA,
                code_collector=FailedCodeCollector(),
            )
            degraded = next(
                item
                for item in json.loads(
                    (root / "degraded/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(degraded["recommended_action"], "watch")
            self.assertIn(
                "last_known",
                set(degraded["watch_semantics"]["evidence_states"].values()),
            )
            recovered_case = replace(
                original_case, fetched_at="2026-07-24T18:05:00+00:00"
            )
            _run(
                root,
                database,
                "recovered",
                "2026-07-24T18:05:00+00:00",
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((recovered_case,)),
            )
            recovered = next(
                item
                for item in json.loads(
                    (root / "recovered/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(recovered["recommended_action"], "watch")
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM watch_trigger_events"
                    ).fetchone()[0],
                    0,
                )
                connection.execute(
                    "UPDATE human_dispositions SET active=0 WHERE property_id=?",
                    (watched_property,),
                )
                connection.execute(
                    """
                    INSERT INTO human_dispositions (
                      disposition_id,property_id,disposition,decided_at,notes,
                      baseline_content_hash,active
                    ) VALUES ('legacy-watch',?,'watch',?,'watch',?,1)
                    """,
                    (watched_property, GENERATED_AT, recovered["content_hash"]),
                )
                connection.commit()
            _run(
                root,
                database,
                "legacy",
                "2026-07-24T18:06:00+00:00",
                criteria=CRITERIA,
            )
            legacy_rows = json.loads(
                (root / "legacy/human_dispositions.json").read_text()
            )
            legacy = next(
                row for row in legacy_rows if row["disposition_id"] == "legacy-watch"
            )
            self.assertEqual(
                legacy["effective_watch_validation_status"],
                "legacy_incomplete_watch",
            )
            legacy_rec = next(
                item
                for item in json.loads(
                    (root / "legacy/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertNotEqual(legacy_rec["recommended_action"], "watch")
            self.assertNotIn(
                legacy_rec["recommended_action"], {"contact_owner", "contact_broker"}
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "municipal-status.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            first_case = replace(case(), fetched_at=GENERATED_AT)
            first = _run(
                root,
                database,
                "first",
                GENERATED_AT,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((first_case,)),
            )
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='municipal_code_case'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                store.record_disposition(
                    row["property_id"],
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=row["content_hash"],
                    watch_specification=WatchSpecification(
                        watch_reason="Reconsider only when municipal case status changes",
                        evidence_ids=(row["evidence_id"],),
                        trigger_type="municipal_status_change",
                        recheck_condition={
                            "field": "municipal_status",
                            "operator": "changes",
                        },
                        evidence_event_trigger={
                            "event_type": "municipal_status_change",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                        },
                        expected_next_action="human_municipal_review",
                        creator="analyst:test-suite",
                    ),
                )
                watched_property = str(row["property_id"])
            changed_case = replace(
                first_case,
                status="Notice of Hearing Sent",
                fetched_at="2026-07-24T17:00:00+00:00",
            )
            _run(
                root,
                database,
                "status-change",
                "2026-07-24T17:00:00+00:00",
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((changed_case,)),
            )
            changed = next(
                item
                for item in json.loads(
                    (root / "status-change/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(
                changed["recommended_action"], "human_municipal_review"
            )
            self.assertEqual(
                changed["watch_semantics"]["triggered_action"],
                "human_municipal_review",
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "material-event.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            first = _run(root, database, "first", GENERATED_AT, criteria=CRITERIA)
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='matrix_listing'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                store.record_disposition(
                    row["property_id"],
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=row["content_hash"],
                    watch_specification=WatchSpecification(
                        watch_reason="Reconsider only when the persisted Matrix evidence changes",
                        evidence_ids=(row["evidence_id"],),
                        trigger_type="material_evidence_change",
                        recheck_condition={
                            "field": "content_hash",
                            "operator": "changes",
                        },
                        evidence_event_trigger={
                            "event_type": "material_evidence_change",
                            "source_name": "matrix_csv",
                            "evidence_class": "matrix_listing",
                        },
                        expected_next_action="manual_triage",
                        creator="analyst:test-suite",
                    ),
                )
                watched_property = str(row["property_id"])
            _run(
                root,
                database,
                "criteria-only",
                "2026-07-24T15:00:00+00:00",
                criteria=InvestmentCriteria(0.05, 0.07),
            )
            criteria_only = next(
                item
                for item in json.loads(
                    (root / "criteria-only/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(criteria_only["recommended_action"], "watch")
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM watch_trigger_events"
                    ).fetchone()[0],
                    0,
                )
            _matrix(root / "matrix.csv", noi="", expenses="", status="W")
            _run(
                root,
                database,
                "matrix-change",
                "2026-07-24T15:05:00+00:00",
                criteria=InvestmentCriteria(0.05, 0.07),
            )
            changed = next(
                item
                for item in json.loads(
                    (root / "matrix-change/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(changed["recommended_action"], "manual_triage")
            self.assertEqual(
                changed["watch_semantics"]["triggered_action"], "manual_triage"
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "new-record.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            first_case = replace(case(), fetched_at=GENERATED_AT)
            first = _run(
                root,
                database,
                "first",
                GENERATED_AT,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((first_case,)),
            )
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='municipal_code_case'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                store.record_disposition(
                    row["property_id"],
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=row["content_hash"],
                    watch_specification=WatchSpecification(
                        watch_reason="Reconsider when a new municipal record is persisted",
                        evidence_ids=(row["evidence_id"],),
                        trigger_type="new_record",
                        recheck_condition={
                            "field": "record_count",
                            "operator": "increases",
                        },
                        evidence_event_trigger={
                            "event_type": "new_record",
                            "source_name": "hialeah_tyler_energov",
                            "signal_type": "off_market_live_code_case",
                        },
                        expected_next_action="human_municipal_review",
                        creator="analyst:test-suite",
                    ),
                )
                watched_property = str(row["property_id"])
            unchanged_case = replace(
                first_case, fetched_at="2026-07-24T16:00:00+00:00"
            )
            _run(
                root,
                database,
                "unchanged",
                "2026-07-24T16:00:00+00:00",
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((unchanged_case,)),
            )
            unchanged = next(
                item
                for item in json.loads(
                    (root / "unchanged/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(unchanged["recommended_action"], "watch")
            second_case = replace(
                case(record_id="case-new"),
                fetched_at="2026-07-24T16:05:00+00:00",
            )
            _run(
                root,
                database,
                "new-record",
                "2026-07-24T16:05:00+00:00",
                criteria=CRITERIA,
                code_collector=VariableCodeCollector(
                    (
                        replace(
                            first_case,
                            fetched_at="2026-07-24T16:05:00+00:00",
                        ),
                        second_case,
                    )
                ),
            )
            new_record = next(
                item
                for item in json.loads(
                    (root / "new-record/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(
                new_record["recommended_action"], "human_municipal_review"
            )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "event.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            first = _run(root, database, "first", GENERATED_AT, criteria=CRITERIA)
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='matrix_listing'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                store.record_disposition(
                    row["property_id"],
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=row["content_hash"],
                    watch_specification=WatchSpecification(
                        watch_reason="Reconsider only after a persisted listing price reduction",
                        evidence_ids=(row["evidence_id"],),
                        trigger_type="listing_price_reduction",
                        recheck_condition={
                            "field": "list_price",
                            "operator": "decreases",
                        },
                        evidence_event_trigger={
                            "event_type": "listing_price_reduction",
                            "source_name": "matrix_csv",
                            "evidence_class": "matrix_listing",
                        },
                        expected_next_action="request_documents",
                        creator="analyst:test-suite",
                    ),
                )
                watched_property = str(row["property_id"])
            _matrix(
                root / "matrix.csv",
                noi="",
                expenses="",
                price=900_000,
            )
            _run(
                root,
                database,
                "reduced",
                "2026-07-24T14:00:00+00:00",
                criteria=CRITERIA,
            )
            reduced = next(
                item
                for item in json.loads(
                    (root / "reduced/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(reduced["recommended_action"], "request_documents")
            _run(
                root,
                database,
                "repeat-reduction",
                "2026-07-24T14:01:00+00:00",
                criteria=CRITERIA,
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM watch_trigger_events"
                    ).fetchone()[0],
                    1,
                )
