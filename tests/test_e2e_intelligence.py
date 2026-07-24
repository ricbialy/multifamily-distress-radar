import json
import tempfile
import unittest
from pathlib import Path

from distress_radar.models import PropertyRecord
from distress_radar.orchestration.refresh import run_fixture_demo

FIXTURES = Path(__file__).parent / "fixtures"


class EndToEndIntelligenceTests(unittest.TestCase):
    def test_combined_scouts_generate_ranked_auditable_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_fixture_demo(
                matrix_path=FIXTURES / "matrix_20.csv",
                off_market_path=FIXTURES / "off_market.csv",
                output_dir=Path(temporary),
                generated_at="2026-07-24T12:00:00+00:00",
            )
            records = json.loads(result.json_path.read_text())
            brief = result.brief_path.read_text()
            csv_text = result.csv_path.read_text()
        self.assertEqual(result.canonical_property_count, 22)
        both = [
            item
            for item in records
            if set(item["discovery_channels"]) == {"mls", "off_market"}
        ]
        self.assertEqual(len(both), 1)
        self.assertEqual(both[0]["folio"], "0431010010010")
        self.assertEqual(both[0]["address"], "101 Palm Ave, Hialeah, FL 33010")
        self.assertEqual(
            both[0]["address_validation_status"], "corroborated"
        )
        self.assertEqual(
            both[0]["address_validation_source"],
            "authorized_off_market_csv + matrix_csv",
        )
        self.assertLess(both[0]["data_completeness_score"], 100)
        validation_evidence = [
            item
            for item in both[0]["evidence"]
            if item["field"] == "validated_address"
        ]
        self.assertEqual(len(validation_evidence), 1)
        self.assertEqual(validation_evidence[0]["value_type"], "unknown")
        self.assertTrue(both[0]["evidence"])
        self.assertEqual(
            both[0]["preliminary_offer_range"]["status"], "complete"
        )
        incomplete = next(
            item
            for item in records
            if item["address"] == "905 West 20 Street, Hialeah, FL 33010"
        )
        self.assertEqual(
            incomplete["preliminary_offer_range"]["status"], "insufficient_data"
        )
        self.assertIn("Top 10 actionable opportunities", brief)
        self.assertIn(
            "101 Palm Ave, Hialeah, FL 33010 [address: corroborated]", brief
        )
        self.assertIn("[address: unverified]", brief)
        self.assertIn("recommended_action", csv_text.splitlines()[0])

    def test_county_record_changes_brief_status_to_verified(self) -> None:
        county_record = PropertyRecord(
            city_slug="hialeah_fl",
            source_name="miami_dade_property_point_view",
            folio="0431010010010",
            address="101 PALM AVE",
            city="HIALEAH",
            zip_code="33010",
            owner_name=None,
            owner_name_2=None,
            mailing_address_1=None,
            mailing_address_2=None,
            mailing_city=None,
            mailing_state=None,
            mailing_zip=None,
            dor_code=None,
            dor_description=None,
            unit_count=4,
            year_built=None,
            floor_count=None,
            building_area=None,
            lot_size=None,
            assessed_value=None,
            last_sale_date=None,
            last_sale_price=None,
            latitude=25.836,
            longitude=-80.281,
            source_url="https://gis-mdc.opendata.arcgis.com/",
            fetched_at="2026-07-24T11:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = run_fixture_demo(
                matrix_path=FIXTURES / "matrix_20.csv",
                off_market_path=FIXTURES / "off_market.csv",
                output_dir=Path(temporary),
                generated_at="2026-07-24T12:00:00+00:00",
                property_records=(county_record,),
            )
            records = json.loads(result.json_path.read_text())
            brief = result.brief_path.read_text()

        validated = next(item for item in records if item["folio"] == "0431010010010")
        self.assertEqual(validated["address_validation_status"], "verified")
        self.assertEqual(
            validated["address_validation_source"],
            "miami_dade_property_point_view",
        )
        validation_evidence = [
            item
            for item in validated["evidence"]
            if item["field"] == "validated_address"
        ]
        self.assertEqual(len(validation_evidence), 1)
        self.assertEqual(validation_evidence[0]["value_type"], "reported")
        self.assertNotIn("validated_address", validated["missing_data"])
        self.assertIn(
            "101 PALM AVE, HIALEAH, FL 33010 [address: verified]",
            brief,
        )


if __name__ == "__main__":
    unittest.main()
