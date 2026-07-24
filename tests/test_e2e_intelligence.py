import json
import tempfile
import unittest
from pathlib import Path

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
        self.assertTrue(both[0]["evidence"])
        self.assertEqual(
            both[0]["preliminary_offer_range"]["status"], "complete"
        )
        self.assertIn("Top 10 actionable opportunities", brief)
        self.assertIn("recommended_action", csv_text.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
