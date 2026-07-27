from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from distress_radar.domain.evidence import EvidenceItem
from distress_radar.domain.listing import ListingChange, ListingSnapshot
from distress_radar.domain.property import CanonicalProperty
from distress_radar.sources.base import CoverageState, SourceHealthState
from distress_radar.sources.mls.matrix_csv import detect_listing_changes
from distress_radar.watch_semantics import (
    WatchEvidenceDescriptor,
    WatchSpecification,
    WatchValidationOutcome,
    validate_watch,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class IntelligenceStore:
    """Normalized SQLite repository for cross-channel intelligence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.current_pilot_run_id: str | None = None
        self._migrate()

    def __enter__(self) -> Self:
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
            CREATE TABLE IF NOT EXISTS property_source_coverage (
                property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
                source_name TEXT NOT NULL, state TEXT NOT NULL,
                query_scope TEXT NOT NULL, checked_at TEXT NOT NULL,
                run_id TEXT REFERENCES source_runs(run_id), source_url TEXT,
                raw_response_hash TEXT,
                records_examined INTEGER NOT NULL DEFAULT 0,
                records_matched INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                PRIMARY KEY(property_id, source_name)
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
                value_type TEXT NOT NULL, metadata_json TEXT NOT NULL,
                pilot_run_id TEXT
            );
            CREATE TABLE IF NOT EXISTS property_signals (
                signal_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                signal_type TEXT NOT NULL, observed_at TEXT NOT NULL,
                value_json TEXT, evidence_ids_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signal_evidence_links (
                signal_id TEXT NOT NULL REFERENCES property_signals(signal_id),
                evidence_id TEXT NOT NULL REFERENCES evidence_items(evidence_id),
                property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
                signal_type TEXT NOT NULL,
                PRIMARY KEY(signal_id,evidence_id)
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
            CREATE TABLE IF NOT EXISTS human_dispositions (
                disposition_id TEXT PRIMARY KEY, property_id TEXT NOT NULL
                    REFERENCES canonical_properties(property_id),
                disposition TEXT NOT NULL, decided_at TEXT NOT NULL, notes TEXT,
                baseline_content_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS watch_trigger_events (
                trigger_event_id TEXT PRIMARY KEY,
                disposition_id TEXT NOT NULL REFERENCES human_dispositions(disposition_id),
                property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
                trigger_fingerprint TEXT NOT NULL,
                triggered_at TEXT NOT NULL,
                trigger_payload_json TEXT NOT NULL,
                UNIQUE(disposition_id,trigger_fingerprint)
            );
            CREATE TABLE IF NOT EXISTS watch_validation_events (
                validation_event_id TEXT PRIMARY KEY,
                disposition_id TEXT NOT NULL REFERENCES human_dispositions(disposition_id),
                property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
                pilot_run_id TEXT NOT NULL,
                validated_at TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                validation_reason TEXT NOT NULL,
                evidence_states_json TEXT NOT NULL,
                source_health_json TEXT NOT NULL,
                evidence_descriptors_json TEXT NOT NULL,
                UNIQUE(disposition_id,pilot_run_id)
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
        disposition_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(human_dispositions)"
            )
        }
        for column, definition in (
            ("watch_reason", "TEXT"),
            ("watch_evidence_ids_json", "TEXT"),
            ("watch_trigger_type", "TEXT"),
            ("watch_recheck_condition_json", "TEXT"),
            ("watch_recheck_at", "TEXT"),
            ("watch_event_trigger_json", "TEXT"),
            ("watch_expected_next_action", "TEXT"),
            ("creator", "TEXT"),
            ("watch_validation_status", "TEXT"),
        ):
            if column not in disposition_columns:
                self.connection.execute(
                    f"ALTER TABLE human_dispositions ADD COLUMN {column} {definition}"
                )
        evidence_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(evidence_items)")
        }
        if "pilot_run_id" not in evidence_columns:
            self.connection.execute(
                "ALTER TABLE evidence_items ADD COLUMN pilot_run_id TEXT"
            )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO signal_evidence_links (
                signal_id,evidence_id,property_id,signal_type
            )
            SELECT s.signal_id,j.value,s.property_id,s.signal_type
            FROM property_signals s,json_each(s.evidence_ids_json) j
            JOIN evidence_items e ON e.evidence_id=j.value
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
                confidence,value_type,metadata_json,pilot_run_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                self.current_pilot_run_id,
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

    def set_source_coverage(
        self,
        *,
        property_id: str,
        source_name: str,
        state: CoverageState,
        query_scope: str,
        records_examined: int,
        records_matched: int,
        run_id: str | None = None,
        source_url: str | None = None,
        raw_response_hash: str | None = None,
        error_message: str | None = None,
        checked_at: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO property_source_coverage (
                property_id,source_name,state,query_scope,checked_at,run_id,
                source_url,raw_response_hash,records_examined,records_matched,
                error_message
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(property_id,source_name) DO UPDATE SET
                state=excluded.state,query_scope=excluded.query_scope,
                checked_at=excluded.checked_at,run_id=excluded.run_id,
                source_url=excluded.source_url,
                raw_response_hash=excluded.raw_response_hash,
                records_examined=excluded.records_examined,
                records_matched=excluded.records_matched,
                error_message=excluded.error_message
            """,
            (
                property_id,
                source_name,
                state.value,
                query_scope,
                checked_at or utc_now(),
                run_id,
                source_url,
                raw_response_hash,
                records_examined,
                records_matched,
                error_message,
            ),
        )
        self.connection.commit()

    def property_source_coverage(self, property_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT source_name,state,query_scope,checked_at,run_id,source_url,
                   raw_response_hash,records_examined,records_matched,error_message
            FROM property_source_coverage
            WHERE property_id=? ORDER BY source_name
            """,
            (property_id,),
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
        if previous is not None and previous.stable_dict() == snapshot.stable_dict():
            if property_id is not None:
                self.connection.execute(
                    """
                    UPDATE listing_snapshots SET property_id=?
                    WHERE mls_number=? AND source_name=?
                    """,
                    (property_id, snapshot.source_record_id, snapshot.source_name),
                )
                self.connection.commit()
            return ()
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

    def record_disposition(
        self,
        property_id: str,
        disposition: str,
        decided_at: str,
        *,
        baseline_content_hash: str,
        notes: str | None = None,
        watch_specification: WatchSpecification | None = None,
    ) -> str:
        allowed = {
            "investigate",
            "request_documents",
            "watch",
            "dismiss",
            "legal_municipal_review",
            "approved_for_contact",
        }
        if disposition not in allowed:
            raise ValueError(f"unsupported disposition: {disposition}")
        if disposition != "watch" and watch_specification is not None:
            raise ValueError("watch specification is only valid for a watch disposition")
        watch_status: str | None = None
        if disposition == "watch":
            if watch_specification is None:
                raise ValueError("new watch dispositions require complete watch semantics")
            outcome = self.validate_watch_specification(
                property_id,
                watch_specification,
                created_at=decided_at,
                reviewed_content_hash=baseline_content_hash,
            )
            watch_status = outcome.status
            if watch_status != "valid":
                raise ValueError(
                    "watch evidence or semantics failed closed validation: "
                    f"{watch_status}: {outcome.reason}"
                )
        disposition_id = str(uuid4())
        self.connection.execute(
            """
            UPDATE human_dispositions SET active=0
            WHERE property_id=? AND active=1
            """,
            (property_id,),
        )
        self.connection.execute(
            """
            INSERT INTO human_dispositions (
                disposition_id,property_id,disposition,decided_at,notes,
                baseline_content_hash,active,watch_reason,watch_evidence_ids_json,
                watch_trigger_type,watch_recheck_condition_json,watch_recheck_at,
                watch_event_trigger_json,watch_expected_next_action,creator,
                watch_validation_status
            ) VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)
            """,
            (
                disposition_id,
                property_id,
                disposition,
                decided_at,
                notes,
                baseline_content_hash,
                watch_specification.watch_reason if watch_specification else None,
                (
                    json.dumps(watch_specification.evidence_ids)
                    if watch_specification
                    else None
                ),
                watch_specification.trigger_type if watch_specification else None,
                (
                    json.dumps(
                        watch_specification.recheck_condition, sort_keys=True
                    )
                    if watch_specification
                    else None
                ),
                watch_specification.recheck_at if watch_specification else None,
                (
                    json.dumps(
                        watch_specification.evidence_event_trigger, sort_keys=True
                    )
                    if watch_specification
                    else None
                ),
                (
                    watch_specification.expected_next_action
                    if watch_specification
                    else None
                ),
                watch_specification.creator if watch_specification else None,
                watch_status,
            ),
        )
        if disposition == "watch":
            pilot_run = self.connection.execute(
                """
                SELECT pilot_run_id FROM recommendations
                WHERE property_id=? AND content_hash=? AND pilot_run_id IS NOT NULL
                ORDER BY generated_at DESC,recommendation_id DESC LIMIT 1
                """,
                (property_id, baseline_content_hash),
            ).fetchone()
            if pilot_run is None:
                raise ValueError(
                    "watch creation requires a reviewed recommendation run"
                )
            self.record_watch_validation_event(
                disposition_id=disposition_id,
                property_id=property_id,
                pilot_run_id=str(pilot_run["pilot_run_id"]),
                validated_at=decided_at,
                outcome=outcome,
            )
        else:
            self.connection.commit()
        return disposition_id

    def effective_disposition(
        self, property_id: str, current_content_hash: str
    ) -> str | None:
        row = self.active_disposition(property_id)
        if row is None or row["baseline_content_hash"] != current_content_hash:
            return None
        return str(row["disposition"])

    def active_disposition(self, property_id: str) -> sqlite3.Row | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM human_dispositions
            WHERE property_id=? AND active=1
            ORDER BY decided_at DESC,disposition_id DESC LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        return row

    def watch_evidence_states(
        self, property_id: str, evidence_ids: tuple[str, ...]
    ) -> dict[str, str]:
        if not evidence_ids:
            return {}
        rows = self.connection.execute(
            f"""
            SELECT evidence_id,property_id,freshness_status
            FROM evidence_items
            WHERE evidence_id IN ({','.join('?' for _ in evidence_ids)})
            """,
            evidence_ids,
        ).fetchall()
        states: dict[str, str] = {}
        signal_rows = self.connection.execute(
            """
            SELECT status,confirmation_status,evidence_ids_json
            FROM property_signals WHERE property_id=?
            """,
            (property_id,),
        ).fetchall()
        signal_by_evidence: dict[str, tuple[str, str]] = {}
        for signal in signal_rows:
            for evidence_id in json.loads(signal["evidence_ids_json"]):
                signal_by_evidence[evidence_id] = (
                    str(signal["status"]),
                    str(signal["confirmation_status"]),
                )
        for row in rows:
            if row["property_id"] != property_id:
                continue
            signal_state = signal_by_evidence.get(str(row["evidence_id"]))
            if signal_state and signal_state[0] == "resolved":
                state = "resolved"
            elif signal_state and signal_state[1] != "confirmed":
                state = "last_known"
            elif row["freshness_status"] == "stale":
                state = "stale"
            else:
                state = "current"
            states[str(row["evidence_id"])] = state
        return states

    def validate_watch_specification(
        self,
        property_id: str,
        specification: WatchSpecification | None,
        *,
        created_at: str,
        reviewed_content_hash: str,
        pilot_run_id: str | None = None,
    ) -> WatchValidationOutcome:
        evidence_states = self.watch_evidence_states(
            property_id, specification.evidence_ids if specification else ()
        )
        evidence_ids = specification.evidence_ids if specification else ()
        evidence_rows = []
        if evidence_ids:
            evidence_rows = self.connection.execute(
                f"""
                SELECT evidence_id,property_id,field_name,source_name
                FROM evidence_items
                WHERE evidence_id IN ({','.join('?' for _ in evidence_ids)})
                """,
                evidence_ids,
            ).fetchall()
        signal_types: dict[str, set[str]] = {
            str(row["evidence_id"]): set() for row in evidence_rows
        }
        if evidence_ids:
            for signal in self.connection.execute(
                """
                SELECT signal_type,evidence_id
                FROM signal_evidence_links WHERE property_id=?
                """,
                (property_id,),
            ).fetchall():
                evidence_id = str(signal["evidence_id"])
                if evidence_id in signal_types:
                    signal_types[evidence_id].add(str(signal["signal_type"]))
        descriptors = tuple(
            WatchEvidenceDescriptor(
                evidence_id=str(row["evidence_id"]),
                property_id=str(row["property_id"]),
                evidence_class=str(row["field_name"]),
                source_name=str(row["source_name"]),
                signal_types=tuple(sorted(signal_types[str(row["evidence_id"])])),
            )
            for row in evidence_rows
        )
        effective_run_id = pilot_run_id or self.current_pilot_run_id
        if effective_run_id is None:
            latest = self.connection.execute(
                """
                SELECT pilot_run_id FROM recommendations
                WHERE property_id=? AND pilot_run_id IS NOT NULL
                ORDER BY generated_at DESC,recommendation_id DESC LIMIT 1
                """,
                (property_id,),
            ).fetchone()
            effective_run_id = str(latest["pilot_run_id"]) if latest else None
        sources = {item.source_name for item in descriptors}
        source_health: dict[str, str] = {}
        previous_source_health: dict[str, str] = {}
        for source_name in sources:
            current = (
                self.connection.execute(
                    """
                    SELECT state FROM source_runs
                    WHERE pilot_run_id=? AND source_name=?
                    ORDER BY started_at DESC,run_id DESC LIMIT 1
                    """,
                    (effective_run_id, source_name),
                ).fetchone()
                if effective_run_id
                else None
            )
            source_health[source_name] = (
                str(current["state"]) if current else "unknown_not_run"
            )
            previous = (
                self.connection.execute(
                    """
                    SELECT state FROM source_runs
                    WHERE source_name=? AND pilot_run_id IS NOT NULL
                      AND pilot_run_id<>?
                    ORDER BY started_at DESC,run_id DESC LIMIT 1
                    """,
                    (source_name, effective_run_id),
                ).fetchone()
                if effective_run_id
                else None
            )
            if previous:
                previous_source_health[source_name] = str(previous["state"])
        return validate_watch(
            specification,
            created_at=created_at,
            reviewed_content_hash=reviewed_content_hash,
            property_id=property_id,
            evidence=descriptors,
            evidence_states=evidence_states,
            source_health=source_health,
            previous_source_health=previous_source_health,
            reviewed_hash_exists=(
                self.connection.execute(
                    """
                    SELECT 1 FROM recommendations
                    WHERE property_id=? AND content_hash=?
                      AND pilot_run_id IS NOT NULL
                    LIMIT 1
                    """,
                    (property_id, reviewed_content_hash),
                ).fetchone()
                is not None
            ),
        )

    def record_watch_validation_event(
        self,
        *,
        disposition_id: str,
        property_id: str,
        pilot_run_id: str,
        validated_at: str,
        outcome: WatchValidationOutcome,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO watch_validation_events (
                validation_event_id,disposition_id,property_id,pilot_run_id,
                validated_at,validation_status,validation_reason,
                evidence_states_json,source_health_json,evidence_descriptors_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(disposition_id,pilot_run_id) DO NOTHING
            """,
            (
                str(uuid4()),
                disposition_id,
                property_id,
                pilot_run_id,
                validated_at,
                outcome.status,
                outcome.reason,
                json.dumps(outcome.evidence_states, sort_keys=True),
                json.dumps(outcome.source_health, sort_keys=True),
                json.dumps(
                    [
                        {
                            "evidence_id": item.evidence_id,
                            "property_id": item.property_id,
                            "evidence_class": item.evidence_class,
                            "source_name": item.source_name,
                            "signal_types": item.signal_types,
                        }
                        for item in outcome.evidence
                    ],
                    sort_keys=True,
                ),
            ),
        )
        self.connection.commit()

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
