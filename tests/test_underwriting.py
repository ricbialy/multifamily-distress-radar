import unittest
from dataclasses import replace

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
        self.assertAlmostEqual(result.stabilized_noi, 58_392)
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
            public_unit_count_verified=True,
        )
        result = underwrite_commercial(inputs)
        self.assertEqual(result.status, "complete")
        self.assertAlmostEqual(result.stabilized_noi, 154_900)
        self.assertAlmostEqual(result.estimated_value_base, 2_581_666.67, places=2)
        self.assertAlmostEqual(result.price_per_unit_at_base_value, 215_138.89, places=2)

    def test_offer_range_deducts_every_required_reserve(self) -> None:
        result = calculate_offer_range(
            OfferInputs(
                stabilized_value_low=900_000,
                stabilized_value_base=1_000_000,
                stabilized_value_high=1_100_000,
                asking_price=600_000,
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
        self.assertEqual(result.conservative, 610_000)
        self.assertEqual(result.base, 690_000)
        self.assertEqual(result.maximum, 770_000)
        self.assertEqual(sum(result.deductions.values()), 310_000)
        exported = result.to_dict()
        self.assertEqual(exported["deductions"]["repairs"], 50_000)
        self.assertEqual(exported["missing_data"], [])

    def test_offer_range_never_fabricates_when_value_is_missing(self) -> None:
        result = calculate_offer_range(OfferInputs())
        self.assertEqual(result.status, "insufficient_data")
        self.assertIsNone(result.maximum)
        self.assertIn("stabilized_value_low", result.missing_data)
        self.assertIn("repairs", result.missing_data)

    def test_commercial_model_rejects_unverified_units_and_bad_economics(self) -> None:
        inputs = CommercialMultifamilyInputs(
            units=12,
            current_noi=-1,
            gross_potential_rent=300_000,
            market_vacancy_rate=0.05,
            taxes_after_sale=1_000,
            insurance=1_000,
            management_rate=0.01,
            utilities=1_000,
            maintenance=1_000,
            other_operating_expenses=1_000,
            deferred_maintenance=100_000,
            capital_expenditures=50_000,
            market_cap_rate_low=0.055,
            market_cap_rate_base=0.06,
            market_cap_rate_high=0.065,
            public_unit_count_verified=False,
        )
        result = underwrite_commercial(inputs)
        self.assertEqual(result.status, "insufficient_data")
        self.assertIn("verified_public_unit_count", result.missing_data)
        self.assertIn("positive_current_noi", result.missing_data)
        self.assertIn("plausible_operating_expenses", result.missing_data)

    def test_underwriting_rejects_invalid_decimal_rates(self) -> None:
        small = SmallMultifamilyInputs(
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
        for field, value, expected in (
            ("vacancy_rate", -0.01, "valid_vacancy_rate"),
            ("vacancy_rate", float("nan"), "valid_vacancy_rate"),
            ("management_rate", 1.01, "valid_management_rate"),
            ("management_rate", float("inf"), "valid_management_rate"),
        ):
            with self.subTest(model="small", field=field, value=value):
                result = underwrite_small_multifamily(
                    replace(small, **{field: value})
                )
                self.assertEqual(result.status, "insufficient_data")
                self.assertIn(expected, result.missing_data)

        commercial = CommercialMultifamilyInputs(
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
            public_unit_count_verified=True,
        )
        for field, value, expected in (
            ("market_vacancy_rate", -0.01, "valid_vacancy_rate"),
            ("management_rate", 1.01, "valid_management_rate"),
            ("market_cap_rate_low", float("nan"), "valid_cap_rate_support"),
            ("market_cap_rate_high", 1.01, "valid_cap_rate_support"),
        ):
            with self.subTest(model="commercial", field=field, value=value):
                result = underwrite_commercial(
                    replace(commercial, **{field: value})
                )
                self.assertEqual(result.status, "insufficient_data")
                self.assertIn(expected, result.missing_data)

        unordered = underwrite_commercial(
            replace(
                commercial,
                market_cap_rate_low=0.07,
                market_cap_rate_base=0.06,
                market_cap_rate_high=0.05,
            )
        )
        self.assertEqual(unordered.status, "insufficient_data")
        self.assertIn("ordered_cap_rate_support", unordered.missing_data)

    def test_offer_requires_all_material_costs(self) -> None:
        result = calculate_offer_range(
            OfferInputs(
                stabilized_value_low=900_000,
                stabilized_value_base=1_000_000,
                stabilized_value_high=1_100_000,
            )
        )
        self.assertEqual(result.status, "insufficient_data")
        self.assertIn("repairs", result.missing_data)
        self.assertIn("financing_cost_rate", result.missing_data)

    def test_negative_stabilized_noi_and_excess_cure_costs_fail(self) -> None:
        inputs = CommercialMultifamilyInputs(
            units=12,
            current_noi=10_000,
            gross_potential_rent=100_000,
            market_vacancy_rate=0.05,
            taxes_after_sale=60_000,
            insurance=30_000,
            management_rate=0.10,
            utilities=20_000,
            maintenance=20_000,
            other_operating_expenses=20_000,
            deferred_maintenance=100_000,
            capital_expenditures=50_000,
            market_cap_rate_low=0.055,
            market_cap_rate_base=0.06,
            market_cap_rate_high=0.065,
            public_unit_count_verified=True,
        )
        self.assertIn(
            "positive_stabilized_noi",
            underwrite_commercial(inputs).missing_data,
        )
        offer = calculate_offer_range(
            OfferInputs(
                stabilized_value_low=900_000,
                stabilized_value_base=1_000_000,
                stabilized_value_high=1_100_000,
                asking_price=950_000,
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
        self.assertEqual(offer.status, "insufficient_data")
        self.assertIn(
            "cure_costs_exceed_available_discount", offer.missing_data
        )


if __name__ == "__main__":
    unittest.main()
