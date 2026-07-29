from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from distress_radar.domain.listing import ListingSnapshot
from distress_radar.identity.folio_resolver import normalize_folio
from distress_radar.sources.base import (
    AuthorizationMode,
    CollectionError,
    CollectionErrorKind,
    SourceSpec,
)

BRIDGE_RESO_BASE_URL = "https://api.bridgedataoutput.com/api/v2/OData"
_DATASET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class BridgeSchemaChangedError(ValueError):
    pass


class BridgeApiError(RuntimeError):
    def __init__(self, error: CollectionError) -> None:
        self.error = error
        super().__init__(f"Bridge API request failed: {error.message}")


@dataclass(frozen=True)
class BridgeCollectionResult:
    listings: tuple[ListingSnapshot, ...]
    raw_pages: tuple[dict[str, Any], ...]
    synthetic: bool = True


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number(value: object, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise BridgeSchemaChangedError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BridgeSchemaChangedError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise BridgeSchemaChangedError(f"{field} must be finite")
    return result


def _integer(value: object, field: str) -> int | None:
    number = _number(value, field)
    if number is None:
        return None
    if not number.is_integer():
        raise BridgeSchemaChangedError(f"{field} must be an integer")
    return int(number)


def _address(record: dict[str, Any]) -> str | None:
    unparsed = _text(record.get("UnparsedAddress"))
    if unparsed:
        return unparsed
    parts = (
        record.get("StreetNumber"),
        record.get("StreetDirPrefix"),
        record.get("StreetName"),
        record.get("StreetSuffix"),
        record.get("StreetDirSuffix"),
        record.get("UnitNumber"),
    )
    return _text(" ".join(str(part).strip() for part in parts if _text(part)))


def _property_class(record: dict[str, Any]) -> str | None:
    values = tuple(
        value
        for value in (
            _text(record.get("PropertyType")),
            _text(record.get("PropertySubType")),
        )
        if value
    )
    return " / ".join(dict.fromkeys(values)) or None


def _http_error(status: int) -> CollectionError:
    if status in {401, 403}:
        kind = (
            CollectionErrorKind.AUTHENTICATION
            if status == 401
            else CollectionErrorKind.BLOCKED
        )
        return CollectionError(kind, f"HTTP {status}", False)
    if status == 429:
        return CollectionError(CollectionErrorKind.RATE_LIMIT, "HTTP 429", True)
    if status == 408:
        return CollectionError(CollectionErrorKind.TIMEOUT, "HTTP 408", True)
    if 400 <= status < 500:
        return CollectionError(
            CollectionErrorKind.VALIDATION,
            f"HTTP {status}",
            False,
        )
    return CollectionError(
        CollectionErrorKind.NETWORK,
        f"HTTP {status}",
        status >= 500,
    )


class BridgeResoCollector:
    source_name = "bridge_reso_test"

    def __init__(
        self,
        *,
        dataset_id: str,
        token: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not _DATASET_ID.fullmatch(dataset_id):
            raise ValueError("Bridge dataset must contain only letters, numbers, _ or -")
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise ValueError("BRIDGE_API_TOKEN contains invalid control characters")
        if not token.strip():
            raise ValueError("BRIDGE_API_TOKEN is required")
        self.dataset_id = dataset_id
        self._token = token.strip()
        self._opener = opener
        self.spec = SourceSpec(
            name=self.source_name,
            jurisdiction="MLS-defined",
            authorization_mode=AuthorizationMode.AUTHORIZED_API,
            rate_limit_per_minute=None,
            request_timeout_seconds=30,
            max_retries=0,
            cache_ttl_seconds=0,
            freshness_window_seconds=86_400,
            supports_pagination=True,
            preserves_raw_response=True,
        )

    @property
    def resource_url(self) -> str:
        dataset = urllib.parse.quote(self.dataset_id, safe="")
        return f"{BRIDGE_RESO_BASE_URL}/{dataset}/Property"

    def _request_page(self, *, top: int, skip: int) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "$top": top,
                "$skip": skip,
                "$orderby": "BridgeModificationTimestamp asc,ListingKey asc",
            }
        )
        request = urllib.request.Request(
            f"{self.resource_url}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "multifamily-distress-radar/0.15",
            },
        )
        try:
            with self._opener(
                request, timeout=self.spec.request_timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BridgeApiError(_http_error(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BridgeApiError(
                CollectionError(
                    CollectionErrorKind.NETWORK,
                    type(exc).__name__,
                    True,
                )
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeSchemaChangedError(
                "Bridge response is not valid JSON"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
            raise BridgeSchemaChangedError(
                "Bridge response is missing the RESO value collection"
            )
        if not all(isinstance(record, dict) for record in payload["value"]):
            raise BridgeSchemaChangedError("Bridge value collection contains a non-object")
        return payload

    def _normalize(
        self, record: dict[str, Any], *, fetched_at: str
    ) -> ListingSnapshot:
        listing_key = _text(record.get("ListingKey"))
        address = _address(record)
        if not listing_key:
            raise BridgeSchemaChangedError("Bridge Property is missing ListingKey")
        if not address:
            raise BridgeSchemaChangedError("Bridge Property is missing a usable address")
        source_key = urllib.parse.quote(listing_key, safe="")
        return ListingSnapshot(
            source_record_id=listing_key,
            source_name=self.source_name,
            fetched_at=fetched_at,
            address=address,
            municipality=_text(record.get("City")) or "",
            folio=normalize_folio(_text(record.get("ParcelNumber"))),
            property_class=_property_class(record),
            status=_text(record.get("StandardStatus"))
            or _text(record.get("MlsStatus")),
            list_price=_number(record.get("ListPrice"), "ListPrice"),
            dom=_integer(record.get("DaysOnMarket"), "DaysOnMarket"),
            cdom=_integer(
                record.get("CumulativeDaysOnMarket"),
                "CumulativeDaysOnMarket",
            ),
            units=_integer(
                record.get("NumberOfUnitsTotal"),
                "NumberOfUnitsTotal",
            ),
            noi=_number(record.get("NetOperatingIncome"), "NetOperatingIncome"),
            rents=_number(
                record.get("GrossScheduledIncome"),
                "GrossScheduledIncome",
            ),
            expenses=_number(record.get("OperatingExpense"), "OperatingExpense"),
            remarks=_text(record.get("PublicRemarks")),
            source_url=f"{self.resource_url}('{source_key}')",
            state=_text(record.get("StateOrProvince")),
            postal_code=_text(record.get("PostalCode")),
            raw_payload=dict(record),
            synthetic=True,
        )

    def collect(
        self,
        *,
        top: int = 20,
        max_pages: int = 1,
        fetched_at: str | None = None,
    ) -> BridgeCollectionResult:
        if isinstance(top, bool) or not 1 <= top <= 200:
            raise ValueError("Bridge top must be between 1 and 200")
        if isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("Bridge max_pages must be at least one")
        if top * max_pages > 10_000:
            raise ValueError(
                "Bridge ordinary pagination is limited to 10,000 records"
            )
        observed_at = fetched_at or datetime.now(UTC).isoformat()
        raw_pages: list[dict[str, Any]] = []
        listings: list[ListingSnapshot] = []
        seen_keys: set[str] = set()
        for page in range(max_pages):
            payload = self._request_page(top=top, skip=page * top)
            raw_pages.append(payload)
            records = payload["value"]
            for record in records:
                listing = self._normalize(record, fetched_at=observed_at)
                listing_key = str(record["ListingKey"])
                if listing_key not in seen_keys:
                    seen_keys.add(listing_key)
                    listings.append(listing)
            if len(records) < top:
                break
        return BridgeCollectionResult(tuple(listings), tuple(raw_pages))
