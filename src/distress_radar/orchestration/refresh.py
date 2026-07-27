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
from distress_radar.identity.address_validation import (
    AddressCandidate,
    AddressValidationResult,
    AddressValidationStatus,
    CountyAddressValidator,
)
from distress_radar.identity.folio_resolver import normalize_folio
from distress_radar.models import PropertyRecord
from distress_radar.recommendations.features import (
    RecommendationFeatures,
    acquisition_attractiveness,
    calculate_evidence_scores,
)
from distress_radar.recommendations.rule_engine import recommend
from distress_radar.reports.daily_brief import render_daily_brief
from distress_radar.reports.exports import export_csv, export_json, normalize_record
from distress_radar.sources.mls.matrix_csv import MatrixCsvImporter
from distress_radar.sources.public.off_market_csv import (
    OffMarketCsvImporter,
)


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


def _address_evidence(result: AddressValidationResult) -> EvidenceItem:
    if result.status == AddressValidationStatus.VERIFIED:
        return EvidenceItem(
            field="validated_address",
            value=result.address,
            source=result.source or "county_address_validation",
            source_record_id=result.source_record_id or "address-validation",
            source_url=result.source_url,
            fetched_at=result.checked_at,
            freshness_status=FreshnessStatus.FRESH,
            confidence=0.99,
            value_type=ValueType.REPORTED,
            metadata={"validation_status": result.status.value},
        )
    return EvidenceItem.unknown(
        field="validated_address",
        source=result.source or "county_address_validation",
        source_record_id=result.source_record_id or "address-validation",
        source_url=result.source_url,
        fetched_at=result.checked_at,
        reason=f"address_validation_{result.status.value}",
    )


def run_fixture_demo(
    *,
    matrix_path: Path,
    off_market_path: Path,
    output_dir: Path,
    generated_at: str,
    property_records: tuple[PropertyRecord, ...] = (),
) -> FixtureDemoResult:
    listings = MatrixCsvImporter().import_file(matrix_path, fetched_at=generated_at)
    off_market_candidates = OffMarketCsvImporter().import_file(
        off_market_path, fetched_at=generated_at
    )
    address_validator = CountyAddressValidator(property_records)
    county_records_by_folio = {
        normalized: record
        for record in property_records
        if (normalized := normalize_folio(record.folio))
    }
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
        folio = off_market.folio if off_market else listing.folio if listing else None
        address_candidates = tuple(
            candidate
            for candidate in (
                AddressCandidate(
                    source=listing.source_name,
                    street=listing.address,
                    municipality=listing.municipality,
                    state=listing.state,
                    postal_code=listing.postal_code,
                )
                if listing
                else None,
                AddressCandidate(
                    source=OffMarketCsvImporter.source_name,
                    street=off_market.address,
                    municipality=off_market.municipality,
                    state=off_market.state,
                    postal_code=off_market.postal_code,
                )
                if off_market
                else None,
            )
            if candidate
        )
        address_validation = address_validator.validate(
            folio=folio,
            candidates=address_candidates,
            checked_at=generated_at,
        )
        county_record = county_records_by_folio.get(normalize_folio(folio))
        evidence = (
            (_listing_evidence(listing, generated_at) if listing else ())
            + (off_market.evidence if off_market else ())
            + (_address_evidence(address_validation),)
        )
        signals = off_market.signals if off_market else ()
        listing_values = listing.stable_dict() if listing else {}
        if not listing and off_market:
            listing_values = {
                "list_price": off_market.estimated_value,
                "noi": off_market.stabilized_noi,
                "expenses": None,
                "status": None,
            }
        county_values = {
            "verified_units": county_record.unit_count if county_record else None,
            "owner": county_record.owner_name if county_record else None,
            "assessed_value": (county_record.assessed_value if county_record else None),
        }
        official_records = tuple(
            {"signal_type": signal.signal_type}
            for signal in signals
            if signal.signal_type in {"recorded_liens", "lis_pendens", "foreclosure"}
        )
        tax_records = tuple(
            signal.value
            for signal in signals
            if signal.signal_type == "tax_delinquency"
            and isinstance(signal.value, dict)
        )
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
        score_result = calculate_evidence_scores(
            county=county_values,
            listing=listing_values,
            municipal=(),
            official_records=official_records,
            tax_records=tax_records,
            missing_fields=missing_data,
        )
        scores = score_result.dimensions
        features = RecommendationFeatures(
            property_id="property-" + hashlib.sha256(key.encode()).hexdigest()[:16],
            discovery_channels=channels,
            scores=scores,
            has_underwriting=False,
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
        recommendation_score = acquisition_attractiveness(scores)
        municipality = listing.municipality if listing else off_market.municipality
        units = (
            off_market.units
            if off_market and off_market.units
            else listing.units
            if listing
            else None
        )
        records.append(
            normalize_record(
                {
                    "property_id": features.property_id,
                    "folio": folio or "",
                    "address": address_validation.address,
                    "address_validation_status": address_validation.status.value,
                    "address_validation_source": address_validation.source or "",
                    "address_validation_source_record_id": (
                        address_validation.source_record_id or ""
                    ),
                    "address_validation_source_url": (
                        address_validation.source_url or ""
                    ),
                    "address_validation_checked_at": address_validation.checked_at,
                    "address_validation_details": address_validation.details,
                    "latitude": address_validation.latitude,
                    "longitude": address_validation.longitude,
                    "jurisdiction": f"Miami-Dade / {municipality}",
                    "discovery_channels": list(channels),
                    "mls_numbers": [
                        item.source_record_id for item in entry["listings"]
                    ],
                    "asset_segment": (
                        "two_to_four_units"
                        if units and units <= 4
                        else "five_plus_units"
                        if units
                        else "unknown"
                    ),
                    "advertised_units": listing.units if listing else None,
                    "public_record_units": (
                        county_record.unit_count if county_record else None
                    ),
                    "recommended_action": recommendation.action,
                    "recommendation_score": recommendation_score,
                    "owner_motivation_score": scores.owner_motivation,
                    "economics_score": scores.economics,
                    "market_pressure_score": scores.market_pressure,
                    "property_risk_score": scores.property_risk,
                    "data_confidence_score": scores.data_confidence,
                    "data_completeness_score": scores.data_completeness,
                    "data_freshness_score": scores.data_freshness,
                    "current_noi": off_market.current_noi
                    if off_market
                    else listing.noi
                    if listing
                    else None,
                    "stabilized_noi": off_market.stabilized_noi if off_market else None,
                    "estimated_value_range": {
                        "low": off_market.estimated_value * 0.9
                        if off_market and off_market.estimated_value
                        else None,
                        "base": off_market.estimated_value if off_market else None,
                        "high": off_market.estimated_value * 1.1
                        if off_market and off_market.estimated_value
                        else None,
                    },
                    "preliminary_offer_range": {
                        "status": "not_available",
                        "reason": "insufficient_property_specific_underwriting_data",
                    },
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
