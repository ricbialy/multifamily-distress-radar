from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AuthorizationMode(StrEnum):
    PUBLIC = "public"
    AUTHORIZED_EXPORT = "authorized_export"
    PAID_API = "paid_api"
    MANUAL = "manual"
    DISABLED = "disabled"


class SourceHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    AUTHENTICATION_REQUIRED = "authentication_required"
    SCHEMA_CHANGED = "schema_changed"
    STALE = "stale"
    DISABLED = "disabled"


class CollectionErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    BLOCKED = "blocked"
    SCHEMA = "schema"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    VALIDATION = "validation"


_ERROR_HEALTH = {
    CollectionErrorKind.AUTHENTICATION: SourceHealthState.AUTHENTICATION_REQUIRED,
    CollectionErrorKind.BLOCKED: SourceHealthState.BLOCKED,
    CollectionErrorKind.SCHEMA: SourceHealthState.SCHEMA_CHANGED,
    CollectionErrorKind.TIMEOUT: SourceHealthState.DEGRADED,
    CollectionErrorKind.RATE_LIMIT: SourceHealthState.DEGRADED,
    CollectionErrorKind.NETWORK: SourceHealthState.DEGRADED,
    CollectionErrorKind.VALIDATION: SourceHealthState.SCHEMA_CHANGED,
}


@dataclass(frozen=True)
class CollectionError:
    kind: CollectionErrorKind
    message: str
    retryable: bool

    @property
    def health_state(self) -> SourceHealthState:
        return _ERROR_HEALTH[self.kind]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    jurisdiction: str
    authorization_mode: AuthorizationMode
    rate_limit_per_minute: int | None
    request_timeout_seconds: int
    max_retries: int
    cache_ttl_seconds: int
    freshness_window_seconds: int
    supports_pagination: bool
    preserves_raw_response: bool

    @classmethod
    def fixture(cls, name: str, jurisdiction: str) -> SourceSpec:
        return cls(
            name=name,
            jurisdiction=jurisdiction,
            authorization_mode=AuthorizationMode.AUTHORIZED_EXPORT,
            rate_limit_per_minute=None,
            request_timeout_seconds=30,
            max_retries=0,
            cache_ttl_seconds=0,
            freshness_window_seconds=86400,
            supports_pagination=False,
            preserves_raw_response=True,
        )
