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
            _run(root, database, "first", GENERATED_AT, criteria=CRITERIA)
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
                    WatchSpecification(
                        watch_reason="Wait for a county-source listing content change",
                        evidence_ids=(evidence_id,),
                        trigger_type="material_evidence_change",
                        recheck_condition={
                            "field": "content_hash",
                            "operator": "changes",
                        },
                        evidence_event_trigger={
                            "event_type": "material_evidence_change",
                            "source_name": "miami_dade_property_point_view",
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
                creation_validation = store.connection.execute(
                    """
                    SELECT pilot_run_id,validation_status,source_health_json
                    FROM watch_validation_events WHERE disposition_id=?
                    """,
                    (disposition_id,),
                ).fetchone()
                self.assertEqual(creation_validation["pilot_run_id"], first.run_id)
                self.assertEqual(creation_validation["validation_status"], "valid")
                self.assertEqual(
                    json.loads(creation_validation["source_health_json"]),
                    {"miami_dade_property_point_view": "healthy"},
                )

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
            self.assertEqual(
                unchanged["recommended_action"],
                "watch",
                unchanged.get("watch_semantics"),
            )
            self.assertEqual(
                unchanged["watch_semantics"]["validation_status"], "valid"
            )
            self.assertEqual(unchanged["watch_semantics"]["evidence_states"], {
                evidence_id: "current"
            })
            daily_brief = (root / "unchanged/daily_brief.md").read_text()
            self.assertIn("Watch workflows and validation", daily_brief)
            self.assertIn("Recheck the verified listing", daily_brief)
            self.assertIn("manual_triage", daily_brief)
            _run(
                root,
                database,
                "unrelated-municipal-outage",
                "2026-07-24T12:40:00+00:00",
                criteria=CRITERIA,
                code_collector=FailedCodeCollector(),
            )
            unrelated_outage = json.loads(
                (
                    root
                    / "unrelated-municipal-outage/recommendations.json"
                ).read_text()
            )[0]
            self.assertEqual(unrelated_outage["recommended_action"], "watch")
            self.assertEqual(
                unrelated_outage["watch_semantics"]["validation_status"], "valid"
            )
            self.assertEqual(
                unrelated_outage["watch_semantics"]["source_health"],
                {"miami_dade_property_point_view": "healthy"},
            )

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

    def test_r12_resolved_immutable_signal_link_invalidates_watch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "resolved-signal.sqlite"
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
                    SELECT r.property_id,r.content_hash,e.evidence_id,s.signal_id
                    FROM recommendations r
                    JOIN evidence_items e ON e.property_id=r.property_id
                    JOIN signal_evidence_links l ON l.evidence_id=e.evidence_id
                    JOIN property_signals s ON s.signal_id=l.signal_id
                    WHERE r.pilot_run_id=? AND e.field_name='municipal_code_case'
                      AND s.signal_type='off_market_live_code_case'
                    ORDER BY e.evidence_id LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                property_id = str(row["property_id"])
                evidence_id = str(row["evidence_id"])
                signal_id = str(row["signal_id"])
                disposition_id = store.record_disposition(
                    property_id,
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=str(row["content_hash"]),
                    watch_specification=WatchSpecification(
                        watch_reason=(
                            "Reconsider when a new municipal record is persisted"
                        ),
                        evidence_ids=(evidence_id,),
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
                original = dict(store.active_disposition(property_id))

            unchanged_at = "2026-07-24T16:00:00+00:00"
            _run(
                root,
                database,
                "unchanged",
                unchanged_at,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector(
                    (replace(first_case, fetched_at=unchanged_at),)
                ),
            )
            unchanged_record = next(
                item
                for item in json.loads(
                    (root / "unchanged/recommendations.json").read_text()
                )
                if item["property_id"] == property_id
            )
            self.assertEqual(unchanged_record["recommended_action"], "watch")
            self.assertEqual(
                unchanged_record["watch_semantics"]["validation_status"], "valid"
            )
            failed = _run(
                root,
                database,
                "failed",
                "2026-07-24T16:15:00+00:00",
                criteria=CRITERIA,
                code_collector=FailedCodeCollector(),
            )
            failed_record = next(
                item
                for item in json.loads(
                    (root / "failed/recommendations.json").read_text()
                )
                if item["property_id"] == property_id
            )
            self.assertEqual(failed_record["recommended_action"], "manual_triage")
            self.assertEqual(
                failed_record["watch_semantics"]["validation_status"],
                "invalid_source_unavailable",
            )
            self.assertEqual(
                failed_record["watch_semantics"]["evidence_states"][evidence_id],
                "last_known",
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM opportunity_alerts
                        WHERE pilot_run_id=?
                        """,
                        (failed.run_id,),
                    ).fetchone()[0],
                    0,
                )
            recovered_at = "2026-07-24T16:30:00+00:00"
            recovered = _run(
                root,
                database,
                "recovered",
                recovered_at,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector(
                    (replace(first_case, fetched_at=recovered_at),)
                ),
            )
            recovered_record = next(
                item
                for item in json.loads(
                    (root / "recovered/recommendations.json").read_text()
                )
                if item["property_id"] == property_id
            )
            self.assertEqual(recovered_record["recommended_action"], "watch")
            self.assertEqual(
                recovered_record["watch_semantics"]["validation_status"], "valid"
            )
            with IntelligenceStore(database) as store:
                signal = store.connection.execute(
                    """
                    SELECT evidence_ids_json FROM property_signals
                    WHERE signal_id=?
                    """,
                    (signal_id,),
                ).fetchone()
                self.assertNotIn(
                    evidence_id, json.loads(signal["evidence_ids_json"])
                )
                self.assertEqual(
                    store.connection.execute(
                        """
                        SELECT COUNT(*) FROM signal_evidence_links
                        WHERE signal_id=? AND evidence_id=?
                        """,
                        (signal_id, evidence_id),
                    ).fetchone()[0],
                    1,
                )
                store.connection.execute(
                    """
                    INSERT INTO property_signals (
                        signal_id,property_id,signal_type,observed_at,value_json,
                        evidence_ids_json,status,pilot_run_id,confirmation_status,
                        source_name
                    ) VALUES (
                        'unrelated-active-signal',?,'foreclosure',?,'{}',?,
                        'active',?,'confirmed',
                        'miami_dade_clerk_official_records'
                    )
                    """,
                    (
                        property_id,
                        recovered_at,
                        json.dumps([evidence_id]),
                        recovered.run_id,
                    ),
                )
                store.connection.execute(
                    """
                    INSERT INTO signal_evidence_links (
                        signal_id,evidence_id,property_id,signal_type
                    ) VALUES (
                        'unrelated-active-signal',?,?,'foreclosure'
                    )
                    """,
                    (evidence_id, property_id),
                )
                store.connection.commit()

            disappeared = _run(
                root,
                database,
                "disappeared",
                "2026-07-24T17:00:00+00:00",
                criteria=CRITERIA,
                code_collector=VariableCodeCollector(()),
            )
            disappeared_record = next(
                item
                for item in json.loads(
                    (root / "disappeared/recommendations.json").read_text()
                )
                if item["property_id"] == property_id
            )
            watch = disappeared_record["watch_semantics"]
            self.assertEqual(disappeared_record["recommended_action"], "manual_triage")
            self.assertEqual(
                watch["validation_status"], "invalid_evidence_lifecycle"
            )
            self.assertEqual(watch["evidence_states"][evidence_id], "resolved")
            exact_link = next(
                link
                for link in watch["supporting_evidence"][0]["signal_links"]
                if link["signal_id"] == signal_id
            )
            self.assertEqual(exact_link["status"], "resolved")
            self.assertEqual(exact_link["confirmation_status"], "resolved")
            self.assertFalse(
                any(
                    item["property_id"] == property_id
                    for item in json.loads(
                        (root / "disappeared/acquisition_queue.json").read_text()
                    )
                )
            )
            self.assertEqual(
                [
                    item["property_id"]
                    for item in json.loads(
                        (root / "disappeared/manual_triage_queue.json").read_text()
                    )
                ],
                [property_id],
            )
            self.assertIn(
                "invalid_evidence_lifecycle",
                (root / "disappeared/daily_brief.md").read_text(),
            )
            self.assertIn(
                "invalid_evidence_lifecycle",
                (root / "disappeared/recommendations.csv").read_text(),
            )
            self.assertIn(
                "invalid_evidence_lifecycle",
                (root / "disappeared/manual_triage_queue.md").read_text(),
            )
            with IntelligenceStore(database) as store:
                resolved = store.connection.execute(
                    """
                    SELECT status,confirmation_status FROM property_signals
                    WHERE signal_id=?
                    """,
                    (signal_id,),
                ).fetchone()
                self.assertEqual(tuple(resolved), ("resolved", "resolved"))
                self.assertEqual(
                    dict(store.active_disposition(property_id)),
                    original,
                )
                self.assertEqual(
                    [
                        row["validation_status"]
                        for row in store.connection.execute(
                            """
                            SELECT validation_status FROM watch_validation_events
                            WHERE disposition_id=?
                            ORDER BY validated_at,validation_event_id
                            """,
                            (disposition_id,),
                        ).fetchall()
                    ],
                    [
                        "valid",
                        "valid",
                        "invalid_source_unavailable",
                        "valid",
                        "invalid_evidence_lifecycle",
                    ],
                )
                self.assertEqual(
                    store.connection.execute(
                        """
                        SELECT COUNT(*) FROM watch_trigger_events
                        WHERE disposition_id=?
                        """,
                        (disposition_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    store.connection.execute(
                        """
                        SELECT validation_status FROM watch_validation_events
                        WHERE disposition_id=? AND pilot_run_id=?
                        """,
                        (disposition_id, disappeared.run_id),
                    ).fetchone()[0],
                    "invalid_evidence_lifecycle",
                )

    def test_r12_trigger_schema_rejects_ignored_or_incompatible_signals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "trigger-schema.sqlite"
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
                    FROM recommendations r
                    JOIN evidence_items e ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='municipal_code_case'
                    ORDER BY e.evidence_id LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                property_id = str(row["property_id"])
                evidence_id = str(row["evidence_id"])
                common = {
                    "watch_reason": (
                        "Reconsider only after a supported structured event"
                    ),
                    "evidence_ids": (evidence_id,),
                    "expected_next_action": "human_municipal_review",
                    "creator": "analyst:test-suite",
                }
                contradictory = WatchSpecification(
                    **common,
                    trigger_type="material_evidence_change",
                    recheck_condition={
                        "field": "content_hash",
                        "operator": "changes",
                    },
                    evidence_event_trigger={
                        "event_type": "material_evidence_change",
                        "source_name": "hialeah_tyler_energov",
                        "evidence_class": "municipal_code_case",
                        "signal_type": "foreclosure",
                    },
                )
                with self.assertRaisesRegex(
                    ValueError, "does not permit semantic fields: signal_type"
                ):
                    store.record_disposition(
                        property_id,
                        "watch",
                        GENERATED_AT,
                        baseline_content_hash=str(row["content_hash"]),
                        watch_specification=contradictory,
                    )
                for contradictory_specification in (
                    replace(
                        contradictory,
                        recheck_condition={
                            "field": "content_hash",
                            "operator": "changes",
                            "signal_type": "foreclosure",
                        },
                        evidence_event_trigger={
                            "event_type": "material_evidence_change",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                        },
                    ),
                    replace(
                        contradictory,
                        evidence_event_trigger={
                            "event_type": "material_evidence_change",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                            "municipal_status": "open",
                        },
                    ),
                ):
                    with self.subTest(
                        extra_semantics=contradictory_specification
                    ), self.assertRaises(ValueError):
                        store.record_disposition(
                            property_id,
                            "watch",
                            GENERATED_AT,
                            baseline_content_hash=str(row["content_hash"]),
                            watch_specification=contradictory_specification,
                        )
                forbidden_signal_specs = (
                    WatchSpecification(
                        **common,
                        trigger_type="scheduled_recheck",
                        recheck_condition={
                            "field": "current_time",
                            "operator": "at_or_after",
                            "value": "2026-07-24T13:00:00+00:00",
                        },
                        recheck_at="2026-07-24T13:00:00+00:00",
                        evidence_event_trigger={
                            "event_type": "scheduled_recheck",
                            "source_name": "hialeah_tyler_energov",
                            "signal_type": "SIGNAL",
                        },
                    ),
                    WatchSpecification(
                        **common,
                        trigger_type="material_evidence_change",
                        recheck_condition={
                            "field": "content_hash",
                            "operator": "changes",
                        },
                        evidence_event_trigger={
                            "event_type": "material_evidence_change",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                            "signal_type": "SIGNAL",
                        },
                    ),
                    WatchSpecification(
                        **common,
                        trigger_type="listing_price_reduction",
                        recheck_condition={
                            "field": "list_price",
                            "operator": "decreases",
                        },
                        evidence_event_trigger={
                            "event_type": "listing_price_reduction",
                            "source_name": "matrix_csv",
                            "evidence_class": "matrix_listing",
                            "signal_type": "SIGNAL",
                        },
                    ),
                    WatchSpecification(
                        **common,
                        trigger_type="municipal_status_change",
                        recheck_condition={
                            "field": "municipal_status",
                            "operator": "changes",
                        },
                        evidence_event_trigger={
                            "event_type": "municipal_status_change",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                            "signal_type": "SIGNAL",
                        },
                    ),
                    WatchSpecification(
                        **common,
                        trigger_type="source_recovery",
                        recheck_condition={
                            "field": "source_health",
                            "operator": "equals",
                            "value": "healthy",
                        },
                        evidence_event_trigger={
                            "event_type": "source_recovery",
                            "source_name": "hialeah_tyler_energov",
                            "evidence_class": "municipal_code_case",
                            "previous_source_health_state": "degraded",
                            "recovery_state": "healthy",
                            "successful_recovery": (
                                "A completed healthy municipal source run"
                            ),
                            "reevaluate_evidence_ids": [evidence_id],
                            "last_known_context": (
                                "Municipal case from the prior healthy run"
                            ),
                            "signal_type": "SIGNAL",
                        },
                    ),
                )
                for signal_type in ("unknown_signal", "foreclosure"):
                    for template in forbidden_signal_specs:
                        event = {
                            key: (
                                signal_type if value == "SIGNAL" else value
                            )
                            for key, value in (
                                template.evidence_event_trigger or {}
                            ).items()
                        }
                        with self.subTest(
                            trigger=template.trigger_type,
                            signal_type=signal_type,
                        ), self.assertRaises(ValueError):
                            store.record_disposition(
                                property_id,
                                "watch",
                                GENERATED_AT,
                                baseline_content_hash=str(row["content_hash"]),
                                watch_specification=replace(
                                    template,
                                    evidence_event_trigger=event,
                                ),
                            )
                for signal_type in ("unknown_signal", "foreclosure"):
                    with self.subTest(
                        trigger="new_record", signal_type=signal_type
                    ), self.assertRaises(ValueError):
                        store.record_disposition(
                            property_id,
                            "watch",
                            GENERATED_AT,
                            baseline_content_hash=str(row["content_hash"]),
                            watch_specification=WatchSpecification(
                                **common,
                                trigger_type="new_record",
                                recheck_condition={
                                    "field": "record_count",
                                    "operator": "increases",
                                },
                                evidence_event_trigger={
                                    "event_type": "new_record",
                                    "source_name": "hialeah_tyler_energov",
                                    "signal_type": signal_type,
                                },
                            ),
                        )
                valid_disposition = store.record_disposition(
                    property_id,
                    "watch",
                    GENERATED_AT,
                    baseline_content_hash=str(row["content_hash"]),
                    watch_specification=WatchSpecification(
                        **common,
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
                    ),
                )
                self.assertIsNotNone(valid_disposition)
            rerun_at = "2026-07-24T16:00:00+00:00"
            _run(
                root,
                database,
                "rerun",
                rerun_at,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector(
                    (replace(first_case, fetched_at=rerun_at),)
                ),
            )
            record = next(
                item
                for item in json.loads(
                    (root / "rerun/recommendations.json").read_text()
                )
                if item["property_id"] == property_id
            )
            self.assertEqual(record["recommended_action"], "watch")
            self.assertEqual(
                record["watch_semantics"]["validation_status"], "valid"
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
                disposition_id = store.record_disposition(
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
                original_disposition = dict(store.active_disposition(watched_property))
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
            self.assertEqual(degraded["recommended_action"], "manual_triage")
            self.assertEqual(
                degraded["watch_semantics"]["validation_status"],
                "invalid_source_unavailable",
            )
            self.assertIn(
                "hialeah_tyler_energov=degraded",
                degraded["watch_semantics"]["validation_reason"],
            )
            self.assertIn(
                "last_known",
                set(degraded["watch_semantics"]["evidence_states"].values()),
            )
            healthy_record = next(
                item
                for item in json.loads(
                    (root / "healthy/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(healthy_record["content_hash"], degraded["content_hash"])
            self.assertEqual(degraded["change_type"], "unchanged")
            self.assertEqual(
                degraded["watch_semantics"]["supporting_evidence"][0][
                    "evidence_class"
                ],
                "municipal_code_case",
            )
            self.assertEqual(
                degraded["watch_semantics"]["supporting_evidence"][0]["property_id"],
                watched_property,
            )
            manual_triage = json.loads(
                (root / "degraded/manual_triage_queue.json").read_text()
            )
            self.assertEqual(
                [item["property_id"] for item in manual_triage],
                [watched_property],
            )
            self.assertFalse(
                any(
                    item["property_id"] == watched_property
                    for item in json.loads(
                        (root / "degraded/acquisition_queue.json").read_text()
                    )
                )
            )
            with (root / "degraded/recommendations.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                csv_row = next(
                    row
                    for row in csv.DictReader(handle)
                    if row["property_id"] == watched_property
                )
            self.assertEqual(
                csv_row["watch_validation_status"], "invalid_source_unavailable"
            )
            self.assertIn(
                "invalid_source_unavailable",
                (root / "degraded/daily_brief.md").read_text(),
            )
            self.assertIn(
                "invalid_source_unavailable",
                (root / "degraded/manual_triage_queue.md").read_text(),
            )
            source_warnings = json.loads(
                (root / "degraded/source_health_warnings.json").read_text()
            )
            self.assertTrue(
                any(
                    row["source_name"] == "hialeah_tyler_energov"
                    and row["state"] == "unknown_failed"
                    for row in source_warnings
                )
            )
            disposition_report = next(
                row
                for row in json.loads(
                    (root / "degraded/human_dispositions.json").read_text()
                )
                if row["disposition_id"] == disposition_id
            )
            self.assertEqual(
                disposition_report["creation_watch_validation_status"], "valid"
            )
            self.assertEqual(
                disposition_report["effective_watch_validation_status"],
                "invalid_source_unavailable",
            )
            with IntelligenceStore(database) as store:
                persisted_after_failure = dict(
                    store.connection.execute(
                        """
                        SELECT * FROM human_dispositions
                        WHERE disposition_id=?
                        """,
                        (disposition_id,),
                    ).fetchone()
                )
                self.assertEqual(persisted_after_failure, original_disposition)
                self.assertEqual(
                    store.connection.execute(
                        """
                        SELECT COUNT(*) FROM opportunity_alerts
                        WHERE pilot_run_id=?
                        """,
                        (
                            json.loads(
                                (root / "degraded/run_manifest.json").read_text()
                            )["run_id"],
                        ),
                    ).fetchone()[0],
                    0,
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
                validation_rows = connection.execute(
                    """
                    SELECT validation_status FROM watch_validation_events
                    WHERE disposition_id=?
                    ORDER BY validated_at
                    """,
                    (disposition_id,),
                ).fetchall()
                self.assertIn(
                    ("invalid_source_unavailable",),
                    validation_rows,
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT watch_validation_status FROM human_dispositions
                        WHERE disposition_id=?
                        """,
                        (disposition_id,),
                    ).fetchone()[0],
                    "valid",
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
            database = root / "source-recovery.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            original_case = replace(case(), fetched_at=GENERATED_AT)
            healthy = _run(
                root,
                database,
                "healthy",
                GENERATED_AT,
                criteria=CRITERIA,
                code_collector=VariableCodeCollector((original_case,)),
            )
            failed = _run(
                root,
                database,
                "failed",
                "2026-07-24T18:10:00+00:00",
                criteria=CRITERIA,
                code_collector=FailedCodeCollector(),
            )
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='municipal_code_case'
                      AND e.pilot_run_id=?
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (failed.run_id, healthy.run_id),
                ).fetchone()
                recovery_specification = WatchSpecification(
                    watch_reason="Reevaluate the retained case after verified source recovery",
                    evidence_ids=(str(row["evidence_id"]),),
                    trigger_type="source_recovery",
                    recheck_condition={
                        "field": "source_health",
                        "operator": "equals",
                        "value": "healthy",
                    },
                    evidence_event_trigger={
                        "event_type": "source_recovery",
                        "source_name": "hialeah_tyler_energov",
                        "evidence_class": "municipal_code_case",
                        "previous_source_health_state": "degraded",
                        "recovery_state": "healthy",
                        "successful_recovery": (
                            "A completed healthy municipal source run for the watched source"
                        ),
                        "reevaluate_evidence_ids": [str(row["evidence_id"])],
                        "last_known_context": (
                            "Municipal case retained from the last successful source run"
                        ),
                    },
                    expected_next_action="human_municipal_review",
                    creator="analyst:test-suite",
                )
                with self.assertRaises(ValueError):
                    store.record_disposition(
                        str(row["property_id"]),
                        "watch",
                        "2026-07-24T18:10:00+00:00",
                        baseline_content_hash=str(row["content_hash"]),
                        watch_specification=replace(
                            recovery_specification,
                            evidence_event_trigger={
                                **recovery_specification.evidence_event_trigger,
                                "successful_recovery": "watch",
                            },
                        ),
                    )
                recovery_disposition_id = store.record_disposition(
                    str(row["property_id"]),
                    "watch",
                    "2026-07-24T18:10:00+00:00",
                    baseline_content_hash=str(row["content_hash"]),
                    watch_specification=recovery_specification,
                )
                watched_property = str(row["property_id"])
            _run(
                root,
                database,
                "still-failed",
                "2026-07-24T18:15:00+00:00",
                criteria=CRITERIA,
                code_collector=FailedCodeCollector(),
            )
            still_failed = next(
                item
                for item in json.loads(
                    (root / "still-failed/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(still_failed["recommended_action"], "watch")
            self.assertEqual(
                still_failed["watch_semantics"]["validation_status"], "valid"
            )
            self.assertIn(
                "last_known",
                set(still_failed["watch_semantics"]["evidence_states"].values()),
            )
            self.assertEqual(
                still_failed["watch_semantics"]["source_health"],
                {"hialeah_tyler_energov": "degraded"},
            )
            recovered_case = replace(
                original_case, fetched_at="2026-07-24T18:20:00+00:00"
            )
            _run(
                root,
                database,
                "recovered",
                "2026-07-24T18:20:00+00:00",
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
            self.assertEqual(
                recovered["recommended_action"], "human_municipal_review"
            )
            self.assertEqual(
                recovered["watch_semantics"]["triggered_action"],
                "human_municipal_review",
            )
            with sqlite3.connect(database) as connection:
                validation_statuses = connection.execute(
                    """
                    SELECT validation_status FROM watch_validation_events
                    WHERE disposition_id=? ORDER BY validated_at
                    """,
                    (recovery_disposition_id,),
                ).fetchall()
                self.assertEqual(
                    validation_statuses,
                    [("valid",), ("valid",), ("valid",)],
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "same-source-cross-class.sqlite"
            _matrix(root / "matrix.csv", noi="", expenses="")
            first = _run(root, database, "first", GENERATED_AT, criteria=CRITERIA)
            with IntelligenceStore(database) as store:
                row = store.connection.execute(
                    """
                    SELECT r.property_id,r.content_hash,e.evidence_id
                    FROM recommendations r JOIN evidence_items e
                      ON e.property_id=r.property_id
                    WHERE r.pilot_run_id=? AND e.field_name='validated_address'
                    ORDER BY e.fetched_at DESC LIMIT 1
                    """,
                    (first.run_id,),
                ).fetchone()
                contradictory = WatchSpecification(
                    watch_reason="Reconsider when county-source Matrix listing content changes",
                    evidence_ids=(str(row["evidence_id"]),),
                    trigger_type="material_evidence_change",
                    recheck_condition={
                        "field": "content_hash",
                        "operator": "changes",
                    },
                    evidence_event_trigger={
                        "event_type": "material_evidence_change",
                        "source_name": "miami_dade_property_point_view",
                        "evidence_class": "matrix_listing",
                    },
                    expected_next_action="manual_triage",
                    creator="analyst:test-suite",
                )
                with self.assertRaises(ValueError):
                    store.record_disposition(
                        str(row["property_id"]),
                        "watch",
                        GENERATED_AT,
                        baseline_content_hash=str(row["content_hash"]),
                        watch_specification=contradictory,
                    )
                unrelated_evidence_id = "same-class-wrong-signal-evidence"
                store.connection.execute(
                    """
                    INSERT INTO evidence_items (
                        evidence_id,property_id,field_name,value_json,source_name,
                        source_record_id,source_url,fetched_at,freshness_status,
                        confidence,value_type,metadata_json,pilot_run_id
                    )
                    SELECT ?,property_id,'official_record',value_json,source_name,
                           'wrong-signal-record',source_url,fetched_at,
                           freshness_status,confidence,value_type,metadata_json,
                           pilot_run_id
                    FROM evidence_items WHERE evidence_id=?
                    """,
                    (unrelated_evidence_id, row["evidence_id"]),
                )
                store.connection.execute(
                    """
                    INSERT INTO property_signals (
                        signal_id,property_id,signal_type,observed_at,value_json,
                        evidence_ids_json,status,pilot_run_id,confirmation_status,
                        source_name
                    ) VALUES (
                        'same-class-wrong-signal',?,'foreclosure',?,'{}',?,
                        'active',?,'confirmed','miami_dade_property_point_view'
                    )
                    """,
                    (
                        row["property_id"],
                        GENERATED_AT,
                        json.dumps([unrelated_evidence_id]),
                        first.run_id,
                    ),
                )
                store.connection.execute(
                    """
                    INSERT INTO signal_evidence_links (
                        signal_id,evidence_id,property_id,signal_type
                    ) VALUES (
                        'same-class-wrong-signal',?,?,'foreclosure'
                    )
                    """,
                    (unrelated_evidence_id, row["property_id"]),
                )
                with self.assertRaisesRegex(
                    ValueError, "exact watched signal type"
                ):
                    store.record_disposition(
                        str(row["property_id"]),
                        "watch",
                        GENERATED_AT,
                        baseline_content_hash=str(row["content_hash"]),
                        watch_specification=WatchSpecification(
                            watch_reason="Reconsider when a recorded lien is added",
                            evidence_ids=(unrelated_evidence_id,),
                            trigger_type="new_record",
                            recheck_condition={
                                "field": "record_count",
                                "operator": "increases",
                            },
                            evidence_event_trigger={
                                "event_type": "new_record",
                                "source_name": "miami_dade_property_point_view",
                                "signal_type": "recorded_liens",
                            },
                            expected_next_action="investigate_owner",
                            creator="analyst:test-suite",
                        ),
                    )
                store.connection.execute(
                    "DELETE FROM signal_evidence_links WHERE signal_id='same-class-wrong-signal'"
                )
                store.connection.execute(
                    "DELETE FROM property_signals WHERE signal_id='same-class-wrong-signal'"
                )
                store.connection.execute(
                    "DELETE FROM evidence_items WHERE evidence_id=?",
                    (unrelated_evidence_id,),
                )
                store.connection.execute(
                    """
                    INSERT INTO human_dispositions (
                        disposition_id,property_id,disposition,decided_at,notes,
                        baseline_content_hash,active,watch_reason,
                        watch_evidence_ids_json,watch_trigger_type,
                        watch_recheck_condition_json,watch_recheck_at,
                        watch_event_trigger_json,watch_expected_next_action,
                        creator,watch_validation_status
                    ) VALUES (
                        'legacy-cross-class',?,'watch',?,'historical import',?,1,
                        ?,?,?,?,NULL,?,?,?,'valid'
                    )
                    """,
                    (
                        row["property_id"],
                        GENERATED_AT,
                        row["content_hash"],
                        contradictory.watch_reason,
                        json.dumps(contradictory.evidence_ids),
                        contradictory.trigger_type,
                        json.dumps(contradictory.recheck_condition),
                        json.dumps(contradictory.evidence_event_trigger),
                        contradictory.expected_next_action,
                        contradictory.creator,
                    ),
                )
                store.connection.commit()
                watched_property = str(row["property_id"])
            second = _run(
                root,
                database,
                "reevaluated",
                "2026-07-24T12:05:00+00:00",
                criteria=CRITERIA,
            )
            reevaluated = next(
                item
                for item in json.loads(
                    (root / "reevaluated/recommendations.json").read_text()
                )
                if item["property_id"] == watched_property
            )
            self.assertEqual(reevaluated["recommended_action"], "manual_triage")
            self.assertEqual(
                reevaluated["watch_semantics"]["validation_status"],
                "invalid_evidence_compatibility",
            )
            self.assertEqual(
                reevaluated["watch_semantics"]["supporting_evidence"][0][
                    "evidence_class"
                ],
                "validated_address",
            )
            self.assertEqual(
                reevaluated["watch_semantics"]["supporting_evidence"][0]["source_name"],
                "miami_dade_property_point_view",
            )
            self.assertFalse(
                any(
                    item["property_id"] == watched_property
                    for item in json.loads(
                        (root / "reevaluated/acquisition_queue.json").read_text()
                    )
                )
            )
            self.assertEqual(
                [
                    item["property_id"]
                    for item in json.loads(
                        (root / "reevaluated/manual_triage_queue.json").read_text()
                    )
                ],
                [watched_property],
            )
            self.assertIn(
                "invalid_evidence_compatibility",
                (root / "reevaluated/daily_brief.md").read_text(),
            )
            disposition_report = next(
                row
                for row in json.loads(
                    (root / "reevaluated/human_dispositions.json").read_text()
                )
                if row["disposition_id"] == "legacy-cross-class"
            )
            self.assertEqual(
                disposition_report["creation_watch_validation_status"], "valid"
            )
            self.assertEqual(
                disposition_report["effective_watch_validation_status"],
                "invalid_evidence_compatibility",
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT watch_trigger_type,watch_event_trigger_json,
                               watch_validation_status
                        FROM human_dispositions
                        WHERE disposition_id='legacy-cross-class'
                        """
                    ).fetchone(),
                    (
                        "material_evidence_change",
                        json.dumps(contradictory.evidence_event_trigger),
                        "valid",
                    ),
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT validation_status FROM watch_validation_events
                        WHERE disposition_id='legacy-cross-class'
                          AND pilot_run_id=?
                        """,
                        (second.run_id,),
                    ).fetchone()[0],
                    "invalid_evidence_compatibility",
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
            self.assertEqual(
                unchanged["recommended_action"],
                "watch",
                unchanged.get("watch_semantics"),
            )
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
