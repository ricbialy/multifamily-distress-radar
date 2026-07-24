import json
import tempfile
import unittest
from pathlib import Path

from distress_radar.ml.dataset import FeatureObservation, OutcomeLabel, build_dataset
from distress_radar.ml.evaluate import time_based_split
from distress_radar.ml.train import training_readiness
from distress_radar.reports.daily_brief import render_daily_brief
from distress_radar.reports.exports import export_csv, export_json, normalize_record


class ReportsAndMlTests(unittest.TestCase):
    def test_recommendation_export_has_required_shape(self) -> None:
        record = normalize_record(
            {
                "property_id": "property-1",
                "folio": "0123456789010",
                "address": "123 Main St",
                "jurisdiction": "Miami-Dade",
                "discovery_channels": ["mls", "off_market"],
                "recommended_action": "underwrite",
                "owner_motivation_score": 80,
                "economics_score": 70,
                "market_pressure_score": 60,
                "property_risk_score": 30,
                "data_confidence_score": 85,
                "data_completeness_score": 75,
                "data_freshness_score": 90,
                "evidence": [],
            },
            generated_at="2026-07-24T12:00:00+00:00",
        )
        required = {
            "estimated_value_range",
            "preliminary_offer_range",
            "why_now",
            "key_risks",
            "missing_data",
            "next_steps",
            "evidence",
            "generated_at",
        }
        self.assertTrue(required.issubset(record))
        self.assertEqual(record["preliminary_offer_range"]["status"], "insufficient_data")

        with tempfile.TemporaryDirectory() as temporary:
            json_path = Path(temporary) / "recommendations.json"
            csv_path = Path(temporary) / "recommendations.csv"
            export_json((record,), json_path)
            export_csv((record,), csv_path)
            self.assertEqual(json.loads(json_path.read_text())[0]["property_id"], "property-1")
            self.assertIn("recommended_action", csv_path.read_text().splitlines()[0])

    def test_daily_brief_contains_operational_sections_and_warnings(self) -> None:
        record = normalize_record(
            {
                "property_id": "property-1",
                "address": "123 Main St",
                "discovery_channels": ["mls", "off_market"],
                "recommended_action": "contact_broker",
                "recommendation_score": 88,
                "preliminary_offer_range": {
                    "conservative": 600_000,
                    "base": 650_000,
                    "maximum": 690_000,
                    "status": "complete",
                },
                "missing_data": ["insurance quote"],
            },
            generated_at="2026-07-24T12:00:00+00:00",
        )
        brief = render_daily_brief(
            (record,),
            important_listing_changes=("A1003 back on market",),
            source_warnings=("miami_dade_clerk: authentication_required",),
            generated_at="2026-07-24T12:00:00+00:00",
        )
        for heading in (
            "Top 10 actionable opportunities",
            "New MLS opportunities",
            "Important listing changes",
            "New off-market signals",
            "Properties discovered through both channels",
            "Missing critical diligence",
            "Source failures or stale data",
            "Recommended actions for today",
        ):
            self.assertIn(heading, brief)
        self.assertIn("$600,000 – $690,000", brief)

    def test_point_in_time_dataset_excludes_future_features(self) -> None:
        observations = (
            FeatureObservation(
                "property-1", "2026-01-01T00:00:00+00:00", {"motivation": 50}
            ),
            FeatureObservation(
                "property-1", "2026-03-01T00:00:00+00:00", {"motivation": 99}
            ),
        )
        outcomes = (
            OutcomeLabel(
                "property-1", "offer_made", "2026-02-01T00:00:00+00:00", 1
            ),
        )
        rows = build_dataset(observations, outcomes, target="offer_made")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].features["motivation"], 50)
        self.assertEqual(rows[0].label, 1)

    def test_time_split_preserves_chronology(self) -> None:
        observations = tuple(
            FeatureObservation(
                f"property-{index}",
                f"2026-0{index + 1}-01T00:00:00+00:00",
                {"score": index},
            )
            for index in range(4)
        )
        outcomes = tuple(
            OutcomeLabel(
                item.property_id, "review_selected", item.observed_at, index % 2
            )
            for index, item in enumerate(observations)
        )
        rows = build_dataset(observations, outcomes, target="review_selected")
        train, validation = time_based_split(rows, validation_fraction=0.25)
        self.assertLess(train[-1].as_of, validation[0].as_of)

    def test_ml_remains_disabled_without_enough_real_labels(self) -> None:
        readiness = training_readiness(positive_labels=2, negative_labels=3, minimum_each=25)
        self.assertFalse(readiness.enabled)
        self.assertIn("insufficient labeled outcomes", readiness.reason)


if __name__ == "__main__":
    unittest.main()
