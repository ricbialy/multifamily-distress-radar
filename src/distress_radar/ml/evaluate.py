from __future__ import annotations

import math

from distress_radar.ml.dataset import DatasetRow


def time_based_split(
    rows: tuple[DatasetRow, ...], *, validation_fraction: float = 0.2
) -> tuple[tuple[DatasetRow, ...], tuple[DatasetRow, ...]]:
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    ordered = tuple(sorted(rows, key=lambda row: row.as_of))
    validation_count = max(1, math.ceil(len(ordered) * validation_fraction))
    if validation_count >= len(ordered):
        raise ValueError("at least two rows are required for a time split")
    return ordered[:-validation_count], ordered[-validation_count:]
