from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalOwner:
    owner_id: str
    display_name: str
    entity_type: str | None = None

    def __post_init__(self) -> None:
        if not self.owner_id or not self.display_name:
            raise ValueError("canonical owner requires identifier and display name")
