from __future__ import annotations

import csv
from pathlib import Path

from distress_radar.identity.folio_resolver import normalize_folio
from distress_radar.models import PropertyRecord

_AUTHORITATIVE_SOURCE = "miami_dade_property_point_view"


def _text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _integer(value: str | None) -> int | None:
    cleaned = _text(value)
    return int(float(cleaned)) if cleaned else None


def _number(value: str | None) -> float | None:
    cleaned = _text(value)
    return float(cleaned) if cleaned else None


class PropertyRecordCsvImporter:
    """Read an ``export-properties`` CSV for authoritative folio validation."""

    def import_file(self, path: Path) -> tuple[PropertyRecord, ...]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {
                "city_slug",
                "source_name",
                "folio",
                "address",
                "city",
                "zip_code",
                "source_url",
                "last_seen_at",
            }
            missing = required.difference(reader.fieldnames or ())
            if missing:
                raise ValueError(
                    "County property CSV missing columns: "
                    + ", ".join(sorted(missing))
                )
            return tuple(self._record(row) for row in reader)

    def _record(self, row: dict[str, str]) -> PropertyRecord:
        source_name = (row.get("source_name") or "").strip()
        if source_name != _AUTHORITATIVE_SOURCE:
            raise ValueError(
                "Address validation requires an authoritative "
                f"{_AUTHORITATIVE_SOURCE} export"
            )
        folio = normalize_folio(row.get("folio"))
        if not folio:
            raise ValueError("County property row is missing a folio")
        return PropertyRecord(
            city_slug=(row.get("city_slug") or "").strip(),
            source_name=source_name,
            folio=folio,
            address=_text(row.get("address")),
            city=_text(row.get("city")),
            zip_code=_text(row.get("zip_code")),
            owner_name=_text(row.get("owner_name")),
            owner_name_2=_text(row.get("owner_name_2")),
            mailing_address_1=_text(row.get("mailing_address_1")),
            mailing_address_2=_text(row.get("mailing_address_2")),
            mailing_city=_text(row.get("mailing_city")),
            mailing_state=_text(row.get("mailing_state")),
            mailing_zip=_text(row.get("mailing_zip")),
            dor_code=_text(row.get("dor_code")),
            dor_description=_text(row.get("dor_description")),
            unit_count=_integer(row.get("unit_count")),
            year_built=_integer(row.get("year_built")),
            floor_count=_integer(row.get("floor_count")),
            building_area=_number(row.get("building_area")),
            lot_size=_number(row.get("lot_size")),
            assessed_value=_number(row.get("assessed_value")),
            last_sale_date=_text(row.get("last_sale_date")),
            last_sale_price=_number(row.get("last_sale_price")),
            latitude=_number(row.get("latitude")),
            longitude=_number(row.get("longitude")),
            source_url=(row.get("source_url") or "").strip(),
            fetched_at=(row.get("last_seen_at") or "").strip(),
        )
