from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from distress_radar.domain.evidence import (
    EvidenceItem,
    FreshnessStatus,
    ValueType,
)
from distress_radar.domain.listing import ListingSnapshot
from distress_radar.identity.address_normalizer import normalize_address
from distress_radar.recommendations.features import (
    RecommendationFeatures,
    ScoreDimensions,
    score_data_quality,
    score_owner_motivation,
    score_property_risk,
)
from distress_radar.recommendations.rule_engine import recommend
from distress_radar.reports.daily_brief import render_daily_brief
from distress_radar.reports.exports import export_csv, export_json, normalize_record
from distress_radar.sources.mls.matrix_csv import MatrixCsvImporter
from distress_radar.sources.public.off_market_csv import (
    OffMarketCandidate,
    OffMarketCsvImporter,
)
from distress_radar.underwriting.offer_range import OfferInputs, calculate_offer_range


@dataclass(frozen=True)
class FixtureDemoResult:
    canonical_property_count: int
    json_path: Path
    csv_path: Path
    brief_path: Path


def _identity(folio: str | None, address: str, municipality: str) -> str:
    return (
        f"folio:{folio}"
        if folio
        else f"address:{normalize_address(address)}|{municipality.casefold()}"
    )


def _listing_evidence(
    listing: ListingSnapshot, generated_at: str
) -> tuple[EvidenceItem, ...]:
    items: list[EvidenceItem] = []
    for field in ("status", "list_price", "dom", "cdom", "units", "noi", "remarks"):
        value = getattr(listing, field)
        if value is None:
            items.append(
                EvidenceItem.unknown(
                    field=field,
                    source=listing.source_name,
                    source_record_id=listing.source_record_id,
                    source_url=listing.source_url,
                    fetched_at=generated_at,
                    reason="not_reported_in_matrix_export",
                )
            )
        else:
            items.append(
                EvidenceItem(
                    field=field,
                    value=value,
                    source=listing.source_name,
                    source_record_id=listing.source_record_id,
                    source_url=listing.source_url,
                    fetched_at=generated_at,
                    freshness_status=FreshnessStatus.FRESH,
                    confidence=0.95,
                    value_type=ValueType.REPORTED,
                )
            )
    return tuple(items)


def _economics_score(
    listing: ListingSnapshot | None, off_market: OffMarketCandidate | None
) -> float:
    if off_market and off_market.stabilized_noi and off_market.estimated_value:
        return min(100, round(off_market.stabilized_noi / off_market.estimated_value * 1000, 2))
    if listing and listing.noi and listing.list_price:
        return min(100, round(listing.noi / listing.list_price * 1000, 2))
    return 0


def _market_pressure_score(listing: ListingSnapshot | None) -> float:
    if listing is None:
        return 0
    score = min(40, listing.cdom or listing.dom or 0)
    remarks = (listing.remarks or "").casefold()
    if any(term in remarks for term in ("motivated", "price reduced", "bring all offers", "estate sale")):
        score += 25
    if (listing.status or "").casefold() in {"expired", "withdrawn"}:
        score += 35
    return min(100, score)


