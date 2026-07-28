import unittest
from pathlib import Path

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

    def test_exact_folio_lookup_uses_authoritative_query(self) -> None:
        collector = ArcGisPropertyCollector(load_city_config("hialeah_fl", CONFIG_DIR))
        requests: list[dict[str, object]] = []

        def request(url: str, params: dict[str, object]) -> dict[str, object]:
            requests.append(params)
            return {"features": []}

        collector._request = request  # type: ignore[method-assign]
        result = collector.lookup_exact_folio("04-2025-001-0241")
        self.assertEqual(result.records, ())
        self.assertEqual(requests[0]["where"], "FOLIO = '0420250010241'")
        self.assertEqual(result.raw_documents[0].kind, "property_exact_folio")

    def test_address_lookup_normalizes_matrix_street_suffix(self) -> None:
        collector = ArcGisPropertyCollector(load_city_config("hialeah_fl", CONFIG_DIR))
        requests: list[dict[str, object]] = []

        def request(url: str, params: dict[str, object]) -> dict[str, object]:
            requests.append(params)
            return {"features": []}

        collector._request = request  # type: ignore[method-assign]
        collector.lookup_address("1440 SW 4th St")
        self.assertEqual(
            requests[0]["where"],
            "UPPER(TRUE_SITE_ADDR) LIKE '1440 SW 4 ST%'",
        )

    def test_address_lookup_sanitizes_apostrophe_and_query_syntax(self) -> None:
        collector = ArcGisPropertyCollector(load_city_config("hialeah_fl", CONFIG_DIR))
        requests: list[dict[str, object]] = []

        def request(url: str, params: dict[str, object]) -> dict[str, object]:
            requests.append(params)
            return {"features": []}

        collector._request = request  # type: ignore[method-assign]
        collector.lookup_address("123 O'Brien Street")
        self.assertEqual(
            requests[-1]["where"],
            "UPPER(TRUE_SITE_ADDR) LIKE '123 O BRIEN ST%'",
        )

        collector.lookup_address("123 Main St%_'; DROP TABLE x; --\nOR 1=1")
        where = str(requests[-1]["where"])
        self.assertEqual(
            where,
            "UPPER(TRUE_SITE_ADDR) LIKE '123 MAIN ST DROP TABLE X OR 1 1%'",
        )
        literal = where.removeprefix("UPPER(TRUE_SITE_ADDR) LIKE '").removesuffix("%'")
        self.assertNotIn("%", literal)
        self.assertNotIn("_", literal)
        self.assertNotIn(";", literal)
        self.assertNotIn("--", literal)
        self.assertNotIn("\n", literal)
        self.assertEqual(where.count("'"), 2)

    def test_address_lookup_rejects_empty_sanitized_stem(self) -> None:
        collector = ArcGisPropertyCollector(load_city_config("hialeah_fl", CONFIG_DIR))
        requests: list[dict[str, object]] = []

        def request(url: str, params: dict[str, object]) -> dict[str, object]:
            requests.append(params)
            return {"features": []}

        collector._request = request  # type: ignore[method-assign]
        with self.assertRaisesRegex(ValueError, "address is required"):
            collector.lookup_address("%_;--\x00")
        self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
