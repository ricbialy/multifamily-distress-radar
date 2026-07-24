from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PropertySignal:
    signal_type: str
    value: Any
    observed_at: str
    source_record_id: str