def run_fixture_demo(
    *,
    matrix_path: Path,
    off_market_path: Path,
    output_dir: Path,
    generated_at: str,
) -> FixtureDemoResult:
    listings = MatrixCsvImporter().import_file(matrix_path, fetched_at=generated_at)
    off_market_candidates = OffMarketCsvImporter().import_file(
        off_market_path, fetched_at=generated_at
    )
    properties: dict[str, dict[str, Any]] = {}
    for listing in listings:
        key = _identity(listing.folio, listing.address, listing.municipality)
        entry = properties.setdefault(
            key, {"listings": [], "off_market": None, "address": listing.address}
        )
        entry["listings"].append(listing)
    for candidate in off_market_candidates:
        key = _identity(candidate.folio, candidate.address, candidate.municipality)
        entry = properties.setdefault(
            key, {"listings": [], "off_market": None, "address": candidate.address}
        )
        entry["off_market"] = candidate

    records: list[dict[str, Any]] = []
    for key, entry in properties.items():
        listing = entry["listings"][0] if entry["listings"] else None
        off_market = entry["off_market"]
        evidence = (
            (_listing_evidence(listing, generated_at) if listing else ())
            + (off_market.evidence if off_market else ())
        )
        signals = off_market.signals if off_market else ()
        confidence, completeness, freshness = score_data_quality(evidence)
        motivation = score_owner_motivation(signals)
        if listing and any(
            term in (listing.remarks or "").casefold()
            for term in ("motivated", "bring all offers", "estate sale", "price reduced")
        ):
            motivation = min(100, motivation + 20)
        risk = score_property_risk(signals)
        economics = _economics_score(listing, off_market)
        market_pressure = _market_pressure_score(listing)
        channels = tuple(
            channel
            for channel, present in (
                ("mls", listing is not None),
                ("off_market", off_market is not None),
            )
            if present
        )
        why_now = tuple(signal.signal_type for signal in signals)
        if listing:
            why_now += (f"MLS {listing.source_record_id} is {listing.status}",)
        missing_data = tuple(
            item.field for item in evidence if item.value_type == ValueType.UNKNOWN
        )
        features = RecommendationFeatures(
            property_id="property-" + hashlib.sha256(key.encode()).hexdigest()[:16],
            discovery_channels=channels,
            scores=ScoreDimensions(
                owner_motivation=motivation,
                economics=economics,
                market_pressure=market_pressure,
                property_risk=risk,
                data_confidence=confidence,
                data_completeness=completeness,
                data_freshness=freshness,
            ),
            has_underwriting=bool(
                off_market and off_market.estimated_value and off_market.stabilized_noi
            ),
            critical_documents_missing=False,
            violation_review_required=any(
                signal.signal_type == "unsafe_structure" for signal in signals
            ),
            municipal_search_required=False,
            why_now=why_now,
            key_risks=tuple(
                signal.signal_type
                for signal in signals
                if signal.signal_type in {"recorded_liens", "unsafe_structure"}
            ),
            missing_data=missing_data,
            evidence=evidence,
        )
        recommendation = recommend(features)
        offer = calculate_offer_range(
            OfferInputs(
                stabilized_value=(
                    off_market.estimated_value
                    if off_market
                    and off_market.estimated_value
                    and off_market.stabilized_noi
                    else None
                ),
                required_margin_rate=0.10,
                repairs=off_market.repairs or 0 if off_market else 0,
                capital_expenditures=off_market.capex or 0 if off_market else 0,
                violation_permit_contingency=25_000 if risk else 0,
                closing_cost_rate=0.03,
                financing_cost_rate=0.02,
                insurance_flood_contingency=25_000,
                data_uncertainty_rate=max(0, (100 - completeness) / 1000),
            )
        )
        recommendation_score = round(
            max(
                0,
                motivation * 0.30
                + economics * 0.35
                + market_pressure * 0.15
                + confidence * 0.20
                - risk * 0.25,
            ),
            2,
        )
        folio = (
            off_market.folio
            if off_market
            else listing.folio
            if listing
            else None
        )
        address = off_market.address if off_market else listing.address
        municipality = (
            off_market.municipality if off_market else listing.municipality
        )
        units = off_market.units if off_market and off_market.units else listing.units if listing else None
        records.append(
            normalize_record(
                {
                    "property_id": features.property_id,
                    "folio": folio or "",
                    "address": address,
                    "jurisdiction": f"Miami-Dade / {municipality}",
                    "discovery_channels": list(channels),
                    "mls_numbers": [item.source_record_id for item in entry["listings"]],
                    "asset_segment": (
                        "two_to_four_units"
                        if units and units <= 4
                        else "five_plus_units"
                        if units
                        else "unknown"
                    ),
                    "advertised_units": listing.units if listing else None,
                    "public_record_units": off_market.units if off_market else None,
                    "recommended_action": recommendation.action,
                    "recommendation_score": recommendation_score,
                    "owner_motivation_score": motivation,
                    "economics_score": economics,
                    "market_pressure_score": market_pressure,
                    "property_risk_score": risk,
                    "data_confidence_score": confidence,
                    "data_completeness_score": completeness,
                    "data_freshness_score": freshness,
                    "current_noi": off_market.current_noi if off_market else listing.noi if listing else None,
                    "stabilized_noi": off_market.stabilized_noi if off_market else None,
                    "estimated_value_range": {
                        "low": off_market.estimated_value * 0.9 if off_market and off_market.estimated_value else None,
                        "base": off_market.estimated_value if off_market else None,
                        "high": off_market.estimated_value * 1.1 if off_market and off_market.estimated_value else None,
                    },
                    "preliminary_offer_range": offer.to_dict(),
                    "why_now": list(why_now),
                    "key_risks": list(features.key_risks),
                    "missing_data": list(missing_data),
                    "next_steps": [recommendation.action],
                    "evidence": [item.to_dict() for item in evidence],
                },
                generated_at=generated_at,
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "recommendations.json"
    csv_path = output_dir / "recommendations.csv"
    brief_path = output_dir / "daily_brief.md"
    ordered = tuple(
        sorted(records, key=lambda item: item["recommendation_score"], reverse=True)
    )
    export_json(ordered, json_path)
    export_csv(ordered, csv_path)
    brief_path.write_text(
        render_daily_brief(ordered, generated_at=generated_at), encoding="utf-8"
    )
    return FixtureDemoResult(len(properties), json_path, csv_path, brief_path)
