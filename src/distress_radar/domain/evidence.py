from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ValueType(StrEnum):
    REPORTED = "reported"
    CALCULATED = "calculated"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceItem:
    field: str
    value: Any
    source: str
    source_record_id: str
    source_url: str | None
    fetched_at: str
    freshness_status: FreshnessStatus
    confidence: float | None
    value_type: ValueType
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field or not self.source or not self.source_record_id or not self.fetched_at:
            raise ValueError("evidence requires field, source, record identifier and timestamp")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    @classmethod
    def unknown(
        cls,
        *,
        field: str,
        source: str,
        source_record_id: str,
        fetched_at: str,
        reason: str,
        source_url: str | None = None,
    ) -> EvidenceItem:
        return cls(
            field=field,
            value=None,
            source=source,
            source_record_id=source_record_id,
            source_url=source_url,
            fetched_at=fetched_at,
            freshness_status=FreshnessStatus.UNAVAILABLE,
            confidence=None,
            value_type=ValueType.UNKNOWN,
            metadata={"reason": reason},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "source_record_id": self.source_record_id,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "freshness_status": self.freshness_status.value,
            "confidence": self.confidence,
            "value_type": self.value_type.value,
            "metadata": dict(self.metadata),
        }
