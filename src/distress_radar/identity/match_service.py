from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from distress_radar.domain.property import CanonicalProperty
from distress_radar.identity.address_normalizer import normalize_address
from distress_radar.identity.folio_resolver import normalize_folio


class MatchStatus(StrEnum):
    CONFIRMED = "confirmed"
    REVIEW = "review"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class MatchResult:
    status: MatchStatus
    method: str | None
    property_id: str | None
    confidence: float


class MatchService:
    def match(
        self,
        *,
        folio: str | None,
        address: str,
        municipality: str,
        candidates: tuple[CanonicalProperty, ...],
    ) -> MatchResult:
        normalized_folio = normalize_folio(folio)
        if normalized_folio:
            for candidate in candidates:
                if normalize_folio(candidate.folio) == normalized_folio:
                    return MatchResult(MatchStatus.CONFIRMED, "folio", candidate.property_id, 1.0)

        normalized_address = normalize_address(address)
        municipality_key = municipality.casefold().strip()
        eligible = tuple(
            candidate
            for candidate in candidates
            if candidate.municipality.casefold().strip() == municipality_key
        )
        for candidate in eligible:
            if normalize_address(candidate.address) == normalized_address:
                return MatchResult(
                    MatchStatus.CONFIRMED, "normalized_address", candidate.property_id, 0.95
                )

        best: tuple[float, CanonicalProperty] | None = None
        for candidate in eligible:
            ratio = SequenceMatcher(
                None, normalized_address, normalize_address(candidate.address)
            ).ratio()
            if ratio >= 0.85 and (best is None or ratio > best[0]):
                best = (ratio, candidate)
        if best:
            return MatchResult(MatchStatus.REVIEW, "fuzzy_address", best[1].property_id, best[0])
        return MatchResult(MatchStatus.UNMATCHED, None, None, 0)
