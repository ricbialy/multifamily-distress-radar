from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalProperty:
    property_id: str
    folio: str | None
    address: str
    municipality: str
    jurisdiction: str
    latitude: float | None = None
    longitude: float | None = None

    def __post_init__(self) -> None:
        if not self.property_id or not self.address or not self.municipality or not self.jurisdiction:
            raise ValueError("canonical property requires identity and jurisdiction")
