import tempfile
import unittest
from pathlib import Path

from distress_radar.sources.public.property_csv import PropertyRecordCsvImporter


class PropertyRecordCsvImporterTests(unittest.TestCase):
    def test_imports_authoritative_property_export_for_address_validation(self) -> None:
        csv_text = (
            "city_slug,source_name,folio,address,city,zip_code,latitude,longitude,"
            "source_url,last_seen_at\n"
            "hialeah_fl,miami_dade_property_point_view,04-3101-001-0010,"
            "101 PALM AVE,HIALEAH,33010,25.836,-80.281,"
            "https://gis-mdc.opendata.arcgis.com/,2026-07-24T11:00:00+00:00\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "properties.csv"
            path.write_text(csv_text, encoding="utf-8")
            records = PropertyRecordCsvImporter().import_file(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].folio, "0431010010010")
        self.assertEqual(records[0].source_name, "miami_dade_property_point_view")
        self.assertEqual(records[0].address, "101 PALM AVE")
        self.assertEqual(records[0].latitude, 25.836)

    def test_rejects_non_county_source(self) -> None:
        csv_text = (
            "city_slug,source_name,folio,address,city,zip_code,source_url,last_seen_at\n"
            "hialeah_fl,user_spreadsheet,04-3101-001-0010,101 PALM AVE,HIALEAH,"
            "33010,manual://sheet,2026-07-24T11:00:00+00:00\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "properties.csv"
            path.write_text(csv_text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "authoritative"):
                PropertyRecordCsvImporter().import_file(path)


if __name__ == "__main__":
    unittest.main()
