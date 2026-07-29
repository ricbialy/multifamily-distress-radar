from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ListingSnapshot:
    source_record_id: str
    source_name: str
    fetched_at: str
    address: str
    municipality: str
    folio: str | None
    property_class: str | None
    status: str | None
    list_price: float | None
    dom: int | None
    cdom: int | None
    units: int | None
    noi: float | None
    rents: float | None
    expenses: float | None
    remarks: str | None
    source_url: str
    state: str | None = None
    postal_code: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    synthetic: bool = False

    def stable_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("fetched_at")
        result.pop("raw_payload")
        return result

    def material_dict(self) -> dict[str, Any]:
        result = self.stable_dict()
        result.pop("source_url")
        return result


@dataclass(frozen=True)
class ListingChange:
    source_record_id: str
    change_type: str
    detected_at: str
    before: Any
    after: Any
