from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureObservation:
    property_id: str
    observed_at: str
    features: dict[str, Any]


@dataclass(frozen=True)
class OutcomeLabel:
    property_id: str
    outcome_type: str
    occurred_at: str
    value: int


@dataclass(frozen=True)
class DatasetRow:
    property_id: str
    as_of: str
    features: dict[str, Any]
    label: int
    label_at: str


def build_dataset(
    observations: tuple[FeatureObservation, ...],
    outcomes: tuple[OutcomeLabel, ...],
    *,
    target: str,
) -> tuple[DatasetRow, ...]:
    by_property: dict[str, list[FeatureObservation]] = {}
    for observation in observations:
        by_property.setdefault(observation.property_id, []).append(observation)
    rows: list[DatasetRow] = []
    for outcome in sorted(outcomes, key=lambda item: item.occurred_at):
        if outcome.outcome_type != target:
            continue
        eligible = [
            item
            for item in by_property.get(outcome.property_id, ())
            if item.observed_at <= outcome.occurred_at
        ]
        if not eligible:
            continue
        observation = max(eligible, key=lambda item: item.observed_at)
        rows.append(
            DatasetRow(
                property_id=outcome.property_id,
                as_of=observation.observed_at,
                features=dict(observation.features),
                label=outcome.value,
                label_at=outcome.occurred_at,
            )
        )
    return tuple(rows)
