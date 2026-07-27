import unittest
from pathlib import Path

from distress_radar.recommendations.features import (
    calculate_evidence_scores,
    score_data_quality,
)
from distress_radar.sources.public.off_market_csv import OffMarketCsvImporter

FIXTURE = Path(__file__).parent / "fixtures" / "off_market.csv"


class OffMarketScoutTests(unittest.TestCase):
    def test_imports_authorized_off_market_signals_with_evidence(self) -> None:
        candidates = OffMarketCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-24T12:00:00+00:00"
        )
        self.assertEqual(len(candidates), 3)
        first = candidates[0]
        self.assertEqual(first.folio, "0431010010010")
        self.assertTrue(
            {
                "tax_delinquency",
                "lis_pendens",
                "recorded_liens",
                "long_ownership",
                "absentee_owner",
                "inactive_entity",
                "unsafe_structure",
            }.issubset({signal.signal_type for signal in first.signals})
        )
        self.assertTrue(
            all(item.source_record_id == "OM-001" for item in first.evidence)
        )

    def test_unknown_source_fields_are_not_converted_to_clean_signals(self) -> None:
        third = OffMarketCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-24T12:00:00+00:00"
        )[2]
        unknown_fields = {
            item.field for item in third.evidence if item.value_type.value == "unknown"
        }
        self.assertTrue(
            {"unpaid_taxes", "lis_pendens", "active_liens"}.issubset(unknown_fields)
        )
        signal_types = {signal.signal_type for signal in third.signals}
        self.assertNotIn("no_liens", signal_types)
        self.assertNotIn("taxes_current", signal_types)

    def test_score_components_are_separate_and_deterministic(self) -> None:
        first = OffMarketCsvImporter().import_file(
            FIXTURE, fetched_at="2026-07-24T12:00:00+00:00"
        )[0]
        scores = calculate_evidence_scores(
            county={"absentee_owner": True, "ownership_duration_years": 20},
            listing={},
            municipal=(),
            official_records=(
                {"signal_type": "lis_pendens"},
                {"signal_type": "recorded_liens"},
            ),
            tax_records=({"amount_due": 1, "status": "unpaid"},),
            missing_fields=(),
        )
        confidence, completeness, freshness = score_data_quality(first.evidence)
        self.assertEqual(scores.dimensions.owner_motivation, 60)
        self.assertEqual(scores.dimensions.property_risk, 0)
        self.assertGreater(confidence, 80)
        self.assertEqual(completeness, 100)
        self.assertEqual(freshness, 100)


if __name__ == "__main__":
    unittest.main()
