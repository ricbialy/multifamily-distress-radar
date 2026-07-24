import unittest

from distress_radar.underwriting.assumptions import UnderwritingAssumptions
from distress_radar.underwriting.commercial_multifamily import (
    CommercialMultifamilyInputs,
    underwrite_commercial,
)
from distress_radar.underwriting.offer_range import OfferInputs, calculate_offer_range
from distress_radar.underwriting.small_multifamily import (
    SmallMultifamilyInputs,
    underwrite_small_multifamily,
)


class UnderwritingTests(unittest.TestCase):
    def test_two_to_four_unit_stabilized_cash_flow_is_transparent(self) -> None:
        inputs = SmallMultifamilyInputs(
            units=4,
            current_monthly_rent=8_000,
            market_monthly_rent=9_000,
            taxes_after_sale=12_000,
            insurance=10_000,
            maintenance=8_000,
            utilities=6_000,
            management_rate=0.08,
            vacancy_rate=0.05,
            repairs=50_000,
            sale_comparable_value=900_000,
            rent_comparable_value=950_000,
            legal_units_verified=True,
        )
        result = underwrite_small_multifamily(inputs)
        self.assertEqual(result.status, "complete")
        self.assertAlmostEqual(result.stabilized_gross_income, 102_600)
        self.assertAlmostEqual(result.stabilized_noi, 66_392)
        self.assertEqual(result.price_per_unit_at_base_value, 231_250)

    def test_small_multifamily_requires_legal_unit_verification(self) -> None:
        inputs = SmallMultifamilyInputs(
            units=4,
            current_monthly_rent=8_000,
            market_monthly_rent=9_000,
            taxes_after_sale=12_000,
            insurance=10_000,
            maintenance=8_000,
            utilities=6_000,
            management_rate=0.08,
            vacancy_rate=0.05,
            repairs=50_000,
            sale_comparable_value=900_000,
            rent_comparable_value=950_000,
            legal_units_verified=False,
        )
        result = underwrite_small_multifamily(inputs)
        self.assertEqual(result.status, "insufficient_data")
        self.assertIn("legal_unit_verification", result.missing_data)

    def test_five_plus_unit_model_reconstructs_and_stabilizes_noi(self) -> None:
        inputs = CommercialMultifamilyInputs(
            units=12,
            current_noi=150_000,
            gross_potential_rent=300_000,
            market_vacancy_rate=0.05,
            taxes_after_sale=35_000,
            insurance=28_000,
            management_rate=0.06,
            utilities=18_000,
            maintenance=20_000,
            other_operating_expenses=12_000,
            deferred_maintenance=100_000,
            capital_expenditures=50_000,
            market_cap_rate_low=0.055,
            market_cap_rate_base=0.06,
            market_cap_rate_high=0.065,
        )
        result = underwrite_commercial(inputs)
        self.assertEqual(result.status, "complete")
        self.assertAlmostEqual(result.stabilized_noi, 154_900)
        self.assertAlmostEqual(result.estimated_value_base, 2_581_666.67, places=2)
        self.assertAlmostEqual(result.price_per_unit_at_base_value, 215_138.89, places=2)

    def test_offer_range_deducts_every_required_reserve(self) -> None:
        result = calculate_offer_range(
            OfferInputs(
                stabilized_value=1_000_000,
                required_margin_rate=0.10,
                repairs=50_000,
                capital_expenditures=25_000,
                violation_permit_contingency=20_000,
                closing_cost_rate=0.03,
                financing_cost_rate=0.02,
                insurance_flood_contingency=15_000,
                data_uncertainty_rate=0.05,
            )
        )
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.maximum, 690_000)
        self.assertEqual(result.base, 655_500)
        self.assertEqual(result.conservative, 621_000)
        self.assertEqual(sum(result.deductions.values()), 310_000)

    def test_offer_range_never_fabricates_when_value_is_missing(self) -> None:
        result = calculate_offer_range(OfferInputs(stabilized_value=None))
        self.assertEqual(result.status, "insufficient_data")
        self.assertIsNone(result.maximum)
        self.assertIn("stabilized_value", result.missing_data)


if __name__ == "__main__":
    unittest.main()
