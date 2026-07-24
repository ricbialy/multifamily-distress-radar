from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:,.0f}"


def _line(record: dict[str, Any]) -> str:
    offer = record.get("preliminary_offer_range") or {}
    offer_text = (
        f"{_money(offer.get('conservative'))} – {_money(offer.get('maximum'))}"
        if offer.get("status") == "complete"
        else "offer range: insufficient data"
    )
    return (
        f"- {record.get('address') or record.get('property_id')}: "
        f"{record.get('recommended_action')} ({offer_text})"
    )


def _section(title: str, lines: Iterable[str]) -> list[str]:
    content = list(lines)
    return [f"## {title}", *(content or ["- None."]), ""]


def render_daily_brief(
    records: tuple[dict[str, Any], ...],
    *,
    important_listing_changes: tuple[str, ...] = (),
    source_warnings: tuple[str, ...] = (),
    generated_at: str,
) -> str:
    ranked = sorted(
        records,
        key=lambda item: item.get("recommendation_score")
        if item.get("recommendation_score") is not None
        else -1,
        reverse=True,
    )
    actionable = tuple(
        item
        for item in ranked
        if item.get("recommended_action")
        not in {"reject", "reject_high_risk", "watch", "insufficient_data"}
    )
    mls = tuple(item for item in records if "mls" in item.get("discovery_channels", ()))
    off_market = tuple(
        item for item in records if "off_market" in item.get("discovery_channels", ())
    )
    both = tuple(
        item
        for item in records
        if {"mls", "off_market"}.issubset(set(item.get("discovery_channels", ())))
    )
    missing = (
        f"- {item.get('address')}: {', '.join(item.get('missing_data', ())) }"
        for item in records
        if item.get("missing_data")
    )
    lines = ["# Daily acquisition-intelligence brief", f"Generated: {generated_at}", ""]
    lines += _section("Top 10 actionable opportunities", (_line(item) for item in actionable[:10]))
    lines += _section("New MLS opportunities", (_line(item) for item in mls))
    lines += _section(
        "Important listing changes", (f"- {change}" for change in important_listing_changes)
    )
    lines += _section("New off-market signals", (_line(item) for item in off_market))
    lines += _section(
        "Properties discovered through both channels", (_line(item) for item in both)
    )
    lines += _section(
        "Preliminary offer ranges",
        (_line(item) for item in records if (item.get("preliminary_offer_range") or {}).get("status") == "complete"),
    )
    lines += _section("Missing critical diligence", missing)
    lines += _section(
        "Source failures or stale data", (f"- {warning}" for warning in source_warnings)
    )
    lines += _section(
        "Recommended actions for today",
        (
            f"- {item.get('recommended_action')}: {item.get('address')}"
            for item in actionable
        ),
    )
    return "\n".join(lines).rstrip() + "\n"
