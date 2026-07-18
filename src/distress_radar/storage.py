from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from distress_radar.models import CodeCase, OfficialRecord, PropertyRecord, RawDocument, TaxDelinquency


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UpsertStats:
    new: int = 0
    changed: int = 0
    unchanged: int = 0


class RadarStore:
    _OWNER_SUFFIXES = {"llc", "lc", "inc", "corp", "corporation", "ltd", "lp", "llp"}

    @classmethod
    def owner_key(cls, owner_name: str | None) -> str:
        """Normalize punctuation, case, and trailing legal-entity suffixes."""
        tokens = re.findall(r"[a-z0-9]+", (owner_name or "").casefold())
        for dotted_suffix in (("l", "l", "c"), ("l", "l", "p"), ("i", "n", "c"), ("l", "p")):
            if tuple(tokens[-len(dotted_suffix):]) == dotted_suffix:
                del tokens[-len(dotted_suffix):]
                break
        while tokens and tokens[-1] in cls._OWNER_SUFFIXES:
            tokens.pop()
        return " ".join(tokens)

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def __enter__(self) -> "RadarStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY,
                city_slug TEXT NOT NULL,
                source_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                requested_statuses_json TEXT NOT NULL,
                record_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS raw_documents (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                kind TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(run_id, kind, source_key)
            );

            CREATE TABLE IF NOT EXISTS code_cases (
                city_slug TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                case_number TEXT NOT NULL,
                status TEXT,
                opened_date TEXT,
                closed_date TEXT,
                address TEXT,
                parcel_number TEXT,
                source_url TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_seen_run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                PRIMARY KEY(city_slug, source_name, source_record_id)
            );

            CREATE INDEX IF NOT EXISTS idx_code_cases_city_status
                ON code_cases(city_slug, status);
            CREATE INDEX IF NOT EXISTS idx_code_cases_parcel
                ON code_cases(parcel_number);

            CREATE TABLE IF NOT EXISTS case_changes (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                city_slug TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                change_type TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS properties (
                city_slug TEXT NOT NULL,
                source_name TEXT NOT NULL,
                folio TEXT NOT NULL,
                address TEXT,
                owner_name TEXT,
                unit_count INTEGER,
                year_built INTEGER,
                source_url TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_seen_run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                PRIMARY KEY(city_slug, source_name, folio)
            );

            CREATE INDEX IF NOT EXISTS idx_properties_city_units
                ON properties(city_slug, unit_count);
            CREATE INDEX IF NOT EXISTS idx_properties_folio
                ON properties(folio);

            CREATE TABLE IF NOT EXISTS property_changes (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                city_slug TEXT NOT NULL,
                source_name TEXT NOT NULL,
                folio TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                change_type TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS official_records (
                city_slug TEXT NOT NULL, source_name TEXT NOT NULL,
                source_record_id TEXT NOT NULL, folio TEXT NOT NULL,
                document_type TEXT, signal_type TEXT NOT NULL, recorded_date TEXT,
                source_url TEXT NOT NULL, normalized_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_seen_run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                PRIMARY KEY(city_slug, source_name, source_record_id, folio)
            );
            CREATE INDEX IF NOT EXISTS idx_official_records_folio ON official_records(folio);
            CREATE TABLE IF NOT EXISTS official_record_changes (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                city_slug TEXT NOT NULL, source_name TEXT NOT NULL,
                source_record_id TEXT NOT NULL, folio TEXT NOT NULL,
                detected_at TEXT NOT NULL, change_type TEXT NOT NULL,
                before_json TEXT, after_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tax_delinquencies (
                city_slug TEXT NOT NULL, source_name TEXT NOT NULL,
                source_record_id TEXT NOT NULL, folio TEXT NOT NULL,
                tax_year INTEGER NOT NULL, amount_due REAL NOT NULL,
                status TEXT, source_url TEXT NOT NULL, normalized_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_seen_run_id INTEGER NOT NULL REFERENCES scrape_runs(id),
                PRIMARY KEY(city_slug, source_name, source_record_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tax_delinquencies_folio ON tax_delinquencies(folio);
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY, city_slug TEXT NOT NULL,
                alert_type TEXT NOT NULL, severity TEXT NOT NULL,
                folio TEXT, source_record_id TEXT NOT NULL,
                title TEXT NOT NULL, payload_json TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_pending
                ON alerts(city_slug, delivered_at, created_at);
            CREATE TABLE IF NOT EXISTS property_leads (
                city_slug TEXT NOT NULL, folio TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'new', assignee TEXT,
                next_follow_up_date TEXT, disposition TEXT, notes TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(city_slug, folio)
            );
            CREATE INDEX IF NOT EXISTS idx_property_leads_follow_up
                ON property_leads(city_slug, stage, next_follow_up_date);
            """
        )
        self.connection.commit()

    def start_run(self, city_slug: str, source_name: str, statuses: tuple[str, ...]) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO scrape_runs
                (city_slug, source_name, started_at, status, requested_statuses_json)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (city_slug, source_name, utc_now(), json.dumps(statuses)),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, record_count: int, error: str | None = None) -> None:
        self.connection.execute(
            """
            UPDATE scrape_runs
            SET completed_at = ?, status = ?, record_count = ?, error_message = ?
            WHERE id = ?
            """,
            (utc_now(), status, record_count, error, run_id),
        )
        self.connection.commit()

    def save_raw_documents(self, run_id: int, documents: tuple[RawDocument, ...]) -> None:
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO raw_documents
                (run_id, kind, source_key, source_url, fetched_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    doc.kind,
                    doc.source_key,
                    doc.source_url,
                    doc.fetched_at,
                    json.dumps(doc.payload, sort_keys=True, separators=(",", ":")),
                )
                for doc in documents
            ],
        )
        self.connection.commit()

    def upsert_cases(self, run_id: int, records: tuple[CodeCase, ...]) -> UpsertStats:
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for record in records:
            stable = record.stable_dict()
            normalized_json = json.dumps(stable, sort_keys=True, separators=(",", ":"))
            payload_hash = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
            key = (record.city_slug, record.source_name, record.source_record_id)
            current = self.connection.execute(
                """
                SELECT normalized_json, payload_hash, first_seen_at
                FROM code_cases
                WHERE city_slug = ? AND source_name = ? AND source_record_id = ?
                """,
                key,
            ).fetchone()

            if current is None:
                counts["new"] += 1
                first_seen = record.fetched_at
                self._insert_change(run_id, record, "new", None, normalized_json)
            elif current["payload_hash"] != payload_hash:
                counts["changed"] += 1
                first_seen = current["first_seen_at"]
                self._insert_change(
                    run_id, record, "changed", current["normalized_json"], normalized_json
                )
            else:
                counts["unchanged"] += 1
                first_seen = current["first_seen_at"]

            if (current is None or current["payload_hash"] != payload_hash) and self._code_enforcement_weight(record.status) >= 10:
                self._enqueue_alert(
                    record.city_slug, "code_enforcement", "high", record.parcel_number,
                    record.source_record_id, f"Code case {record.case_number}: {record.status or 'significant update'}",
                    stable, payload_hash, record.fetched_at,
                )

            self.connection.execute(
                """
                INSERT INTO code_cases (
                    city_slug, source_name, source_record_id, case_number, status,
                    opened_date, closed_date, address, parcel_number, source_url,
                    normalized_json, payload_hash, first_seen_at, last_seen_at,
                    last_seen_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city_slug, source_name, source_record_id) DO UPDATE SET
                    case_number = excluded.case_number,
                    status = excluded.status,
                    opened_date = excluded.opened_date,
                    closed_date = excluded.closed_date,
                    address = excluded.address,
                    parcel_number = excluded.parcel_number,
                    source_url = excluded.source_url,
                    normalized_json = excluded.normalized_json,
                    payload_hash = excluded.payload_hash,
                    last_seen_at = excluded.last_seen_at,
                    last_seen_run_id = excluded.last_seen_run_id
                """,
                (
                    *key,
                    record.case_number,
                    record.status,
                    record.opened_date,
                    record.closed_date,
                    record.address,
                    record.parcel_number,
                    record.source_url,
                    normalized_json,
                    payload_hash,
                    first_seen,
                    record.fetched_at,
                    run_id,
                ),
            )
        self.connection.commit()
        return UpsertStats(**counts)

    def upsert_official_records(
        self, run_id: int, records: tuple[OfficialRecord, ...]
    ) -> UpsertStats:
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for record in records:
            normalized_json = json.dumps(record.stable_dict(), sort_keys=True, separators=(",", ":"))
            payload_hash = hashlib.sha256(normalized_json.encode()).hexdigest()
            key = (record.city_slug, record.source_name, record.source_record_id, record.folio)
            current = self.connection.execute(
                "SELECT normalized_json, payload_hash, first_seen_at FROM official_records WHERE city_slug=? AND source_name=? AND source_record_id=? AND folio=?",
                key,
            ).fetchone()
            if current is None:
                counts["new"] += 1
                first_seen, change_type, before = record.fetched_at, "new", None
            elif current["payload_hash"] != payload_hash:
                counts["changed"] += 1
                first_seen, change_type, before = current["first_seen_at"], "changed", current["normalized_json"]
            else:
                counts["unchanged"] += 1
                first_seen, change_type, before = current["first_seen_at"], None, None
            if change_type:
                self.connection.execute(
                    "INSERT INTO official_record_changes (run_id,city_slug,source_name,source_record_id,folio,detected_at,change_type,before_json,after_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (run_id, *key, record.fetched_at, change_type, before, normalized_json),
                )
                if record.signal_type in {"lis_pendens", "lien"}:
                    self._enqueue_alert(
                        record.city_slug, record.signal_type, "critical" if record.signal_type == "lis_pendens" else "high",
                        record.folio, record.source_record_id,
                        f"New {record.signal_type.replace('_', ' ')} instrument {record.instrument_id}",
                        record.stable_dict(), payload_hash, record.fetched_at,
                    )
            self.connection.execute(
                """INSERT INTO official_records (city_slug,source_name,source_record_id,folio,document_type,signal_type,recorded_date,source_url,normalized_json,payload_hash,first_seen_at,last_seen_at,last_seen_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(city_slug,source_name,source_record_id,folio) DO UPDATE SET
                document_type=excluded.document_type, signal_type=excluded.signal_type,
                recorded_date=excluded.recorded_date, source_url=excluded.source_url,
                normalized_json=excluded.normalized_json, payload_hash=excluded.payload_hash,
                last_seen_at=excluded.last_seen_at, last_seen_run_id=excluded.last_seen_run_id""",
                (*key, record.document_type, record.signal_type, record.recorded_date,
                 record.source_url, normalized_json, payload_hash, first_seen,
                 record.fetched_at, run_id),
            )
        self.connection.commit()
        return UpsertStats(**counts)

    def upsert_tax_delinquencies(
        self, run_id: int, records: tuple[TaxDelinquency, ...]
    ) -> UpsertStats:
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for record in records:
            normalized = json.dumps(record.stable_dict(), sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(normalized.encode()).hexdigest()
            key = (record.city_slug, record.source_name, record.source_record_id)
            current = self.connection.execute(
                "SELECT payload_hash,first_seen_at FROM tax_delinquencies WHERE city_slug=? AND source_name=? AND source_record_id=?", key
            ).fetchone()
            if current is None:
                counts["new"] += 1; first_seen = record.fetched_at
            elif current["payload_hash"] != digest:
                counts["changed"] += 1; first_seen = current["first_seen_at"]
            else:
                counts["unchanged"] += 1; first_seen = current["first_seen_at"]
            if (current is None or current["payload_hash"] != digest) and record.amount_due > 0 and "paid" not in str(record.status or "").casefold():
                self._enqueue_alert(
                    record.city_slug, "tax_delinquency", "high", record.folio,
                    record.source_record_id,
                    f"Delinquent property tax for {record.tax_year}: ${record.amount_due:,.2f}",
                    record.stable_dict(), digest, record.fetched_at,
                )
            self.connection.execute(
                """INSERT INTO tax_delinquencies (city_slug,source_name,source_record_id,folio,tax_year,amount_due,status,source_url,normalized_json,payload_hash,first_seen_at,last_seen_at,last_seen_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(city_slug,source_name,source_record_id) DO UPDATE SET
                folio=excluded.folio,tax_year=excluded.tax_year,amount_due=excluded.amount_due,status=excluded.status,
                source_url=excluded.source_url,normalized_json=excluded.normalized_json,payload_hash=excluded.payload_hash,
                last_seen_at=excluded.last_seen_at,last_seen_run_id=excluded.last_seen_run_id""",
                (*key, record.folio, record.tax_year, record.amount_due, record.status,
                 record.source_url, normalized, digest, first_seen, record.fetched_at, run_id),
            )
        self.connection.commit()
        return UpsertStats(**counts)

    def _enqueue_alert(
        self, city_slug: str, alert_type: str, severity: str, folio: str | None,
        source_record_id: str, title: str, payload: dict[str, Any],
        payload_hash: str, created_at: str,
    ) -> None:
        fingerprint = hashlib.sha256(
            f"{city_slug}|{alert_type}|{source_record_id}|{payload_hash}".encode()
        ).hexdigest()
        self.connection.execute(
            """INSERT OR IGNORE INTO alerts
               (city_slug,alert_type,severity,folio,source_record_id,title,payload_json,fingerprint,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (city_slug, alert_type, severity, folio, source_record_id, title,
             json.dumps(payload, sort_keys=True, separators=(",", ":")), fingerprint, created_at),
        )

    def pending_alerts(self, city_slug: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT id,alert_type,severity,folio,source_record_id,title,payload_json,created_at
               FROM alerts WHERE city_slug=? AND delivered_at IS NULL
               ORDER BY created_at,id LIMIT ?""", (city_slug, limit)
        )
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def mark_alerts_delivered(self, alert_ids: list[int]) -> None:
        if not alert_ids:
            return
        placeholders = ",".join("?" for _ in alert_ids)
        self.connection.execute(
            f"UPDATE alerts SET delivered_at=? WHERE id IN ({placeholders})",
            (utc_now(), *alert_ids),
        )
        self.connection.commit()

    def set_property_lead(
        self, city_slug: str, folio: str, *, stage: str, assignee: str | None = None,
        next_follow_up_date: str | None = None, disposition: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        allowed = {"new", "researching", "qualified", "contacted", "negotiating", "won", "lost", "paused"}
        if stage not in allowed:
            raise ValueError(f"Invalid lead stage '{stage}'; choose from {', '.join(sorted(allowed))}")
        exists = self.connection.execute(
            "SELECT 1 FROM properties WHERE city_slug=? AND folio=?", (city_slug, folio)
        ).fetchone()
        if not exists:
            raise ValueError(f"Unknown property folio '{folio}' for {city_slug}")
        now = utc_now()
        self.connection.execute(
            """INSERT INTO property_leads
               (city_slug,folio,stage,assignee,next_follow_up_date,disposition,notes,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(city_slug,folio) DO UPDATE SET
               stage=excluded.stage,assignee=excluded.assignee,
               next_follow_up_date=excluded.next_follow_up_date,
               disposition=excluded.disposition,notes=excluded.notes,
               updated_at=excluded.updated_at""",
            (city_slug, folio, stage, assignee, next_follow_up_date, disposition, notes, now, now),
        )
        self.connection.commit()
        return self.get_property_lead(city_slug, folio)

    def get_property_lead(self, city_slug: str, folio: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM property_leads WHERE city_slug=? AND folio=?", (city_slug, folio)
        ).fetchone()
        return dict(row) if row else {"stage": "new", "assignee": None, "next_follow_up_date": None, "disposition": None, "notes": None}

    def export_leads_csv(self, city_slug: str, output: Path) -> int:
        rows = self.connection.execute(
            """SELECT l.*,p.address,p.owner_name,p.unit_count
               FROM property_leads l JOIN properties p
                 ON p.city_slug=l.city_slug AND p.folio=l.folio
               WHERE l.city_slug=? ORDER BY
                 CASE WHEN l.next_follow_up_date IS NULL THEN 1 ELSE 0 END,
                 l.next_follow_up_date,l.updated_at DESC""", (city_slug,)
        ).fetchall()
        output.parent.mkdir(parents=True, exist_ok=True)
        fields = ["city_slug","folio","address","owner_name","unit_count","stage","assignee","next_follow_up_date","disposition","notes","created_at","updated_at"]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({key: row[key] for key in fields} for row in rows)
        return len(rows)

    def _insert_change(
        self,
        run_id: int,
        record: CodeCase,
        change_type: str,
        before_json: str | None,
        after_json: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO case_changes (
                run_id, city_slug, source_name, source_record_id, detected_at,
                change_type, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.city_slug,
                record.source_name,
                record.source_record_id,
                record.fetched_at,
                change_type,
                before_json,
                after_json,
            ),
        )

    def upsert_properties(
        self, run_id: int, records: tuple[PropertyRecord, ...]
    ) -> UpsertStats:
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        for record in records:
            stable = record.stable_dict()
            normalized_json = json.dumps(stable, sort_keys=True, separators=(",", ":"))
            payload_hash = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
            key = (record.city_slug, record.source_name, record.folio)
            current = self.connection.execute(
                """
                SELECT normalized_json, payload_hash, first_seen_at
                FROM properties
                WHERE city_slug = ? AND source_name = ? AND folio = ?
                """,
                key,
            ).fetchone()
            if current is None:
                counts["new"] += 1
                first_seen = record.fetched_at
                self._insert_property_change(run_id, record, "new", None, normalized_json)
            elif current["payload_hash"] != payload_hash:
                counts["changed"] += 1
                first_seen = current["first_seen_at"]
                self._insert_property_change(
                    run_id, record, "changed", current["normalized_json"], normalized_json
                )
            else:
                counts["unchanged"] += 1
                first_seen = current["first_seen_at"]

            self.connection.execute(
                """
                INSERT INTO properties (
                    city_slug, source_name, folio, address, owner_name, unit_count,
                    year_built, source_url, normalized_json, payload_hash,
                    first_seen_at, last_seen_at, last_seen_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city_slug, source_name, folio) DO UPDATE SET
                    address = excluded.address,
                    owner_name = excluded.owner_name,
                    unit_count = excluded.unit_count,
                    year_built = excluded.year_built,
                    source_url = excluded.source_url,
                    normalized_json = excluded.normalized_json,
                    payload_hash = excluded.payload_hash,
                    last_seen_at = excluded.last_seen_at,
                    last_seen_run_id = excluded.last_seen_run_id
                """,
                (
                    *key,
                    record.address,
                    record.owner_name,
                    record.unit_count,
                    record.year_built,
                    record.source_url,
                    normalized_json,
                    payload_hash,
                    first_seen,
                    record.fetched_at,
                    run_id,
                ),
            )
        self.connection.commit()
        return UpsertStats(**counts)

    def _insert_property_change(
        self,
        run_id: int,
        record: PropertyRecord,
        change_type: str,
        before_json: str | None,
        after_json: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO property_changes (
                run_id, city_slug, source_name, folio, detected_at,
                change_type, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                record.city_slug,
                record.source_name,
                record.folio,
                record.fetched_at,
                change_type,
                before_json,
                after_json,
            ),
        )

    def export_csv(self, city_slug: str, output: Path) -> int:
        rows = self.connection.execute(
            """
            SELECT normalized_json, last_seen_at
            FROM code_cases
            WHERE city_slug = ?
            ORDER BY opened_date DESC, case_number DESC
            """,
            (city_slug,),
        ).fetchall()
        output.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "city_slug",
            "source_name",
            "source_record_id",
            "case_number",
            "case_type",
            "status",
            "opened_date",
            "closed_date",
            "address",
            "parcel_number",
            "description",
            "project_name",
            "assigned_to",
            "violation_count",
            "violations",
            "source_url",
            "last_seen_at",
        ]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                item: dict[str, Any] = json.loads(row["normalized_json"])
                item["violations"] = json.dumps(item.get("violations", []), sort_keys=True)
                item["last_seen_at"] = row["last_seen_at"]
                writer.writerow({key: item.get(key) for key in fieldnames})
        return len(rows)

    def export_properties_csv(self, city_slug: str, output: Path) -> int:
        rows = self.connection.execute(
            """
            SELECT normalized_json, last_seen_at
            FROM properties
            WHERE city_slug = ?
            ORDER BY unit_count DESC, address
            """,
            (city_slug,),
        ).fetchall()
        output.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "city_slug",
            "source_name",
            "folio",
            "address",
            "city",
            "zip_code",
            "owner_name",
            "owner_name_2",
            "mailing_address_1",
            "mailing_address_2",
            "mailing_city",
            "mailing_state",
            "mailing_zip",
            "dor_code",
            "dor_description",
            "unit_count",
            "year_built",
            "floor_count",
            "building_area",
            "lot_size",
            "assessed_value",
            "last_sale_date",
            "last_sale_price",
            "latitude",
            "longitude",
            "source_url",
            "last_seen_at",
        ]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                item: dict[str, Any] = json.loads(row["normalized_json"])
                item["last_seen_at"] = row["last_seen_at"]
                writer.writerow({key: item.get(key) for key in fieldnames})
        return len(rows)

    def top_property_folios(self, city_slug: str, limit: int) -> list[str]:
        properties = {
            row["folio"]: json.loads(row["normalized_json"])
            for row in self.connection.execute(
                "SELECT folio, normalized_json FROM properties WHERE city_slug = ?",
                (city_slug,),
            )
        }
        owner_counts: dict[str, int] = {}
        for prop in properties.values():
            owner_key = self.owner_key(prop.get("owner_name"))
            if owner_key:
                owner_counts[owner_key] = owner_counts.get(owner_key, 0) + 1
        cases_by_folio: dict[str, list[str | None]] = {}
        financial = self._financial_profiles(city_slug)
        for row in self.connection.execute(
            """
            SELECT parcel_number, status
            FROM code_cases
            WHERE city_slug = ? AND parcel_number IS NOT NULL
            """,
            (city_slug,),
        ):
            if row["parcel_number"] in properties:
                cases_by_folio.setdefault(row["parcel_number"], []).append(row["status"])
        ranked: list[tuple[int, int, int, int, str]] = []
        for folio, prop in properties.items():
            statuses = cases_by_folio.get(folio, [])
            distress_score = sum(
                self._code_enforcement_weight(status) for status in statuses
            )
            distress_score += min(20, max(0, len(statuses) - 1) * 3)
            owner_key = self.owner_key(prop.get("owner_name"))
            opportunity = self._opportunity_profile(
                prop, owner_counts.get(owner_key, 0)
            )
            financial_score = int(financial.get(folio, {}).get("financial_distress_score", 0))
            ranked.append(
                (
                    distress_score + financial_score + int(opportunity["opportunity_score"]),
                    distress_score + financial_score,
                    len(statuses),
                    int(prop.get("unit_count") or 0),
                    folio,
                )
            )
        ranked.sort(reverse=True)
        return [item[4] for item in ranked[:limit]]

    def folios_needing_official_records(
        self, city_slug: str, folios: list[str], refresh_days: int
    ) -> list[str]:
        cutoff = datetime.now(timezone.utc).timestamp() - refresh_days * 86400
        result: list[str] = []
        for folio in folios:
            row = self.connection.execute(
                """SELECT fetched_at FROM raw_documents
                   WHERE kind='official_records' AND source_key=?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (folio,),
            ).fetchone()
            if row is None:
                result.append(folio)
                continue
            try:
                fetched = datetime.fromisoformat(row["fetched_at"]).timestamp()
            except ValueError:
                result.append(folio)
                continue
            if fetched < cutoff:
                result.append(folio)
        return result

    def _financial_profiles(self, city_slug: str) -> dict[str, dict[str, Any]]:
        instruments: dict[tuple[str, str], dict[str, Any]] = {}
        releases: set[tuple[str, str]] = set()
        for row in self.connection.execute(
            "SELECT normalized_json FROM official_records WHERE city_slug=?",
            (city_slug,),
        ):
            item = json.loads(row["normalized_json"])
            folio = str(item.get("folio") or "")
            instrument_id = str(item.get("instrument_id") or item.get("source_record_id") or "")
            key = (folio, instrument_id)
            instruments.setdefault(key, item)
            original = item.get("original_instrument_id")
            if item.get("signal_type") == "release" and original:
                releases.add((folio, str(original)))
        profiles: dict[str, dict[str, Any]] = {}
        for (folio, instrument_id), item in instruments.items():
            profile = profiles.setdefault(folio, {
                "financial_distress_score": 0, "active_lis_pendens_count": 0,
                "active_recorded_lien_count": 0, "mortgage_instrument_count": 0,
                "release_instrument_count": 0, "latest_official_record_date": None,
            })
            signal = item.get("signal_type")
            resolved = (folio, instrument_id) in releases
            if signal == "lis_pendens" and not resolved:
                profile["active_lis_pendens_count"] += 1
                profile["financial_distress_score"] += 40
            elif signal == "lien" and not resolved:
                profile["active_recorded_lien_count"] += 1
                profile["financial_distress_score"] += 25
            elif signal == "mortgage":
                profile["mortgage_instrument_count"] += 1
            elif signal == "release":
                profile["release_instrument_count"] += 1
            recorded = item.get("recorded_date")
            if recorded and (not profile["latest_official_record_date"] or recorded > profile["latest_official_record_date"]):
                profile["latest_official_record_date"] = recorded
        tax_by_folio: dict[str, list[sqlite3.Row]] = {}
        for row in self.connection.execute(
            "SELECT folio,tax_year,amount_due,status FROM tax_delinquencies WHERE city_slug=? AND amount_due>0",
            (city_slug,),
        ):
            if "paid" not in str(row["status"] or "").casefold():
                tax_by_folio.setdefault(row["folio"], []).append(row)
        for folio, rows in tax_by_folio.items():
            profile = profiles.setdefault(folio, {
                "financial_distress_score": 0, "active_lis_pendens_count": 0,
                "active_recorded_lien_count": 0, "mortgage_instrument_count": 0,
                "release_instrument_count": 0, "latest_official_record_date": None,
            })
            years = sorted({int(row["tax_year"]) for row in rows})
            profile["delinquent_tax_year_count"] = len(years)
            profile["delinquent_tax_amount"] = round(sum(float(row["amount_due"]) for row in rows), 2)
            profile["oldest_delinquent_tax_year"] = years[0]
            profile["financial_distress_score"] += 35 + min(15, max(0, len(years) - 1) * 5)
        for profile in profiles.values():
            profile.setdefault("delinquent_tax_year_count", 0)
            profile.setdefault("delinquent_tax_amount", 0.0)
            profile.setdefault("oldest_delinquent_tax_year", None)
        return profiles

    def official_record_events(self, city_slug: str, folio: str) -> list[dict[str, Any]]:
        """Collapse Clerk party rows into instrument lifecycle events."""
        instruments: dict[str, dict[str, Any]] = {}
        releases: dict[str, dict[str, Any]] = {}
        rows = self.connection.execute(
            """SELECT normalized_json,first_seen_at,last_seen_at
               FROM official_records WHERE city_slug=? AND folio=?
               ORDER BY recorded_date DESC""",
            (city_slug, folio),
        )
        for row in rows:
            item = json.loads(row["normalized_json"])
            instrument_id = str(item.get("instrument_id") or item.get("source_record_id") or "")
            event = instruments.setdefault(instrument_id, {
                **item,
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "parties": [],
            })
            event["first_seen_at"] = min(event["first_seen_at"], row["first_seen_at"])
            event["last_seen_at"] = max(event["last_seen_at"], row["last_seen_at"])
            for party in (item.get("first_party"), item.get("second_party")):
                if party and party not in event["parties"]:
                    event["parties"].append(party)
            original = item.get("original_instrument_id")
            if item.get("signal_type") == "release" and original:
                releases[str(original)] = item
        events: list[dict[str, Any]] = []
        for instrument_id, event in instruments.items():
            release = releases.get(instrument_id)
            actionable = event.get("signal_type") in {"lis_pendens", "lien"}
            event["lifecycle_status"] = "resolved" if release else ("active" if actionable else "recorded")
            event["resolution_instrument_id"] = (
                release.get("instrument_id") or release.get("source_record_id") if release else None
            )
            event["resolution_date"] = release.get("recorded_date") if release else None
            events.append(event)
        return sorted(
            events,
            key=lambda event: (str(event.get("recorded_date") or ""), str(event.get("instrument_id") or "")),
            reverse=True,
        )

    def cases_for_folios(
        self,
        city_slug: str,
        folios: list[str],
        *,
        only_missing_details: bool = False,
    ) -> tuple[CodeCase, ...]:
        if not folios:
            return ()
        placeholders = ",".join("?" for _ in folios)
        missing_clause = """
            AND NOT EXISTS (
                SELECT 1 FROM raw_documents rd
                WHERE rd.kind = 'case_detail'
                  AND rd.source_key = code_cases.source_record_id
            )
        """ if only_missing_details else ""
        rows = self.connection.execute(
            f"""
            SELECT normalized_json
            FROM code_cases
            WHERE city_slug = ? AND parcel_number IN ({placeholders})
            {missing_clause}
            ORDER BY case_number
            """,
            (city_slug, *folios),
        ).fetchall()
        records: list[CodeCase] = []
        for row in rows:
            item: dict[str, Any] = json.loads(row["normalized_json"])
            item["violations"] = tuple(item.get("violations") or [])
            item["fetched_at"] = utc_now()
            records.append(CodeCase(**item))
        return tuple(records)

    @staticmethod
    def _code_enforcement_weight(status: str | None) -> int:
        value = (status or "").casefold()
        if "lien" in value and "release" not in value:
            return 30
        if "special master order" in value:
            return 20
        if "special master" in value or "hearing" in value:
            return 15
        if "notice of violation" in value:
            return 10
        if "re-inspection" in value or "appeal" in value:
            return 7
        if "warning" in value:
            return 4
        return 5

    @staticmethod
    def _years_since(raw_date: str | None) -> int | None:
        if not raw_date:
            return None
        value = str(raw_date).strip()
        parsed = None
        for pattern in ("%Y%m%d", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(value[:19], pattern).date()
                break
            except ValueError:
                continue
        if parsed is None:
            return None
        today = date.today()
        return today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))

    @classmethod
    def _opportunity_profile(
        cls, prop: dict[str, Any], owner_target_property_count: int
    ) -> dict[str, Any]:
        score = 0
        reasons: list[str] = []
        ownership_years = cls._years_since(prop.get("last_sale_date"))
        if ownership_years is not None:
            if ownership_years >= 20:
                score += 20
                reasons.append("20+ year ownership")
            elif ownership_years >= 12:
                score += 12
                reasons.append("12+ year ownership")
            elif ownership_years >= 7:
                score += 6
                reasons.append("7+ year ownership")

        year_built = int(prop.get("year_built") or 0)
        building_age = date.today().year - year_built if year_built else None
        if building_age is not None:
            if building_age >= 60:
                score += 15
                reasons.append("60+ year-old building")
            elif building_age >= 40:
                score += 10
                reasons.append("40+ year-old building")
            elif building_age >= 25:
                score += 5
                reasons.append("25+ year-old building")

        site_city = str(prop.get("city") or "").strip().casefold()
        mailing_city = str(prop.get("mailing_city") or "").strip().casefold()
        site_address = "".join(ch for ch in str(prop.get("address") or "").upper() if ch.isalnum())
        mailing_address = "".join(
            ch for ch in str(prop.get("mailing_address_1") or "").upper() if ch.isalnum()
        )
        absentee_owner = bool(
            (mailing_city and site_city and mailing_city != site_city)
            or (mailing_address and site_address and mailing_address != site_address)
        )
        if absentee_owner:
            score += 5
            reasons.append("absentee mailing")

        if owner_target_property_count == 1:
            score += 5
            reasons.append("single target property")
        elif 1 < owner_target_property_count <= 3:
            score += 3
            reasons.append("small target portfolio")

        return {
            "opportunity_score": score,
            "opportunity_reasons": " | ".join(reasons),
            "ownership_years": ownership_years,
            "building_age": building_age,
            "absentee_owner": absentee_owner,
            "owner_target_property_count": owner_target_property_count,
        }

    def export_opportunities_csv(self, city_slug: str, output: Path, limit: int | None) -> int:
        property_rows = self.connection.execute(
            "SELECT normalized_json FROM properties WHERE city_slug = ?",
            (city_slug,),
        ).fetchall()
        cases_by_folio: dict[str, list[dict[str, Any]]] = {}
        for row in self.connection.execute(
            """
            SELECT parcel_number, normalized_json
            FROM code_cases
            WHERE city_slug = ? AND parcel_number IS NOT NULL
            """,
            (city_slug,),
        ):
            cases_by_folio.setdefault(row["parcel_number"], []).append(
                json.loads(row["normalized_json"])
            )

        owner_counts: dict[str, int] = {}
        owner_units: dict[str, int] = {}
        owner_folios: dict[str, list[str]] = {}
        decoded_properties = [json.loads(row["normalized_json"]) for row in property_rows]
        for prop in decoded_properties:
            owner_key = self.owner_key(prop.get("owner_name"))
            if owner_key:
                owner_counts[owner_key] = owner_counts.get(owner_key, 0) + 1
                owner_units[owner_key] = owner_units.get(owner_key, 0) + int(prop.get("unit_count") or 0)
                owner_folios.setdefault(owner_key, []).append(str(prop.get("folio") or ""))

        opportunities: list[dict[str, Any]] = []
        financial = self._financial_profiles(city_slug)
        for prop in decoded_properties:
            cases = cases_by_folio.get(prop["folio"], [])
            base_score = sum(self._code_enforcement_weight(case.get("status")) for case in cases)
            repeat_bonus = min(20, max(0, len(cases) - 1) * 3)
            distress_score = base_score + repeat_bonus
            owner_key = self.owner_key(prop.get("owner_name"))
            opportunity = self._opportunity_profile(
                prop, owner_counts.get(owner_key, 0)
            )
            financial_profile = financial.get(prop["folio"], {
                "financial_distress_score": 0, "active_lis_pendens_count": 0,
                "active_recorded_lien_count": 0, "mortgage_instrument_count": 0,
                "release_instrument_count": 0, "latest_official_record_date": None,
                "delinquent_tax_year_count": 0, "delinquent_tax_amount": 0.0,
                "oldest_delinquent_tax_year": None,
            })
            opened_dates = sorted(
                str(case.get("opened_date"))
                for case in cases
                if case.get("opened_date")
            )
            opportunities.append(
                {
                    "priority_score": distress_score + financial_profile["financial_distress_score"] + opportunity["opportunity_score"],
                    "distress_score": distress_score + financial_profile["financial_distress_score"],
                    "code_enforcement_score": distress_score,
                    **financial_profile,
                    **opportunity,
                    "owner_canonical_key": owner_key,
                    "owner_target_unit_count": owner_units.get(owner_key, 0),
                    "owner_target_folios": " | ".join(sorted(owner_folios.get(owner_key, []))),
                    "open_case_count": len(cases),
                    "open_case_numbers": " | ".join(
                        str(case.get("case_number") or "") for case in cases
                    ),
                    "open_case_statuses": " | ".join(
                        sorted({str(case.get("status") or "") for case in cases})
                    ),
                    "oldest_open_case_date": opened_dates[0] if opened_dates else None,
                    "newest_open_case_date": opened_dates[-1] if opened_dates else None,
                    "open_case_urls": " | ".join(
                        str(case.get("source_url") or "") for case in cases
                    ),
                    "structured_violation_count": sum(
                        int(case.get("violation_count") or 0) for case in cases
                    ),
                    "open_case_descriptions": " | ".join(
                        str(case.get("description") or "")
                        for case in cases
                        if case.get("description")
                    ),
                    "folio": prop.get("folio"),
                    "address": prop.get("address"),
                    "unit_count": prop.get("unit_count"),
                    "year_built": prop.get("year_built"),
                    "owner_name": prop.get("owner_name"),
                    "mailing_address_1": prop.get("mailing_address_1"),
                    "mailing_city": prop.get("mailing_city"),
                    "mailing_state": prop.get("mailing_state"),
                    "mailing_zip": prop.get("mailing_zip"),
                    "assessed_value": prop.get("assessed_value"),
                    "last_sale_date": prop.get("last_sale_date"),
                    "last_sale_price": prop.get("last_sale_price"),
                    "property_source_url": prop.get("source_url"),
                }
            )
        opportunities.sort(
            key=lambda item: (
                int(item["priority_score"] or 0),
                int(item["distress_score"] or 0),
                int(item["open_case_count"] or 0),
                int(item["unit_count"] or 0),
            ),
            reverse=True,
        )
        if limit is not None:
            opportunities = opportunities[:limit]

        output.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(opportunities[0]) if opportunities else [
            "priority_score",
            "distress_score",
            "opportunity_score",
            "open_case_count",
            "folio",
            "address",
        ]
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(opportunities)
        return len(opportunities)

    def export_dashboard_json(
        self, city_slug: str, output: Path, limit: int | None = 50
    ) -> int:
        with tempfile.TemporaryDirectory() as temp:
            ranked_path = Path(temp) / "ranked.csv"
            self.export_opportunities_csv(city_slug, ranked_path, limit)
            with ranked_path.open(encoding="utf-8", newline="") as handle:
                ranked = list(csv.DictReader(handle))
        result: list[dict[str, Any]] = []
        all_properties = [
            json.loads(row["normalized_json"])
            for row in self.connection.execute(
                "SELECT normalized_json FROM properties WHERE city_slug=?", (city_slug,)
            )
        ]
        for prop in ranked:
            folio = str(prop.get("folio") or "")
            cases = [
                json.loads(row["normalized_json"])
                for row in self.connection.execute(
                    "SELECT normalized_json FROM code_cases WHERE city_slug=? AND parcel_number=? ORDER BY opened_date DESC",
                    (city_slug, folio),
                )
            ]
            events = self.official_record_events(city_slug, folio)
            lead = self.get_property_lead(city_slug, folio)
            owner_key = str(prop.get("owner_canonical_key") or "")
            related = [
                {"folio": item.get("folio"), "address": item.get("address"), "unit_count": item.get("unit_count")}
                for item in all_properties
                if owner_key and self.owner_key(item.get("owner_name")) == owner_key and item.get("folio") != folio
            ]
            result.append(
                {
                    **prop,
                    "cases": cases,
                    "instruments": events,
                    "official_record_events": events,
                    "clerk_queried": bool(events),
                    "related_properties": related,
                    "lead": lead,
                }
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
        return len(result)
