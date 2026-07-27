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
KNOWN_EVIDENCE_CLASSES = {
    "matrix_listing",
    "validated_address",
    "public_property_record",
    "municipal_code_case",
    "official_record",
    "tax_delinquency",
}
TRIGGER_EVIDENCE_CLASSES = {
    "listing_price_reduction": {"matrix_listing"},
    "municipal_status_change": {"municipal_code_case"},
}
SIGNAL_EVIDENCE_CLASSES = {
    "off_market_live_code_case": {"municipal_code_case"},
    "recorded_liens": {"official_record"},
    "lis_pendens": {"official_record"},
    "foreclosure": {"official_record"},
    "tax_delinquency": {"tax_delinquency"},
}
SIGNAL_SOURCES = {
    "off_market_live_code_case": {"hialeah_tyler_energov"},
    "recorded_liens": {"miami_dade_clerk_official_records"},
    "lis_pendens": {"miami_dade_clerk_official_records"},
    "foreclosure": {"miami_dade_clerk_official_records"},
    "tax_delinquency": {"authorized_tax_csv"},
}
EVIDENCE_CLASS_SOURCES = {
    "matrix_listing": {"matrix_csv"},
    "validated_address": {"miami_dade_property_point_view"},
    "public_property_record": {"miami_dade_property_point_view"},
    "municipal_code_case": {"hialeah_tyler_energov"},
    "official_record": {"miami_dade_clerk_official_records"},
    "tax_delinquency": {"authorized_tax_csv"},
}
TRIGGER_EVENT_FIELDS = {
    "new_record": {
        "event_type",
        "source_name",
        "signal_type",
    },
    "material_evidence_change": {
        "event_type",
        "source_name",
        "evidence_class",
    },
    "source_recovery": {
        "event_type",
        "source_name",
        "evidence_class",
        "previous_source_health_state",
        "recovery_state",
        "successful_recovery",
        "reevaluate_evidence_ids",
        "last_known_context",
    },
    "listing_price_reduction": {
        "event_type",
        "source_name",
        "evidence_class",
    },
    "municipal_status_change": {
        "event_type",
        "source_name",
        "evidence_class",
    },
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
        allowed_condition_fields = (
            {"field", "operator", "value"}
            if self.trigger_type in {"scheduled_recheck", "source_recovery"}
            else {"field", "operator"}
        )
        unsupported_condition_fields = sorted(
            set(self.recheck_condition) - allowed_condition_fields
        )
        if unsupported_condition_fields:
            raise ValueError(
                f"{self.trigger_type} does not permit condition fields: "
                + ", ".join(unsupported_condition_fields)
            )
        missing_condition_fields = sorted(
            {"field", "operator"} - set(self.recheck_condition)
        )
        if missing_condition_fields:
            raise ValueError(
                f"{self.trigger_type} requires condition fields: "
                + ", ".join(missing_condition_fields)
            )

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
        allowed_fields = TRIGGER_EVENT_FIELDS[self.trigger_type]
        supplied_fields = set(event)
        unsupported_fields = sorted(supplied_fields - allowed_fields)
        if unsupported_fields:
            raise ValueError(
                f"{self.trigger_type} does not permit semantic fields: "
                + ", ".join(unsupported_fields)
            )
        missing_fields = sorted(allowed_fields - supplied_fields)
        if missing_fields:
            raise ValueError(
                f"{self.trigger_type} requires semantic fields: "
                + ", ".join(missing_fields)
            )
        source_name = str(event.get("source_name") or "").strip()
        if not source_name:
            raise ValueError("event-driven watch must name an exact source")
        if event.get("event_type") != self.trigger_type:
            raise ValueError("event trigger type must match trigger_type")
        if self.recheck_at is not None:
            raise ValueError("event-driven watch cannot use recheck_at")
        watched_class = str(event.get("evidence_class") or "")
        if watched_class and watched_class not in KNOWN_EVIDENCE_CLASSES:
            raise ValueError(f"unknown evidence_class: {watched_class}")
        watched_signal = str(event.get("signal_type") or "")
        if watched_signal and watched_signal not in SIGNAL_EVIDENCE_CLASSES:
            raise ValueError(f"unknown signal_type: {watched_signal}")
        if self.trigger_type == "source_recovery":
            _specific(
                str(event.get("last_known_context") or ""),
                field="last_known_context",
            )
            previous_state = str(event.get("previous_source_health_state") or "")
            if previous_state not in {
                "degraded",
                "disabled",
                "unknown_failed",
                "unknown_not_run",
                "unknown_stale",
            }:
                raise ValueError(
                    "source_recovery requires an exact unavailable previous source state"
                )
            if event.get("recovery_state") != "healthy":
                raise ValueError("source_recovery must define healthy as recovery")
            _specific(
                str(event.get("successful_recovery") or ""),
                field="successful_recovery",
            )
            if tuple(event.get("reevaluate_evidence_ids") or ()) != self.evidence_ids:
                raise ValueError(
                    "source_recovery must name the exact evidence IDs to reevaluate"
                )
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


@dataclass(frozen=True)
class WatchEvidenceDescriptor:
    evidence_id: str
    property_id: str
    evidence_class: str
    source_name: str
    signal_types: tuple[str, ...] = ()
    signal_links: tuple[WatchSignalDescriptor, ...] = ()


@dataclass(frozen=True)
class WatchSignalDescriptor:
    signal_id: str
    signal_type: str
    property_id: str
    source_name: str
    status: str
    confirmation_status: str
    pilot_run_id: str | None
    observation_statuses: tuple[str, ...] = ()
    observations: tuple[WatchSignalObservationDescriptor, ...] = ()


@dataclass(frozen=True)
class WatchSignalObservationDescriptor:
    observation_id: str
    pilot_run_id: str
    status: str
    observed_at: str


@dataclass(frozen=True)
class WatchValidationOutcome:
    status: str
    reason: str
    evidence_states: dict[str, str]
    source_health: dict[str, str]
    evidence: tuple[WatchEvidenceDescriptor, ...]


def validate_watch(
    specification: WatchSpecification | None,
    *,
    created_at: str,
    reviewed_content_hash: str,
    property_id: str,
    evidence: tuple[WatchEvidenceDescriptor, ...],
    evidence_states: dict[str, str],
    source_health: dict[str, str],
    previous_source_health: dict[str, str],
    reviewed_hash_exists: bool,
) -> WatchValidationOutcome:
    def outcome(status: str, reason: str) -> WatchValidationOutcome:
        return WatchValidationOutcome(
            status,
            reason,
            evidence_states,
            source_health,
            evidence,
        )

    if specification is None:
        return outcome(
            "legacy_incomplete_watch",
            "The persisted legacy watch has no complete structured semantics.",
        )
    try:
        specification.validate_structure(created_at=created_at)
    except ValueError as exc:
        return outcome("invalid_structure", str(exc))
    if (
        len(reviewed_content_hash) != 64
        or any(character not in "0123456789abcdef" for character in reviewed_content_hash)
        or not reviewed_hash_exists
    ):
        return outcome(
            "invalid_reviewed_content_hash",
            "The watch is not tied to a persisted reviewed opportunity content hash.",
        )
    expected_ids = set(specification.evidence_ids)
    actual_ids = {item.evidence_id for item in evidence}
    if expected_ids != actual_ids or expected_ids != set(evidence_states):
        return outcome(
            "invalid_evidence_reference",
            "One or more supporting evidence IDs are missing or belong to another property.",
        )
    if not evidence or any(item.property_id != property_id for item in evidence):
        return outcome(
            "invalid_property_identity",
            "Every supporting evidence item must belong to the watched property.",
        )
    if any(
        item.evidence_class not in KNOWN_EVIDENCE_CLASSES for item in evidence
    ):
        return outcome(
            "invalid_evidence_class",
            "A supporting evidence class is not recognized by the watch model.",
        )

    relevant_sources = {item.source_name for item in evidence}
    unavailable = {
        source: source_health.get(source, "unknown_not_run")
        for source in relevant_sources
        if source_health.get(source) != "healthy"
    }
    if specification.trigger_type != "source_recovery" and unavailable:
        details = ", ".join(
            f"{source}={state}" for source, state in sorted(unavailable.items())
        )
        return outcome(
            "invalid_source_unavailable",
            f"Supporting source is unavailable in the current run: {details}.",
        )

    allowed_states = (
        {"current", "last_known"}
        if specification.trigger_type == "source_recovery"
        else {"current"}
    )
    if set(evidence_states.values()) - allowed_states:
        return outcome(
            "invalid_evidence_lifecycle",
            "Supporting evidence is missing, stale, resolved, or not currently eligible.",
        )

    event = specification.evidence_event_trigger or {}
    watched_source = str(event.get("source_name") or "")
    if watched_source and any(
        item.source_name != watched_source for item in evidence
    ):
        return outcome(
            "invalid_evidence_compatibility",
            "Every cited evidence item must come from the exact watched source.",
        )
    if specification.trigger_type != "scheduled_recheck":
        watched_class = str(event.get("evidence_class") or "")
        watched_signal = str(event.get("signal_type") or "")
        if watched_class:
            if watched_class not in KNOWN_EVIDENCE_CLASSES:
                return outcome(
                    "invalid_evidence_class",
                    f"Unknown watched evidence class: {watched_class}.",
                )
            if any(item.evidence_class != watched_class for item in evidence):
                return outcome(
                    "invalid_evidence_compatibility",
                    "Supporting evidence class does not match the watched evidence class.",
                )
        required_classes = TRIGGER_EVIDENCE_CLASSES.get(
            specification.trigger_type
        )
        if required_classes and any(
            item.evidence_class not in required_classes for item in evidence
        ):
            return outcome(
                "invalid_evidence_compatibility",
                "Supporting evidence is incompatible with the selected trigger type.",
            )
        if watched_signal:
            compatible_classes = SIGNAL_EVIDENCE_CLASSES.get(watched_signal)
            if not compatible_classes:
                return outcome(
                    "invalid_signal_type",
                    f"Unknown watched signal type: {watched_signal}.",
                )
            if any(
                item.evidence_class not in compatible_classes for item in evidence
            ):
                return outcome(
                    "invalid_evidence_compatibility",
                    "Supporting evidence class is incompatible with the watched signal type.",
                )
            if any(
                watched_signal not in item.signal_types for item in evidence
            ):
                return outcome(
                    "invalid_evidence_compatibility",
                    "Supporting evidence is not linked to the exact watched signal type.",
                )
            if watched_source not in SIGNAL_SOURCES[watched_signal]:
                return outcome(
                    "invalid_evidence_compatibility",
                    "Watched source is incompatible with the watched signal type.",
                )
            if any(
                not any(
                    link.signal_type == watched_signal
                    and link.property_id == property_id
                    and link.source_name == watched_source
                    for link in item.signal_links
                )
                for item in evidence
            ):
                return outcome(
                    "invalid_evidence_compatibility",
                    "Supporting evidence lacks the exact immutable signal relationship.",
                )
        if any(
            item.source_name
            not in EVIDENCE_CLASS_SOURCES.get(item.evidence_class, set())
            for item in evidence
        ):
            return outcome(
                "invalid_evidence_compatibility",
                "Supporting evidence source is incompatible with its evidence class.",
            )

    if specification.trigger_type == "source_recovery":
        declared_previous = str(event["previous_source_health_state"])
        current_state = source_health.get(watched_source, "unknown_not_run")
        observed_previous = previous_source_health.get(watched_source)
        observed_unavailable = (
            observed_previous if current_state == "healthy" else current_state
        )
        if observed_unavailable != declared_previous:
            return outcome(
                "invalid_source_recovery_state",
                "Declared previous source state does not match persisted source health.",
            )

    return outcome(
        "valid",
        "All watch structure, evidence, lifecycle, source-health, and compatibility checks passed.",
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
