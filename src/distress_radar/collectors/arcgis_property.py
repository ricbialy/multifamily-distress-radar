from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from distress_radar.config import CityConfig, PropertySourceConfig
from distress_radar.models import PropertyCollectionResult, PropertyRecord, RawDocument
from distress_radar.normalize import clean_text, normalize_parcel

LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def optional_int(value: object) -> int | None:
    return None if value is None or value == "" else int(value)


def optional_float(value: object) -> float | None:
    return None if value is None or value == "" else float(value)


class ArcGisPropertyCollector:
    OUT_FIELDS = (
        "OBJECTID,FOLIO,TRUE_SITE_ADDR,TRUE_SITE_CITY,TRUE_SITE_ZIP_CODE,"
        "TRUE_OWNER1,TRUE_OWNER2,TRUE_MAILING_ADDR1,TRUE_MAILING_ADDR2,"
        "TRUE_MAILING_CITY,TRUE_MAILING_STATE,TRUE_MAILING_ZIP_CODE,"
        "DOR_CODE_CUR,DOR_DESC,UNIT_COUNT,YEAR_BUILT,FLOOR_COUNT,"
        "BUILDING_ACTUAL_AREA,LOT_SIZE,ASSESSED_VAL_CUR,DOS_1,PRICE_1"
    )

    def __init__(self, config: CityConfig) -> None:
        if config.property_source is None:
            raise ValueError(f"{config.slug} has no property source")
        if config.property_source.adapter != "arcgis_property":
            raise ValueError(f"Unsupported property adapter: {config.property_source.adapter}")
        self.city = config
        self.config: PropertySourceConfig = config.property_source
        self._last_request_at = 0.0

    def _request(self, url: str, params: dict[str, object]) -> dict[str, Any]:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        for attempt in range(self.config.max_retries + 1):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.config.request_delay_seconds:
                time.sleep(self.config.request_delay_seconds - elapsed)
            request = urllib.request.Request(
                full_url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "MultifamilyDistressRadar/0.2 (+public-record research)",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    self._last_request_at = time.monotonic()
                    result = json.loads(response.read().decode("utf-8"))
                    if "error" in result:
                        raise RuntimeError(f"ArcGIS error: {result['error']}")
                    return result
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= self.config.max_retries:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"HTTP {exc.code} from ArcGIS: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                self._last_request_at = time.monotonic()
                if attempt >= self.config.max_retries:
                    raise RuntimeError(f"ArcGIS request failed: {exc}") from exc
            delay = min(30.0, (2**attempt) + random.random())
            LOGGER.warning("Retrying ArcGIS in %.1fs", delay)
            time.sleep(delay)
        raise AssertionError("unreachable")

    def _where_clause(self) -> str:
        municipality = self.config.municipality.replace("'", "''")
        clauses = [
            f"UPPER(TRUE_SITE_CITY) = '{municipality.upper()}'",
            f"UNIT_COUNT >= {self.config.min_units}",
            f"UNIT_COUNT <= {self.config.max_units}",
        ]
        if self.config.dor_codes:
            escaped_codes = [code.replace("'", "''") for code in self.config.dor_codes]
            codes = ",".join("'" + code + "'" for code in escaped_codes)
            clauses.append(f"DOR_CODE_CUR IN ({codes})")
        return " AND ".join(clauses)

    def _parse(self, feature: dict[str, Any], fetched_at: str) -> PropertyRecord:
        attributes = feature.get("attributes") or {}
        geometry = feature.get("geometry") or {}
        folio = normalize_parcel(attributes.get("FOLIO"))
        if not folio:
            raise RuntimeError("Property feature is missing FOLIO")
        return PropertyRecord(
            city_slug=self.city.slug,
            source_name=self.config.name,
            folio=folio,
            address=clean_text(attributes.get("TRUE_SITE_ADDR")),
            city=clean_text(attributes.get("TRUE_SITE_CITY")),
            zip_code=clean_text(attributes.get("TRUE_SITE_ZIP_CODE")),
            owner_name=clean_text(attributes.get("TRUE_OWNER1")),
            owner_name_2=clean_text(attributes.get("TRUE_OWNER2")),
            mailing_address_1=clean_text(attributes.get("TRUE_MAILING_ADDR1")),
            mailing_address_2=clean_text(attributes.get("TRUE_MAILING_ADDR2")),
            mailing_city=clean_text(attributes.get("TRUE_MAILING_CITY")),
            mailing_state=clean_text(attributes.get("TRUE_MAILING_STATE")),
            mailing_zip=clean_text(attributes.get("TRUE_MAILING_ZIP_CODE")),
            dor_code=clean_text(attributes.get("DOR_CODE_CUR")),
            dor_description=clean_text(attributes.get("DOR_DESC")),
            unit_count=optional_int(attributes.get("UNIT_COUNT")),
            year_built=optional_int(attributes.get("YEAR_BUILT")),
            floor_count=optional_int(attributes.get("FLOOR_COUNT")),
            building_area=optional_float(attributes.get("BUILDING_ACTUAL_AREA")),
            lot_size=optional_float(attributes.get("LOT_SIZE")),
            assessed_value=optional_float(attributes.get("ASSESSED_VAL_CUR")),
            last_sale_date=clean_text(attributes.get("DOS_1")),
            last_sale_price=optional_float(attributes.get("PRICE_1")),
            latitude=optional_float(geometry.get("y")),
            longitude=optional_float(geometry.get("x")),
            source_url=self.config.dataset_url,
            fetched_at=fetched_at,
        )

    def collect(self, *, max_pages: int | None = None) -> PropertyCollectionResult:
        fetched_at = utc_now()
        metadata = self._request(self.config.layer_url, {"f": "json"})
        field_names = {field.get("name") for field in metadata.get("fields", [])}
        required = {"FOLIO", "TRUE_SITE_CITY", "DOR_CODE_CUR", "UNIT_COUNT"}
        missing = sorted(required - field_names)
        if missing:
            raise RuntimeError(f"ArcGIS property schema changed; missing: {', '.join(missing)}")

        raw_documents = [
            RawDocument(
                kind="property_schema",
                source_key="layer_0",
                source_url=self.config.layer_url,
                fetched_at=fetched_at,
                payload=metadata,
            )
        ]
        records: dict[str, PropertyRecord] = {}
        offset = 0
        page = 1
        query_url = f"{self.config.layer_url.rstrip('/')}/query"
        while True:
            response = self._request(
                query_url,
                {
                    "where": self._where_clause(),
                    "outFields": self.OUT_FIELDS,
                    "returnGeometry": "true",
                    "outSR": 4326,
                    "orderByFields": "OBJECTID",
                    "resultOffset": offset,
                    "resultRecordCount": self.config.page_size,
                    "f": "json",
                },
            )
            raw_documents.append(
                RawDocument(
                    kind="property_page",
                    source_key=str(page),
                    source_url=query_url,
                    fetched_at=fetched_at,
                    payload=response,
                )
            )
            features = response.get("features") or []
            for feature in features:
                record = self._parse(feature, fetched_at)
                records[record.folio] = record
            if (
                not response.get("exceededTransferLimit")
                or len(features) < self.config.page_size
                or (max_pages and page >= max_pages)
            ):
                break
            offset += len(features)
            page += 1

        return PropertyCollectionResult(
            records=tuple(records.values()), raw_documents=tuple(raw_documents)
        )
