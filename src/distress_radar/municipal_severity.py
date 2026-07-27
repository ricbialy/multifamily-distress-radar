from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from distress_radar.models import CodeCase


@dataclass(frozen=True)
class MunicipalSeverity:
    category: str
    score: float | None
    status_category: str
    age_days: int | None
    case_type: str
    reasons: tuple[str, ...]
    enforcement_stage: str
    substantive_hazard: str
    cure_complexity: str
    likely_cost: str
    repetition_score: float
    currently_active: bool
    enrichment_state: str


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def active_violations(
    violations: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Return only violation rows that remain operative."""
    active: list[dict[str, Any]] = []
    for violation in violations:
        status = " ".join(
            str(violation.get(field) or "")
            for field in ("CodeStatus", "Status", "ViolationStatus")
        ).casefold()
        if violation.get("ResolveDate") or any(
            marker in status
            for marker in (
                "closed",
                "resolved",
                "complied",
                "corrected",
                "dismissed",
                "cancelled",
                "canceled",
            )
        ):
            continue
        active.append(violation)
    return tuple(active)


def _text(case: CodeCase, violations: tuple[dict[str, Any], ...]) -> str:
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
        for violation in violations
        for field in operative_violation_fields
        if violation.get(field) is not None
    )
    return " ".join(
        value
        for value in (
            case.case_type,
            case.description,
            case.project_name,
            violation_text,
        )
        if value
    ).casefold()


def _enforcement_stage(case: CodeCase) -> str:
    status = str(case.status or "").casefold()
    case_type = str(case.case_type or "").casefold()
    if case.closed_date or any(
        marker in status for marker in ("closed", "resolved", "complied", "dismissed")
    ):
        return "closed"
    if (
        status.strip() == "lien"
        or "recorded lien" in status
        or "lien recorded" in status
    ):
        return "lien"
    if any(
        marker in status
        for marker in (
            "intent to lien",
            "itl ",
            "itl appealed",
            "itl noh",
        )
    ):
        return "itl"
    if (
        "special master" in status
        or "special magistrate" in status
        or "special master" in case_type
        or "special magistrate" in case_type
    ):
        return "special_master"
    if "notice of hearing" in status or "hearing" in status or "noh sent" in status:
        return "hearing"
    if "notice of violation" in status or status.strip() == "nov":
        return "nov"
    if "warning" in status or "courtesy" in status:
        return "warning"
    return "open"


def _matches(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _substantive_hazard(text: str, *, enriched: bool) -> str:
    if not enriched:
        return "unknown_hazard"
    # Life-safety conditions take precedence when multiple hazards are described.
    if _matches(
        text,
        r"\bunsafe\s+structures?\b",
        r"\blife[-\s]?safety\b",
        r"\bimminent\s+danger\b",
        r"\bfire\s+hazards?\b",
        r"\bstructural(?:ly)?\s+(?:failure|instability|unstable)\b",
        r"\b(?:roof|balcon(?:y|ies))\s+(?:collapse|collapsed|failing|failure)\b",
        r"\belectrical\s+(?:hazard|danger|failure|violation)\b",
        r"\b(?:inoperable|missing|failed?)\s+(?:fire\s+alarms?|smoke\s+detectors?|sprinklers?)\b",
        r"\b(?:blocked|inadequate|missing)\s+(?:egress|exits?)\b",
        r"\bno\s+(?:electricity|power)\b",
        r"\bgas\s+leaks?\b",
    ):
        return "unsafe_life_safety"
    if _matches(
        text,
        r"\bminimum\s+housing\b",
        r"\bmold\b",
        r"\bsewage\b",
        r"\bno\s+hot\s+water\b",
        r"\binfestation\b",
    ):
        return "minimum_housing"
    if _matches(text, r"\brecertification\b", r"\b(?:40|50)[-\s]?year\b"):
        return "recertification"
    if _matches(
        text,
        r"\bbuilding\s+without\s+(?:a\s+)?permit\b",
        r"\bwithout\s+(?:a\s+)?permit\b",
        r"\bwork\s+without\s+(?:a\s+)?permit\b",
        r"\bunpermitted\b",
    ):
        return "permit"
    if _matches(
        text,
        r"\bgraffiti\b",
        r"\bsigns?\b",
        r"\bdumpsters?\b",
        r"\btrash\b",
        r"\bgrass\b",
        r"\blandscap(?:e|ing)\b",
        r"\baddress\s+numbers?\b",
    ):
        return "cosmetic"
    if _matches(
        text,
        r"\badministrative\b",
        r"\bfees?\b",
        r"\bregistration\b",
        r"\bpaperwork\b",
        r"\brecords?\s+request\b",
    ):
        return "administrative"
    return "unknown_hazard"


def _cure_profile(hazard: str) -> tuple[str, str]:
    if hazard == "unknown_hazard":
        return "unknown", "unknown"
    if hazard in {"unsafe_life_safety", "recertification"}:
        return "complex", "high_or_unknown"
    if hazard in {"minimum_housing", "permit"}:
        return "moderate_to_complex", "moderate_or_unknown"
    return "simple_to_moderate", "low_or_unknown"


def classify_municipal_case(
    case: CodeCase,
    *,
    as_of: str | datetime,
    enriched: bool | None = None,
) -> MunicipalSeverity:
    as_of_date = _date(as_of) if isinstance(as_of, str) else as_of
    opened = _date(case.opened_date)
    age_days = (
        max(0, (as_of_date.date() - opened.date()).days)
        if as_of_date is not None and opened is not None
        else None
    )
    violations = active_violations(case.violations)
    enforcement_stage = _enforcement_stage(case)
    currently_active = enforcement_stage != "closed"
    text = _text(case, violations)
    if enriched is None:
        enriched = bool(case.description or case.project_name or violations)
    hazard = _substantive_hazard(text, enriched=enriched)
    cure_complexity, likely_cost = _cure_profile(hazard)

    stage_points = {
        "closed": 0,
        "open": 5,
        "warning": 8,
        "nov": 15,
        "hearing": 25,
        "special_master": 50,
        "itl": 35,
        "lien": 45,
    }[enforcement_stage]
    hazard_points = {
        "cosmetic": 0,
        "administrative": 5,
        "permit": 25,
        "recertification": 40,
        "minimum_housing": 50,
        "unsafe_life_safety": 65,
        "unknown_hazard": 0,
    }[hazard]
    cure_points = {
        "simple_to_moderate": 3,
        "moderate_to_complex": 12,
        "complex": 20,
        "unknown": 0,
    }[cure_complexity]
    repetition_score = 0.0
    if currently_active:
        if age_days is not None and age_days >= 365:
            repetition_score += 10
        if case.violation_count >= 2 or len(violations) >= 2:
            repetition_score += 5
        if "repeat" in text:
            repetition_score += 5
    repetition_score = min(20.0, repetition_score)
    score: float | None = (
        min(
            100.0,
            stage_points + hazard_points + cure_points + repetition_score,
        )
        if currently_active and enriched
        else 0.0
    )
    if currently_active and not enriched:
        score = None
    if hazard == "unsafe_life_safety":
        category = "unsafe_or_life_safety"
    elif enforcement_stage == "special_master":
        category = "special_master_escalation"
    elif enforcement_stage in {"itl", "lien"}:
        category = "intent_to_lien_or_lien"
    elif hazard == "unknown_hazard":
        category = "unknown_hazard"
    else:
        category = "minor_warning"

    reasons = (
        f"Enforcement stage: {enforcement_stage}.",
        f"Substantive hazard: {hazard}.",
        f"Cure complexity: {cure_complexity}; likely cost: {likely_cost}.",
        f"Age/repetition contribution: {repetition_score:.0f}/20.",
    )
    if not currently_active:
        reasons = (*reasons, "The case is closed or resolved and is not current risk.")

    return MunicipalSeverity(
        category=category,
        score=score,
        status_category=(
            "closed_or_resolved"
            if not currently_active
            else "escalated_open"
            if enforcement_stage in {"hearing", "special_master", "itl", "lien"}
            else "open_or_unknown"
        ),
        age_days=age_days,
        case_type=case.case_type or "Unknown case type",
        reasons=reasons,
        enforcement_stage=enforcement_stage,
        substantive_hazard=hazard,
        cure_complexity=cure_complexity,
        likely_cost=likely_cost,
        repetition_score=repetition_score,
        currently_active=currently_active,
        enrichment_state="confirmed_enriched" if enriched else "never_enriched",
    )


def severity_from_mapping(value: dict[str, Any]) -> MunicipalSeverity:
    return MunicipalSeverity(
        category=str(value.get("severity_category") or "minor_warning"),
        score=(
            float(value["severity_score"])
            if value.get("severity_score") is not None
            else None
        ),
        status_category=str(value.get("status_category") or "open_or_unknown"),
        age_days=(
            int(value["age_days"]) if value.get("age_days") is not None else None
        ),
        case_type=str(value.get("case_type") or "Unknown case type"),
        reasons=tuple(value.get("severity_reasons") or ()),
        enforcement_stage=str(value.get("enforcement_stage") or "open"),
        substantive_hazard=str(value.get("substantive_hazard") or "unknown_hazard"),
        cure_complexity=str(value.get("cure_complexity") or "simple_to_moderate"),
        likely_cost=str(value.get("likely_cost") or "unknown"),
        repetition_score=float(value.get("repetition_score") or 0),
        currently_active=bool(value.get("currently_active", True)),
        enrichment_state=str(
            value.get("enrichment_state")
            or (
                "confirmed_enriched"
                if value.get("severity_score") is not None
                else "never_enriched"
            )
        ),
    )
