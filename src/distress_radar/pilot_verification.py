from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from distress_radar.pilot import run_pilot
from distress_radar.sources.mls.matrix_csv import MatrixCsvImporter

EXPECTED_MATRIX_FILENAME = "Agent Single Line - COM.csv"
EXPECTED_MATRIX_SHA256 = (
    "f85540c3510b294e7aaaeb77f7ee2ab6022c45cdd2bfd3799c1fe9dbbe59c649"
)
EXPECTED_MATRIX_ROWS = 20
EXPECTED_HEADER_MAPPING = {
    "mls_number": "MLS # Link",
    "address": "Address",
    "status": "St",
    "list_price": "Current Price",
    "property_class": "Type of Property",
}


@dataclass(frozen=True)
class GateResult:
    gate: str
    status: str
    evidence: str


@dataclass(frozen=True)
class VerificationResult:
    status: str
    gates: tuple[GateResult, ...]
    first_run_counts: dict[str, int]
    second_run_counts: dict[str, int]
    next_day_run_counts: dict[str, int]
    controlled_change: dict[str, Any]
    source_statuses: dict[str, str]
    output_path: Path


def _count(
    connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()
) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def _active_intersection_count(
    connection: sqlite3.Connection, pilot_run_id: str
) -> int:
    return _count(
        connection,
        """
        SELECT COUNT(DISTINCT property_id) FROM property_signals
        WHERE signal_type='off_market_live_code_case'
          AND status='active'
          AND confirmation_status='confirmed'
          AND pilot_run_id=?
        """,
        (pilot_run_id,),
    )


