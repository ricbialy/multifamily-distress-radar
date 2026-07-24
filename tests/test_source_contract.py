import unittest

from distress_radar.sources.base import (
    AuthorizationMode,
    CollectionError,
    CollectionErrorKind,
    SourceHealthState,
    SourceSpec,
)
from distress_radar.sources.registry import SourceRegistry


class SourceContractTests(unittest.TestCase):
    def test_source_spec_exposes_reliability_contract(self) -> None:
        spec = SourceSpec(
            name="matrix_csv",
            jurisdiction="South Florida",
            authorization_mode=AuthorizationMode.AUTHORIZED_EXPORT,
            rate_limit_per_minute=None,
            request_timeout_seconds=30,
            max_retries=3,
            cache_ttl_seconds=3600,
            freshness_window_seconds=86400,
            supports_pagination=False,
            preserves_raw_response=True,
        )
        self.assertEqual(spec.max_retries, 3)
        self.assertTrue(spec.preserves_raw_response)

    def test_structured_failure_maps_to_visible_health_state(self) -> None:
        error = CollectionError(
            kind=CollectionErrorKind.AUTHENTICATION,
            message="credential missing",
            retryable=False,
        )
        self.assertEqual(error.health_state, SourceHealthState.AUTHENTICATION_REQUIRED)

    def test_registry_rejects_duplicate_source_names(self) -> None:
        registry = SourceRegistry()
        spec = SourceSpec.fixture("matrix_csv", "South Florida")
        registry.register(spec)
        with self.assertRaises(ValueError):
            registry.register(spec)


if __name__ == "__main__":
    unittest.main()
