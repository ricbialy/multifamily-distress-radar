from __future__ import annotations

import copy
import json
import logging
import random
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from distress_radar.collectors.base import Collector
from distress_radar.config import CityConfig
from distress_radar.models import CodeCase, CollectionResult, RawDocument
from distress_radar.normalize import clean_text, first_present, normalize_parcel

LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: int,
        max_retries: int,
        request_delay_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.request_delay_seconds = request_delay_seconds
        self.headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "MultifamilyDistressRadar/0.2 (+public-record research)",
        }
        self._last_request_at = 0.0

    def set_tenant(self, tenant: dict[str, Any]) -> None:
        self.headers.update(
            {
                "tenantId": str(tenant["TenantID"]),
                "tenantName": str(tenant["TenantName"]),
                "Tyler-TenantUrl": str(tenant["TenantUrl"]),
            }
        )

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_delay_seconds:
                time.sleep(self.request_delay_seconds - elapsed)
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    self._last_request_at = time.monotonic()
                    result = json.loads(response.read().decode("utf-8"))
                    if result.get("Success") is False:
                        raise RuntimeError(result.get("ErrorMessage") or f"API failure at {path}")
                    return result
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= self.max_retries:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                self._last_request_at = time.monotonic()
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Request failed for {url}: {exc}") from exc

            delay = min(30.0, (2**attempt) + random.random())
            LOGGER.warning("Retrying %s in %.1fs", url, delay)
            time.sleep(delay)

        raise AssertionError("unreachable")


