from __future__ import annotations

import re
from dataclasses import dataclass


def normalize_folio(value: str | None) -> str | None:
    normalized = "".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))
    return normalized or None


@dataclass(frozen=True)
class FolioResolution:
    folio: str | None
    conflicting: bool
    method: str | None


class FolioResolver:
    """Resolve only exact, unambiguous authoritative parcel identifiers."""

    def resolve(
        self, submitted: str | None, authoritative: tuple[str, ...]
    ) -> FolioResolution:
        submitted_folio = normalize_folio(submitted)
        candidates = tuple(
            dict.fromkeys(
                folio
                for value in authoritative
                if (folio := normalize_folio(value))
            )
        )
        if submitted_folio and submitted_folio in candidates:
            return FolioResolution(submitted_folio, False, "exact_authoritative")
        if submitted_folio and candidates:
            return FolioResolution(None, True, None)
        if len(candidates) == 1:
            return FolioResolution(candidates[0], False, "unique_authoritative")
        return FolioResolution(None, len(candidates) > 1, None)
