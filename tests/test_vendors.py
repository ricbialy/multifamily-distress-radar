import unittest

from distress_radar.sources.vendors.attom import AttomAdapter
from distress_radar.sources.vendors.base import VendorDisabledError
from distress_radar.sources.vendors.melissa import MelissaAdapter
from distress_radar.sources.vendors.propertyradar import PropertyRadarAdapter
from distress_radar.sources.vendors.rentcast import RentCastAdapter


class VendorAdapterTests(unittest.TestCase):
    def test_all_paid_adapters_are_disabled_without_credentials(self) -> None:
        adapters = (
            PropertyRadarAdapter(environ={}),
            AttomAdapter(environ={}),
            MelissaAdapter(environ={}),
            RentCastAdapter(environ={}),
        )
        for adapter in adapters:
            with self.subTest(adapter=adapter.spec.name):
                self.assertFalse(adapter.enabled)
                self.assertEqual(adapter.health_state.value, "disabled")
                with self.assertRaises(VendorDisabledError):
                    adapter.collect("0123456789010")

    def test_fixture_normalization_does_not_claim_live_collection(self) -> None:
        adapter = AttomAdapter(environ={})
        result = adapter.normalize_fixture(
            {"record_id": "fixture-1", "estimated_value": 1_000_000}
        )
        self.assertEqual(result["source_mode"], "fixture")
        self.assertEqual(result["estimated_value"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
