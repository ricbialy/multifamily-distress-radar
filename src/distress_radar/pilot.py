from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from distress_radar.collectors.arcgis_property import ArcGisPropertyCollector
from distress_radar.collectors.miami_dade_clerk import MiamiDadeClerkCollector
from distress_radar.collectors.tyler_energov import TylerEnerGovCollector
from distress_radar.config import load_city_config
from distress_radar.domain.evidence import EvidenceItem, FreshnessStatus, ValueType
from distress_radar.domain.owner import CanonicalOwner
from distress_radar.domain.property import CanonicalProperty
from distress_radar.identity.address_normalizer import normalize_address
from distress_radar.identity.address_validation import (
    AddressCandidate,
    CountyAddressValidator,
    format_address,
)
from distress_radar.identity.folio_resolver import FolioResolver, normalize_folio
from distress_radar.identity.match_service import MatchService, MatchStatus
from distress_radar.identity.owner_resolver import OwnerResolver, normalize_owner_name
from distress_radar.intelligence_store import IntelligenceStore
from distress_radar.models import CodeCase, PropertyRecord, RawDocument
from distress_radar.municipal_severity import (
    MunicipalSeverity,
    classify_municipal_case,
    severity_from_mapping,
)
from distress_radar.recommendations.features import (
    RecommendationFeatures,
    ScoreDimensions,
    acquisition_attractiveness,
    calculate_evidence_scores,
    municipal_review_urgency,
)
from distress_radar.recommendations.rule_engine import recommend
from distress_radar.sources.base import CoverageState, SourceHealthState
from distress_radar.sources.mls.matrix_csv import MatrixCsvImporter
from distress_radar.tax_import import import_tax_csv, is_unpaid_status
from distress_radar.underwriting.commercial_multifamily import (
    CommercialMultifamilyInputs,
    underwrite_commercial,
)
from distress_radar.underwriting.offer_range import OfferInputs, calculate_offer_range

