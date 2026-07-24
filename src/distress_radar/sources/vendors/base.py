from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from distress_radar.sources.base import (
    AuthorizationMode,
    SourceHealthState,
    SourceSpec,
)


class VendorDisabledError(RuntimeError):
    pass


class VendorAdapter:
    name = ""
    credential_environment_variable = ""

    def __init__(self, *, environ: Mapping[str, str]) -> None:
        self._credential = environ.get(self.credential_environment_variable)
        self.spec = SourceSpec(
            name=self.name,
            jurisdiction="South Florida",
            authorization_mode=AuthorizationMode.PAID_API,
            rate_limit_per_minute=None,
            request_timeout_seconds=30,
            max_retries=3,
            cache_ttl_seconds=86400,
            freshness_window_seconds=2_592_000,
            supports_pagination=True,
            preserves_raw_response=True,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._credential)

    @property
    def health_state(self) -> SourceHealthState:
        return SourceHealthState.DEGRADED if self.enabled else SourceHealthState.DISABLED

    def collect(self, folio: str) -> dict[str, Any]:
        if not self.enabled:
            raise VendorDisabledError(
                f"{self.name} is disabled; configure {self.credential_environment_variable}"
            )
        raise NotImplementedError(f"{self.name} live API collection is not implemented")

    def normalize_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "source": self.name, "source_mode": "fixture"}
