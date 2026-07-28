from __future__ import annotations

import csv
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from distress_radar.domain.listing import ListingChange, ListingSnapshot
from distress_radar.identity.address_normalizer import normalize_address
from distress_radar.identity.folio_resolver import normalize_folio


class MatrixSchemaChangedError(ValueError):
    pass


_ALIASES = {
    "mls_number": ("mls number", "mls # link", "mls#", "mls", "listing id"),
    "address": ("property address", "address", "street address"),
    "municipality": ("city", "municipality"),
    "state": ("state", "state code"),
    "postal_code": ("zip code", "zip", "postal code"),
    "folio": ("folio number", "folio", "apn", "parcel number"),
    "property_class": (
        "type of property",
        "prop type/type of building",
        "property type",
        "class",
        "property class",
    ),
    "status": ("status", "listing status", "st"),
    "list_price": ("list price", "price", "current price"),
    "dom": ("dom", "days on market"),
    "cdom": ("cdom", "cumulative days on market"),
    "units": ("units", "unit count", "total units"),
    "noi": ("noi", "net operating income"),
    "rents": ("annual rents", "gross rents", "rents"),
    "expenses": ("annual expenses", "expenses", "operating expenses"),
    "remarks": ("public remarks", "remarks", "broker remarks"),
}

_MOTIVATION_PHRASES = (
    "motivated",
    "bring all offers",
    "price reduced",
    "estate sale",
    "as-is",
    "needs tlc",
    "owner financing",
    "must sell",
)


@dataclass(frozen=True)
class MatrixImportLedgerRow:
    line_number: int
    status: str
    source_record_id: str | None
    address: str | None
    reason: str | None = None


@dataclass(frozen=True)
class MatrixImportBatch:
    headers: tuple[str, ...]
    header_mapping: dict[str, str | None]
    accepted: tuple[ListingSnapshot, ...]
    ledger: tuple[MatrixImportLedgerRow, ...]


def _columns(headers: list[str]) -> dict[str, str | None]:
    lookup = {header.strip().casefold(): header for header in headers}
    return {
        logical: next((lookup[alias] for alias in aliases if alias in lookup), None)
        for logical, aliases in _ALIASES.items()
    }


def _text(row: dict[str, str], column: str | None) -> str | None:
    value = str(row.get(column or "", "") or "").strip()
    return value or None


