from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


WATCH_TRIGGER_TYPES = {
    "scheduled_recheck",
    "new_record",
    "material_evidence_change",
    "source_recovery",
    "listing_price_reduction",
    "municipal_status_change",
}
WATCH_NEXT_ACTIONS = {
    "investigate_owner",
    "request_documents",
    "human_municipal_review",
    "manual_triage",
}
_GENERIC_TEXT = {
    "watch",
    "monitor",
    "check later",
    "keep an eye on it",
    "watch for changes",
}
_OPERATORS = {"equals", "changes", "decreases", "increases", "at_or_after"}


def _timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _specific(value: str, *, field: str) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) < 8 or normalized.casefold() in _GENERIC_TEXT:
        raise ValueError(f"{field} must be specific, not generic watch language")
    return normalized


@dataclass(frozen=True)
class WatchSpecification:
    watch_reason: str
    evidence_ids: tuple[str, ...]
    trigger_type: str
    recheck_condition: dict[str, Any]
    expected_next_action: str
    creator: str
    recheck_at: str | None = None
    evidence_event_trigger: dict[str, Any] | None = None

    def validate_structure(self, *, created_at: str) -> None:
        _specific(self.watch_reason, field="watch_reason")
        _specific(self.creator, field="creator")
        if not self.evidence_ids or any(not str(value).strip() for value in self.evidence_ids):
            raise ValueError("watch requires at least one supporting evidence_id")
        if self.trigger_type not in WATCH_TRIGGER_TYPES:
            raise ValueError(f"unsupported watch trigger_type: {self.trigger_type}")
        if self.expected_next_action not in WATCH_NEXT_ACTIONS:
            raise ValueError("watch expected_next_action is absent or unsupported")
        if not isinstance(self.recheck_condition, dict):
            raise ValueError("watch recheck_condition must be structured")
        field = str(self.recheck_condition.get("field") or "")
        operator = str(self.recheck_condition.get("operator") or "")
        if not field or operator not in _OPERATORS:
            raise ValueError("watch recheck_condition is not machine-evaluable")

        created = _timestamp(created_at, field="created_at")
        if self.trigger_type == "scheduled_recheck":
            if not self.recheck_at:
                raise ValueError("scheduled_recheck requires recheck_at")
            due = _timestamp(self.recheck_at, field="recheck_at")
            if due <= created:
                raise ValueError("scheduled recheck_at must be in the future")
            if (
                field != "current_time"
                or operator != "at_or_after"
                or self.recheck_condition.get("value") != self.recheck_at
            ):
                raise ValueError(
                    "scheduled recheck condition must compare current_time to recheck_at"
                )
            if self.evidence_event_trigger is not None:
                raise ValueError("scheduled_recheck cannot include an event trigger")
            return

        event = self.evidence_event_trigger
        if not isinstance(event, dict):
            raise ValueError("event-driven watch requires structured evidence_event_trigger")
        source_name = str(event.get("source_name") or "").strip()
        if not source_name:
            raise ValueError("event-driven watch must name an exact source")
        if event.get("event_type") != self.trigger_type:
            raise ValueError("event trigger type must match trigger_type")
        if self.recheck_at is not None:
            raise ValueError("event-driven watch cannot use recheck_at")
        if self.trigger_type == "source_recovery":
            _specific(
                str(event.get("last_known_context") or ""),
                field="last_known_context",
            )
        elif not (event.get("signal_type") or event.get("evidence_class")):
            raise ValueError(
                "event-driven watch must name a signal_type or evidence_class"
            )
        if self.trigger_type in {
            "material_evidence_change",
            "municipal_status_change",
        } and not event.get("evidence_class"):
            raise ValueError(f"{self.trigger_type} requires an evidence_class")
        required_conditions = {
            "new_record": ("record_count", "increases"),
            "material_evidence_change": ("content_hash", "changes"),
            "source_recovery": ("source_health", "equals"),
            "listing_price_reduction": ("list_price", "decreases"),
            "municipal_status_change": ("municipal_status", "changes"),
        }
        required_field, required_operator = required_conditions[self.trigger_type]
        if field != required_field or operator != required_operator:
            raise ValueError(
                f"{self.trigger_type} requires condition "
                f"{required_field} {required_operator}"
            )
        if (
            self.trigger_type == "source_recovery"
            and self.recheck_condition.get("value") != "healthy"
        ):
            raise ValueError("source_recovery condition must target healthy")


def watch_validation_status(
    specification: WatchSpecification | None,
    *,
    created_at: str,
    evidence_states: dict[str, str],
) -> str:
    if specification is None:
        return "legacy_incomplete_watch"
    try:
        specification.validate_structure(created_at=created_at)
    except ValueError:
        return "invalid_watch"
    if set(specification.evidence_ids) != set(evidence_states):
        return "invalid_watch"
    allowed = (
        {"current", "last_known"}
        if specification.trigger_type == "source_recovery"
        else {"current"}
    )
    return (
        "valid"
        if evidence_states and set(evidence_states.values()) <= allowed
        else "invalid_watch"
    )


def watch_specification_from_mapping(
    row: Any,
) -> WatchSpecification | None:
    if not row or not row["watch_reason"]:
        return None
    return WatchSpecification(
        watch_reason=str(row["watch_reason"]),
        evidence_ids=tuple(json.loads(row["watch_evidence_ids_json"] or "[]")),
        trigger_type=str(row["watch_trigger_type"] or ""),
        recheck_condition=json.loads(row["watch_recheck_condition_json"] or "{}"),
        recheck_at=row["watch_recheck_at"],
        evidence_event_trigger=(
            json.loads(row["watch_event_trigger_json"])
            if row["watch_event_trigger_json"]
            else None
        ),
        expected_next_action=str(row["watch_expected_next_action"] or ""),
        creator=str(row["creator"] or ""),
    )