COUNTY_SOURCE = "miami_dade_property_point_view"
CODE_SOURCE = "hialeah_tyler_energov"
CLERK_SOURCE = "miami_dade_clerk_official_records"
TAX_SOURCE = "authorized_tax_csv"
MISSING_DILIGENCE = (
    "rent_roll",
    "T12",
    "NOI",
    "expenses",
    "insurance",
    "repairs",
    "capex",
    "taxes_after_sale",
    "valuation_support",
    "cap_rate_support",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    if isinstance(value, dict):
        value = {str(key): item for key, item in value.items()}
    return json.dumps(value, sort_keys=True, default=str)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _property_id(folio: str | None, address: str, municipality: str) -> str:
    identity = (
        f"folio:{normalize_folio(folio)}"
        if normalize_folio(folio)
        else f"address:{normalize_address(address)}|{municipality.casefold()}"
    )
    return "property-" + hashlib.sha256(identity.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class PilotRunResult:
    run_id: str
    matrix_sha256: str
    accepted_rows: int
    rejected_rows: int
    output_files: tuple[Path, ...]
    database_counts: dict[str, int]


def _migrate_pilot(store: IntelligenceStore) -> None:
    store.connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS pilot_runs (
            run_id TEXT PRIMARY KEY, input_filename TEXT NOT NULL,
            input_sha256 TEXT NOT NULL, municipality TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS import_ledger (
            run_id TEXT NOT NULL REFERENCES pilot_runs(run_id),
            line_number INTEGER NOT NULL, status TEXT NOT NULL,
            source_record_id TEXT, address TEXT, reason TEXT,
            PRIMARY KEY(run_id, line_number)
        );
        CREATE TABLE IF NOT EXISTS opportunity_alerts (
            alert_id TEXT PRIMARY KEY,
            pilot_run_id TEXT NOT NULL REFERENCES pilot_runs(run_id),
            property_id TEXT NOT NULL REFERENCES canonical_properties(property_id),
            alert_type TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(pilot_run_id,property_id,alert_type)
        );
        CREATE TABLE IF NOT EXISTS signal_observations (
            observation_id TEXT PRIMARY KEY,
            pilot_run_id TEXT NOT NULL REFERENCES pilot_runs(run_id),
            signal_id TEXT NOT NULL REFERENCES property_signals(signal_id),
            status TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        """
    )
    recommendation_columns = {
        row["name"]
        for row in store.connection.execute("PRAGMA table_info(recommendations)")
    }
    for column, definition in (
        ("pilot_run_id", "TEXT"),
        ("content_hash", "TEXT"),
        ("change_type", "TEXT"),
    ):
        if column not in recommendation_columns:
            store.connection.execute(
                f"ALTER TABLE recommendations ADD COLUMN {column} {definition}"
            )
    signal_columns = {
        row["name"]
        for row in store.connection.execute("PRAGMA table_info(property_signals)")
    }
    if "pilot_run_id" not in signal_columns:
        store.connection.execute(
            "ALTER TABLE property_signals ADD COLUMN pilot_run_id TEXT"
        )
    if "confirmation_status" not in signal_columns:
        store.connection.execute(
            """
            ALTER TABLE property_signals
            ADD COLUMN confirmation_status TEXT NOT NULL DEFAULT 'confirmed'
            """
        )
    if "source_name" not in signal_columns:
        store.connection.execute(
            "ALTER TABLE property_signals ADD COLUMN source_name TEXT"
        )
    store.connection.execute(
        """
        UPDATE property_signals
        SET source_name=CASE
            WHEN signal_type='off_market_live_code_case' THEN ?
            WHEN signal_type IN ('lien','recorded_liens','lis_pendens') THEN ?
            WHEN signal_type='tax_delinquency' THEN ?
            ELSE source_name
        END
        WHERE source_name IS NULL
        """,
        (CODE_SOURCE, CLERK_SOURCE, TAX_SOURCE),
    )
    source_run_columns = {
        row["name"]
        for row in store.connection.execute("PRAGMA table_info(source_runs)")
    }
    if "pilot_run_id" not in source_run_columns:
        store.connection.execute(
            "ALTER TABLE source_runs ADD COLUMN pilot_run_id TEXT"
        )
    store.connection.commit()


def _link_source_run(
    store: IntelligenceStore, source_run_id: str, pilot_run_id: str
) -> None:
    store.connection.execute(
        "UPDATE source_runs SET pilot_run_id=? WHERE run_id=?",
        (pilot_run_id, source_run_id),
    )
    store.connection.commit()


def _years_since(value: str | None, as_of: str) -> float | None:
    if not value:
        return None
    try:
        then = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            then = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    now = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return round(max(0, (now - then).days) / 365.25, 1)


def _display_date(value: str | None) -> str:
    if not value:
        return "Not available"
    compact = value.strip()
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return compact[:10]


def _county_value(record: PropertyRecord, generated_at: str) -> dict[str, Any]:
    property_address = normalize_address(
        " ".join(value for value in (record.address, record.city) if value)
    )
    mailing_address = " ".join(
        value
        for value in (
            record.mailing_address_1,
            record.mailing_address_2,
            record.mailing_city,
            record.mailing_state,
            record.mailing_zip,
        )
        if value
    )
    normalized_mailing = normalize_address(mailing_address)
    absentee = (
        bool(normalized_mailing)
        and bool(property_address)
        and property_address not in normalized_mailing
    )
    return {
        "folio": normalize_folio(record.folio),
        "address": record.address,
        "owner": record.owner_name,
        "owner_2": record.owner_name_2,
        "mailing_address": mailing_address or None,
        "absentee_owner": absentee if mailing_address else None,
        "last_sale_date": record.last_sale_date,
        "last_sale_price": record.last_sale_price,
        "ownership_duration_years": _years_since(record.last_sale_date, generated_at),
        "assessed_value": record.assessed_value,
        "year_built": record.year_built,
        "building_area": record.building_area,
        "lot_size": record.lot_size,
        "verified_units": record.unit_count,
        "units": record.unit_count,
        "property_class": record.dor_description,
    }


def _canonical_properties(store: IntelligenceStore) -> tuple[CanonicalProperty, ...]:
    rows = store.connection.execute(
        """
        SELECT property_id,folio,address,municipality,jurisdiction,latitude,longitude
        FROM canonical_properties ORDER BY property_id
        """
    ).fetchall()
    return tuple(CanonicalProperty(**dict(row)) for row in rows)


def _canonical_owners(store: IntelligenceStore) -> tuple[CanonicalOwner, ...]:
    rows = store.connection.execute(
        "SELECT owner_id,display_name,entity_type FROM canonical_owners ORDER BY owner_id"
    ).fetchall()
    return tuple(CanonicalOwner(**dict(row)) for row in rows)


def _save_raw_document(
    store: IntelligenceStore, run_id: str, source_name: str, document: RawDocument
) -> tuple[str, bool]:
    payload = _json(document.payload)
    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    previous = store.connection.execute(
        """
        SELECT 1 FROM source_records
        WHERE source_name=? AND source_record_id=? AND payload_hash=? LIMIT 1
        """,
        (source_name, f"{document.kind}:{document.source_key}", payload_hash),
    ).fetchone()
    store.connection.execute(
        """
        INSERT OR IGNORE INTO source_records (
            source_name,source_record_id,run_id,source_url,fetched_at,
            raw_payload_json,normalized_payload_json,payload_hash
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            source_name,
            f"{document.kind}:{document.source_key}",
            run_id,
            document.source_url,
            document.fetched_at,
            payload,
            None,
            payload_hash,
        ),
    )
    store.connection.commit()
    return payload_hash, previous is None


def _save_owner(store: IntelligenceStore, prop: CanonicalProperty, record: PropertyRecord) -> None:
    if not record.owner_name:
        return
    owners = _canonical_owners(store)
    resolution = OwnerResolver(owners).resolve(record.owner_name)
    owner_id = resolution.owner_id
    if owner_id is None:
        key = normalize_owner_name(record.owner_name)
        owner_id = "owner-" + hashlib.sha256(key.encode()).hexdigest()[:20]
        now = utc_now()
        store.connection.execute(
            """
            INSERT OR IGNORE INTO canonical_owners (
                owner_id,display_name,entity_type,created_at,updated_at
            ) VALUES (?,?,?,?,?)
            """,
            (owner_id, record.owner_name, None, now, now),
        )
    store.connection.execute(
        """
        INSERT OR IGNORE INTO owner_aliases (
            owner_id,alias,normalized_alias,source_record_id,confidence
        ) VALUES (?,?,?,?,?)
        """,
        (owner_id, record.owner_name, normalize_owner_name(record.owner_name), record.folio, 1.0),
    )
    store.connection.execute(
        """
        INSERT OR IGNORE INTO property_ownership (
            property_id,owner_id,start_date,end_date,source_record_id,confidence
        ) VALUES (?,?,?,?,?,?)
        """,
        (prop.property_id, owner_id, None, None, record.folio, 1.0),
    )
    store.connection.commit()


_CLOCK_ONLY_KEYS = {
    "age_days",
    "ownership_duration_years",
    "generated_at",
    "fetched_at",
    "observed_at",
    "checked_at",
    "data_freshness",
    "severity_score",
    "severity_reasons",
    "repetition_score",
}


def _material_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _material_value(item)
            for key, item in value.items()
            if str(key) not in _CLOCK_ONLY_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_material_value(item) for item in value]
    return value


def _material_content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json(_material_value(payload)).encode()).hexdigest()


def _has_verified_address_evidence(
    store: IntelligenceStore, property_id: str
) -> bool:
    row = store.connection.execute(
        """
        SELECT value_json,value_type,metadata_json
        FROM evidence_items
        WHERE property_id=? AND field_name='validated_address'
        ORDER BY fetched_at DESC,rowid DESC LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    if not row or row["value_type"] != ValueType.REPORTED.value:
        return False
    metadata = json.loads(row["metadata_json"])
    return bool(
        json.loads(row["value_json"])
        and metadata.get("validation_status") == "verified"
    )


def _is_serious_municipal_matter(item: MunicipalSeverity) -> bool:
    return bool(
        item.currently_active
        and (
            item.score >= 50
            or item.enforcement_stage in {"itl", "lien", "special_master"}
            or item.category
            in {
                "intent_to_lien_or_lien",
                "special_master_escalation",
                "unsafe_or_life_safety",
            }
            or item.substantive_hazard == "unsafe_life_safety"
        )
    )


def _active_clerk_records(records: tuple[Any, ...]) -> tuple[Any, ...]:
    """Collapse Clerk party rows and remove instruments with linked resolutions."""
    instruments: dict[str, Any] = {}
    released: set[str] = set()
    for record in records:
        instrument_id = str(record.instrument_id or record.source_record_id)
        instruments.setdefault(instrument_id, record)
        if record.signal_type == "release" and record.original_instrument_id:
            released.add(str(record.original_instrument_id))
    return tuple(
        record
        for instrument_id, record in sorted(instruments.items())
        if record.signal_type in {"lien", "recorded_liens", "lis_pendens"}
        and instrument_id not in released
    )


def _apply_disposition_action(
    *,
    disposition: str | None,
    baseline_content_hash: str | None,
    current_content_hash: str,
    default_action: str,
    listing_present: bool,
    identity_verified: bool,
    in_scope: bool,
    serious_municipal: bool,
    underwriting_complete: bool,
) -> str:
    if not disposition or baseline_content_hash != current_content_hash:
        return default_action
    if disposition == "approved_for_contact" and (
        not identity_verified
        or not in_scope
        or serious_municipal
        or not underwriting_complete
    ):
        return default_action
    return {
        "investigate": "investigate_owner",
        "request_documents": (
            "contact_broker_for_documents" if listing_present else "request_documents"
        ),
        "watch": "watch",
        "dismiss": "dismiss",
        "legal_municipal_review": "human_municipal_review",
        "approved_for_contact": (
            "contact_broker" if listing_present else "contact_owner"
        ),
    }.get(disposition, default_action)


def _save_signal(
    store: IntelligenceStore,
    *,
    property_id: str,
    signal_type: str,
    source_name: str,
    observed_at: str,
    value: Any,
    evidence_ids: tuple[str, ...],
    pilot_run_id: str,
) -> str:
    identity = _json((property_id, signal_type, value))
    signal_id = "signal-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    previous = store.connection.execute(
        """
        SELECT status,confirmation_status FROM property_signals WHERE signal_id=?
        """,
        (signal_id,),
    ).fetchone()
    store.connection.execute(
        """
        INSERT INTO property_signals (
            signal_id,property_id,signal_type,observed_at,value_json,
            evidence_ids_json,status,pilot_run_id,confirmation_status,source_name
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signal_id) DO UPDATE SET
            observed_at=excluded.observed_at,
            evidence_ids_json=excluded.evidence_ids_json,
            status='active',
            pilot_run_id=excluded.pilot_run_id,
            confirmation_status='confirmed',
            source_name=excluded.source_name
        """,
        (
            signal_id,
            property_id,
            signal_type,
            observed_at,
            _json(value),
            _json(evidence_ids),
            "active",
            pilot_run_id,
            "confirmed",
            source_name,
        ),
    )
    if previous is None or previous["status"] == "resolved":
        store.connection.execute(
            """
            INSERT INTO signal_observations (
                observation_id,pilot_run_id,signal_id,status,observed_at
            ) VALUES (?,?,?,?,?)
            """,
            (str(uuid4()), pilot_run_id, signal_id, "active", observed_at),
        )
    store.connection.commit()
    return signal_id


def _reconcile_source_signals(
    store: IntelligenceStore,
    *,
    source_name: str,
    seen_signal_ids: set[str],
    healthy: bool,
    pilot_run_id: str,
    observed_at: str,
) -> None:
    if not healthy:
        store.connection.execute(
            """
            UPDATE property_signals SET confirmation_status='last_known'
            WHERE source_name=? AND status='active'
            """,
            (source_name,),
        )
        store.connection.commit()
        return
    current = store.connection.execute(
        """
        SELECT signal_id FROM property_signals
        WHERE source_name=? AND status='active'
        """,
        (source_name,),
    ).fetchall()
    for row in current:
        signal_id = row["signal_id"]
        if signal_id in seen_signal_ids:
            continue
        store.connection.execute(
            """
            UPDATE property_signals
            SET status='resolved',confirmation_status='resolved',pilot_run_id=?
            WHERE signal_id=? AND status='active'
            """,
            (pilot_run_id, signal_id),
        )
        store.connection.execute(
            """
            INSERT INTO signal_observations (
                observation_id,pilot_run_id,signal_id,status,observed_at
            ) VALUES (?,?,?,?,?)
            """,
            (str(uuid4()), pilot_run_id, signal_id, "resolved", observed_at),
        )
    store.connection.commit()


def _signal_evidence_ids(
    store: IntelligenceStore, property_id: str, confirmation_status: str
) -> set[str]:
    rows = store.connection.execute(
        """
        SELECT evidence_ids_json FROM property_signals
        WHERE property_id=? AND status='active'
          AND confirmation_status=?
        """,
        (property_id, confirmation_status),
    ).fetchall()
    return {
        str(evidence_id)
        for row in rows
        for evidence_id in json.loads(row["evidence_ids_json"])
    }


def _coverage_is_unknown(
    coverage: list[Any], source_name: str
) -> bool:
    row = next((item for item in coverage if item["source_name"] == source_name), None)
    return bool(
        row
        and row["state"]
        in {
            CoverageState.UNKNOWN_FAILED.value,
            CoverageState.UNKNOWN_NOT_RUN.value,
            CoverageState.UNKNOWN_STALE.value,
        }
    )


def _price_reduction_count(
    store: IntelligenceStore, mls_number: str, source_name: str
) -> int:
    rows = store.connection.execute(
        """
        SELECT before_json,after_json FROM listing_changes
        WHERE mls_number=? AND source_name=? AND change_type='price_change'
        """,
        (mls_number, source_name),
    ).fetchall()
    reductions = 0
    for row in rows:
        try:
            before = json.loads(row["before_json"])
            after = json.loads(row["after_json"])
            before_price = before.get("list_price") if isinstance(before, dict) else before
            after_price = after.get("list_price") if isinstance(after, dict) else after
            reductions += int(float(after_price) < float(before_price))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return reductions


def _record_for_matrix(
    *,
    store: IntelligenceStore,
    listing: Any,
    county_run_id: str,
    collector: Any,
    generated_at: str,
) -> tuple[str, bool, bool, int]:
    query_scope = f"address:{listing.address}"
    county_record: PropertyRecord | None = None
    exact_documents: tuple[RawDocument, ...] = ()
    error: str | None = None
    address_records_examined = 0
    address_source_url: str | None = None
    address_raw_hash: str | None = None
    try:
        address_result = collector.lookup_address(listing.address)
        address_records_examined = len(address_result.records)
        for document in address_result.raw_documents:
            address_source_url = document.source_url
            address_raw_hash, _ = _save_raw_document(
                store, county_run_id, COUNTY_SOURCE, document
            )
        candidates = tuple(
            record
            for record in address_result.records
            if normalize_address(record.address) == normalize_address(listing.address)
        )
        if candidates:
            resolution = FolioResolver().resolve(
                listing.folio, tuple(record.folio for record in candidates)
            )
            folio = resolution.folio
            exact_result = collector.lookup_exact_folio(folio or "")
            exact_documents = exact_result.raw_documents
            for document in exact_documents:
                _save_raw_document(store, county_run_id, COUNTY_SOURCE, document)
            county_record = next(
                (
                    record
                    for record in exact_result.records
                    if normalize_folio(record.folio) == folio
                ),
                None,
            )
    except Exception as exc:
        error = str(exc)

    municipality = county_record.city if county_record and county_record.city else "Miami-Dade"
    folio = normalize_folio(county_record.folio) if county_record else None
    address = (
        format_address(
            county_record.address or listing.address,
            municipality,
            "FL",
            county_record.zip_code,
        )
        if county_record
        else listing.address
    )
    existing = _canonical_properties(store)
    prior_listing = store.connection.execute(
        """
        SELECT property_id FROM listing_snapshots
        WHERE mls_number=? AND source_name=? AND property_id IS NOT NULL
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (listing.source_record_id, listing.source_name),
    ).fetchone()
    match = (
        None
        if prior_listing
        else MatchService().match(
            folio=folio,
            address=county_record.address if county_record and county_record.address else address,
            municipality=municipality,
            candidates=existing,
        )
    )
    property_id = (
        prior_listing["property_id"]
        if prior_listing
        else match.property_id
        if match and match.status == MatchStatus.CONFIRMED and match.property_id
        else _property_id(folio, address, municipality)
    )
    prop = CanonicalProperty(
        property_id=property_id,
        folio=folio,
        address=address,
        municipality=municipality,
        jurisdiction="Miami-Dade",
        latitude=county_record.latitude if county_record else None,
        longitude=county_record.longitude if county_record else None,
    )
    store.upsert_property(prop)
    listing_changes = store.save_listing_snapshot(listing, property_id)

    listing_evidence = EvidenceItem(
        field="matrix_listing",
        value={
            "mls_number": listing.source_record_id,
            "address": listing.address,
            "status": listing.status,
            "list_price": listing.list_price,
            "dom": listing.dom,
            "cdom": listing.cdom,
            "advertised_units": listing.units,
            "noi": listing.noi,
            "rents": listing.rents,
            "expenses": listing.expenses,
            "remarks": listing.remarks,
        },
        source=listing.source_name,
        source_record_id=listing.source_record_id,
        source_url=listing.source_url,
        fetched_at=listing.fetched_at,
        freshness_status=FreshnessStatus.FRESH,
        confidence=1.0,
        value_type=ValueType.REPORTED,
    )
    store.add_evidence(property_id, listing_evidence)

    verified = False
    if county_record:
        validation = CountyAddressValidator((county_record,)).validate(
            folio=county_record.folio,
            candidates=(
                AddressCandidate(
                    source="matrix_csv",
                    street=listing.address,
                    municipality=county_record.city or municipality,
                    state=listing.state,
                    postal_code=listing.postal_code,
                ),
            ),
            checked_at=generated_at,
        )
        verified = validation.is_authoritatively_verified
        county_evidence = EvidenceItem(
            field="validated_address",
            value=validation.address if verified else None,
            source=COUNTY_SOURCE,
            source_record_id=county_record.folio,
            source_url=county_record.source_url,
            fetched_at=county_record.fetched_at,
            freshness_status=FreshnessStatus.FRESH,
            confidence=1.0 if verified else 0.5,
            value_type=ValueType.REPORTED if verified else ValueType.UNKNOWN,
            metadata={
                "validation_status": validation.status.value,
                "details": validation.details,
                "query_scope": f"folio:{county_record.folio}",
            },
        )
        store.add_evidence(property_id, county_evidence)
        store.add_evidence(
            property_id,
            EvidenceItem(
                field="public_property_record",
                value={
                    **_county_value(county_record, generated_at),
                    "address": validation.address,
                    "pilot_segment_10_80": bool(
                        county_record.unit_count
                        and 10 <= county_record.unit_count <= 80
                    ),
                },
                source=COUNTY_SOURCE,
                source_record_id=county_record.folio,
                source_url=county_record.source_url,
                fetched_at=county_record.fetched_at,
                freshness_status=FreshnessStatus.FRESH,
                confidence=1.0,
                value_type=ValueType.REPORTED,
            ),
        )
        store.set_source_coverage(
            property_id=property_id,
            source_name=COUNTY_SOURCE,
            state=CoverageState.CONFIRMED_PRESENT,
            query_scope=f"folio:{county_record.folio}",
            records_examined=1,
            records_matched=1,
            run_id=county_run_id,
            source_url=county_record.source_url,
            raw_response_hash=(
                hashlib.sha256(_json(exact_documents[0].payload).encode()).hexdigest()
                if exact_documents
                else None
            ),
            checked_at=generated_at,
        )
        _save_owner(store, prop, county_record)
    else:
        state = CoverageState.UNKNOWN_FAILED if error else CoverageState.CONFIRMED_ABSENT
        store.set_source_coverage(
            property_id=property_id,
            source_name=COUNTY_SOURCE,
            state=state,
            query_scope=query_scope,
            records_examined=address_records_examined,
            records_matched=0,
            run_id=county_run_id,
            source_url=address_source_url,
            raw_response_hash=address_raw_hash,
            error_message=error,
            checked_at=generated_at,
        )
        store.add_evidence(
            property_id,
            EvidenceItem.unknown(
                field="validated_address",
                source=COUNTY_SOURCE,
                source_record_id=listing.source_record_id,
                fetched_at=generated_at,
                reason=error or "no_exact_county_match",
            ),
        )
    return property_id, verified, error is not None, len(listing_changes)


def _persist_off_market(
    store: IntelligenceStore,
    *,
    records: tuple[PropertyRecord, ...],
    cases_by_folio: dict[str, list[CodeCase]],
    county_run_id: str,
    code_run_id: str,
    pilot_run_id: str,
    generated_at: str,
    source_healthy: bool,
) -> int:
    count = 0
    seen_signal_ids: set[str] = set()
    for record in records:
        folio = normalize_folio(record.folio)
        cases = [
            case
            for case in cases_by_folio.get(folio or "", [])
            if classify_municipal_case(case, as_of=generated_at).currently_active
        ]
        if not folio or not cases or not record.address:
            continue
        municipality = record.city or "HIALEAH"
        property_id = _property_id(folio, record.address, municipality)
        complete_address = format_address(
            record.address, municipality, "FL", record.zip_code
        )
        prop = CanonicalProperty(
            property_id=property_id,
            folio=folio,
            address=complete_address,
            municipality=municipality,
            jurisdiction="Miami-Dade",
            latitude=record.latitude,
            longitude=record.longitude,
        )
        match = MatchService().match(
            folio=folio,
            address=record.address,
            municipality=municipality,
            candidates=_canonical_properties(store),
        )
        if match.status == MatchStatus.CONFIRMED and match.property_id:
            prop = CanonicalProperty(
                property_id=match.property_id,
                folio=folio,
                address=complete_address,
                municipality=municipality,
                jurisdiction="Miami-Dade",
                latitude=record.latitude,
                longitude=record.longitude,
            )
        store.upsert_property(prop)
        _save_owner(store, prop, record)
        county_evidence_id = store.add_evidence(
            prop.property_id,
            EvidenceItem(
                field="public_property_record",
                value=_county_value(record, generated_at),
                source=COUNTY_SOURCE,
                source_record_id=folio,
                source_url=record.source_url,
                fetched_at=record.fetched_at,
                freshness_status=FreshnessStatus.FRESH,
                confidence=1.0,
                value_type=ValueType.REPORTED,
            ),
        )
        case_evidence_ids: list[str] = []
        for case in cases:
            severity = classify_municipal_case(case, as_of=generated_at)
            case_evidence_id = store.add_evidence(
                    prop.property_id,
                    EvidenceItem(
                        field="municipal_code_case",
                        value={
                            "case_number": case.case_number,
                            "case_type": case.case_type,
                            "status": case.status,
                            "opened_date": case.opened_date,
                            "closed_date": case.closed_date,
                            "description": case.description,
                            "violation_count": case.violation_count,
                            "violations": list(case.violations),
                            "severity_category": severity.category,
                            "severity_score": severity.score,
                            "status_category": severity.status_category,
                            "age_days": severity.age_days,
                            "severity_reasons": severity.reasons,
                            "enforcement_stage": severity.enforcement_stage,
                            "substantive_hazard": severity.substantive_hazard,
                            "cure_complexity": severity.cure_complexity,
                            "likely_cost": severity.likely_cost,
                            "repetition_score": severity.repetition_score,
                            "currently_active": severity.currently_active,
                        },
                        source=CODE_SOURCE,
                        source_record_id=case.source_record_id,
                        source_url=case.source_url,
                        fetched_at=case.fetched_at,
                        freshness_status=FreshnessStatus.FRESH,
                        confidence=1.0,
                        value_type=ValueType.REPORTED,
                    ),
                )
            case_evidence_ids.append(case_evidence_id)
            seen_signal_ids.add(
                _save_signal(
                    store,
                    property_id=prop.property_id,
                    signal_type="off_market_live_code_case",
                    source_name=CODE_SOURCE,
                    observed_at=generated_at,
                    value={"case_source_record_id": case.source_record_id},
                    evidence_ids=(county_evidence_id, case_evidence_id),
                    pilot_run_id=pilot_run_id,
                )
            )
        store.set_source_coverage(
            property_id=prop.property_id,
            source_name=COUNTY_SOURCE,
            state=CoverageState.CONFIRMED_PRESENT,
            query_scope="hialeah multifamily 10-80 units",
            records_examined=len(records),
            records_matched=1,
            run_id=county_run_id,
            source_url=record.source_url,
            checked_at=generated_at,
        )
        store.set_source_coverage(
            property_id=prop.property_id,
            source_name=CODE_SOURCE,
            state=CoverageState.CONFIRMED_PRESENT,
            query_scope=f"folio:{folio}",
            records_examined=sum(len(value) for value in cases_by_folio.values()),
            records_matched=len(cases),
            run_id=code_run_id,
            source_url=cases[0].source_url,
            checked_at=generated_at,
        )
        count += 1
    _reconcile_source_signals(
        store,
        source_name=CODE_SOURCE,
        seen_signal_ids=seen_signal_ids,
        healthy=source_healthy,
        pilot_run_id=pilot_run_id,
        observed_at=generated_at,
    )
    return count


def _persist_underwriting_and_recommendations_legacy(
    store: IntelligenceStore, *, generated_at: str
) -> None:
    for prop in _canonical_properties(store):
        property_id = prop.property_id
        listing = store.connection.execute(
            "SELECT 1 FROM listing_snapshots WHERE property_id=? LIMIT 1",
            (property_id,),
        ).fetchone()
        off_market = store.connection.execute(
            """
            SELECT 1 FROM property_signals
            WHERE property_id=? AND signal_type='off_market_live_code_case' LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        case_evidence = store.connection.execute(
            """
            SELECT 1 FROM evidence_items
            WHERE property_id=? AND field_name='municipal_code_case' LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        coverage = store.property_source_coverage(property_id)
        county = next(
            (row for row in coverage if row["source_name"] == COUNTY_SOURCE), None
        )
        identity_verified = bool(
            prop.folio
            and county
            and county["state"] == CoverageState.CONFIRMED_PRESENT.value
            and (not listing or _has_verified_address_evidence(store, property_id))
        )
        source_gaps = tuple(
            f"{row['source_name']}:{row['state']}"
            for row in coverage
            if row["state"]
            in {
                CoverageState.UNKNOWN_FAILED.value,
                CoverageState.UNKNOWN_NOT_RUN.value,
                CoverageState.UNKNOWN_STALE.value,
            }
        )
        source_gap_evidence_ids: list[str] = []
        for row in coverage:
            if row["state"] not in {
                CoverageState.UNKNOWN_FAILED.value,
                CoverageState.UNKNOWN_NOT_RUN.value,
                CoverageState.UNKNOWN_STALE.value,
            }:
                continue
            source_gap_evidence_ids.append(
                store.add_evidence(
                    property_id,
                    EvidenceItem.unknown(
                        field=f"source_coverage:{row['source_name']}",
                        source=row["source_name"],
                        source_record_id=row["run_id"] or property_id,
                        fetched_at=row["checked_at"],
                        reason=row["error_message"] or row["state"],
                        source_url=row["source_url"],
                    ),
                )
            )
        diligence_evidence_ids = tuple(
            store.add_evidence(
                property_id,
                EvidenceItem.unknown(
                    field=f"diligence:{field}",
                    source="acquisition_diligence",
                    source_record_id=property_id,
                    fetched_at=generated_at,
                    reason="not_provided_or_not_supported",
                ),
            )
            for field in MISSING_DILIGENCE
        )
        units_row = store.connection.execute(
            """
            SELECT value_json FROM evidence_items
            WHERE property_id=? AND field_name='public_property_record'
            ORDER BY fetched_at DESC LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        units = None
        if units_row:
            units = json.loads(units_row["value_json"]).get("units")
        in_scope = units is None or 10 <= units <= 80
        inputs = CommercialMultifamilyInputs(
            units=units,
            current_noi=None,
            gross_potential_rent=None,
            market_vacancy_rate=None,
            taxes_after_sale=None,
            insurance=None,
            management_rate=None,
            utilities=None,
            maintenance=None,
            other_operating_expenses=None,
            deferred_maintenance=None,
            capital_expenditures=None,
            market_cap_rate_low=None,
            market_cap_rate_base=None,
            market_cap_rate_high=None,
            public_unit_count_verified=bool(units),
        )
        underwriting = underwrite_commercial(inputs)
        underwriting_run_id = str(uuid4())
        store.connection.execute(
            """
            INSERT INTO underwriting_runs (
                run_id,property_id,model_name,as_of,inputs_json,outputs_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                underwriting_run_id,
                property_id,
                "commercial_multifamily",
                generated_at,
                _json(asdict(inputs)),
                _json(asdict(underwriting)),
            ),
        )
        offer = calculate_offer_range(OfferInputs())
        channels = tuple(
            name
            for name, present in (("mls", listing), ("off_market", off_market))
            if present
        )
        missing = tuple(dict.fromkeys((*MISSING_DILIGENCE, *source_gaps)))
        features = RecommendationFeatures(
            property_id=property_id,
            discovery_channels=channels,
            scores=ScoreDimensions(
                owner_motivation=0,
                economics=0,
                market_pressure=20 if listing else 0,
                property_risk=70 if case_evidence else 10,
                data_confidence=80 if identity_verified else 25,
                data_completeness=25,
                data_freshness=90,
            ),
            has_underwriting=underwriting.status == "complete",
            critical_documents_missing=True,
            violation_review_required=bool(case_evidence),
            municipal_search_required=False,
            why_now=(
                ("Live municipal code case matched by exact folio.",)
                if case_evidence
                else ("Current authorized Matrix listing.",)
            ),
            key_risks=(
                ("Municipal case requires human review.",)
                if case_evidence
                else ("Operating documents are unavailable.",)
            ),
            missing_data=missing,
            evidence=(),
            is_synthetic=False,
            identity_verified=identity_verified,
            specific_opportunity=bool(case_evidence),
            in_scope=in_scope,
        )
        result = recommend(features)
        evidence_ids = tuple(
            row["evidence_id"]
            for row in store.connection.execute(
                """
                SELECT evidence_id FROM evidence_items
                WHERE property_id=? ORDER BY fetched_at,evidence_id
                """,
                (property_id,),
            ).fetchall()
        )
        listing_evidence_ids = tuple(
            row["evidence_id"]
            for row in store.connection.execute(
                """
                SELECT evidence_id FROM evidence_items
                WHERE property_id=? AND field_name='matrix_listing'
                ORDER BY fetched_at,evidence_id
                """,
                (property_id,),
            ).fetchall()
        )
        identity_evidence_ids = tuple(
            row["evidence_id"]
            for row in store.connection.execute(
                """
                SELECT evidence_id FROM evidence_items
                WHERE property_id=? AND field_name IN (
                    'validated_address','public_property_record'
                ) ORDER BY fetched_at,evidence_id
                """,
                (property_id,),
            ).fetchall()
        )
        municipal_evidence_ids = tuple(
            row["evidence_id"]
            for row in store.connection.execute(
                """
                SELECT evidence_id FROM evidence_items
                WHERE property_id=? AND field_name='municipal_code_case'
                ORDER BY fetched_at,evidence_id
                """,
                (property_id,),
            ).fetchall()
        )
        explanation = {
            **result.explanation,
            "missing_data": missing,
            "evidence_ids": evidence_ids,
            "statement_evidence_ids": {
                "why_this_property_surfaced": (
                    municipal_evidence_ids or listing_evidence_ids
                ),
                "identity": identity_evidence_ids,
                "property_risk": municipal_evidence_ids,
                "missing_source_data": tuple(source_gap_evidence_ids),
                "missing_diligence": diligence_evidence_ids,
                "recommended_action": (
                    municipal_evidence_ids
                    if result.action == "human_violation_review"
                    else identity_evidence_ids
                    if result.action == "verify_identity"
                    else diligence_evidence_ids
                    if result.action in {"request_documents", "insufficient_data"}
                    else evidence_ids
                ),
            },
            "discovery_channels": channels,
            "identity_verified": identity_verified,
            "underwriting_status": underwriting.status,
            "underwriting_run_id": underwriting_run_id,
            "offer_gate": {
                **offer.to_dict(),
                "display": "OFFER GATE: FAIL — INSUFFICIENT DATA",
            },
        }
        store.connection.execute(
            """
            INSERT INTO recommendations (
                recommendation_id,property_id,generated_at,action,
                scores_json,explanation_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                str(uuid4()),
                property_id,
                generated_at,
                result.action,
                _json(asdict(result.scores)),
                _json(explanation),
            ),
        )
        store.connection.commit()


def _persist_underwriting_and_recommendations(
    store: IntelligenceStore, *, generated_at: str, pilot_run_id: str
) -> None:
    for prop in _canonical_properties(store):
        property_id = prop.property_id
        listing_row = store.connection.execute(
            """
            SELECT mls_number,status,list_price,dom,cdom,normalized_json,source_name
            FROM listing_snapshots WHERE property_id=?
            ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        listing = json.loads(listing_row["normalized_json"]) if listing_row else {}
        if listing_row:
            listing.update(
                {
                    "mls_number": listing_row["mls_number"],
                    "status": listing_row["status"],
                    "list_price": listing_row["list_price"],
                    "dom": listing_row["dom"],
                    "cdom": listing_row["cdom"],
                }
            )
            listing["price_reduction_count"] = _price_reduction_count(
                store, listing_row["mls_number"], listing_row["source_name"]
            )

        off_market = store.connection.execute(
            """
            SELECT 1 FROM property_signals
            WHERE property_id=? AND signal_type='off_market_live_code_case'
              AND status='active' AND confirmation_status='confirmed' LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        coverage = store.property_source_coverage(property_id)
        confirmed_evidence_ids = _signal_evidence_ids(
            store, property_id, "confirmed"
        )
        last_known_evidence_ids = _signal_evidence_ids(
            store, property_id, "last_known"
        )
        county_coverage = next(
            (row for row in coverage if row["source_name"] == COUNTY_SOURCE), None
        )
        identity_verified = bool(
            prop.folio
            and county_coverage
            and county_coverage["state"] == CoverageState.CONFIRMED_PRESENT.value
            and (
                listing_row is None
                or _has_verified_address_evidence(store, property_id)
            )
        )
        county_row = store.connection.execute(
            """
            SELECT value_json FROM evidence_items
            WHERE property_id=? AND field_name='public_property_record'
            ORDER BY fetched_at DESC,rowid DESC LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        county = json.loads(county_row["value_json"]) if county_row else {}
        units = county.get("verified_units", county.get("units"))

        municipal_rows = store.connection.execute(
            """
            SELECT e.evidence_id,e.source_record_id,e.value_json FROM evidence_items e
            WHERE e.property_id=? AND e.field_name='municipal_code_case'
              AND e.evidence_id=(
                  SELECT e2.evidence_id FROM evidence_items e2
                  WHERE e2.property_id=e.property_id
                    AND e2.field_name=e.field_name
                    AND e2.source_record_id=e.source_record_id
                  ORDER BY e2.fetched_at DESC,e2.rowid DESC LIMIT 1
              )
            ORDER BY e.source_record_id
            """,
            (property_id,),
        ).fetchall()
        historical_municipal_records: list[tuple[str, dict[str, Any]]] = []
        municipal_values: list[dict[str, Any]] = []
        seen_cases: set[str] = set()
        for row in municipal_rows:
            if row["source_record_id"] in seen_cases:
                continue
            seen_cases.add(row["source_record_id"])
            value = json.loads(row["value_json"])
            historical_municipal_records.append((row["evidence_id"], value))
            if row["evidence_id"] in confirmed_evidence_ids:
                municipal_values.append(value)
        municipal = tuple(severity_from_mapping(value) for value in municipal_values)
        historical_official_records = tuple(
            sorted(
                (
                    (row["evidence_id"], json.loads(row["value_json"]))
                    for row in store.connection.execute(
                """
                SELECT e.evidence_id,e.value_json FROM evidence_items e
                WHERE e.property_id=? AND e.field_name='official_record'
                  AND e.evidence_id=(
                      SELECT e2.evidence_id FROM evidence_items e2
                      WHERE e2.property_id=e.property_id
                        AND e2.field_name=e.field_name
                        AND e2.source_record_id=e.source_record_id
                      ORDER BY e2.fetched_at DESC,e2.rowid DESC LIMIT 1
                  )
                ORDER BY e.source_record_id
                """,
                (property_id,),
                    ).fetchall()
                ),
                key=lambda item: _json(item[1]),
            )
        )
        official_records = tuple(
            value
            for evidence_id, value in historical_official_records
            if evidence_id in confirmed_evidence_ids
        )
        historical_tax_records = tuple(
            sorted(
                (
                    (row["evidence_id"], json.loads(row["value_json"]))
                    for row in store.connection.execute(
                """
                SELECT e.evidence_id,e.value_json FROM evidence_items e
                WHERE e.property_id=? AND e.field_name='tax_delinquency'
                  AND e.evidence_id=(
                      SELECT e2.evidence_id FROM evidence_items e2
                      WHERE e2.property_id=e.property_id
                        AND e2.field_name=e.field_name
                        AND e2.source_record_id=e.source_record_id
                      ORDER BY e2.fetched_at DESC,e2.rowid DESC LIMIT 1
                  )
                ORDER BY e.source_record_id
                """,
                (property_id,),
                    ).fetchall()
                ),
                key=lambda item: _json(item[1]),
            )
        )
        tax_records = tuple(
            value
            for evidence_id, value in historical_tax_records
            if evidence_id in confirmed_evidence_ids
        )
        source_gaps = tuple(
            f"{row['source_name']}:{row['state']}"
            for row in coverage
            if row["state"]
            in {
                CoverageState.UNKNOWN_FAILED.value,
                CoverageState.UNKNOWN_NOT_RUN.value,
                CoverageState.UNKNOWN_STALE.value,
            }
        )
        missing_fields = list(MISSING_DILIGENCE)
        if listing.get("noi") is not None:
            missing_fields.remove("NOI")
        if listing.get("expenses") is not None:
            missing_fields.remove("expenses")
        missing = tuple(dict.fromkeys((*missing_fields, *source_gaps)))

        source_gap_evidence_ids: list[str] = []
        for row in coverage:
            if f"{row['source_name']}:{row['state']}" not in source_gaps:
                continue
            source_gap_evidence_ids.append(
                store.add_evidence(
                    property_id,
                    EvidenceItem.unknown(
                        field=f"source_coverage:{row['source_name']}",
                        source=row["source_name"],
                        source_record_id=row["run_id"] or property_id,
                        fetched_at=row["checked_at"],
                        reason=row["error_message"] or row["state"],
                        source_url=row["source_url"],
                    ),
                )
            )
        diligence_evidence_ids = tuple(
            store.add_evidence(
                property_id,
                EvidenceItem.unknown(
                    field=f"diligence:{field}",
                    source="acquisition_diligence",
                    source_record_id=property_id,
                    fetched_at=generated_at,
                    reason="not_provided_or_not_supported",
                ),
            )
            for field in missing_fields
        )

        inputs = CommercialMultifamilyInputs(
            units=units,
            current_noi=listing.get("noi"),
            gross_potential_rent=listing.get("rents"),
            market_vacancy_rate=None,
            taxes_after_sale=None,
            insurance=None,
            management_rate=None,
            utilities=None,
            maintenance=None,
            other_operating_expenses=listing.get("expenses"),
            deferred_maintenance=None,
            capital_expenditures=None,
            market_cap_rate_low=None,
            market_cap_rate_base=None,
            market_cap_rate_high=None,
            building_area=county.get("building_area"),
            public_unit_count_verified=isinstance(units, int) and units > 0,
            advertised_units=listing.get("units"),
        )
        underwriting = underwrite_commercial(inputs)
        underwriting_run_id = str(uuid4())
        store.connection.execute(
            """
            INSERT INTO underwriting_runs (
                run_id,property_id,model_name,as_of,inputs_json,outputs_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                underwriting_run_id,
                property_id,
                "commercial_multifamily",
                generated_at,
                _json(asdict(inputs)),
                _json(asdict(underwriting)),
            ),
        )
        score_result = calculate_evidence_scores(
            county=county,
            listing=listing,
            municipal=municipal,
            official_records=official_records,
            tax_records=tax_records,
            missing_fields=missing,
        )
        scores = score_result.dimensions
        channels = tuple(
            name
            for name, present in (("mls", listing_row), ("off_market", off_market))
            if present
        )
        listing_active = str(listing.get("status") or "").casefold() in {
            "a",
            "active",
            "act",
        }
        serious_municipal = any(
            _is_serious_municipal_matter(item) for item in municipal
        )
        independent_motivation = scores.owner_motivation > 0
        in_scope = units is None or 10 <= units <= 80
        features = RecommendationFeatures(
            property_id=property_id,
            discovery_channels=channels,
            scores=scores,
            has_underwriting=underwriting.status == "complete",
            critical_documents_missing=bool(missing_fields),
            violation_review_required=False,
            municipal_search_required=False,
            why_now=tuple(
                [
                    f"{item.case_type}: {item.category} ({item.score:.0f}/100)."
                    for item in municipal
                ]
                or (
                    ["Current authorized Matrix listing."]
                    if listing_row
                    else ["Persisted county property record."]
                )
            ),
            key_risks=tuple(
                score_result.components["property_risk"]
                or ("Operating documents are unavailable.",)
            ),
            missing_data=missing,
            evidence=(),
            is_synthetic=False,
            identity_verified=identity_verified,
            specific_opportunity=bool(municipal or independent_motivation or listing_row),
            in_scope=in_scope,
            listing_active=listing_active,
            serious_municipal_matter=serious_municipal,
            municipal_case_present=bool(municipal),
            independent_motivation=independent_motivation,
            human_contact_approved=False,
        )
        result = recommend(features)
        qualification_score = acquisition_attractiveness(scores)
        municipal_urgency_score = municipal_review_urgency(municipal)
        ranking_tiebreakers = {
            "acquisition_attractiveness": qualification_score,
            "municipal_review_urgency": municipal_urgency_score,
            "economics": scores.economics,
            "market_pressure": scores.market_pressure,
            "owner_motivation": scores.owner_motivation,
            "data_confidence": scores.data_confidence,
            "data_completeness": scores.data_completeness,
            "maximum_municipal_severity": municipal_urgency_score,
            "oldest_unresolved_case_days": max(
                (
                    item.age_days
                    for item in municipal
                    if item.age_days is not None
                    and item.status_category != "closed_or_resolved"
                ),
                default=0,
            ),
            "serious_case_count": sum(item.score >= 50 for item in municipal),
        }
        material_municipal = (
            [
                value
                for evidence_id, value in historical_municipal_records
                if evidence_id in last_known_evidence_ids
            ]
            if _coverage_is_unknown(coverage, CODE_SOURCE)
            else municipal_values
        )
        material_official = (
            tuple(
                value
                for evidence_id, value in historical_official_records
                if evidence_id in last_known_evidence_ids
            )
            if _coverage_is_unknown(coverage, CLERK_SOURCE)
            else official_records
        )
        material_tax = (
            tuple(
                value
                for evidence_id, value in historical_tax_records
                if evidence_id in last_known_evidence_ids
            )
            if _coverage_is_unknown(coverage, TAX_SOURCE)
            else tax_records
        )
        stable_payload = {
            "property_id": property_id,
            "listing": listing,
            "county": county,
            "municipal": material_municipal,
            "official_records": material_official,
            "tax_records": material_tax,
        }
        content_hash = _material_content_hash(stable_payload)
        disposition_row = store.active_disposition(property_id)
        action = _apply_disposition_action(
            disposition=(
                str(disposition_row["disposition"]) if disposition_row else None
            ),
            baseline_content_hash=(
                str(disposition_row["baseline_content_hash"])
                if disposition_row
                else None
            ),
            current_content_hash=content_hash,
            default_action=result.action,
            listing_present=listing_row is not None,
            identity_verified=identity_verified,
            in_scope=in_scope,
            serious_municipal=serious_municipal,
            underwriting_complete=underwriting.status == "complete",
        )

        evidence_rows = store.connection.execute(
            """
            SELECT evidence_id,field_name FROM evidence_items
            WHERE property_id=? ORDER BY fetched_at,evidence_id
            """,
            (property_id,),
        ).fetchall()
        current_signal_fields = {
            "municipal_code_case",
            "official_record",
            "tax_delinquency",
        }
        evidence_ids = tuple(
            row["evidence_id"]
            for row in evidence_rows
            if row["field_name"] not in current_signal_fields
            or row["evidence_id"] in confirmed_evidence_ids
        )
        by_field = {
            field: tuple(
                row["evidence_id"]
                for row in evidence_rows
                if row["field_name"] == field
                and (
                    field not in current_signal_fields
                    or row["evidence_id"] in confirmed_evidence_ids
                )
            )
            for field in (
                "matrix_listing",
                "validated_address",
                "public_property_record",
                "municipal_code_case",
                "official_record",
                "tax_delinquency",
            )
        }
        identity_ids = (
            *by_field["validated_address"],
            *by_field["public_property_record"],
        )
        motivation_ids = (*by_field["official_record"], *by_field["tax_delinquency"])
        if action == "human_municipal_review":
            action_ids = by_field["municipal_code_case"]
        elif action == "verify_identity":
            action_ids = identity_ids
        elif action in {"contact_broker_for_documents", "request_documents"}:
            action_ids = (*by_field["matrix_listing"], *diligence_evidence_ids)
        elif action == "investigate_owner":
            action_ids = motivation_ids
        else:
            action_ids = identity_ids or evidence_ids
        offer = calculate_offer_range(OfferInputs())
        explanation = {
            **result.explanation,
            "What the next human action should be": (action,),
            "missing_data": missing,
            "evidence_ids": evidence_ids,
            "statement_evidence_ids": {
                "why_this_property_surfaced": (
                    by_field["municipal_code_case"]
                    or by_field["matrix_listing"]
                    or identity_ids
                ),
                "identity": identity_ids,
                "owner_motivation": motivation_ids,
                "property_risk": by_field["municipal_code_case"],
                "missing_source_data": tuple(source_gap_evidence_ids),
                "missing_diligence": diligence_evidence_ids,
                "recommended_action": action_ids,
            },
            "score_components": score_result.components,
            "preliminary_metrics": score_result.metrics,
            "qualification_score": qualification_score,
            "acquisition_attractiveness_score": qualification_score,
            "municipal_review_urgency_score": municipal_urgency_score,
            "ranking_tiebreakers": ranking_tiebreakers,
            "discovery_channels": channels,
            "identity_verified": identity_verified,
            "underwriting_status": underwriting.status,
            "underwriting_run_id": underwriting_run_id,
            "pilot_run_id": pilot_run_id,
            "municipal_cases": municipal_values,
            "offer_gate": {
                **offer.to_dict(),
                "display": "OFFER GATE: FAIL — INSUFFICIENT DATA",
            },
        }
        previous = store.connection.execute(
            """
            SELECT content_hash FROM recommendations
            WHERE property_id=? AND content_hash IS NOT NULL
            ORDER BY generated_at DESC,recommendation_id DESC LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        change_type = (
            "new"
            if previous is None
            else "unchanged"
            if previous["content_hash"] == content_hash
            else "materially_changed"
        )
        store.connection.execute(
            """
            INSERT INTO recommendations (
                recommendation_id,property_id,generated_at,action,
                scores_json,explanation_json,pilot_run_id,content_hash,change_type
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid4()),
                property_id,
                generated_at,
                action,
                _json(asdict(scores)),
                _json(explanation),
                pilot_run_id,
                content_hash,
                change_type,
            ),
        )
        if change_type != "unchanged":
            store.connection.execute(
                """
                INSERT INTO opportunity_alerts (
                    alert_id,pilot_run_id,property_id,alert_type,content_hash,created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    str(uuid4()),
                    pilot_run_id,
                    property_id,
                    change_type,
                    content_hash,
                    generated_at,
                ),
            )
        store.connection.commit()


def _database_counts(store: IntelligenceStore) -> dict[str, int]:
    tables = (
        "canonical_properties",
        "canonical_owners",
        "property_ownership",
        "listing_snapshots",
        "listing_changes",
        "source_runs",
        "source_records",
        "property_source_coverage",
        "evidence_items",
        "property_signals",
        "underwriting_runs",
        "recommendations",
        "opportunity_alerts",
        "human_dispositions",
    )
    return {
        table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }


_ACQUISITION_WEIGHTS = {
    "economics": 0.35,
    "market_pressure": 0.25,
    "owner_motivation": 0.25,
    "data_confidence": 0.10,
    "data_completeness": 0.05,
}
_HAZARD_PRIORITY = {
    "unsafe_life_safety": 6,
    "minimum_housing": 5,
    "recertification": 4,
    "permit": 3,
    "administrative": 2,
    "cosmetic": 1,
}


def _acquisition_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -float(item["acquisition_attractiveness_score"]),
        *(
            -float(item["scores"].get(component, 0))
            for component in _ACQUISITION_WEIGHTS
        ),
        str(item["property_id"]),
    )


def _municipal_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    cases = item.get("municipal_cases") or ()
    strongest_hazard = max(
        (
            _HAZARD_PRIORITY.get(str(case.get("substantive_hazard")), 0)
            for case in cases
        ),
        default=0,
    )
    return (
        -float(item["municipal_review_urgency_score"]),
        -strongest_hazard,
        -max((int(case.get("age_days") or 0) for case in cases), default=0),
        str(item["property_id"]),
    )


def _acquisition_rank_reason(
    item: dict[str, Any], next_item: dict[str, Any] | None
) -> str:
    if next_item is None:
        return "Final item in the capped acquisition-attractiveness ranking."
    contributions = {
        component: _ACQUISITION_WEIGHTS[component]
        * float(item["scores"].get(component, 0))
        for component in _ACQUISITION_WEIGHTS
    }
    next_contributions = {
        component: _ACQUISITION_WEIGHTS[component]
        * float(next_item["scores"].get(component, 0))
        for component in _ACQUISITION_WEIGHTS
    }
    deltas = {
        component: contributions[component] - next_contributions[component]
        for component in _ACQUISITION_WEIGHTS
    }
    positive = [(component, delta) for component, delta in deltas.items() if delta > 0]
    if positive:
        component, delta = max(positive, key=lambda pair: pair[1])
        return (
            f"{component} contributes {contributions[component]:.2f} weighted "
            f"points versus {next_contributions[component]:.2f}, a {delta:.2f}-point "
            "ordering advantage."
        )
    return "Weighted components tie; stable canonical property identity sets the order."


def _municipal_rank_reason(
    item: dict[str, Any], next_item: dict[str, Any] | None
) -> str:
    if next_item is None:
        return "Final item in the capped municipal-review urgency ranking."
    item_urgency = float(item["municipal_review_urgency_score"])
    next_urgency = float(next_item["municipal_review_urgency_score"])
    if item_urgency != next_urgency:
        return (
            f"Current municipal-review urgency is {item_urgency:.2f} versus "
            f"{next_urgency:.2f}; that component caused the ordering."
        )
    item_hazard = max(
        (
            _HAZARD_PRIORITY.get(str(case.get("substantive_hazard")), 0)
            for case in item["municipal_cases"]
        ),
        default=0,
    )
    next_hazard = max(
        (
            _HAZARD_PRIORITY.get(str(case.get("substantive_hazard")), 0)
            for case in next_item["municipal_cases"]
        ),
        default=0,
    )
    if item_hazard != next_hazard:
        return "Urgency ties; substantive-hazard priority caused the ordering."
    item_age = max(
        (int(case.get("age_days") or 0) for case in item["municipal_cases"]),
        default=0,
    )
    next_age = max(
        (int(case.get("age_days") or 0) for case in next_item["municipal_cases"]),
        default=0,
    )
    if item_age != next_age:
        return (
            f"Urgency and hazard tie; active-case age ({item_age} versus "
            f"{next_age} days) caused the ordering."
        )
    return "Municipal components tie; stable canonical property identity sets the order."


def _report_records(store: IntelligenceStore) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        """
        SELECT p.*,r.action,r.scores_json,r.explanation_json,r.generated_at,
               r.pilot_run_id,r.content_hash,r.change_type
        FROM canonical_properties p
        JOIN recommendations r ON r.recommendation_id=(
            SELECT r2.recommendation_id FROM recommendations r2
            WHERE r2.property_id=p.property_id
            ORDER BY r2.generated_at DESC,r2.recommendation_id DESC LIMIT 1
        )
        """
    ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        explanation = json.loads(row["explanation_json"])
        scores = json.loads(row["scores_json"])
        county_row = store.connection.execute(
            """
            SELECT value_json,source_url FROM evidence_items
            WHERE property_id=? AND field_name='public_property_record'
            ORDER BY fetched_at DESC,rowid DESC LIMIT 1
            """,
            (row["property_id"],),
        ).fetchone()
        county = json.loads(county_row["value_json"]) if county_row else {}
        listing_row = store.connection.execute(
            """
            SELECT mls_number,status,list_price,dom,cdom,normalized_json
            FROM listing_snapshots WHERE property_id=?
            ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1
            """,
            (row["property_id"],),
        ).fetchone()
        listing = json.loads(listing_row["normalized_json"]) if listing_row else {}
        municipal_cases = explanation.get("municipal_cases") or []
        source_links = sorted(
            {
                evidence_row["source_url"]
                for evidence_row in store.connection.execute(
                    """
                    SELECT source_url FROM evidence_items
                    WHERE property_id=? AND source_url IS NOT NULL
                    """,
                    (row["property_id"],),
                ).fetchall()
                if evidence_row["source_url"]
            }
        )
        records.append(
            {
                "property_id": row["property_id"],
                "folio": row["folio"],
                "address": row["address"],
                "municipality": row["municipality"],
                "jurisdiction": row["jurisdiction"],
                "discovery_channels": explanation["discovery_channels"],
                "recommended_action": row["action"],
                "scores": scores,
                "score_components": explanation.get("score_components") or {},
                "preliminary_metrics": explanation.get("preliminary_metrics") or {},
                "qualification_score": explanation.get("qualification_score", 0),
                "acquisition_attractiveness_score": explanation.get(
                    "acquisition_attractiveness_score",
                    explanation.get("qualification_score", 0),
                ),
                "municipal_review_urgency_score": explanation.get(
                    "municipal_review_urgency_score", 0
                ),
                "ranking_tiebreakers": explanation.get("ranking_tiebreakers") or {},
                "why_now": explanation["Why this property surfaced"],
                "motivation_evidence": explanation["Why the owner may be motivated"],
                "key_risks": explanation["What could destroy the deal"],
                "missing_data": explanation["missing_data"],
                "evidence_ids": explanation["evidence_ids"],
                "statement_evidence_ids": explanation["statement_evidence_ids"],
                "underwriting_status": explanation["underwriting_status"],
                "underwriting_run_id": explanation["underwriting_run_id"],
                "offer_gate": explanation["offer_gate"],
                "identity_verified": explanation["identity_verified"],
                "generated_at": row["generated_at"],
                "pilot_run_id": row["pilot_run_id"],
                "change_type": row["change_type"],
                "content_hash": row["content_hash"],
                "mls_number": listing_row["mls_number"] if listing_row else None,
                "listing_status": listing_row["status"] if listing_row else None,
                "asking_price": listing_row["list_price"] if listing_row else None,
                "dom": listing_row["dom"] if listing_row else None,
                "cdom": listing_row["cdom"] if listing_row else None,
                "remarks": listing.get("remarks"),
                "owner": county.get("owner"),
                "mailing_address": county.get("mailing_address"),
                "absentee_owner": county.get("absentee_owner"),
                "verified_units": county.get("verified_units", county.get("units")),
                "last_sale_date": county.get("last_sale_date"),
                "last_sale_price": county.get("last_sale_price"),
                "ownership_duration_years": county.get("ownership_duration_years"),
                "assessed_value": county.get("assessed_value"),
                "year_built": county.get("year_built"),
                "building_area": county.get("building_area"),
                "lot_size": county.get("lot_size"),
                "municipal_cases": municipal_cases,
                "source_links": source_links,
            }
        )
    return sorted(records, key=_acquisition_sort_key)


def _write_reports(store: IntelligenceStore, output_dir: Path, run_id: str) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run = dict(
        store.connection.execute(
            "SELECT * FROM pilot_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    )
    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(_json(run) + "\n", encoding="utf-8")

    ledger_path = output_dir / "import_ledger.csv"
    ledger_rows = store.connection.execute(
        """
        SELECT line_number,status,source_record_id,address,reason
        FROM import_ledger WHERE run_id=? ORDER BY line_number
        """,
        (run_id,),
    ).fetchall()
    with ledger_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("line_number", "status", "source_record_id", "address", "reason"),
        )
        writer.writeheader()
        writer.writerows(dict(row) for row in ledger_rows)

    coverage_path = output_dir / "source_coverage.json"
    coverage = [
        dict(row)
        for row in store.connection.execute(
            """
            SELECT * FROM property_source_coverage
            ORDER BY property_id,source_name
            """
        ).fetchall()
    ]
    coverage_path.write_text(json.dumps(coverage, indent=2) + "\n", encoding="utf-8")

    summary_path = output_dir / "database_summary.json"
    counts = _database_counts(store)
    summary_path.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")

    recommendations = _report_records(store)
    recommendations_json = output_dir / "recommendations.json"
    recommendations_json.write_text(
        json.dumps(recommendations, indent=2) + "\n", encoding="utf-8"
    )
    recommendations_csv = output_dir / "recommendations.csv"
    fields = (
        "property_id",
        "folio",
        "address",
        "municipality",
        "discovery_channels",
        "recommended_action",
        "missing_data",
        "evidence_ids",
        "underwriting_status",
        "identity_verified",
    )
    with recommendations_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in recommendations:
            writer.writerow(
                {
                    key: _json(record[key]) if isinstance(record[key], (list, tuple, dict)) else record[key]
                    for key in fields
                }
            )

    qualified_actions = {
        "contact_broker_for_documents",
        "human_municipal_review",
        "investigate_owner",
        "verify_identity",
        "request_documents",
        "watch",
    }
    eligible = [
        item
        for item in recommendations
        if item["recommended_action"] in qualified_actions
    ]
    queue = sorted(eligible, key=_acquisition_sort_key)[:10]
    for index, item in enumerate(queue, start=1):
        item["rank"] = index
        next_item = queue[index] if index < len(queue) else None
        item["ranked_above_next_reason"] = _acquisition_rank_reason(item, next_item)

    queue_path = output_dir / "qualified_queue.json"
    queue_path.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")
    acquisition_queue_path = output_dir / "acquisition_queue.json"
    acquisition_queue_path.write_text(
        json.dumps(queue, indent=2) + "\n", encoding="utf-8"
    )
    municipal_queue = sorted(
        (
            dict(item)
            for item in recommendations
            if float(item["municipal_review_urgency_score"]) > 0
            and item["municipal_cases"]
        ),
        key=_municipal_sort_key,
    )[:10]
    for index, item in enumerate(municipal_queue, start=1):
        item["municipal_rank"] = index
        item["municipal_rank_reason"] = _municipal_rank_reason(
            item,
            municipal_queue[index] if index < len(municipal_queue) else None,
        )
    municipal_queue_path = output_dir / "municipal_review_queue.json"
    municipal_queue_path.write_text(
        json.dumps(municipal_queue, indent=2) + "\n", encoding="utf-8"
    )
    mls_example = next(
        (dict(item) for item in recommendations if "mls" in item["discovery_channels"]),
        None,
    )
    mls_example_path = output_dir / "mls_example.json"
    mls_example_path.write_text(
        json.dumps(
            {
                "label": "MLS example; not forced into either numerical top ten",
                "record": mls_example,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    brief_path = output_dir / "acquisition_brief.md"
    brief_lines = [
        "# REAL-PILOT-02 Qualified Acquisition Queue",
        "",
        f"Pilot run: {run_id}",
        f"Acquisition-attractiveness analyst tasks: {len(queue)} (maximum 10)",
        "",
        "Municipal severity is property-risk evidence only. It does not independently create owner-motivation points.",
        "Acquisition attractiveness excludes municipal property risk, absentee ownership, and ownership duration.",
        "Municipal-review urgency is reported in a separate ranking.",
        "",
    ]
    if queue:
        top = queue[0]
        score_terms = [
            f"0.35×economics({top['scores']['economics']:.2f})",
            f"0.25×market_pressure({top['scores']['market_pressure']:.2f})",
            f"0.25×owner_motivation({top['scores']['owner_motivation']:.2f})",
            f"0.10×data_confidence({top['scores']['data_confidence']:.2f})",
            f"0.05×data_completeness({top['scores']['data_completeness']:.2f})",
        ]
        brief_lines.extend(
            [
                "## Exact scoring calculation for the top-ranked property",
                "",
                " + ".join(score_terms)
                + f" = **{top['qualification_score']:.2f}**",
                "",
            ]
        )
    brief_lines.extend(
        [
            "## Separate municipal-review urgency ranking",
            "",
            *(
                [
                    f"- {item['municipal_rank']}. {item['address']}: "
                    f"{item['municipal_review_urgency_score']:.2f}/100 — "
                    f"{item['municipal_rank_reason']}"
                    for item in municipal_queue
                ]
                or ["- No currently confirmed municipal matters."]
            ),
            "",
            "## MLS example (not forced into either top ten)",
            "",
            (
                f"- {mls_example['address']} / {mls_example['mls_number']}"
                if mls_example
                else "- No current MLS example."
            ),
            "",
        ]
    )
    for item in queue:
        asking = (
            f"${item['asking_price']:,.0f}"
            if item["asking_price"] is not None
            else "Not available"
        )
        ppu = item["preliminary_metrics"].get("price_per_unit")
        price_per_unit = f"${ppu:,.0f}" if ppu is not None else "Not available"
        municipal_lines = []
        for case_value in item["municipal_cases"]:
            age = (
                f"{case_value['age_days']} days"
                if case_value.get("age_days") is not None
                else "age unknown"
            )
            municipal_lines.append(
                f"{case_value.get('case_type') or 'Unknown type'} / "
                f"{case_value.get('status') or 'status unknown'} / {age} / "
                f"{case_value.get('severity_category')} "
                f"({case_value.get('severity_score')}/100)"
            )
        links = item["source_links"] or ["No human-readable source URL persisted"]
        component_lines = [
            f"{dimension}: {score:.2f} — "
            + "; ".join(item["score_components"].get(dimension) or ("No supported points.",))
            for dimension, score in item["scores"].items()
        ]
        brief_lines.extend(
            [
                f"## {item['rank']}. {item['address']}",
                "",
                f"- Folio: {item['folio'] or 'Unverified'}",
                f"- MLS: {item['mls_number'] or 'Off market'}",
                f"- Owner: {item['owner'] or 'Not available'}",
                f"- Verified units: {item['verified_units'] if item['verified_units'] is not None else 'Not available'}",
                f"- Asking price: {asking}",
                f"- Asking price per verified unit: {price_per_unit}",
                f"- Last sale: {_display_date(item['last_sale_date'])}"
                + (
                    f" for ${item['last_sale_price']:,.0f}"
                    if item["last_sale_price"] not in (None, 0)
                    else ""
                ),
                f"- Ownership duration: {item['ownership_duration_years'] if item['ownership_duration_years'] is not None else 'Not available'} years",
                "- Assessed value: "
                + (
                    f"${item['assessed_value']:,.0f}"
                    if item["assessed_value"] is not None
                    else "Not available"
                ),
                "- Municipal matters: "
                + ("; ".join(municipal_lines) if municipal_lines else "None matched"),
                "- Motivation evidence: "
                + "; ".join(item["score_components"].get("owner_motivation") or ("None independently supported.",)),
                "- Property-risk evidence: "
                + "; ".join(item["score_components"].get("property_risk") or ("No active municipal risk supported.",)),
                "- Exact missing diligence: " + ", ".join(item["missing_data"]),
                "- Score components:",
                *[f"  - {line}" for line in component_lines],
                f"- Exact next human action: **{item['recommended_action']}**",
                f"- Why ranked above the next property: {item['ranked_above_next_reason']}",
                "- Source links:",
                *[f"  - {link}" for link in links],
                "",
            ]
        )
    brief_path.write_text("\n".join(brief_lines).rstrip() + "\n", encoding="utf-8")

    groups = (
        ("Act now", {"contact_owner", "contact_broker"}),
        (
            "Request documents",
            {
                "contact_broker_for_documents",
                "request_documents",
                "insufficient_data",
            },
        ),
        ("Verify identity/source", {"verify_identity"}),
        (
            "Human municipal review",
            {
                "human_municipal_review",
                "human_violation_review",
                "order_municipal_search",
            },
        ),
        ("Investigate owner", {"investigate_owner"}),
        (
            "Watch/reject",
            {"watch", "dismiss", "reject", "reject_high_risk", "excluded"},
        ),
    )
    brief_lines = ["# Daily acquisition-intelligence brief", ""]
    actionable_count = sum(
        item["recommended_action"] in {"contact_owner", "contact_broker"}
        for item in recommendations
    )
    if not actionable_count:
        brief_lines.extend(
            ["No properties are ready for broker/owner contact today.", ""]
        )
    for title, actions in groups:
        brief_lines.append(f"## {title}")
        matching = [
            item for item in recommendations if item["recommended_action"] in actions
        ]
        brief_lines.extend(
            (
                f"- {item['address']} ({item['folio'] or 'folio unverified'}): "
                f"{item['recommended_action']}; evidence {', '.join(item['evidence_ids'])}"
            )
            for item in matching
        )
        if not matching:
            brief_lines.append("- None.")
        brief_lines.append("")
    brief_lines.append("## Source failures and stale data")
    warnings = [
        row
        for row in coverage
        if row["state"]
        in {
            CoverageState.UNKNOWN_FAILED.value,
            CoverageState.UNKNOWN_NOT_RUN.value,
            CoverageState.UNKNOWN_STALE.value,
        }
    ]
    brief_lines.extend(
        (
            f"- {row['property_id']} / {row['source_name']}: {row['state']}"
            + (f" — {row['error_message']}" if row["error_message"] else "")
        )
        for row in warnings
    )
    if not warnings:
        brief_lines.append("- None.")
    source_warnings_path = output_dir / "source_health_warnings.json"
    source_warnings_path.write_text(
        json.dumps(warnings, indent=2) + "\n", encoding="utf-8"
    )
    change_summary = {
        change_type: store.connection.execute(
            """
            SELECT COUNT(*) FROM recommendations
            WHERE pilot_run_id=? AND change_type=?
            """,
            (run_id, change_type),
        ).fetchone()[0]
        for change_type in ("new", "materially_changed", "unchanged")
    }
    changed_items = [
        dict(row)
        for row in store.connection.execute(
            """
            SELECT property_id,change_type,action,content_hash
            FROM recommendations
            WHERE pilot_run_id=? AND change_type IN ('new','materially_changed')
            ORDER BY change_type,property_id
            """,
            (run_id,),
        ).fetchall()
    ]
    resolved_items = [
        dict(row)
        for row in store.connection.execute(
            """
            SELECT s.property_id,s.signal_type,o.signal_id
            FROM signal_observations o
            JOIN property_signals s ON s.signal_id=o.signal_id
            WHERE o.pilot_run_id=? AND o.status='resolved'
            ORDER BY s.property_id,s.signal_type,o.signal_id
            """,
            (run_id,),
        ).fetchall()
    ]
    change_summary["resolved"] = len(resolved_items)
    change_summary["items"] = changed_items
    change_summary["resolved_items"] = resolved_items
    change_summary_path = output_dir / "change_summary.json"
    change_summary_path.write_text(
        json.dumps(change_summary, indent=2) + "\n", encoding="utf-8"
    )
    brief_lines.extend(
        [
            "",
            "## Opportunity changes",
            f"- New: {change_summary['new']}",
            f"- Materially changed: {change_summary['materially_changed']}",
            f"- Resolved: {change_summary['resolved']}",
            *[
                f"  - {item['change_type']}: {item['property_id']} / {item['action']}"
                for item in changed_items
            ],
            *[
                f"  - resolved: {item['property_id']} / {item['signal_type']}"
                for item in resolved_items
            ],
        ]
    )
    daily_brief_path = output_dir / "daily_brief.md"
    daily_brief_path.write_text(
        "\n".join(brief_lines).rstrip() + "\n", encoding="utf-8"
    )

    top = queue[0] if queue else {}
    trace: dict[str, Any] = {"recommendation": top}
    if top:
        property_id = top["property_id"]
        trace["evidence"] = [
            dict(row)
            for row in store.connection.execute(
                """
                SELECT evidence_id,field_name,value_json,source_name,source_record_id,
                       source_url,fetched_at,freshness_status,confidence,value_type,
                       metadata_json
                FROM evidence_items WHERE property_id=?
                ORDER BY fetched_at,evidence_id
                """,
                (property_id,),
            ).fetchall()
        ]
        trace["source_coverage"] = store.property_source_coverage(property_id)
        trace["listings"] = [
            dict(row)
            for row in store.connection.execute(
                """
                SELECT mls_number,source_name,fetched_at,status,list_price,
                       normalized_json
                FROM listing_snapshots WHERE property_id=? ORDER BY fetched_at
                """,
                (property_id,),
            ).fetchall()
        ]
    trace_json = output_dir / "top_candidate_trace.json"
    trace_json.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    trace_md = output_dir / "top_candidate_trace.md"
    trace_md.write_text(
        "# Top candidate trace\n\n"
        + (
            f"- Property: {top.get('address')}\n"
            f"- Folio: {top.get('folio')}\n"
            f"- Action: {top.get('recommended_action')}\n"
            f"- Evidence IDs: {', '.join(top.get('evidence_ids', []))}\n"
            f"- Underwriting: {top.get('underwriting_status')}\n"
            f"- Offer: {top.get('offer_gate', {}).get('display')}\n"
            if top
            else "No persisted candidate.\n"
        ),
        encoding="utf-8",
    )
    return (
        manifest_path,
        ledger_path,
        coverage_path,
        summary_path,
        recommendations_json,
        recommendations_csv,
        daily_brief_path,
        brief_path,
        queue_path,
        acquisition_queue_path,
        municipal_queue_path,
        mls_example_path,
        source_warnings_path,
        change_summary_path,
        trace_json,
        trace_md,
    )


def run_pilot(
    *,
    matrix_path: Path,
    database_path: Path,
    output_dir: Path,
    municipality: str,
    generated_at: str | None = None,
    property_collector: Any | None = None,
    code_collector: Any | None = None,
    simulate_source_failure: bool = False,
) -> PilotRunResult:
    if not matrix_path.is_file():
        raise FileNotFoundError(matrix_path)
    generated_at = generated_at or utc_now()
    matrix_sha = _sha256(matrix_path)
    config = load_city_config("hialeah_fl")
    property_collector = property_collector or ArcGisPropertyCollector(config)
    code_collector = code_collector or TylerEnerGovCollector(config)
    importer = MatrixCsvImporter()
    with matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        batch = importer.import_reader(
            handle,
            fetched_at=generated_at,
            source_url=f"manual-import://{matrix_path.name}",
        )

    run_id = str(uuid4())
    with IntelligenceStore(database_path) as store:
        _migrate_pilot(store)
        store.connection.execute(
            """
            INSERT INTO pilot_runs (
                run_id,input_filename,input_sha256,municipality,started_at,status
            ) VALUES (?,?,?,?,?,'running')
            """,
            (run_id, matrix_path.name, matrix_sha, municipality, generated_at),
        )
        store.connection.executemany(
            """
            INSERT INTO import_ledger (
                run_id,line_number,status,source_record_id,address,reason
            ) VALUES (?,?,?,?,?,?)
            """,
            [
                (
                    run_id,
                    row.line_number,
                    row.status,
                    row.source_record_id,
                    row.address,
                    row.reason,
                )
                for row in batch.ledger
            ],
        )
        store.connection.commit()

        matrix_run = store.start_source_run("matrix_csv")
        _link_source_run(store, matrix_run, run_id)
        for listing in batch.accepted:
            payload = _json(listing.raw_payload)
            store.connection.execute(
                """
                INSERT OR IGNORE INTO source_records (
                    source_name,source_record_id,run_id,source_url,fetched_at,
                    raw_payload_json,normalized_payload_json,payload_hash
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    "matrix_csv",
                    listing.source_record_id,
                    matrix_run,
                    listing.source_url,
                    listing.fetched_at,
                    payload,
                    _json(listing.stable_dict()),
                    hashlib.sha256(payload.encode()).hexdigest(),
                ),
            )
        store.connection.commit()
        county_run = store.start_source_run(COUNTY_SOURCE)
        _link_source_run(store, county_run, run_id)
        county_failures = 0
        matrix_changes = 0
        for listing in batch.accepted:
            _, _, lookup_failed, listing_change_count = _record_for_matrix(
                store=store,
                listing=listing,
                county_run_id=county_run,
                collector=property_collector,
                generated_at=generated_at,
            )
            county_failures += int(lookup_failed)
            matrix_changes += listing_change_count
        store.finish_source_run(
            run_id=matrix_run,
            state=SourceHealthState.HEALTHY,
            records_examined=len(batch.ledger),
            records_changed=matrix_changes,
        )

        code_run = store.start_source_run(CODE_SOURCE)
        _link_source_run(store, code_run, run_id)
        cases: tuple[CodeCase, ...] = ()
        inventory: tuple[PropertyRecord, ...] = ()
        code_error: str | None = None
        inventory_error: str | None = None
        if simulate_source_failure:
            code_error = "simulated source failure"
        else:
            try:
                code_result = code_collector.collect(
                    tuple(config.source.active_statuses),
                    fetch_details=False,
                    include_violations=False,
                )
                cases = code_result.records
                for document in code_result.raw_documents:
                    _save_raw_document(store, code_run, CODE_SOURCE, document)
            except Exception as exc:
                code_error = str(exc)
            try:
                inventory_result = property_collector.collect()
                inventory = inventory_result.records
                for document in inventory_result.raw_documents:
                    _save_raw_document(store, county_run, COUNTY_SOURCE, document)
            except Exception as exc:
                inventory_error = str(exc)
                county_failures += 1

        enriched_case_count = 0
        target_folios = {
            normalize_folio(record.folio)
            for record in inventory
            if normalize_folio(record.folio)
        }
        intersecting_cases = tuple(
            case
            for case in cases
            if normalize_folio(case.parcel_number) in target_folios
        )
        enrich_records = getattr(code_collector, "enrich_records", None)
        if not code_error and intersecting_cases and callable(enrich_records):
            try:
                enrichment_result = enrich_records(
                    intersecting_cases, include_violations=True
                )
                enriched_by_id = {
                    case.source_record_id: case for case in enrichment_result.records
                }
                cases = tuple(
                    enriched_by_id.get(case.source_record_id, case) for case in cases
                )
                enriched_case_count = len(enriched_by_id)
                for document in enrichment_result.raw_documents:
                    _save_raw_document(store, code_run, CODE_SOURCE, document)
            except Exception as exc:
                code_error = f"targeted case enrichment failed: {exc}"

        cases_by_folio: dict[str, list[CodeCase]] = {}
        for case in cases:
            folio = normalize_folio(case.parcel_number)
            if folio:
                cases_by_folio.setdefault(folio, []).append(case)
        _persist_off_market(
            store,
            records=inventory,
            cases_by_folio=cases_by_folio,
            county_run_id=county_run,
            code_run_id=code_run,
            pilot_run_id=run_id,
            generated_at=generated_at,
            source_healthy=code_error is None and inventory_error is None,
        )

        store.finish_source_run(
            run_id=county_run,
            state=(
                SourceHealthState.DEGRADED
                if inventory_error or county_failures
                else SourceHealthState.HEALTHY
            ),
            records_examined=len(inventory) + len(batch.accepted),
            records_changed=0,
            error_message=inventory_error
            or (
                f"{county_failures} Matrix county lookup(s) failed"
                if county_failures
                else None
            ),
        )
        store.finish_source_run(
            run_id=code_run,
            state=SourceHealthState.DEGRADED if code_error else SourceHealthState.HEALTHY,
            records_examined=len(cases),
            records_changed=enriched_case_count,
            error_message=code_error,
        )

        clerk_run = store.start_source_run(CLERK_SOURCE)
        _link_source_run(store, clerk_run, run_id)
        clerk_by_folio: dict[str, list[Any]] = {}
        clerk_error: str | None = None
        clerk_configured = bool(os.environ.get("MIAMI_DADE_CLERK_AUTH_KEY"))
        if clerk_configured:
            try:
                folios = [
                    prop.folio
                    for prop in _canonical_properties(store)
                    if prop.folio
                ]
                clerk_result = MiamiDadeClerkCollector(config).collect(folios)
                for document in clerk_result.raw_documents:
                    _save_raw_document(store, clerk_run, CLERK_SOURCE, document)
                for record in clerk_result.records:
                    clerk_by_folio.setdefault(
                        normalize_folio(record.folio) or "", []
                    ).append(record)
                store.finish_source_run(
                    run_id=clerk_run,
                    state=SourceHealthState.HEALTHY,
                    records_examined=len(folios),
                    records_changed=0,
                )
            except Exception as exc:
                clerk_error = str(exc)
                store.finish_source_run(
                    run_id=clerk_run,
                    state=SourceHealthState.DEGRADED,
                    records_examined=0,
                    records_changed=0,
                    error_message=clerk_error,
                )
        else:
            store.finish_source_run(
                run_id=clerk_run,
                state=SourceHealthState.DISABLED,
                records_examined=0,
                records_changed=0,
                error_message="MIAMI_DADE_CLERK_AUTH_KEY not configured",
            )

        tax_run = store.start_source_run(TAX_SOURCE)
        _link_source_run(store, tax_run, run_id)
        tax_path_value = os.environ.get("RADAR_TAX_CSV")
        tax_by_folio: dict[str, list[Any]] = {}
        tax_error: str | None = None
        if tax_path_value:
            try:
                tax_records = import_tax_csv(config.slug, Path(tax_path_value))
                for record in tax_records:
                    tax_by_folio.setdefault(
                        normalize_folio(record.folio) or "", []
                    ).append(record)
                store.finish_source_run(
                    run_id=tax_run,
                    state=SourceHealthState.HEALTHY,
                    records_examined=len(tax_records),
                    records_changed=0,
                )
            except Exception as exc:
                tax_error = str(exc)
                store.finish_source_run(
                    run_id=tax_run,
                    state=SourceHealthState.DEGRADED,
                    records_examined=0,
                    records_changed=0,
                    error_message=tax_error,
                )
        else:
            store.finish_source_run(
                run_id=tax_run,
                state=SourceHealthState.DISABLED,
                records_examined=0,
                records_changed=0,
                error_message="RADAR_TAX_CSV not configured",
            )

        seen_clerk_signal_ids: set[str] = set()
        seen_tax_signal_ids: set[str] = set()
        for prop in _canonical_properties(store):
            is_hialeah = prop.municipality.casefold() == "hialeah"
            matched = cases_by_folio.get(normalize_folio(prop.folio) or "", [])
            state = (
                CoverageState.NOT_APPLICABLE
                if not is_hialeah
                else CoverageState.UNKNOWN_FAILED
                if code_error
                else CoverageState.CONFIRMED_PRESENT
                if matched
                else CoverageState.CONFIRMED_ABSENT
            )
            store.set_source_coverage(
                property_id=prop.property_id,
                source_name=CODE_SOURCE,
                state=state,
                query_scope=f"folio:{prop.folio or 'unverified'}",
                records_examined=len(cases),
                records_matched=len(matched),
                run_id=code_run,
                error_message=code_error if state == CoverageState.UNKNOWN_FAILED else None,
                checked_at=generated_at,
            )
            official_records = clerk_by_folio.get(
                normalize_folio(prop.folio) or "", []
            )
            clerk_state = (
                CoverageState.UNKNOWN_NOT_RUN
                if not clerk_configured
                else CoverageState.UNKNOWN_FAILED
                if clerk_error
                else CoverageState.NOT_APPLICABLE
                if not prop.folio
                else CoverageState.CONFIRMED_PRESENT
                if official_records
                else CoverageState.CONFIRMED_ABSENT
            )
            active_official_records = _active_clerk_records(tuple(official_records))
            active_official_ids = {
                record.source_record_id for record in active_official_records
            }
            for official_record in official_records:
                evidence_id = store.add_evidence(
                    prop.property_id,
                    EvidenceItem(
                        field="official_record",
                        value={
                            "instrument_id": official_record.instrument_id,
                            "document_type": official_record.document_type,
                            "signal_type": official_record.signal_type,
                            "recorded_date": official_record.recorded_date,
                        },
                        source=CLERK_SOURCE,
                        source_record_id=official_record.source_record_id,
                        source_url=official_record.source_url,
                        fetched_at=official_record.fetched_at,
                        freshness_status=FreshnessStatus.FRESH,
                        confidence=1.0,
                        value_type=ValueType.REPORTED,
                    ),
                )
                if official_record.source_record_id in active_official_ids:
                    signal_type = (
                        "recorded_liens"
                        if official_record.signal_type in {"lien", "recorded_liens"}
                        else official_record.signal_type
                    )
                    seen_clerk_signal_ids.add(
                        _save_signal(
                            store,
                            property_id=prop.property_id,
                            signal_type=signal_type,
                            source_name=CLERK_SOURCE,
                            observed_at=generated_at,
                            value={"instrument_id": official_record.instrument_id},
                            evidence_ids=(evidence_id,),
                            pilot_run_id=run_id,
                        )
                    )
            store.set_source_coverage(
                property_id=prop.property_id,
                source_name=CLERK_SOURCE,
                state=clerk_state,
                query_scope=f"folio:{prop.folio or 'unverified'}",
                records_examined=1 if clerk_configured and prop.folio else 0,
                records_matched=len(official_records),
                run_id=clerk_run,
                error_message=clerk_error
                or (
                    "MIAMI_DADE_CLERK_AUTH_KEY not configured"
                    if not clerk_configured
                    else None
                ),
                checked_at=generated_at,
            )
            tax_records_for_property = tax_by_folio.get(
                normalize_folio(prop.folio) or "", []
            )
            tax_state = (
                CoverageState.UNKNOWN_NOT_RUN
                if not tax_path_value
                else CoverageState.UNKNOWN_FAILED
                if tax_error
                else CoverageState.NOT_APPLICABLE
                if not prop.folio
                else CoverageState.CONFIRMED_PRESENT
                if tax_records_for_property
                else CoverageState.CONFIRMED_ABSENT
            )
            for tax_record in tax_records_for_property:
                evidence_id = store.add_evidence(
                    prop.property_id,
                    EvidenceItem(
                        field="tax_delinquency",
                        value={
                            "tax_year": tax_record.tax_year,
                            "amount_due": tax_record.amount_due,
                            "status": tax_record.status,
                        },
                        source=TAX_SOURCE,
                        source_record_id=tax_record.source_record_id,
                        source_url=tax_record.source_url,
                        fetched_at=tax_record.fetched_at,
                        freshness_status=FreshnessStatus.FRESH,
                        confidence=1.0,
                        value_type=ValueType.REPORTED,
                    ),
                )
                if tax_record.amount_due > 0 and is_unpaid_status(tax_record.status):
                    seen_tax_signal_ids.add(
                        _save_signal(
                            store,
                            property_id=prop.property_id,
                            signal_type="tax_delinquency",
                            source_name=TAX_SOURCE,
                            observed_at=generated_at,
                            value={
                                "tax_year": tax_record.tax_year,
                                "certificate": tax_record.certificate_number,
                            },
                            evidence_ids=(evidence_id,),
                            pilot_run_id=run_id,
                        )
                    )
            store.set_source_coverage(
                property_id=prop.property_id,
                source_name=TAX_SOURCE,
                state=tax_state,
                query_scope=f"folio:{prop.folio or 'unverified'}",
                records_examined=len(tax_by_folio),
                records_matched=len(tax_records_for_property),
                run_id=tax_run,
                error_message=tax_error
                or (
                    "RADAR_TAX_CSV not configured"
                    if not tax_path_value
                    else None
                ),
                checked_at=generated_at,
            )
        _reconcile_source_signals(
            store,
            source_name=CLERK_SOURCE,
            seen_signal_ids=seen_clerk_signal_ids,
            healthy=clerk_configured and clerk_error is None,
            pilot_run_id=run_id,
            observed_at=generated_at,
        )
        _reconcile_source_signals(
            store,
            source_name=TAX_SOURCE,
            seen_signal_ids=seen_tax_signal_ids,
            healthy=bool(tax_path_value) and tax_error is None,
            pilot_run_id=run_id,
            observed_at=generated_at,
        )
        _persist_underwriting_and_recommendations(
            store, generated_at=generated_at, pilot_run_id=run_id
        )
        store.connection.execute(
            """
            UPDATE pilot_runs SET completed_at=?,status='completed' WHERE run_id=?
            """,
            (utc_now(), run_id),
        )
        store.connection.commit()
        output_files = _write_reports(store, output_dir, run_id)
        counts = _database_counts(store)

    return PilotRunResult(
        run_id=run_id,
        matrix_sha256=matrix_sha,
        accepted_rows=len(batch.accepted),
        rejected_rows=len(batch.ledger) - len(batch.accepted),
        output_files=output_files,
        database_counts=counts,
    )
