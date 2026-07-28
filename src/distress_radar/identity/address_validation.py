from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from distress_radar.identity.address_normalizer import normalize_address
from distress_radar.identity.folio_resolver import normalize_folio
from distress_radar.models import PropertyRecord


class AddressValidationStatus(StrEnum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class AddressCandidate:
    source: str
    street: str
    municipality: str
    state: str | None = None
    postal_code: str | None = None

    @property
    def address(self) -> str:
        return format_address(
            self.street, self.municipality, self.state, self.postal_code
        )

    @property
    def normalized_key(self) -> str:
        return "|".join(
            (
                normalize_address(self.street),
                normalize_address(self.municipality),
                (self.state or "").casefold().strip(),
                (self.postal_code or "").casefold().strip(),
            )
        )


@dataclass(frozen=True)
class AddressValidationResult:
    status: AddressValidationStatus
    address: str
    source: str | None
    source_record_id: str | None
    source_url: str | None
    checked_at: str
    details: str
    latitude: float | None = None
    longitude: float | None = None

    @property
    def is_authoritatively_verified(self) -> bool:
        return self.status == AddressValidationStatus.VERIFIED


def format_address(
    street: str,
    municipality: str,
    state: str | None,
    postal_code: str | None,
) -> str:
    locality = ", ".join(part for part in (municipality, state) if part)
    if postal_code:
        locality = f"{locality} {postal_code}".strip()
    return ", ".join(part for part in (street, locality) if part)


def _matches_county(
    candidate: AddressCandidate, record: PropertyRecord
) -> bool:
    if normalize_address(candidate.street) != normalize_address(record.address):
        return False
    if record.city and normalize_address(candidate.municipality) != normalize_address(
        record.city
    ):
        return False
    return not (
        record.zip_code
        and candidate.postal_code
        and record.zip_code.strip() != candidate.postal_code.strip()
    )


class CountyAddressValidator:
    def __init__(self, records: tuple[PropertyRecord, ...]) -> None:
        self._by_folio = {
            normalized: record
            for record in records
            if (normalized := normalize_folio(record.folio))
        }

    def validate(
        self,
        *,
        folio: str | None,
        candidates: tuple[AddressCandidate, ...],
        checked_at: str,
    ) -> AddressValidationResult:
        if not candidates:
            raise ValueError("At least one address candidate is required")

        normalized_folio = normalize_folio(folio)
        county = self._by_folio.get(normalized_folio) if normalized_folio else None
        if county and county.address:
            county_address = format_address(
                county.address,
                county.city or candidates[0].municipality,
                "FL",
                county.zip_code,
            )
            matching = tuple(
                candidate for candidate in candidates if _matches_county(candidate, county)
            )
            status = (
                AddressValidationStatus.VERIFIED
                if matching
                else AddressValidationStatus.MISMATCH
            )
            if matching:
                details = (
                    "Exact folio and normalized site address match the authoritative "
                    "county property record."
                )
            else:
                supplied = "; ".join(
                    f"{candidate.source}: {candidate.address}" for candidate in candidates
                )
                details = (
                    "Exact folio found, but supplied address differs from the "
                    f"authoritative county site address ({supplied.casefold()})."
                )
            return AddressValidationResult(
                status=status,
                address=county_address,
                source=county.source_name,
                source_record_id=normalized_folio,
                source_url=county.source_url,
                checked_at=checked_at,
                details=details,
                latitude=county.latitude,
                longitude=county.longitude,
            )

        candidates_by_key: dict[str, list[AddressCandidate]] = {}
        for candidate in candidates:
            candidates_by_key.setdefault(candidate.normalized_key, []).append(
                candidate
            )
        corroborated = next(
            (
                matching
                for matching in candidates_by_key.values()
                if len({candidate.source for candidate in matching}) >= 2
            ),
            None,
        )
        if corroborated:
            corroborated_sources = sorted(
                {candidate.source for candidate in corroborated}
            )
            return AddressValidationResult(
                status=AddressValidationStatus.CORROBORATED,
                address=corroborated[0].address,
                source=" + ".join(corroborated_sources),
                source_record_id=normalized_folio,
                source_url=None,
                checked_at=checked_at,
                details=(
                    "Independent input sources agree after normalization, but no "
                    "authoritative county record was available."
                ),
            )

        return AddressValidationResult(
            status=AddressValidationStatus.UNVERIFIED,
            address=candidates[0].address,
            source=candidates[0].source,
            source_record_id=normalized_folio,
            source_url=None,
            checked_at=checked_at,
            details=(
                "No authoritative county record was available and fewer than two "
                "independent sources supplied the same address."
            ),
        )
