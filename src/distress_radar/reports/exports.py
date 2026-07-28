from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "property_id": "",
    "folio": "",
    "address": "",
    "address_validation_status": "unverified",
    "address_validation_source": "",
    "address_validation_source_record_id": "",
    "address_validation_source_url": "",
    "address_validation_checked_at": "",
    "address_validation_details": "",
    "latitude": None,
    "longitude": None,
    "jurisdiction": "",
    "discovery_channels": [],
    "mls_numbers": [],
    "asset_segment": "",
    "advertised_units": None,
    "public_record_units": None,
    "recommended_action": "insufficient_data",
    "recommendation_score": None,
    "owner_motivation_score": None,
    "economics_score": None,
    "market_pressure_score": None,
    "property_risk_score": None,
    "data_confidence_score": None,
    "data_completeness_score": None,
    "data_freshness_score": None,
    "estimated_equity": None,
    "current_noi": None,
    "stabilized_noi": None,
    "estimated_value_range": {"low": None, "base": None, "high": None},
    "preliminary_offer_range": {
        "conservative": None,
        "base": None,
        "maximum": None,
        "status": "insufficient_data",
    },
    "why_now": [],
    "key_risks": [],
    "missing_data": [],
    "next_steps": [],
    "evidence": [],
}


def normalize_record(record: dict[str, Any], *, generated_at: str) -> dict[str, Any]:
    normalized = {
        key: (
            dict(value)
            if isinstance(value, dict)
            else list(value)
            if isinstance(value, list)
            else value
        )
        for key, value in _DEFAULTS.items()
    }
    normalized.update(record)
    normalized["generated_at"] = generated_at
    return normalized


def export_json(records: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(records), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_csv(records: Iterable[dict[str, Any]], path: Path) -> None:
    rows = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(_DEFAULTS) + ["generated_at"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, sort_keys=True)
                        if isinstance(value, (dict, list, tuple))
                        else value
                    )
                    for key, value in record.items()
                }
            )