def _evidence_value_supports_action(
    action: str,
    field_name: str,
    value: Any,
    *,
    value_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    if action == "human_municipal_review":
        return (
            isinstance(value, dict)
            and field_name == "municipal_code_case"
            and bool(value.get("currently_active"))
            and bool(value.get("case_number"))
            and bool(value.get("enforcement_stage"))
            and value.get("substantive_hazard")
            in {
                "unknown_hazard",
                "permit",
                "recertification",
                "minimum_housing",
                "unsafe_life_safety",
            }
        )
    if action == "investigate_owner":
        if not isinstance(value, dict):
            return False
        if field_name == "official_record":
            return str(value.get("signal_type") or "") in {
                "lien",
                "recorded_liens",
                "lis_pendens",
            }
        if field_name == "tax_delinquency":
            from distress_radar.tax_import import is_unpaid_status

            return float(value.get("amount_due") or 0) > 0 and is_unpaid_status(
                value.get("status")
            )
    if action == "verify_identity":
        return field_name in {
            "validated_address",
            "public_property_record",
        } and (
            bool(value)
            or (value_type == "unknown" and bool((metadata or {}).get("reason")))
        )
    if action == "contact_broker_for_documents":
        return (
            field_name == "matrix_listing"
            and isinstance(value, dict)
            and str(value.get("status") or "").casefold() in {"a", "active", "act"}
            and bool(value.get("broker_contact_path"))
            and bool(value.get("missing_documents"))
        )
    if action == "request_documents":
        return (
            field_name.startswith("diligence:")
            and value_type == "unknown"
            and bool((metadata or {}).get("reason"))
        )
    if action == "reject":
        return (
            field_name == "matrix_listing"
            and isinstance(value, dict)
            and isinstance(value.get("noi"), (int, float))
            and float(value["noi"]) <= 0
        )
    if action in {"insufficient_data", "manual_triage"}:
        return value_type == "unknown" and bool((metadata or {}).get("reason"))
    if action == "excluded":
        return field_name in {"public_property_record", "validated_address"}
    if action in {"contact_owner", "contact_broker", "dismiss"}:
        # These require hash-bound disposition validation, not evidence truthiness.
        return False
    if action == "reject_high_risk":
        return (
            field_name == "municipal_code_case"
            and isinstance(value, dict)
            and value.get("substantive_hazard") == "unsafe_life_safety"
            and value.get("confirmation_status") == "currently_confirmed"
        )
    if action == "watch":
        return (
            isinstance(value, dict)
            and bool(value.get("watch_trigger"))
            and bool(value.get("recheck_condition"))
        )
    return False


def _disposition_supports_action(
    connection: sqlite3.Connection,
    *,
    property_id: str,
    content_hash: str,
    action: str,
) -> bool:
    return bool(
        connection.execute(
            """
            SELECT 1 FROM human_dispositions
            WHERE property_id=?
              AND baseline_content_hash=?
              AND active=1
              AND (
                (disposition='dismiss' AND ?='dismiss')
                OR
                (disposition='approved_for_contact'
                 AND ? IN ('contact_owner','contact_broker'))
              )
            LIMIT 1
            """,
            (property_id, content_hash, action, action),
        ).fetchone()
    )


def _controlled_change_is_isolated(
    changes: list[dict[str, Any]],
    *,
    target_mls: str,
    materially_changed_property_ids: set[str],
    target_property_id: str,
) -> bool:
    return (
        len(changes) == 1
        and changes[0].get("mls_number") == target_mls
        and changes[0].get("change_type") == "status_change"
        and materially_changed_property_ids == {target_property_id}
    )


def _controlled_copy(source: Path, destination: Path) -> tuple[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or "St" not in rows[0] or "MLS # Link" not in rows[0] or len(rows) < 2:
        raise ValueError(
            "Controlled change requires real Matrix St and MLS # Link columns"
        )
    status_index = rows[0].index("St")
    mls_index = rows[0].index("MLS # Link")
    before = rows[1][status_index]
    after = "W" if before != "W" else "A"
    target_mls = rows[1][mls_index]
    rows[1][status_index] = after
    with destination.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return f"line 2 / {target_mls} St: {before} -> {after}", target_mls


def _run_tests(repository: Path) -> tuple[bool, int, str]:
    tests = subprocess.run(
        [
            str(repository / ".venv/bin/python"),
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        cwd=repository,
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )
    compile_check = subprocess.run(
        [
            str(repository / ".venv/bin/python"),
            "-m",
            "compileall",
            "-q",
            "src",
            "tests",
        ],
        cwd=repository,
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(
        (
            tests.stdout,
            tests.stderr,
            compile_check.stdout,
            compile_check.stderr,
        )
    ).strip()
    match = re.search(r"Ran (\d+) tests?", output)
    return (
        tests.returncode == 0 and compile_check.returncode == 0,
        int(match.group(1)) if match else 0,
        output,
    )


def verify_real_pilot(
    *,
    matrix_path: Path,
    database_path: Path,
    output_dir: Path,
    municipality: str,
) -> VerificationResult:
    if database_path.exists():
        raise ValueError("Acceptance database must not already exist")
    output_dir.mkdir(parents=True, exist_ok=True)
    first_generated_at = datetime.now(UTC).replace(microsecond=0)
    first = run_pilot(
        matrix_path=matrix_path,
        database_path=database_path,
        output_dir=output_dir / "first",
        municipality=municipality,
        generated_at=first_generated_at.isoformat(),
    )
    with sqlite3.connect(database_path) as connection:
        first_change_count = _count(connection, "SELECT COUNT(*) FROM listing_changes")
    second = run_pilot(
        matrix_path=matrix_path,
        database_path=database_path,
        output_dir=output_dir / "second",
        municipality=municipality,
        generated_at=(first_generated_at + timedelta(hours=1)).isoformat(),
    )
    with sqlite3.connect(database_path) as connection:
        second_change_count = _count(connection, "SELECT COUNT(*) FROM listing_changes")
    next_day = run_pilot(
        matrix_path=matrix_path,
        database_path=database_path,
        output_dir=output_dir / "next-day",
        municipality=municipality,
        generated_at=(first_generated_at + timedelta(days=1)).isoformat(),
    )

    controlled_path = output_dir / "controlled" / matrix_path.name
    controlled_description, controlled_mls = _controlled_copy(
        matrix_path, controlled_path
    )
    controlled_database = output_dir / "controlled.sqlite"
    controlled_baseline = run_pilot(
        matrix_path=matrix_path,
        database_path=controlled_database,
        output_dir=output_dir / "controlled-baseline",
        municipality=municipality,
    )
    with sqlite3.connect(controlled_database) as connection:
        connection.row_factory = sqlite3.Row
        status_changes_before = _count(
            connection,
            "SELECT COUNT(*) FROM listing_changes WHERE change_type='status_change'",
        )
        last_change_rowid = connection.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM listing_changes"
        ).fetchone()[0]
        controlled_property = connection.execute(
            """
            SELECT property_id FROM listing_snapshots
            WHERE mls_number=? ORDER BY fetched_at,snapshot_id LIMIT 1
            """,
            (controlled_mls,),
        ).fetchone()["property_id"]
        action_before = connection.execute(
            """
            SELECT action FROM recommendations
            WHERE property_id=? ORDER BY generated_at DESC,recommendation_id DESC LIMIT 1
            """,
            (controlled_property,),
        ).fetchone()["action"]
    controlled = run_pilot(
        matrix_path=controlled_path,
        database_path=controlled_database,
        output_dir=output_dir / "controlled",
        municipality=municipality,
    )
    with sqlite3.connect(controlled_database) as connection:
        connection.row_factory = sqlite3.Row
        status_changes_after = _count(
            connection,
            "SELECT COUNT(*) FROM listing_changes WHERE change_type='status_change'",
        )
        action_after = connection.execute(
            """
            SELECT action FROM recommendations
            WHERE property_id=? ORDER BY generated_at DESC,recommendation_id DESC LIMIT 1
            """,
            (controlled_property,),
        ).fetchone()["action"]
        controlled_changes = [
            {
                "mls_number": row["mls_number"],
                "change_type": row["change_type"],
                "before": json.loads(row["before_json"]),
                "after": json.loads(row["after_json"]),
            }
            for row in connection.execute(
                """
                SELECT mls_number,change_type,before_json,after_json
                FROM listing_changes WHERE rowid>? ORDER BY rowid
                """,
                (last_change_rowid,),
            ).fetchall()
        ]
        controlled_material_changes = {
            row["property_id"]
            for row in connection.execute(
                """
                SELECT property_id FROM recommendations
                WHERE pilot_run_id=? AND change_type='materially_changed'
                """,
                (controlled.run_id,),
            ).fetchall()
        }
        controlled_isolated = _controlled_change_is_isolated(
            controlled_changes,
            target_mls=controlled_mls,
            materially_changed_property_ids=controlled_material_changes,
            target_property_id=controlled_property,
        )

    failed_database = output_dir / "failed-source.sqlite"
    shutil.copy2(database_path, failed_database)
    failed = run_pilot(
        matrix_path=matrix_path,
        database_path=failed_database,
        output_dir=output_dir / "failed-source",
        municipality=municipality,
        simulate_source_failure=True,
    )
    repository = Path(__file__).resolve().parents[2]
    tests_passed, test_count, test_output = _run_tests(repository)

    required_outputs = {
        "run_manifest.json",
        "import_ledger.csv",
        "source_coverage.json",
        "database_summary.json",
        "recommendations.json",
        "recommendations.csv",
        "daily_brief.md",
        "acquisition_brief.md",
        "qualified_queue.json",
        "acquisition_queue.json",
        "acquisition_queue.csv",
        "acquisition_queue.md",
        "municipal_review_queue.json",
        "municipal_review_queue.csv",
        "municipal_review_queue.md",
        "mls_example.json",
        "source_health_warnings.json",
        "evidence_state.json",
        "human_dispositions.json",
        "change_summary.json",
        "opportunity_changes.json",
        "top_candidate_trace.json",
        "top_candidate_trace.md",
    }
    with matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        matrix_batch = MatrixCsvImporter().import_reader(
            handle,
            fetched_at="acceptance-check",
            source_url=f"manual-import://{matrix_path.name}",
        )
    actual_sha = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    mapping_matches = all(
        matrix_batch.header_mapping.get(logical) == header
        for logical, header in EXPECTED_HEADER_MAPPING.items()
    )
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        real_headers = _count(
            connection,
            """
            SELECT COUNT(*) FROM import_ledger
            WHERE run_id=? AND status='accepted'
            """,
            (first.run_id,),
        )
        verified_matrix = _count(
            connection,
            """
            SELECT COUNT(DISTINCT l.property_id)
            FROM listing_snapshots l
            JOIN property_source_coverage c ON c.property_id=l.property_id
            WHERE c.source_name='miami_dade_property_point_view'
              AND c.state='confirmed_present' AND l.property_id IS NOT NULL
            """,
        )
        persisted_core = all(
            first.database_counts.get(table, 0) > 0
            for table in (
                "canonical_properties",
                "listing_snapshots",
                "source_runs",
                "evidence_items",
                "recommendations",
            )
        )
        live_sources = {
            row["source_name"]: row["state"]
            for row in connection.execute(
                "SELECT source_name,state FROM source_health ORDER BY source_name"
            ).fetchall()
        }
        source_attempts = {
            row["source_name"]: {
                "state": row["state"],
                "records_examined": row["records_examined"],
            }
            for row in connection.execute(
                """
                SELECT source_name,state,records_examined
                FROM source_runs WHERE pilot_run_id=?
                ORDER BY source_name
                """,
                (first.run_id,),
            ).fetchall()
        }
        off_market_count = _active_intersection_count(connection, next_day.run_id)
        second_alerts = _count(
            connection,
            "SELECT COUNT(*) FROM opportunity_alerts WHERE pilot_run_id=?",
            (second.run_id,),
        )
        second_unchanged = _count(
            connection,
            """
            SELECT COUNT(*) FROM recommendations
            WHERE pilot_run_id=? AND change_type='unchanged'
            """,
            (second.run_id,),
        )
        next_day_alerts = _count(
            connection,
            "SELECT COUNT(*) FROM opportunity_alerts WHERE pilot_run_id=?",
            (next_day.run_id,),
        )
        next_day_unchanged = _count(
            connection,
            """
            SELECT COUNT(*) FROM recommendations
            WHERE pilot_run_id=? AND change_type='unchanged'
            """,
            (next_day.run_id,),
        )
        repeat_transitions = _count(
            connection,
            """
            SELECT COUNT(*) FROM signal_observations
            WHERE pilot_run_id IN (?,?)
              AND status IN ('active','resolved')
            """,
            (second.run_id, next_day.run_id),
        )
        first_hashes = {
            row["property_id"]: row["content_hash"]
            for row in connection.execute(
                """
                SELECT property_id,content_hash FROM recommendations
                WHERE pilot_run_id=?
                """,
                (first.run_id,),
            ).fetchall()
        }
        second_hashes = {
            row["property_id"]: row["content_hash"]
            for row in connection.execute(
                """
                SELECT property_id,content_hash FROM recommendations
                WHERE pilot_run_id=?
                """,
                (second.run_id,),
            ).fetchall()
        }
        next_day_hashes = {
            row["property_id"]: row["content_hash"]
            for row in connection.execute(
                """
                SELECT property_id,content_hash FROM recommendations
                WHERE pilot_run_id=?
                """,
                (next_day.run_id,),
            ).fetchall()
        }
        latest_recommendations = connection.execute(
            """
            SELECT r.property_id,r.action,r.explanation_json,r.content_hash
            FROM recommendations r
            WHERE r.recommendation_id=(
                SELECT r2.recommendation_id FROM recommendations r2
                WHERE r2.property_id=r.property_id
                ORDER BY r2.generated_at DESC,r2.recommendation_id DESC LIMIT 1
            )
            """
        ).fetchall()
        evidence_linked = 0
        semantic_evidence = 0
        expected_action_fields = {
            "verify_identity": {"validated_address", "public_property_record"},
            "human_municipal_review": {"municipal_code_case"},
            "contact_broker_for_documents": {
                "matrix_listing",
                "diligence:rent_roll",
                "diligence:T12",
            },
            "investigate_owner": {
                "public_property_record",
                "official_record",
                "tax_delinquency",
            },
            "request_documents": {"diligence:rent_roll", "diligence:T12"},
            "insufficient_data": {"diligence:rent_roll", "diligence:T12"},
            "manual_triage": {"diligence:rent_roll", "diligence:T12"},
            "reject": {"matrix_listing"},
            "excluded": {"validated_address", "public_property_record"},
            "reject_high_risk": {"municipal_code_case"},
        }
        for recommendation in latest_recommendations:
            explanation = json.loads(recommendation["explanation_json"])
            mappings = explanation.get("statement_evidence_ids") or {}
            required_groups = (
                "why_this_property_surfaced",
                "recommended_action",
            )
            ids = {evidence_id for group in mappings.values() for evidence_id in group}
            existing_ids = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT evidence_id FROM evidence_items
                    WHERE property_id=?
                    """,
                    (recommendation["property_id"],),
                ).fetchall()
            }
            action_ids = set(mappings.get("recommended_action") or ())
            action_evidence = [
                row
                for row in connection.execute(
                    """
                    SELECT field_name,value_json,value_type,metadata_json
                    FROM evidence_items
                    WHERE property_id=? AND evidence_id IN (
                        SELECT value FROM json_each(?)
                    )
                    """,
                    (
                        recommendation["property_id"],
                        json.dumps(sorted(action_ids)),
                    ),
                ).fetchall()
            ]
            if (
                all(mappings.get(group) for group in required_groups)
                and ids
                and ids.issubset(existing_ids)
            ):
                evidence_linked += 1
                expected = expected_action_fields.get(recommendation["action"])
                value_supported = any(
                    _evidence_value_supports_action(
                        recommendation["action"],
                        row["field_name"],
                        (
                            json.loads(row["value_json"])
                            if row["value_json"] is not None
                            else None
                        ),
                        value_type=row["value_type"],
                        metadata=json.loads(row["metadata_json"] or "{}"),
                    )
                    for row in action_evidence
                )
                disposition_supported = _disposition_supports_action(
                    connection,
                    property_id=recommendation["property_id"],
                    content_hash=recommendation["content_hash"],
                    action=recommendation["action"],
                )
                if disposition_supported or (
                    expected is not None
                    and {row["field_name"] for row in action_evidence} & expected
                    and value_supported
                ):
                    semantic_evidence += 1

        queue = json.loads((output_dir / "first" / "qualified_queue.json").read_text())
        queue_has_mls = any("mls" in item["discovery_channels"] for item in queue)
        queue_has_off_market = any(
            "off_market" in item["discovery_channels"] for item in queue
        )

    with sqlite3.connect(failed_database) as failed_connection:
        failed_connection.row_factory = sqlite3.Row
        failed_coverage = _count(
            failed_connection,
            """
            SELECT COUNT(*) FROM property_source_coverage
            WHERE source_name='hialeah_tyler_energov'
              AND state='unknown_failed'
            """,
        )
        failure_recommendations = [
            json.loads(row["explanation_json"])
            for row in failed_connection.execute(
                """
                SELECT explanation_json FROM recommendations
                WHERE pilot_run_id=?
                """,
                (failed.run_id,),
            ).fetchall()
        ]
        failure_visible_in_recommendations = any(
            "hialeah_tyler_energov:unknown_failed"
            in (explanation.get("missing_data") or ())
            for explanation in failure_recommendations
        )

    failure_brief = (output_dir / "failed-source" / "daily_brief.md").read_text()
    gates = (
        GateResult(
            "G0",
            "PASS"
            if matrix_path.name == EXPECTED_MATRIX_FILENAME
            and actual_sha == EXPECTED_MATRIX_SHA256
            and len(matrix_batch.accepted) == EXPECTED_MATRIX_ROWS
            else "FAIL",
            (
                f"{matrix_path.name} / {actual_sha} / "
                f"{len(matrix_batch.accepted)} genuine rows"
            ),
        ),
        GateResult(
            "G1",
            "PASS"
            if mapping_matches
            and real_headers == EXPECTED_MATRIX_ROWS
            and first.accepted_rows == EXPECTED_MATRIX_ROWS
            else "FAIL",
            json.dumps(matrix_batch.header_mapping, sort_keys=True),
        ),
        GateResult(
            "G2",
            "PASS" if verified_matrix > 0 else "FAIL",
            f"{verified_matrix} Matrix properties with county presence",
        ),
        GateResult(
            "G3",
            "PASS" if persisted_core else "FAIL",
            json.dumps(first.database_counts, sort_keys=True),
        ),
        GateResult(
            "G4",
            "PASS"
            if all(
                source_attempts.get(source, {}).get("state") == "healthy"
                and source_attempts.get(source, {}).get("records_examined", 0) > 0
                for source in (
                    "miami_dade_property_point_view",
                    "hialeah_tyler_energov",
                )
            )
            else "FAIL",
            json.dumps(source_attempts, sort_keys=True),
        ),
        GateResult(
            "G5",
            "PASS" if off_market_count > 0 and 0 < len(queue) <= 10 else "FAIL",
            (
                f"{off_market_count} distinct active, confirmed, current-run "
                f"off-market intersections; {len(queue)} ranked tasks; "
                f"MLS={queue_has_mls}; off_market={queue_has_off_market}"
            ),
        ),
        GateResult(
            "G6",
            "PASS"
            if failed_coverage > 0
            and failure_visible_in_recommendations
            and "simulated source failure" in failure_brief
            else "FAIL",
            (
                f"{failed_coverage} unknown_failed coverage rows; "
                f"recommendations={failure_visible_in_recommendations}; report=true"
            ),
        ),
        GateResult(
            "G7",
            "PASS"
            if evidence_linked == len(latest_recommendations)
            and semantic_evidence == len(latest_recommendations)
            and evidence_linked > 0
            else "FAIL",
            (
                f"{semantic_evidence}/{len(latest_recommendations)} latest "
                "recommendations have semantically valid statement/action evidence"
            ),
        ),
        GateResult(
            "G8",
            "PASS"
            if {path.name for path in first.output_files} == required_outputs
            else "FAIL",
            f"{len(first.output_files)} database-derived files",
        ),
        GateResult(
            "G9",
            "PASS"
            if first.database_counts["canonical_properties"]
            == second.database_counts["canonical_properties"]
            == next_day.database_counts["canonical_properties"]
            and first.database_counts["listing_snapshots"]
            == second.database_counts["listing_snapshots"]
            == next_day.database_counts["listing_snapshots"]
            and first.database_counts["property_signals"]
            == second.database_counts["property_signals"]
            == next_day.database_counts["property_signals"]
            and first_change_count == second_change_count
            and first_hashes == second_hashes
            and second_hashes == next_day_hashes
            and second_alerts == 0
            and next_day_alerts == 0
            and second_unchanged == len(second_hashes)
            and next_day_unchanged == len(next_day_hashes)
            and repeat_transitions == 0
            else "FAIL",
            (
                f"properties {first.database_counts['canonical_properties']} -> "
                f"{second.database_counts['canonical_properties']}; listings "
                f"{first.database_counts['listing_snapshots']} -> "
                f"{second.database_counts['listing_snapshots']}; changes "
                f"{first_change_count} -> {second_change_count}; signals "
                f"{first.database_counts['property_signals']} -> "
                f"{second.database_counts['property_signals']}; "
                f"same-day alerts={second_alerts}; next-day alerts={next_day_alerts}; "
                f"same-day unchanged={second_unchanged}; "
                f"next-day unchanged={next_day_unchanged}; transitions={repeat_transitions}"
            ),
        ),
        GateResult(
            "G10",
            "PASS"
            if controlled_isolated
            and status_changes_after - status_changes_before == 1
            and action_before != action_after
            else "FAIL",
            (
                f"{controlled_description}; detected "
                f"{status_changes_after - status_changes_before} status change; "
                f"isolated={controlled_isolated}; action {action_before} -> {action_after}"
            ),
        ),
        GateResult(
            "G11",
            "PASS" if tests_passed else "FAIL",
            test_output[-2000:],
        ),
    )
    status = (
        "REAL-PILOT-02: PASS"
        if all(gate.status == "PASS" for gate in gates)
        else "REAL-PILOT-02: FAIL"
    )
    payload = {
        "status": status,
        "gates": [asdict(gate) for gate in gates],
        "first_run": {
            "run_id": first.run_id,
            "counts": first.database_counts,
        },
        "second_run": {
            "run_id": second.run_id,
            "counts": second.database_counts,
        },
        "next_day_run": {
            "run_id": next_day.run_id,
            "counts": next_day.database_counts,
        },
        "test_count": test_count,
        "controlled_run_id": controlled.run_id,
        "controlled_baseline_run_id": controlled_baseline.run_id,
        "failed_source_run_id": failed.run_id,
        "controlled_change": {
            "description": controlled_description,
            "status_changes_detected": status_changes_after - status_changes_before,
        },
        "source_statuses": live_sources,
    }
    output_path = output_dir / "acceptance_gates.json"
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return VerificationResult(
        status=status,
        gates=gates,
        first_run_counts=first.database_counts,
        second_run_counts=second.database_counts,
        next_day_run_counts=next_day.database_counts,
        controlled_change=payload["controlled_change"],
        source_statuses=live_sources,
        output_path=output_path,
    )
