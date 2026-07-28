from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from distress_radar.models import TaxDelinquency
from distress_radar.normalize import normalize_parcel

ALIASES = {
    "folio": ("folio", "folio number", "folio_number", "parcel", "parcel number", "account"),
    "tax_year": ("tax year", "tax_year", "year", "roll year"),
    "amount_due": ("amount due", "amount_due", "balance", "balance due", "total due", "delinquent amount"),
    "status": ("status", "payment status", "tax status"),
    "certificate": ("certificate", "certificate number", "certificate_number", "tax certificate"),
    "owner": ("owner", "owner name", "owner_name", "taxpayer"),
    "address": ("property address", "property_address", "site address", "address"),
}


def _field(headers: list[str], logical: str) -> str | None:
    lookup = {header.strip().casefold(): header for header in headers}
    return next((lookup[name] for name in ALIASES[logical] if name in lookup), None)


def _money(value: object) -> float:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return 0.0
    return float(text.strip("()")) * (-1 if text.startswith("(") else 1)


def is_unpaid_status(value: object) -> bool:
    status = str(value or "").casefold().strip()
    if not status:
        return True
    if re.search(
        r"\b(?:not|no)\s+(?:currently\s+)?"
        r"(?:delinquent|outstanding(?:\s+balance)?|past\s+due|open)\b",
        status,
    ):
        return False
    if re.search(r"\bnot\s+paid\b", status):
        return True
    if re.search(
        r"\b(?:paid|satisfied|released|redeemed|cancelled|canceled|closed)\b",
        status,
    ):
        return False
    return any(
        marker in status
        for marker in ("unpaid", "delinquent", "outstanding", "past due", "open")
    )


def import_tax_csv(city_slug: str, path: Path) -> tuple[TaxDelinquency, ...]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        columns = {name: _field(headers, name) for name in ALIASES}
        missing = [name for name in ("folio", "tax_year", "amount_due") if not columns[name]]
        if missing:
            raise ValueError(f"Tax CSV is missing required fields: {', '.join(missing)}. Headers: {', '.join(headers)}")
        records: list[TaxDelinquency] = []
        for line, row in enumerate(reader, start=2):
            folio = normalize_parcel(row.get(columns["folio"] or ""))
            if not folio:
                continue
            try:
                year = int(str(row.get(columns["tax_year"] or "") or "").strip())
                amount = _money(row.get(columns["amount_due"] or ""))
            except ValueError as exc:
                raise ValueError(f"Invalid tax year or amount on CSV line {line}") from exc
            certificate = str(row.get(columns["certificate"] or "") or "").strip() or None
            identity = f"{folio}|{year}|{certificate or ''}"
            record_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
            records.append(TaxDelinquency(
                city_slug, "miami_dade_tax_csv", record_id, folio, year, amount,
                str(row.get(columns["status"] or "") or "").strip() or None,
                certificate,
                str(row.get(columns["owner"] or "") or "").strip() or None,
                str(row.get(columns["address"] or "") or "").strip() or None,
                f"manual-import://{path.name}", fetched_at,
            ))
    return tuple(records)