class TylerEnerGovCollector(Collector):
    SEARCH_MODULE_CODE_CASE = 5

    def __init__(self, config: CityConfig, http: JsonHttpClient | None = None) -> None:
        if not config.source.base_url:
            raise ValueError(f"{config.slug} has no source base URL")
        self.config = config
        self.http = http or JsonHttpClient(
            config.source.base_url,
            timeout_seconds=config.source.timeout_seconds,
            max_retries=config.source.max_retries,
            request_delay_seconds=config.source.request_delay_seconds,
        )
        self._prepared = False
        self._tenant: dict[str, Any] | None = None
        self._status_response: dict[str, Any] | None = None
        self._criteria_response: dict[str, Any] | None = None

    def _api_url(self, path: str) -> str:
        return f"{self.config.source.base_url.rstrip('/')}/api/{path.lstrip('/')}"

    def prepare(self) -> dict[str, Any]:
        if self._prepared:
            return self._tenant or {}
        response = self.http.request_json("GET", "tenants/gettenantslist")
        tenants = [tenant for tenant in response.get("Result", []) if tenant.get("IsActive")]
        if not tenants:
            raise RuntimeError("EnerGov returned no active public tenant")
        tenant = tenants[0]
        setter = getattr(self.http, "set_tenant", None)
        if setter:
            setter(tenant)
        self._tenant = tenant
        self._prepared = True
        return tenant

    def _status_map(self) -> dict[str, str]:
        self.prepare()
        if self._status_response is None:
            self._status_response = self.http.request_json(
                "GET", "energov/codecases/codecase/status"
            )
        return {
            str(clean_text(item["Name"]) or item["Name"]): str(item["CodeCaseStatusId"])
            for item in self._status_response.get("Result", [])
        }

    def list_statuses(self) -> list[str]:
        return sorted(self._status_map(), key=str.casefold)

    def _criteria_template(self) -> dict[str, Any]:
        if self._criteria_response is None:
            self._criteria_response = self.http.request_json("GET", "energov/search/criteria")
        result = self._criteria_response.get("Result")
        if not isinstance(result, dict) or "CodeCaseCriteria" not in result:
            raise RuntimeError("EnerGov search criteria schema changed")
        return result

    def _search_page(
        self,
        template: dict[str, Any],
        status_id: str,
        page_number: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        page_size = self.config.source.page_size
        payload = copy.deepcopy(template)
        payload.update(
            {
                "SearchModule": self.SEARCH_MODULE_CODE_CASE,
                "FilterModule": self.SEARCH_MODULE_CODE_CASE,
                "PageNumber": page_number,
                "PageSize": page_size,
                "SortBy": "CaseNumber.keyword",
                "SortAscending": True,
            }
        )
        payload["CodeCaseCriteria"].update(
            {
                "CodeCaseTypeId": "none",
                "CodeCaseStatusId": status_id,
                "PageNumber": page_number,
                "PageSize": page_size,
                "SortBy": "CaseNumber.keyword",
                "SortAscending": True,
            }
        )
        response = self.http.request_json("POST", "energov/search/search", payload)
        result = response.get("Result")
        if not isinstance(result, dict) or "EntityResults" not in result:
            raise RuntimeError("EnerGov search response schema changed")
        return response, result

    def _detail(self, record_id: str) -> dict[str, Any]:
        return self.http.request_json("GET", f"energov/codecases/{record_id}")

    def _violations(self, record_id: str) -> dict[str, Any]:
        criteria = {
            "PageNumber": 1,
            "PageSize": 1000,
            "SortField": "",
            "IsSortedInAscendingOrder": True,
            "ModuleId": self.SEARCH_MODULE_CODE_CASE,
            "EntityId": record_id,
        }
        return self.http.request_json("POST", "energov/entity/violations/search", criteria)

    def _record_url(self, record_id: str) -> str:
        template = self.config.source.record_url_template
        return template.format(record_id=record_id) if template else self.config.source.public_search_url or ""

    def _parse_case(
        self,
        search_record: dict[str, Any],
        *,
        fetched_at: str,
        detail: dict[str, Any] | None,
        violations: Sequence[dict[str, Any]],
    ) -> CodeCase:
        detail = detail or {}
        record_id = str(search_record.get("CaseId") or detail.get("CodeCaseId") or "")
        if not record_id:
            raise RuntimeError("EnerGov record is missing CaseId")
        return CodeCase(
            city_slug=self.config.slug,
            source_name=self.config.source.name,
            source_record_id=record_id,
            case_number=str(search_record.get("CaseNumber") or detail.get("CaseNumber") or ""),
            case_type=first_present(detail.get("CodeCaseType"), search_record.get("CaseType")),
            status=first_present(detail.get("StatusName"), search_record.get("CaseStatus")),
            opened_date=first_present(
                detail.get("OpenedDate"), search_record.get("OpenedDate"), search_record.get("ApplyDate")
            ),
            closed_date=first_present(detail.get("ClosedDate"), search_record.get("ClosedDate")),
            address=first_present(detail.get("MainAddress"), search_record.get("AddressDisplay")),
            parcel_number=normalize_parcel(
                detail.get("MainParcelNumber") or search_record.get("MainParcel")
            ),
            description=first_present(detail.get("Description"), search_record.get("Description")),
            project_name=first_present(detail.get("ProjectName"), search_record.get("ProjectName")),
            assigned_to=clean_text(detail.get("AssignedTo")),
            violation_count=len(violations),
            violations=tuple(dict(item) for item in violations),
            source_url=self._record_url(record_id),
            fetched_at=fetched_at,
        )

    def collect(
        self,
        statuses: Sequence[str],
        *,
        max_pages: int | None = None,
        fetch_details: bool = True,
        include_violations: bool = False,
    ) -> CollectionResult:
        tenant = self.prepare()
        fetched_at = utc_now()
        raw_documents: list[RawDocument] = [
            RawDocument(
                kind="tenant",
                source_key=str(tenant.get("TenantID", "unknown")),
                source_url=self._api_url("tenants/gettenantslist"),
                fetched_at=fetched_at,
                payload={"Result": [tenant], "Success": True},
            )
        ]
        status_map = self._status_map()
        unknown = sorted(set(statuses) - set(status_map), key=str.casefold)
        if unknown:
            raise ValueError(
                f"Unknown EnerGov status(es): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(status_map, key=str.casefold))}"
            )
        criteria = self._criteria_template()
        raw_documents.extend(
            [
                RawDocument(
                    kind="status_catalog",
                    source_key="code_case_statuses",
                    source_url=self._api_url("energov/codecases/codecase/status"),
                    fetched_at=fetched_at,
                    payload=self._status_response or {},
                ),
                RawDocument(
                    kind="search_schema",
                    source_key="code_case_criteria",
                    source_url=self._api_url("energov/search/criteria"),
                    fetched_at=fetched_at,
                    payload=self._criteria_response or {},
                ),
            ]
        )
        records_by_id: dict[str, dict[str, Any]] = {}

        for status in statuses:
            page_number = 1
            while True:
                response, result = self._search_page(criteria, status_map[status], page_number)
                raw_documents.append(
                    RawDocument(
                        kind="search_page",
                        source_key=f"{status}:{page_number}",
                        source_url=self._api_url("energov/search/search"),
                        fetched_at=fetched_at,
                        payload=response,
                    )
                )
                for item in result.get("EntityResults", []):
                    record_id = str(item.get("CaseId") or "")
                    if record_id:
                        records_by_id[record_id] = item
                total_pages = int(result.get("TotalPages") or 0)
                if page_number >= total_pages or (max_pages and page_number >= max_pages):
                    break
                page_number += 1

        normalized: list[CodeCase] = []
        for index, (record_id, search_record) in enumerate(records_by_id.items(), start=1):
            LOGGER.info("Normalizing case %s/%s: %s", index, len(records_by_id), record_id)
            detail_result: dict[str, Any] | None = None
            violation_rows: list[dict[str, Any]] = []
            if fetch_details:
                detail_response = self._detail(record_id)
                detail_result = detail_response.get("Result") or {}
                raw_documents.append(
                    RawDocument(
                        kind="case_detail",
                        source_key=record_id,
                        source_url=self._api_url(f"energov/codecases/{record_id}"),
                        fetched_at=fetched_at,
                        payload=detail_response,
                    )
                )
            if include_violations:
                violation_response = self._violations(record_id)
                raw_result = violation_response.get("Result") or []
                if isinstance(raw_result, list):
                    violation_rows = [item for item in raw_result if isinstance(item, dict)]
                raw_documents.append(
                    RawDocument(
                        kind="violations",
                        source_key=record_id,
                        source_url=self._api_url("energov/entity/violations/search"),
                        fetched_at=fetched_at,
                        payload=violation_response,
                    )
                )
            normalized.append(
                self._parse_case(
                    search_record,
                    fetched_at=fetched_at,
                    detail=detail_result,
                    violations=violation_rows,
                )
            )

        return CollectionResult(
            records=tuple(normalized),
            raw_documents=tuple(raw_documents),
            requested_statuses=tuple(statuses),
        )

    def enrich_records(
        self,
        records: Sequence[CodeCase],
        *,
        include_violations: bool = True,
    ) -> CollectionResult:
        """Fetch detail tabs for already-collected cases without repeating searches."""
        tenant = self.prepare()
        fetched_at = utc_now()
        raw_documents: list[RawDocument] = [
            RawDocument(
                kind="tenant",
                source_key=str(tenant.get("TenantID", "unknown")),
                source_url=self._api_url("tenants/gettenantslist"),
                fetched_at=fetched_at,
                payload={"Result": [tenant], "Success": True},
            )
        ]
        enriched: list[CodeCase] = []
        for index, record in enumerate(records, start=1):
            LOGGER.info(
                "Enriching case %s/%s: %s", index, len(records), record.source_record_id
            )
            detail_response = self._detail(record.source_record_id)
            detail_result = detail_response.get("Result") or {}
            raw_documents.append(
                RawDocument(
                    kind="case_detail",
                    source_key=record.source_record_id,
                    source_url=self._api_url(
                        f"energov/codecases/{record.source_record_id}"
                    ),
                    fetched_at=fetched_at,
                    payload=detail_response,
                )
            )
            violation_rows: list[dict[str, Any]] = [dict(item) for item in record.violations]
            if include_violations:
                violation_response = self._violations(record.source_record_id)
                raw_result = violation_response.get("Result") or []
                if isinstance(raw_result, list):
                    violation_rows = [item for item in raw_result if isinstance(item, dict)]
                raw_documents.append(
                    RawDocument(
                        kind="violations",
                        source_key=record.source_record_id,
                        source_url=self._api_url("energov/entity/violations/search"),
                        fetched_at=fetched_at,
                        payload=violation_response,
                    )
                )
            search_record = {
                "CaseId": record.source_record_id,
                "CaseNumber": record.case_number,
                "CaseType": record.case_type,
                "CaseStatus": record.status,
                "OpenedDate": record.opened_date,
                "ClosedDate": record.closed_date,
                "AddressDisplay": record.address,
                "MainParcel": record.parcel_number,
                "Description": record.description,
                "ProjectName": record.project_name,
            }
            enriched.append(
                self._parse_case(
                    search_record,
                    fetched_at=fetched_at,
                    detail=detail_result,
                    violations=violation_rows,
                )
            )
        return CollectionResult(
            records=tuple(enriched),
            raw_documents=tuple(raw_documents),
            requested_statuses=tuple(
                sorted({record.status or "unknown" for record in records}, key=str.casefold)
            ),
        )
