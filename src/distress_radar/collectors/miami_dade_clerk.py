from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from distress_radar.config import CityConfig
from distress_radar.models import OfficialRecord, OfficialRecordCollectionResult, RawDocument


def _text(value: Any) -> str | None:
    return None if value is None or str(value).strip() == "" else str(value).strip()


def classify_document_type(value: Any) -> str:
    normalized = " ".join(str(value or "").upper().replace("_", " ").split())
    if normalized in {"LP", "LIS", "LIS PENDENS"} or "LIS PENDENS" in normalized:
        return "lis_pendens"
    if normalized in {"REL", "SAT", "CNL"} or "RELEASE" in normalized or "SATISFACTION" in normalized or "CANCEL" in normalized:
        return "release"
    if normalized in {"LIE", "LN"} or "LIEN" in normalized:
        return "lien"
    if "MORTGAGE" in normalized or normalized in {"MTG", "MOR"}:
        return "mortgage"
    return "other"


class MiamiDadeClerkCollector:
    def __init__(self, config: CityConfig) -> None:
        source = config.official_records_source
        if source is None or source.adapter != "miami_dade_clerk_api":
            raise ValueError(f"City '{config.slug}' has no Miami-Dade Clerk API source")
        if not source.enabled:
            raise ValueError(
                "Miami-Dade Clerk API source is disabled; enable it after obtaining a "
                "developer account and API units"
            )
        self.config, self.source = config, source
        self.auth_key = os.environ.get(source.auth_key_env)
        if not self.auth_key:
            raise ValueError(f"Set {source.auth_key_env} in the environment; do not put it in YAML")

    def collect(self, folios: list[str]) -> OfficialRecordCollectionResult:
        records: list[OfficialRecord] = []
        raw: list[RawDocument] = []
        for index, folio in enumerate(folios):
            if index:
                time.sleep(self.source.request_delay_seconds)
            payload, fetched_at = self._request(folio)
            raw.append(RawDocument("official_records", folio, self.source.documentation_url, fetched_at, payload))
            records.extend(self._parse(folio, payload, fetched_at))
        return OfficialRecordCollectionResult(tuple(records), tuple(raw))

    def _request(self, folio: str) -> tuple[dict[str, Any], str]:
        query = urllib.parse.urlencode({"parameter1": folio, "parameter2": "FN", "authKey": self.auth_key})
        request = urllib.request.Request(f"{self.source.base_url}?{query}", headers={"Accept": "application/xml", "User-Agent": "multifamily-distress-radar/0.4"})
        for attempt in range(self.source.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.source.timeout_seconds) as response:
                    payload = self._parse_xml(response.read())
                if str(payload.get("Status") or "").casefold() == "failed":
                    description = _text(payload.get("StatusDesc")) or "unspecified API error"
                    raise RuntimeError(f"Clerk API rejected the request: {description}")
                return payload, datetime.now(timezone.utc).isoformat()
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.source.max_retries:
                    raise RuntimeError(f"Clerk API request failed with HTTP {exc.code}") from exc
                time.sleep(min(8.0, 2 ** attempt))
        raise RuntimeError("Clerk API retry loop exhausted")

    @staticmethod
    def _parse_xml(raw: bytes) -> dict[str, Any]:
        root = ET.fromstring(raw)
        def name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]
        payload: dict[str, Any] = {}
        records: list[dict[str, Any]] = []
        for child in root:
            if name(child.tag) == "OfficialRecordList":
                for node in child:
                    records.append({name(field.tag): _text(field.text) for field in node})
            else:
                payload[name(child.tag)] = _text(child.text)
        payload["OfficialRecordList"] = records
        return payload

    def _parse(self, folio: str, payload: dict[str, Any], fetched_at: str) -> list[OfficialRecord]:
        container: Any = payload.get("OfficialRecordList", [])
        if isinstance(container, dict):
            container = container.get("OfficialRecords", container.get("OfficialRecord", []))
        if isinstance(container, dict):
            container = [container]
        if not isinstance(container, list):
            return []
        result: list[OfficialRecord] = []
        for item in container:
            if not isinstance(item, dict):
                continue
            instrument_id = "-".join(filter(None, [_text(item.get("CFN_YEAR")), _text(item.get("CFN_SEQ"))])) or _text(item.get("CFN_MASTER_ID"))
            original_instrument_id = "-".join(filter(None, [_text(item.get("ORIG_CFN_YEAR")), _text(item.get("ORIG_CFN_SEQ"))])) or None
            master_id = _text(item.get("CFN_MASTER_ID")) or instrument_id
            party_id = _text(item.get("PARTY_SEQUENCE")) or _text(item.get("REC_ROWID")) or _text(item.get("KEY"))
            record_id = "-".join(filter(None, [master_id, party_id]))
            if not record_id:
                record_id = str(abs(hash(json.dumps(item, sort_keys=True))))
            consideration = item.get("CONSIDERATION_1") or item.get("CONSIDERATION_2")
            try:
                consideration = float(str(consideration).replace(",", "")) if consideration not in (None, "") else None
            except ValueError:
                consideration = None
            result.append(OfficialRecord(
                self.config.slug, self.source.name, record_id, instrument_id,
                original_instrument_id, _text(item.get("LINK_DOCTYPE")), folio,
                _text(item.get("DOC_TYPE")), classify_document_type(item.get("DOC_TYPE")),
                _text(item.get("REC_DATE")), _text(item.get("DOC_DATE")),
                _text(item.get("FIRST_PARTY")), _text(item.get("SECOND_PARTY")),
                _text(item.get("CASE_NUM")), _text(item.get("STATUS")),
                _text(item.get("REC_BOOK")), _text(item.get("REC_PAGE")), consideration,
                self.source.documentation_url, fetched_at,
            ))
        return result
