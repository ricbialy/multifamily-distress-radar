from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from distress_radar.pilot import PilotRunResult, run_pilot


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
    controlled_change: dict[str, Any]
    source_statuses: dict[str, str]
    output_path: Path


def _count(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def _controlled_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or "St" not in rows[0] or len(rows) < 2:
        raise ValueError("Controlled change requires a real Matrix St column and data row")
    status_index = rows[0].index("St")
    before = rows[1][status_index]
    after = "W" if before != "W" else "A"
    rows[1][status_index] = after
    with destination.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return f"line 2 St: {before} -> {after}"


def _run_tests(repository: Path) -> tuple[bool, str]:
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
    return tests.returncode == 0 and compile_check.returncode == 0, output


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
    first = run_pilot(
        matrix_path=matrix_path,
        database_path=database_path,
        output_dir=output_dir / "first",
        municipality=municipality,
    )
    with sqlite3.connect(database_path) as connection:
        first_change_count = _count(connection, "SELECT COUNT(*) FROM listing_changes")
    second = run_pilot(
        matrix_path=matrix_path,
        database_path=database_path,
        output_dir=output_dir / "second",
        municipality=municipality,
    )
    with sqlite3.connect(database_path) as connection:
        second_change_count = _count(connection, "SELECT COUNT(*) FROM listing_changes")

    controlled_path = output_dir / "controlled" / matrix_path.name
    controlled_description = _controlled_copy(matrix_path, controlled_path)
    with sqlite3.connect(database_path) as connection:
        status_changes_before = _count(
            connection,
            "SELECT COUNT(*) FROM listing_changes WHERE change_type='status_change'",
        )
    controlled = run_pilot(
        matrix_path=controlled_path,
        database_path=database_path,
        output_dir=output_dir / "controlled",
        municipality=municipality,
    )
    with sqlite3.connect(database_path) as connection:
        status_changes_after = _count(
            connection,
            "SELECT COUNT(*) FROM listing_changes WHERE change_type='status_change'",
        )

    failed = run_pilot(
        matrix_path=matrix_path,
        database_path=database_path,
        output_dir=output_dir / "failed-source",
        municipality=municipality,
        simulate_source_failure=True,
    )
    repository = Path(__file__).resolve().parents[2]
    tests_passed, test_output = _run_tests(repository)

    required_outputs = {
        "run_manifest.json",
        "import_ledger.csv",
        "source_coverage.json",
        "database_summary.json",
        "recommendations.json",
        "recommendations.csv",
        "daily_brief.md",
        "top_candidate_trace.json",
        "top_candidate_trace.md",
    }
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
        off_market_count = _count(
            connection,
            """
            SELECT COUNT(*) FROM property_signals
            WHERE signal_type='off_market_live_code_case'
            """,
        )
        failed_coverage = _count(
            connection,
            """
            SELECT COUNT(*) FROM property_source_coverage
            WHERE source_name='hialeah_tyler_energov'
              AND state='unknown_failed'
            """,
        )
        latest_recommendations = connection.execute(
            """
            SELECT r.property_id,r.action,r.explanation_json
            FROM recommendations r
            WHERE r.recommendation_id=(
                SELECT r2.recommendation_id FROM recommendations r2
                WHERE r2.property_id=r.property_id
                ORDER BY r2.generated_at DESC,r2.recommendation_id DESC LIMIT 1
            )
            """
        ).fetchall()
        evidence_linked = 0
        for recommendation in latest_recommendations:
            explanation = json.loads(recommendation["explanation_json"])
            mappings = explanation.get("statement_evidence_ids") or {}
            required_groups = (
                "why_this_property_surfaced",
                "recommended_action",
            )
            ids = {
                evidence_id
                for group in mappings.values()
                for evidence_id in group
            }
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
            if (
                all(mappings.get(group) for group in required_groups)
                and ids
                and ids.issubset(existing_ids)
            ):
                evidence_linked += 1

    failure_brief = (output_dir / "failed-source" / "daily_brief.md").read_text()
    gates = (
        GateResult("G0", "PASS" if first.matrix_sha256 else "FAIL", f"{matrix_path.name} / {first.matrix_sha256}"),
        GateResult("G1", "PASS" if real_headers == first.accepted_rows and real_headers > 0 else "FAIL", f"{real_headers} accepted ledger rows"),
        GateResult("G2", "PASS" if verified_matrix > 0 else "FAIL", f"{verified_matrix} Matrix properties with county presence"),
        GateResult("G3", "PASS" if persisted_core else "FAIL", json.dumps(first.database_counts, sort_keys=True)),
        GateResult(
            "G4",
            "PASS"
            if {"miami_dade_property_point_view", "hialeah_tyler_energov"}.issubset(live_sources)
            else "FAIL",
            json.dumps(live_sources, sort_keys=True),
        ),
        GateResult("G5", "PASS" if off_market_count > 0 else "FAIL", f"{off_market_count} live off-market candidates"),
        GateResult(
            "G6",
            "PASS"
            if failed_coverage > 0 and "simulated source failure" in failure_brief
            else "FAIL",
            f"{failed_coverage} unknown_failed coverage rows; failure visible in brief",
        ),
        GateResult(
            "G7",
            "PASS"
            if evidence_linked == len(latest_recommendations)
            and evidence_linked > 0
            else "FAIL",
            (
                f"{evidence_linked}/{len(latest_recommendations)} latest "
                "recommendations have valid statement/action evidence"
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
            and first.database_counts["listing_snapshots"]
            == second.database_counts["listing_snapshots"]
            and first.database_counts["property_signals"]
            == second.database_counts["property_signals"]
            and first_change_count == second_change_count
            else "FAIL",
            (
                f"properties {first.database_counts['canonical_properties']} -> "
                f"{second.database_counts['canonical_properties']}; listings "
                f"{first.database_counts['listing_snapshots']} -> "
                f"{second.database_counts['listing_snapshots']}; changes "
                f"{first_change_count} -> {second_change_count}; signals "
                f"{first.database_counts['property_signals']} -> "
                f"{second.database_counts['property_signals']}"
            ),
        ),
        GateResult(
            "G10",
            "PASS" if status_changes_after - status_changes_before == 1 else "FAIL",
            f"{controlled_description}; detected {status_changes_after - status_changes_before} status change",
        ),
        GateResult(
            "G11",
            "PASS" if tests_passed else "FAIL",
            test_output[-2000:],
        ),
    )
    status = (
        "REAL VERTICAL SLICE: PASS"
        if all(gate.status == "PASS" for gate in gates)
        else "REAL VERTICAL SLICE: FAIL"
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
        "controlled_run_id": controlled.run_id,
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
        controlled_change=payload["controlled_change"],
        source_statuses=live_sources,
        output_path=output_path,
    )
