from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrainingReadiness:
    enabled: bool
    reason: str


def training_readiness(
    *, positive_labels: int, negative_labels: int, minimum_each: int = 25
) -> TrainingReadiness:
    if positive_labels < minimum_each or negative_labels < minimum_each:
        return TrainingReadiness(
            False,
            "ML disabled: insufficient labeled outcomes "
            f"({positive_labels} positive, {negative_labels} negative; "
            f"need at least {minimum_each} each).",
        )
    return TrainingReadiness(True, "Sufficient labels for offline evaluation; activation still requires baseline outperformance.")