def _number(row: dict[str, str], column: str | None) -> float | None:
    value = _text(row, column)
    if value is None:
        return None
    cleaned = value.replace("$", "").replace(",", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    parsed = float(cleaned)
    return -parsed if negative else parsed


def _integer(row: dict[str, str], column: str | None) -> int | None:
    value = _number(row, column)
    return int(value) if value is not None else None


class MatrixCsvImporter:
    source_name = "matrix_csv"

    def import_file(self, path: Path, *, fetched_at: str) -> tuple[ListingSnapshot, ...]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return self.import_reader(
                handle, fetched_at=fetched_at, source_url=f"manual-import://{path.name}"
            ).accepted

    def import_bytes(
        self, payload: bytes, *, filename: str, fetched_at: str
    ) -> tuple[ListingSnapshot, ...]:
        from io import StringIO

        handle = StringIO(payload.decode("utf-8-sig"))
        return self.import_reader(
            handle,
            fetched_at=fetched_at,
            source_url=f"email-attachment://{filename}",
        ).accepted

    def import_reader(
        self, handle: TextIO, *, fetched_at: str, source_url: str
    ) -> MatrixImportBatch:
        reader = csv.DictReader(handle)
        accepted, ledger, headers, columns = self._read(reader, fetched_at, source_url)
        return MatrixImportBatch(
            headers=tuple(headers),
            header_mapping=columns,
            accepted=accepted,
            ledger=ledger,
        )

    def _read(
        self, reader: csv.DictReader[str], fetched_at: str, source_url: str
    ) -> tuple[
        tuple[ListingSnapshot, ...],
        tuple[MatrixImportLedgerRow, ...],
        list[str],
        dict[str, str | None],
    ]:
        headers = reader.fieldnames or []
        columns = _columns(headers)
        missing = [
            label
            for logical, label in (("mls_number", "MLS number"), ("address", "address"))
            if columns[logical] is None
        ]
        if missing:
            raise MatrixSchemaChangedError(
                f"Matrix CSV missing required columns: {', '.join(missing)}. "
                f"Headers: {', '.join(headers)}"
            )
        records: list[ListingSnapshot] = []
        ledger: list[MatrixImportLedgerRow] = []
        for line, row in enumerate(reader, start=2):
            mls_number = _text(row, columns["mls_number"])
            address = _text(row, columns["address"])
            if not mls_number or not address:
                missing_fields = [
                    label
                    for value, label in ((mls_number, "MLS number"), (address, "address"))
                    if not value
                ]
                ledger.append(
                    MatrixImportLedgerRow(
                        line_number=line,
                        status="rejected",
                        source_record_id=mls_number,
                        address=address,
                        reason=f"blank {', '.join(missing_fields)}",
                    )
                )
                continue
            try:
                records.append(
                    ListingSnapshot(
                        source_record_id=mls_number,
                        source_name=self.source_name,
                        fetched_at=fetched_at,
                        address=address,
                        municipality=_text(row, columns["municipality"]) or "",
                        folio=normalize_folio(_text(row, columns["folio"])),
                        property_class=_text(row, columns["property_class"]),
                        status=_text(row, columns["status"]),
                        list_price=_number(row, columns["list_price"]),
                        dom=_integer(row, columns["dom"]),
                        cdom=_integer(row, columns["cdom"]),
                        units=_integer(row, columns["units"]),
                        noi=_number(row, columns["noi"]),
                        rents=_number(row, columns["rents"]),
                        expenses=_number(row, columns["expenses"]),
                        remarks=_text(row, columns["remarks"]),
                        source_url=source_url,
                        state=_text(row, columns["state"]),
                        postal_code=_text(row, columns["postal_code"]),
                        raw_payload={
                            str(key): value
                            for key, value in row.items()
                            if key is not None
                        },
                    )
                )
            except (TypeError, ValueError) as exc:
                ledger.append(
                    MatrixImportLedgerRow(
                        line_number=line,
                        status="rejected",
                        source_record_id=mls_number,
                        address=address,
                        reason=f"invalid numeric value: {exc}",
                    )
                )
                continue
            ledger.append(
                MatrixImportLedgerRow(
                    line_number=line,
                    status="accepted",
                    source_record_id=mls_number,
                    address=address,
                )
            )
        return tuple(records), tuple(ledger), headers, columns


def _changed(
    current: ListingSnapshot, change_type: str, before: Any, after: Any
) -> ListingChange:
    return ListingChange(
        source_record_id=current.source_record_id,
        change_type=change_type,
        detected_at=current.fetched_at,
        before=before,
        after=after,
    )


def detect_listing_changes(
    previous: ListingSnapshot | None, current: ListingSnapshot
) -> tuple[ListingChange, ...]:
    if previous is None:
        return (_changed(current, "new_listing", None, current.stable_dict()),)

    changes: list[ListingChange] = []
    scalar_changes = (
        ("list_price", "price_change"),
        ("status", "status_change"),
        ("units", "unit_count_change"),
        ("noi", "noi_change"),
        ("rents", "rents_change"),
        ("expenses", "expenses_change"),
    )
    for attribute, change_type in scalar_changes:
        before = getattr(previous, attribute)
        after = getattr(current, attribute)
        if before != after:
            changes.append(_changed(current, change_type, before, after))

    previous_status = (previous.status or "").casefold()
    current_status = (current.status or "").casefold()
    contract_statuses = {"pending", "contingent", "under contract", "active under contract"}
    if current_status == "active" and previous_status in contract_statuses:
        changes.append(_changed(current, "back_on_market", previous.status, current.status))
        changes.append(_changed(current, "contract_failure", previous.status, current.status))
    if current_status == "withdrawn" and previous_status != "withdrawn":
        changes.append(_changed(current, "withdrawal", previous.status, current.status))
    if current_status == "expired" and previous_status != "expired":
        changes.append(_changed(current, "expiration", previous.status, current.status))
    if current.dom is not None and (previous.dom is None or current.dom > previous.dom):
        changes.append(_changed(current, "dom_increase", previous.dom, current.dom))
    if current.cdom is not None and (previous.cdom is None or current.cdom > previous.cdom):
        changes.append(_changed(current, "cdom_increase", previous.cdom, current.cdom))

    old_remarks = (previous.remarks or "").casefold()
    new_remarks = (current.remarks or "").casefold()
    added_phrases = [
        phrase for phrase in _MOTIVATION_PHRASES if phrase in new_remarks and phrase not in old_remarks
    ]
    if added_phrases:
        changes.append(_changed(current, "motivated_seller_language", (), tuple(added_phrases)))
    return tuple(changes)


def deduplicate_candidates(
    listings: Iterable[ListingSnapshot],
) -> tuple[tuple[ListingSnapshot, ...], ...]:
    groups: OrderedDict[str, list[ListingSnapshot]] = OrderedDict()
    for listing in listings:
        key = (
            f"folio:{listing.folio}"
            if listing.folio
            else f"address:{normalize_address(listing.address)}|{listing.municipality.casefold()}"
        )
        groups.setdefault(key, []).append(listing)
    return tuple(tuple(group) for group in groups.values())
