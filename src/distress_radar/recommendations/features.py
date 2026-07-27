from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from distress_radar.domain.evidence import EvidenceItem
from distress_radar.municipal_severity import MunicipalSeverity
from distress_radar.tax_import import is_unpaid_status


@dataclass(frozen=True)
class ScoreDimensions:
    owner_motivation: float
    economics: float | None
    market_pressure: float
    property_risk: float
    data_confidence: float
    data_completeness: float
    data_freshness: float

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if value is None and name == "economics":
                continue
            lower_bound = -100 if name == "economics" else 0
            if value is None or not lower_bound <= value <= 100:
                raise ValueError(
                    "score dimensions must be between 0 and 100, except "
                    "demonstrably bad economics may be negative"
                )


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
    in_scope: bool = True
    listing_active: bool = False
    serious_municipal_matter: bool = False
    municipal_case_present: bool = False
    independent_motivation: bool = False
    human_contact_approved: bool = True
    broker_contact_available: bool = False

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
            human_contact_approved=True,
        )

    def with_scores(self, **changes: float) -> RecommendationFeatures:
        return replace(self, scores=replace(self.scores, **changes))

    def with_flags(self, **changes: bool) -> RecommendationFeatures:
        return replace(self, **changes)


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


@dataclass(frozen=True)
class EvidenceScoreResult:
    dimensions: ScoreDimensions
    components: dict[str, tuple[str, ...]]
    metrics: dict[str, Any]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def acquisition_attractiveness(scores: ScoreDimensions) -> float:
    """Evidence-backed acquisition potential; municipal risk is never a benefit."""
    components = (
        (scores.economics, 0.35),
        (scores.market_pressure, 0.25),
        (scores.owner_motivation, 0.25),
    )
    available = tuple(
        (value, weight) for value, weight in components if value is not None
    )
    if not available or not any(value != 0 for value, _ in available):
        return 0.0
    # A missing component makes no contribution. Do not renormalize the remaining
    # components: doing so can make unknown economics outrank supported economics.
    return round(sum(value * weight for value, weight in available), 2)


def municipal_review_urgency(
    municipal: tuple[MunicipalSeverity, ...],
) -> float | None:
    active = tuple(item for item in municipal if item.currently_active)
    numeric = tuple(item.score for item in active if item.score is not None)
    if numeric:
        return round(max(numeric), 2)
    return None if active else 0.0


