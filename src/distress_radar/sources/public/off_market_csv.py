from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from distress_radar.domain.evidence import EvidenceItem, FreshnessStatus, ValueType
from distress_radar.domain.signals import PropertySignal
from distress_radar.identity.folio_resolver import normalize_folio


@dataclass(frozen=True)
class OffMarketCandidate:
    source_record_id: str
    folio: str | None
    address: str
    municipality: str
    state: str | None
    postal_code: str | None
    owner_name: str | None
    units: int | None
    current_noi: float | None
    stabilized_noi: float | None
    estimated_value: float | None
    repairs: float | None
    capex: float | None
    evidence: tuple[EvidenceItem, ...]
    signals: tuple[PropertySignal, ...]


_FIELDS = (
    "owner_name",
    "state",
    "postal_code",
    "unpaid_taxes",
    "lis_pendens",
    "active_liens",
    "ownership_years",
    "absentee",
    "entity_status",
    "code_escalation",
    "units",
    "current_noi",
    "stabilized_noi",
    "estimated_value",
    "repairs",
    "capex",
)


def _number(value: str | None) -> float | None:
    text = (value or "").replace("$", "").replace(",", "").strip()
    return float(text) if text else None


def _integer(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _boolean(value: str | None) -> bool | None:
    text = (value or "").casefold().strip()
    if text in {"true", "yes", "1", "y"}:
        return True
    if text in {"false", "no", "0", "n"}:
        return False
    return None


class OffMarketCsvImporter:
    source_name = "authorized_off_market_csv"

    def import_file(self, path: Path, *, fetched_at: str) -> tuple[OffMarketCandidate, ...]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"source_record_id", "folio", "address", "city"}
            missing = required.difference(reader.fieldnames or ())
            if missing:
                raise ValueError(f"Off-market CSV missing columns: {', '.join(sorted(missing))}")
            return tuple(self._candidate(row, fetched_at) for row in reader)

    def _candidate(self, row: dict[str, str], fetched_at: str) -> OffMarketCandidate:
        record_id = row["source_record_id"].strip()
        source_url = row.get("source_url") or f"manual-import://off-market#{record_id}"
        parsed = {
            "owner_name": row.get("owner_name") or None,
            "state": row.get("state") or None,
            "postal_code": row.get("zip_code") or row.get("postal_code") or None,
            "unpaid_taxes": _number(row.get("unpaid_taxes")),
            "lis_pendens": _boolean(row.get("lis_pendens")),
            "active_liens": _integer(row.get("active_liens")),
            "ownership_years": _integer(row.get("ownership_years")),
            "absentee": _boolean(row.get("absentee")),
            "entity_status": row.get("entity_status") or None,
            "code_escalation": row.get("code_escalation") or None,
            "units": _integer(row.get("units")),
            "current_noi": _number(row.get("current_noi")),
            "stabilized_noi": _number(row.get("stabilized_noi")),
            "estimated_value": _number(row.get("estimated_value")),
            "repairs": _number(row.get("repairs")),
            "capex": _number(row.get("capex")),
        }
        evidence: list[EvidenceItem] = []
        for field in _FIELDS:
            value = parsed[field]
            if value is None:
                evidence.append(
                    EvidenceItem.unknown(
                        field=field,
                        source=self.source_name,
                        source_record_id=record_id,
                        source_url=source_url,
                        fetched_at=fetched_at,
                        reason="not_reported_in_authorized_export",
                    )
                )
            else:
                evidence.append(
                    EvidenceItem(
                        field=field,
                        value=value,
                        source=self.source_name,
                        source_record_id=record_id,
                        source_url=source_url,
                        fetched_at=fetched_at,
                        freshness_status=FreshnessStatus.FRESH,
                        confidence=0.95,
                        value_type=ValueType.REPORTED,
                    )
                )

        signals: list[PropertySignal] = []

        def add(signal_type: str, value: object) -> None:
            signals.append(PropertySignal(signal_type, value, fetched_at, record_id))

        if (parsed["unpaid_taxes"] or 0) > 0:
            add("tax_delinquency", parsed["unpaid_taxes"])
        if parsed["lis_pendens"] is True:
            add("lis_pendens", True)
        if (parsed["active_liens"] or 0) > 0:
            add("recorded_liens", parsed["active_liens"])
        if (parsed["ownership_years"] or 0) >= 10:
            add("long_ownership", parsed["ownership_years"])
        if parsed["absentee"] is True:
            add("absentee_owner", True)
        if str(parsed["entity_status"] or "").casefold() in {"inactive", "dissolved"}:
            add("inactive_entity", parsed["entity_status"])
        escalation = str(parsed["code_escalation"] or "").casefold()
        if escalation == "unsafe_structure":
            add("unsafe_structure", parsed["code_escalation"])
        elif escalation:
            add("code_enforcement_escalation", parsed["code_escalation"])

        return OffMarketCandidate(
            source_record_id=record_id,
            folio=normalize_folio(row.get("folio")),
            address=row["address"].strip(),
            municipality=row["city"].strip(),
            state=parsed["state"],
            postal_code=parsed["postal_code"],
            owner_name=parsed["owner_name"],
            units=parsed["units"],
            current_noi=parsed["current_noi"],
            stabilized_noi=parsed["stabilized_noi"],
            estimated_value=parsed["estimated_value"],
            repairs=parsed["repairs"],
            capex=parsed["capex"],
            evidence=tuple(evidence),
            signals=tuple(signals),
        )
