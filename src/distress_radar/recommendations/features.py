from __future__ import annotations

from dataclasses import dataclass, replace

from distress_radar.domain.evidence import EvidenceItem
from distress_radar.domain.signals import PropertySignal


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
    is_synthetic: bool = False
    identity_verified: bool = True
    specific_opportunity: bool = True

    @classmethod
    def actionable(
        cls,
        *,
        property_id: str,
        discovery_channels: tuple[str, ...] = ("off_market",),
    ) -> RecommendationFeatures:
        return cls(
            property_id=property_id,
            discovery_channels=discovery_channels,
            scores=ScoreDimensions(80, 80, 50, 20, 90, 90, 90),
            has_underwriting=True,
            critical_documents_missing=False,
            violation_review_required=False,
            municipal_search_required=False,
        )

    def with_scores(self, **changes: float) -> RecommendationFeatures:
        return replace(self, scores=replace(self.scores, **changes))

    def with_flags(self, **changes: bool) -> RecommendationFeatures:
        return replace(self, **changes)


def score_owner_motivation(signals: tuple[PropertySignal, ...]) -> float:
    weights = {
        "tax_delinquency": 30,
        "lis_pendens": 35,
        "recorded_liens": 15,
        "long_ownership": 10,
        "absentee_owner": 10,
        "inactive_entity": 15,
    }
    return min(100, sum(weights.get(signal.signal_type, 0) for signal in signals))


def score_property_risk(signals: tuple[PropertySignal, ...]) -> float:
    weights = {
        "recorded_liens": 15,
        "code_enforcement_escalation": 30,
        "unsafe_structure": 60,
    }
    return min(100, sum(weights.get(signal.signal_type, 0) for signal in signals))


def score_data_quality(
    evidence: tuple[EvidenceItem, ...],
) -> tuple[float, float, float]:
    if not evidence:
        return 0, 0, 0
    known = [item for item in evidence if item.value_type.value != "unknown"]
    confidence = (
        sum(item.confidence or 0 for item in known) / len(known) * 100 if known else 0
    )
    completeness = len(known) / len(evidence) * 100
    fresh = sum(item.freshness_status.value == "fresh" for item in evidence)
    freshness = fresh / len(evidence) * 100
    return round(confidence, 2), round(completeness, 2), round(freshness, 2)
