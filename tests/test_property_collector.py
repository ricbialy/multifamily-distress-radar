from pathlib import Path
import unittest

from distress_radar.collectors.arcgis_property import ArcGisPropertyCollector
from distress_radar.config import load_city_config


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "cities"


class PropertyCollectorTests(unittest.TestCase):
    def test_filter_is_municipality_units_and_multifamily_class(self) -> None:
        collector = ArcGisPropertyCollector(load_city_config("hialeah_fl", CONFIG_DIR))
        where = collector._where_clause()
        self.assertIn("HIALEAH", where)
        self.assertIn("UNIT_COUNT >= 10", where)
        self.assertIn("UNIT_COUNT <= 80", where)
        self.assertIn("'0303'", where)

    def test_parses_property_appraiser_feature(self) -> None:
        collector = ArcGisPropertyCollector(load_city_config("hialeah_fl", CONFIG_DIR))
        record = collector._parse(
            {
                "attributes": {
                    "FOLIO": "04-2025-001-0241",
                    "TRUE_SITE_ADDR": "1105 W 76 ST",
                    "TRUE_SITE_CITY": "Hialeah",
                    "TRUE_SITE_ZIP_CODE": "33014",
                    "TRUE_OWNER1": "HUDSON PARK PARTNERS LLC",
                    "DOR_CODE_CUR": "0303",
                    "DOR_DESC": "MULTIFAMILY 10 UNITS PLUS",
                    "UNIT_COUNT": 38,
                    "YEAR_BUILT": 1968,
                    "PRICE_1": 10400000,
                },
                "geometry": {"x": -80.3, "y": 25.9},
            },
            "2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(record.folio, "0420250010241")
        self.assertEqual(record.unit_count, 38)
        self.assertEqual(record.owner_name, "HUDSON PARK PARTNERS LLC")
        self.assertEqual(record.longitude, -80.3)


if __name__ == "__main__":
    unittest.main()
