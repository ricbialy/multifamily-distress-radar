from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from distress_radar.models import PropertyContact
from distress_radar.normalize import normalize_parcel


ALIASES = {
    "folio": ("folio", "folio number", "parcel", "parcel number"),
    "name": ("contact name", "name", "owner", "owner name"),
    "role": ("role", "title", "contact type"),
    "email": ("email", "email address"),
    "phone": ("phone", "phone number", "telephone"),
    "address": ("mailing address", "address"),
    "verification": ("verification status", "verified", "status"),
    "confidence": ("confidence", "confidence score"),
    "source_url": ("source url", "source", "url"),
}


def _column(headers: list[str], logical: str) -> str | None:
    lookup = {header.strip().casefold(): header for header in headers}
    return next((lookup[item] for item in ALIASES[logical] if item in lookup), None)


def import_contacts_csv(city_slug: str, path: Path) -> tuple[PropertyContact, ...]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        columns = {name: _column(headers, name) for name in ALIASES}
        missing = [name for name in ("folio", "name") if not columns[name]]
        if missing:
            raise ValueError(f"Contact CSV is missing required fields: {', '.join(missing)}")
        records: list[PropertyContact] = []
        for line, row in enumerate(reader, start=2):
            folio = normalize_parcel(row.get(columns["folio"] or ""))
            name = str(row.get(columns["name"] or "") or "").strip()
            if not folio or not name:
                continue
            confidence_raw = str(row.get(columns["confidence"] or "") or "").strip()
            try:
                confidence = float(confidence_raw) if confidence_raw else 0.5
            except ValueError as exc:
                raise ValueError(f"Invalid confidence on CSV line {line}") from exc
            if not 0 <= confidence <= 1:
                raise ValueError(f"Confidence must be between 0 and 1 on CSV line {line}")
            email = str(row.get(columns["email"] or "") or "").strip().casefold() or None
            phone = str(row.get(columns["phone"] or "") or "").strip() or None
            identity = f"{folio}|{name.casefold()}|{email or ''}|{phone or ''}"
            records.append(PropertyContact(
                city_slug, "authorized_contact_csv", hashlib.sha256(identity.encode()).hexdigest()[:24],
                folio, name, str(row.get(columns["role"] or "") or "").strip() or None,
                email, phone, str(row.get(columns["address"] or "") or "").strip() or None,
                str(row.get(columns["verification"] or "") or "unverified").strip().casefold(),
                confidence, str(row.get(columns["source_url"] or "") or f"manual-import://{path.name}").strip(),
                fetched_at,
            ))
    return tuple(records)
