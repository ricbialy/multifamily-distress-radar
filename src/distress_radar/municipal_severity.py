from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from distress_radar.models import CodeCase


@dataclass(frozen=True)
class MunicipalSeverity:
    category: str
    score: float
    status_category: str
    age_days: int | None
    case_type: str
    reasons: tuple[str, ...]


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _text(case: CodeCase) -> str:
    operative_violation_fields = (
        "CodeNumber",
        "CodeDescription",
        "CorrectiveAction",
        "CategoryName",
        "CodeStatus",
        "ViolationPriority",
        "WFActionName",
        "Description",
        "Status",
        "ViolationType",
    )
    violation_text = " ".join(
        str(violation.get(field))
        for violation in case.violations
        for field in operative_violation_fields
        if violation.get(field) is not None
    )
    return " ".join(
        value
        for value in (
            case.case_type,
            case.status,
            case.description,
            case.project_name,
            violation_text,
        )
        if value
    ).casefold()


def _status_category(case: CodeCase) -> str:
    text = " ".join(value for value in (case.status, case.closed_date) if value).casefold()
    if case.closed_date or any(word in text for word in ("closed", "resolved", "complied")):
        return "closed_or_resolved"
    if any(word in text for word in ("lien", "special master", "hearing", "unsafe")):
        return "escalated_open"
    return "open_or_unknown"


def classify_municipal_case(
    case: CodeCase, *, as_of: str | datetime
) -> MunicipalSeverity:
    as_of_date = _date(as_of) if isinstance(as_of, str) else as_of
    opened = _date(case.opened_date)
    age_days = (
        max(0, (as_of_date - opened).days)
        if as_of_date is not None and opened is not None
        else None
    )
    text = _text(case)
    status_category = _status_category(case)
    reasons: list[str] = []

    unsafe_terms = (
        "unsafe structure",
        "life safety",
        "imminent danger",
        "fire hazard",
        "uninhabitable",
    )
    special_master_terms = ("special master", "special magistrate")
    lien_terms = ("intent to lien", "lien pending", "lien stage", "recorded lien")
    administrative_terms = (
        "administrative",
        "payment",
        "fee due",
        "invoice",
        "registration",
        "renewal",
    )
    minor_terms = (
        "warning",
        "courtesy",
        "grass",
        "landscape",
        "trash",
        "sign",
        "address numbers",
    )

    if any(term in text for term in unsafe_terms):
        category, score = "unsafe_or_life_safety", 100.0
        reasons.append("Unsafe-structure or life-safety language is present.")
    elif any(term in text for term in special_master_terms):
        category, score = "special_master_escalation", 85.0
        reasons.append("Special-master or special-magistrate escalation is present.")
    elif any(term in text for term in lien_terms) or "lien" in (
        case.status or ""
    ).casefold():
        category, score = "intent_to_lien_or_lien", 70.0
        reasons.append("Intent-to-lien or lien-stage language is present.")
    elif any(term in text for term in minor_terms):
        category, score = "minor_warning", 10.0
        reasons.append("Minor-warning terminology is present.")
    elif status_category != "closed_or_resolved" and (
        (age_days is not None and age_days >= 365)
        or case.violation_count >= 2
        or "repeat" in text
    ):
        category, score = "repeated_or_old_unresolved", 50.0
        reasons.append("The matter is repeated or has remained unresolved for at least one year.")
    elif any(term in text for term in administrative_terms):
        category, score = "administrative_or_payment", 25.0
        reasons.append("The matter is administrative or payment-oriented.")
    else:
        category, score = "minor_warning", 10.0
        reasons.append(
            "No lien, special-master, unsafe-structure, or sustained-escalation evidence was found."
        )

    if status_category == "closed_or_resolved":
        score = min(score, 10.0)
        reasons.append("The case is closed or resolved, so active risk is capped.")

    return MunicipalSeverity(
        category=category,
        score=score,
        status_category=status_category,
        age_days=age_days,
        case_type=case.case_type or "Unknown case type",
        reasons=tuple(reasons),
    )


def severity_from_mapping(value: dict[str, Any]) -> MunicipalSeverity:
    return MunicipalSeverity(
        category=str(value.get("severity_category") or "minor_warning"),
        score=float(value.get("severity_score") or 0),
        status_category=str(value.get("status_category") or "open_or_unknown"),
        age_days=(
            int(value["age_days"]) if value.get("age_days") is not None else None
        ),
        case_type=str(value.get("case_type") or "Unknown case type"),
        reasons=tuple(value.get("severity_reasons") or ()),
    )
