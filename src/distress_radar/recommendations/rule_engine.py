from __future__ import annotations

from dataclasses import dataclass

from distress_radar.domain.evidence import EvidenceItem
from distress_radar.recommendations.features import (
    RecommendationFeatures,
    ScoreDimensions,
)

SUPPORTED_ACTIONS = {
    "contact_broker_for_documents",
    "investigate_owner",
    "human_municipal_review",
    "dismiss",
    "contact_owner",
    "contact_broker",
    "excluded",
    "verify_identity",
    "request_documents",
    "human_violation_review",
    "order_municipal_search",
    "watch",
    "reject",
    "reject_high_risk",
    "insufficient_data",
    "manual_triage",
}


@dataclass(frozen=True)
class RecommendationResult:
    property_id: str
    action: str
    scores: ScoreDimensions
    explanation: dict[str, tuple[str, ...]]
    evidence: tuple[EvidenceItem, ...]


def _action(features: RecommendationFeatures) -> str:
    scores = features.scores
    if features.is_synthetic:
        return "excluded"
    if not features.identity_verified:
        return "verify_identity"
    if not features.in_scope:
        return "excluded"
    if features.municipal_search_required:
        return "order_municipal_search"
    if features.serious_municipal_matter:
        return "human_municipal_review"
    if features.violation_review_required:
        return "human_violation_review"
    if scores.economics is not None and scores.economics < 0:
        return "reject"
    if (
        features.listing_active
        and "mls" in features.discovery_channels
        and features.critical_documents_missing
        and features.broker_contact_available
    ):
        return "contact_broker_for_documents"
    if (
        features.listing_active
        and "mls" in features.discovery_channels
        and features.critical_documents_missing
    ):
        return "request_documents"
    if "off_market" in features.discovery_channels and features.independent_motivation:
        return "investigate_owner"
    if features.municipal_case_present and not features.independent_motivation:
        return "manual_triage"
    if features.critical_documents_missing:
        return "request_documents"
    if (
        scores.data_completeness < 40
        or scores.data_confidence < 40
        or scores.data_freshness < 35
    ):
        return "insufficient_data"
    if not features.has_underwriting:
        return "insufficient_data"
    if scores.property_risk >= 85:
        return "reject_high_risk"
    if scores.economics is None:
        return "insufficient_data"
    if scores.economics < 30:
        return "reject"
    if scores.owner_motivation >= 65 and scores.economics >= 60:
        if not features.specific_opportunity:
            return "manual_triage"
        if not features.human_contact_approved:
            return "investigate_owner"
        return (
            "contact_broker"
            if "mls" in features.discovery_channels
            else "contact_owner"
        )
    return "manual_triage"


def recommend(features: RecommendationFeatures) -> RecommendationResult:
    action = _action(features)
    motivation = tuple(
        item
        for item in features.why_now
        if any(word in item.casefold() for word in ("owner", "tax", "lien", "estate"))
    )
    explanation = {
        "Why this property surfaced": features.why_now
        or ("Rule-based screening threshold met.",),
        "Why the owner may be motivated": motivation
        or ("Motivation is not yet confirmed.",),
        "Why the economics may work": (
            (
                f"Economics score is {features.scores.economics:.0f}/100."
                if features.scores.economics is not None
                else "Economics are unknown because required inputs are unsupported."
            ),
        ),
        "What could destroy the deal": features.key_risks
        or ("No risk conclusion without diligence.",),
        "What information is missing": features.missing_data
        or ("No critical gap recorded.",),
        "What the next human action should be": (action,),
    }
    return RecommendationResult(
        property_id=features.property_id,
        action=action,
        scores=features.scores,
        explanation=explanation,
        evidence=features.evidence,
    )
