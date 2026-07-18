from pathlib import Path
import unittest

from distress_radar.config import load_city_config


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "cities"


class ConfigTests(unittest.TestCase):
    def test_loads_enabled_surfside(self) -> None:
        config = load_city_config("surfside_fl", CONFIG_DIR)
        self.assertTrue(config.enabled)
        self.assertEqual(config.source.adapter, "tyler_energov")
        self.assertIn("In Progress", config.source.active_statuses)

    def test_hialeah_has_verified_sources(self) -> None:
        config = load_city_config("hialeah_fl", CONFIG_DIR)
        self.assertTrue(config.enabled)
        self.assertEqual(config.source.adapter, "tyler_energov")
        self.assertIsNotNone(config.property_source)
        self.assertEqual(config.property_source.min_units, 10)
        self.assertEqual(config.property_source.max_units, 80)
        self.assertIsNotNone(config.official_records_source)
        self.assertTrue(config.official_records_source.enabled)
        self.assertEqual(
            config.official_records_source.auth_key_env,
            "MIAMI_DADE_CLERK_AUTH_KEY",
        )


if __name__ == "__main__":
    unittest.main()
