from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RawDocument:
    kind: str
    source_key: str
    source_url: str
    fetched_at: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CodeCase:
    city_slug: str
    source_name: str
    source_record_id: str
    case_number: str
    case_type: str | None
    status: str | None
    opened_date: str | None
    closed_date: str | None
    address: str | None
    parcel_number: str | None
    description: str | None
    project_name: str | None
    assigned_to: str | None
    violation_count: int
    violations: tuple[dict[str, Any], ...]
    source_url: str
    fetched_at: str

    def stable_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("fetched_at")
        return result

    def export_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["violations"] = list(self.violations)
        return result


@dataclass(frozen=True)
class PropertyRecord:
    city_slug: str
    source_name: str
    folio: str
    address: str | None
    city: str | None
    zip_code: str | None
    owner_name: str | None
    owner_name_2: str | None
    mailing_address_1: str | None
    mailing_address_2: str | None
    mailing_city: str | None
    mailing_state: str | None
    mailing_zip: str | None
    dor_code: str | None
    dor_description: str | None
    unit_count: int | None
    year_built: int | None
    floor_count: int | None
    building_area: float | None
    lot_size: float | None
    assessed_value: float | None
    last_sale_date: str | None
    last_sale_price: float | None
    latitude: float | None
    longitude: float | None
    source_url: str
    fetched_at: str

    def stable_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("fetched_at")
        return result


@dataclass(frozen=True)
class CollectionResult:
    records: tuple[CodeCase, ...]
    raw_documents: tuple[RawDocument, ...]
    requested_statuses: tuple[str, ...]


@dataclass(frozen=True)
class PropertyCollectionResult:
    records: tuple[PropertyRecord, ...]
    raw_documents: tuple[RawDocument, ...]


@dataclass(frozen=True)
class OfficialRecord:
    city_slug: str
    source_name: str
    source_record_id: str
    instrument_id: str
    original_instrument_id: str | None
    link_document_type: str | None
    folio: str
    document_type: str | None
    signal_type: str
    recorded_date: str | None
    document_date: str | None
    first_party: str | None
    second_party: str | None
    case_number: str | None
    status: str | None
    book: str | None
    page: str | None
    consideration: float | None
    source_url: str
    fetched_at: str

    def stable_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("fetched_at")
        return result


@dataclass(frozen=True)
class OfficialRecordCollectionResult:
    records: tuple[OfficialRecord, ...]
    raw_documents: tuple[RawDocument, ...]


@dataclass(frozen=True)
class TaxDelinquency:
    city_slug: str
    source_name: str
    source_record_id: str
    folio: str
    tax_year: int
    amount_due: float
    status: str | None
    certificate_number: str | None
    owner_name: str | None
    property_address: str | None
    source_url: str
    fetched_at: str

    def stable_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("fetched_at")
        return result