def calculate_evidence_scores(
    *,
    county: dict[str, Any],
    listing: dict[str, Any],
    municipal: tuple[MunicipalSeverity, ...],
    official_records: tuple[dict[str, Any], ...],
    tax_records: tuple[dict[str, Any], ...],
    missing_fields: tuple[str, ...],
) -> EvidenceScoreResult:
    components: dict[str, list[str]] = {
        "owner_motivation": [],
        "economics": [],
        "market_pressure": [],
        "property_risk": [],
        "data_confidence": [],
        "data_completeness": [],
        "data_freshness": [],
    }

    motivation = 0.0
    if county.get("absentee_owner") is True:
        components["owner_motivation"].append(
            "Absentee ownership is screening context, not confirmed motivation: +0"
        )
    ownership_years = _number(county.get("ownership_duration_years"))
    if ownership_years is not None and ownership_years >= 15:
        components["owner_motivation"].append(
            f"Ownership duration {ownership_years:.1f} years is screening context, not confirmed motivation: +0"
        )
    lien_count = sum(
        str(item.get("signal_type") or "").casefold()
        in {"lien", "recorded_liens", "lis_pendens", "foreclosure"}
        for item in official_records
    )
    if lien_count:
        points = min(35, lien_count * 15)
        motivation += points
        components["owner_motivation"].append(
            f"{lien_count} qualifying Clerk record(s): +{points}"
        )
    unpaid_taxes = [
        item
        for item in tax_records
        if _number(item.get("amount_due")) not in (None, 0)
        and is_unpaid_status(item.get("status"))
    ]
    if unpaid_taxes:
        motivation += 30
        components["owner_motivation"].append(
            f"{len(unpaid_taxes)} supported unpaid-tax record(s): +30"
        )

    metrics: dict[str, Any] = {}
    economics: float | None = None
    price = _number(listing.get("list_price"))
    units = _number(county.get("verified_units"))
    price_per_unit: float | None = None
    if price is not None and units not in (None, 0):
        price_per_unit = round(price / units, 2)
        metrics["price_per_unit"] = price_per_unit
        components["economics"].append(
            f"Asking price per verified unit ${price_per_unit:,.0f}; "
            "screening metric only until NOI and expenses support economics."
        )
    noi = _number(listing.get("noi"))
    expenses = _number(listing.get("expenses"))
    cap_rate_supported = False
    if noi is not None and noi <= 0:
        economics = -50.0
        metrics["economics_status"] = "demonstrably_bad"
        components["economics"].append(
            f"Reported NOI ${noi:,.0f} is non-positive: -50 and qualification fails."
        )
    elif (
        price not in (None, 0)
        and noi is not None
        and noi > 0
        and expenses is not None
        and expenses >= 0
    ):
        cap_rate_supported = True
        cap_rate = round(noi / price, 4)
        metrics["reported_noi_cap_rate"] = cap_rate
        cap_points = 30 if cap_rate >= 0.06 else 15 if cap_rate >= 0.045 else 0
        ppu_points = (
            20
            if price_per_unit is not None and price_per_unit <= 100_000
            else 10
            if price_per_unit is not None and price_per_unit <= 150_000
            else 5
            if price_per_unit is not None and price_per_unit <= 200_000
            else 0
        )
        economics = float(cap_points + ppu_points)
        metrics["economics_status"] = (
            "supported_good" if economics > 0 else "supported_neutral"
        )
        components["economics"].append(
            f"Supported NOI/asking-price rate {cap_rate:.2%}: +{cap_points}; "
            f"supported price-per-unit contribution: +{ppu_points}."
        )
    else:
        metrics["economics_status"] = "unknown"
        components["economics"].append(
            "Economics unknown: positive NOI, asking price, and expenses are not all supported."
        )
    if not cap_rate_supported:
        metrics["cap_rate_status"] = "unknown"

    pressure = 0.0
    dom = _number(listing.get("dom"))
    cdom = _number(listing.get("cdom"))
    if dom is not None and dom >= 90:
        pressure += 15
        components["market_pressure"].append(f"DOM {dom:.0f}: +15")
    if cdom is not None and cdom >= 120:
        pressure += 10
        components["market_pressure"].append(f"CDOM {cdom:.0f}: +10")
    price_reductions = int(listing.get("price_reduction_count") or 0)
    if price_reductions:
        points = min(20, price_reductions * 10)
        pressure += points
        components["market_pressure"].append(
            f"{price_reductions} supported price reduction(s): +{points}"
        )
    remarks = str(listing.get("remarks") or "")
    negated_motivation = re.search(
        r"\b(?:unmotivated|not\s+(?:a\s+)?motivated|not\s+motivated)\b",
        remarks,
        flags=re.IGNORECASE,
    )
    if not negated_motivation and re.search(
        r"\b(?:motivated|bring\s+all\s+offers|must\s+sell)\b",
        remarks,
        flags=re.IGNORECASE,
    ):
        metrics["unverified_broker_language_flag"] = True
        components["market_pressure"].append(
            "Broker urgency language retained as an unverified analyst flag: +0"
        )
    status = str(listing.get("status") or "").casefold()
    if status in {"w", "withdrawn", "expired", "x"}:
        pressure += 10
        components["market_pressure"].append(
            f"Non-active listing status {listing.get('status')}: +10"
        )

    municipal_urgency = municipal_review_urgency(municipal)
    risk = municipal_urgency if municipal_urgency is not None else 0.0
    for item in sorted(
        municipal,
        key=lambda value: value.score if value.score is not None else -1,
        reverse=True,
    ):
        if not item.currently_active:
            continue
        components["property_risk"].append(
            f"{item.case_type}: {item.substantive_hazard}; "
            f"stage={item.enforcement_stage}; urgency="
            + (f"{item.score:.0f}/100" if item.score is not None else "unknown")
        )

    known_county = sum(
        county.get(field) is not None
        for field in (
            "owner",
            "mailing_address",
            "absentee_owner",
            "last_sale_date",
            "ownership_duration_years",
            "assessed_value",
            "year_built",
            "building_area",
            "lot_size",
            "verified_units",
        )
    )
    known_listing = sum(
        listing.get(field) is not None
        for field in (
            "status",
            "list_price",
            "dom",
            "cdom",
            "remarks",
            "noi",
            "rents",
            "expenses",
        )
    )
    completeness = round((known_county + known_listing) / 18 * 100, 2)
    confidence = 90.0 if county.get("verified_units") is not None else 60.0
    freshness = 90.0 if county or listing or municipal else 0.0
    components["data_confidence"].append(
        "Authoritative county identity and fields: "
        + ("+90" if confidence == 90 else "+60; unit count unavailable")
    )
    components["data_completeness"].append(
        f"{known_county + known_listing}/18 screening fields present: {completeness:.2f}"
    )
    if missing_fields:
        components["data_completeness"].append(
            f"{len(missing_fields)} diligence field(s) still missing"
        )
    components["data_freshness"].append(
        "Current-run county, Matrix, and municipal evidence: 90"
    )

    return EvidenceScoreResult(
        dimensions=ScoreDimensions(
            owner_motivation=min(100, motivation),
            economics=(None if economics is None else max(-100, min(100, economics))),
            market_pressure=min(100, pressure),
            property_risk=min(100, risk),
            data_confidence=confidence,
            data_completeness=completeness,
            data_freshness=freshness,
        ),
        components={key: tuple(value) for key, value in components.items()},
        metrics=metrics,
    )
