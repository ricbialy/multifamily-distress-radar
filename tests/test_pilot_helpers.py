import unittest

from distress_radar.pilot import _listing_change_price


class PilotHelperTests(unittest.TestCase):
    def test_listing_change_price_accepts_scalar_and_snapshot_payloads(self) -> None:
        self.assertEqual(_listing_change_price(1_000_000), 1_000_000)
        self.assertEqual(
            _listing_change_price({"list_price": 900_000}),
            900_000,
        )
        self.assertIsNone(_listing_change_price({"status": "Active"}))
        self.assertIsNone(_listing_change_price("not-a-price"))


if __name__ == "__main__":
    unittest.main()
