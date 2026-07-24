from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from distress_radar.domain.evidence import EvidenceItem
from distress_radar.domain.listing import ListingChange, ListingSnapshot
from distress_radar.domain.property import CanonicalProperty
from distress_radar.sources.base import SourceHealthState
from distress_radar.sources.mls.matrix_csv import detect_listing_changes


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntelligenceStore:
    """Normalized SQLite repository for cross-channel intelligence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def __enter__(self) -> "IntelligenceStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS canonical_properties (
                property_id TEXT PRIMARY KEY, folio TEXT,
                address TEXT NOT NULL, municipality TEXT NOT NULL,
                jurisdiction TEXT NOT NULL, latitude REAL, longitude REAL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_canonical_properties_folio
                ON canonical_properties(folio) WHERE folio IS NOT NULL;
            CREATE TABLE IF NOT EXISTS canonical_owners (
                owner_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                entity_type TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owner_aliases (
                owner_id TEXT NOT NULL REFERENCES canonical_owners(owner_id),
                alias TEXT NOT NULL, normalized_alias TEXT NOT NULL,
                source_record_id TEXT, confidence REAL,
                PRIMARY KEY(owner_id, normalized_alias)
            );
            CREATE TABLE IF NOT EXISTS property_ownership (
                property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
                owner_id TEXT NOT NULL REFERENCES canonical_owners(owner_id),
                start_date TEXT, end_date TEXT, source_record_id TEXT NOT NULL,
                confidence REAL, PRIMARY KEY(property_id, owner_id, source_record_id)
            );
            CREATE TABLE IF NOT EXISTS source_runs (
                run_id TEXT PRIMARY KEY, source_name TEXT NOT NULL,
                started_at TEXT NOT NULL, completed_at TEXT, state TEXT NOT NULL,
                records_examined INTEGER NOT NULL DEFAULT 0,
                records_changed INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS source_health (
                source_name TEXT PRIMARY KEY, state TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL, last_success_at TEXT,
                error_message TEXT, records_examined INTEGER NOT NULL DEFAULT 0,
                records_changed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS source_records (
                source_name TEXT NOT NULL, source_record_id TEXT NOT NULL,
                run_id TEXT REFERENCES source_runs(run_id),
                source_url TEXT, fetched_at TEXT NOT NULL,
                raw_payload_json TEXT NOT NULL, normalized_payload_json TEXT,
                payload_hash TEXT, PRIMARY KEY(source_name, source_record_id, fetched_at)
            );
            CREATE TABLE IF NOT EXISTS evidence_items (
                evidence_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                field_name TEXT NOT NULL, value_json TEXT,
                source_name TEXT NOT NULL, source_record_id TEXT NOT NULL,
                source_url TEXT, fetched_at TEXT NOT NULL,
                freshness_status TEXT NOT NULL, confidence REAL,
                value_type TEXT NOT NULL, metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS property_signals (
                signal_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                signal_type TEXT NOT NULL, observed_at TEXT NOT NULL,
                value_json TEXT, evidence_ids_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS underwriting_runs (
                run_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                model_name TEXT NOT NULL, as_of TEXT NOT NULL,
                inputs_json TEXT NOT NULL, outputs_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                recommendation_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                generated_at TEXT NOT NULL, action TEXT NOT NULL,
                scores_json TEXT NOT NULL, explanation_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS human_decisions (
                decision_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                decision_type TEXT NOT NULL, decided_at TEXT NOT NULL, notes TEXT
            );
            CREATE TABLE IF NOT EXISTS acquisition_outcomes (
                outcome_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                outcome_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
                amount REAL, reason TEXT, metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ml_models (
                model_id TEXT PRIMARY KEY, target TEXT NOT NULL,
                version TEXT NOT NULL, trained_at TEXT,
                metrics_json TEXT NOT NULL, model_card_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ml_predictions (
                prediction_id TEXT PRIMARY KEY, model_id TEXT NOT NULL
                    REFERENCES ml_models(model_id),
                property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
                predicted_at TEXT NOT NULL, as_of TEXT NOT NULL,
                probability REAL, features_json TEXT NOT NULL,
                explanation_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listing_snapshots (
                snapshot_id TEXT PRIMARY KEY, property_id TEXT
                    REFERENCES canonical_properties(property_id),
                mls_number TEXT NOT NULL, source_name TEXT NOT NULL,
                fetched_at TEXT NOT NULL, status TEXT, list_price REAL,
                dom INTEGER, cdom INTEGER, normalized_json TEXT NOT NULL,
                raw_payload_json TEXT NOT NULL,
                UNIQUE(mls_number, source_name, fetched_at)
            );
            CREATE TABLE IF NOT EXISTS listing_changes (
                change_id TEXT PRIMARY KEY, mls_number TEXT NOT NULL,
                source_name TEXT NOT NULL, detected_at TEXT NOT NULL,
                change_type TEXT NOT NULL, before_json TEXT, after_json TEXT
            );
            """
        )
        self.connection.commit()

    def upsert_property(self, prop: CanonicalProperty) -> None:
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO canonical_properties (
                property_id,folio,address,municipality,jurisdiction,
                latitude,longitude,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(property_id) DO UPDATE SET
                folio=excluded.folio,address=excluded.address,
                municipality=excluded.municipality,
                jurisdiction=excluded.jurisdiction,latitude=excluded.latitude,
                longitude=excluded.longitude,updated_at=excluded.updated_at
            """,
            (
                prop.property_id,
                prop.folio,
                prop.address,
                prop.municipality,
                prop.jurisdiction,
                prop.latitude,
                prop.longitude,
                now,
                now,
            ),
        )
        self.connection.commit()

    def add_evidence(self, property_id: str, evidence: EvidenceItem) -> str:
        evidence_id = str(uuid4())
        self.connection.execute(
            """
            INSERT INTO evidence_items (
                evidence_id,property_id,field_name,value_json,source_name,
                source_record_id,source_url,fetched_at,freshness_status,
                confidence,value_type,metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                evidence_id,
                property_id,
                evidence.field,
                json.dumps(evidence.value),
                evidence.source,
                evidence.source_record_id,
                evidence.source_url,
                evidence.fetched_at,
                evidence.freshness_status.value,
                evidence.confidence,
                evidence.value_type.value,
                json.dumps(evidence.metadata, sort_keys=True),
            ),
        )
        self.connection.commit()
        return evidence_id

    def property_evidence(self, property_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT field_name,value_json,source_name,source_record_id,source_url,
                   fetched_at,freshness_status,confidence,value_type,metadata_json
            FROM evidence_items WHERE property_id=? ORDER BY fetched_at,evidence_id
            """,
            (property_id,),
        ).fetchall()
        return [
            {
                "field": row["field_name"],
                "value": json.loads(row["value_json"]),
                "source": row["source_name"],
                "source_record_id": row["source_record_id"],
                "source_url": row["source_url"],
                "fetched_at": row["fetched_at"],
                "freshness_status": row["freshness_status"],
                "confidence": row["confidence"],
                "value_type": row["value_type"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def start_source_run(self, source_name: str) -> str:
        run_id = str(uuid4())
        self.connection.execute(
            "INSERT INTO source_runs (run_id,source_name,started_at,state) VALUES (?,?,?,'degraded')",
            (run_id, source_name, utc_now()),
        )
        self.connection.commit()
        return run_id

    def finish_source_run(
        self,
        *,
        run_id: str,
        state: SourceHealthState,
        records_examined: int,
        records_changed: int,
        error_message: str | None = None,
    ) -> None:
        now = utc_now()
        row = self.connection.execute(
            "SELECT source_name FROM source_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        self.connection.execute(
            """
            UPDATE source_runs SET completed_at=?,state=?,records_examined=?,
                records_changed=?,error_message=? WHERE run_id=?
            """,
            (now, state.value, records_examined, records_changed, error_message, run_id),
        )
        last_success = now if state == SourceHealthState.HEALTHY else None
        self.connection.execute(
            """
            INSERT INTO source_health (
                source_name,state,last_attempt_at,last_success_at,error_message,
                records_examined,records_changed
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(source_name) DO UPDATE SET
                state=excluded.state,last_attempt_at=excluded.last_attempt_at,
                last_success_at=COALESCE(excluded.last_success_at,source_health.last_success_at),
                error_message=excluded.error_message,
                records_examined=excluded.records_examined,
                records_changed=excluded.records_changed
            """,
            (
                row["source_name"],
                state.value,
                now,
                last_success,
                error_message,
                records_examined,
                records_changed,
            ),
        )
        self.connection.commit()

    def source_coverage_warnings(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT source_name,state,last_attempt_at,last_success_at,error_message
            FROM source_health WHERE state != 'healthy' ORDER BY source_name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def save_listing_snapshot(
        self, snapshot: ListingSnapshot, property_id: str | None = None
    ) -> tuple[ListingChange, ...]:
        previous_row = self.connection.execute(
            """
            SELECT fetched_at,normalized_json,raw_payload_json
            FROM listing_snapshots
            WHERE mls_number=? AND source_name=?
            ORDER BY fetched_at DESC LIMIT 1
            """,
            (snapshot.source_record_id, snapshot.source_name),
        ).fetchone()
        previous = None
        if previous_row:
            previous = ListingSnapshot(
                **json.loads(previous_row["normalized_json"]),
                fetched_at=previous_row["fetched_at"],
                raw_payload=json.loads(previous_row["raw_payload_json"]),
            )
        changes = detect_listing_changes(previous, snapshot)
        self.connection.execute(
            """
            INSERT INTO listing_snapshots (
                snapshot_id,property_id,mls_number,source_name,fetched_at,
                status,list_price,dom,cdom,normalized_json,raw_payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid4()),
                property_id,
                snapshot.source_record_id,
                snapshot.source_name,
                snapshot.fetched_at,
                snapshot.status,
                snapshot.list_price,
                snapshot.dom,
                snapshot.cdom,
                json.dumps(snapshot.stable_dict(), sort_keys=True),
                json.dumps(snapshot.raw_payload, sort_keys=True),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO listing_changes (
                change_id,mls_number,source_name,detected_at,change_type,
                before_json,after_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            [
                (
                    str(uuid4()),
                    snapshot.source_record_id,
                    snapshot.source_name,
                    change.detected_at,
                    change.change_type,
                    json.dumps(change.before, sort_keys=True),
                    json.dumps(change.after, sort_keys=True),
                )
                for change in changes
            ],
        )
        self.connection.commit()
        return changes

    def record_human_decision(
        self,
        property_id: str,
        decision_type: str,
        decided_at: str,
        notes: str | None = None,
    ) -> str:
        decision_id = str(uuid4())
        self.connection.execute(
            """
            INSERT INTO human_decisions (
                decision_id,property_id,decision_type,decided_at,notes
            ) VALUES (?,?,?,?,?)
            """,
            (decision_id, property_id, decision_type, decided_at, notes),
        )
        self.connection.commit()
        return decision_id

    def record_outcome(
        self,
        property_id: str,
        outcome_type: str,
        occurred_at: str,
        *,
        amount: float | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        outcome_id = str(uuid4())
        self.connection.execute(
            """
            INSERT INTO acquisition_outcomes (
                outcome_id,property_id,outcome_type,occurred_at,amount,reason,metadata_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                outcome_id,
                property_id,
                outcome_type,
                occurred_at,
                amount,
                reason,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        self.connection.commit()
        return outcome_id

    def outcome_labels(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT property_id,outcome_type,occurred_at,amount,reason,metadata_json
            FROM acquisition_outcomes ORDER BY occurred_at,outcome_id
            """
        ).fetchall()
        return [
            {
                **dict(row),
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]
