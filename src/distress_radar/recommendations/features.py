from __future__ import annotations

from dataclasses import dataclass, field, replace

from distress_radar.domain.evidence import EvidenceItem


@dataclass(frozen=True)
class ScoreDimensions:
    owner_motivation: float
    economics: float
    market_pressure: float
    property_risk: float
    data_confidence: float
    data_completeness: float
    data_freshness: float

    def __post_init__(self) -> None:
        if any(not 0 <= value <= 100 for value in self.__dict__.values()):
            raise ValueError("score dimensions must be between 0 and 100")


@dataclass(frozen=True)
class RecommendationFeatures:
    property_id: str
    discovery_channels: tuple[str, ...]
    scores: ScoreDimensions
    has_underwriting: bool
    critical_documents_missing: bool
    violation_review_required: bool
    municipal_search_required: bool
    why_now: tuple[str, ...] = ()
    key_risks: tuple[str, ...] = ()
    missing_data: tuple[str, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()

    @classmethod
    def actionable(
        cls,
        *,
        property_id: str,
        discovery_channels: tuple[str, ...] = ("off_market",),
    ) -> "RecommendationFeatures":
        return cls(
            property_id=property_id,
            discovery_channels=discovery_channels,
            scores=ScoreDimensions(80, 80, 50, 20, 90, 90, 90),
            has_underwriting=True,
            critical_documents_missing=False,
            violation_review_required=False,
            municipal_search_required=False,
        )

    def with_scores(self, **changes: float) -> "RecommendationFeatures":
        return replace(self, scores=replace(self.scores, **changes))

    def with_flags(self, **changes: bool) -> "RecommendationFeatures":
        return replace(self, **changes)
