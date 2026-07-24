from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommercialMultifamilyInputs:
    units: int | None
    current_noi: float | None
    gross_potential_rent: float | None
    market_vacancy_rate: float | None
    taxes_after_sale: float | None
    insurance: float | None
    management_rate: float | None
    utilities: float | None
    maintenance: float | None
    other_operating_expenses: float | None
    deferred_maintenance: float | None
    capital_expenditures: float | None
    market_cap_rate_low: float | None
    market_cap_rate_base: float | None
    market_cap_rate_high: float | None
    building_area: float | None = None
    public_unit_count_verified: bool = False
    advertised_units: int | None = None


@dataclass(frozen=True)
class CommercialMultifamilyResult:
    status: str
    missing_data: tuple[str, ...]
    current_noi: float | None = None
    reconstructed_noi: float | None = None
    stabilized_noi: float | None = None
    estimated_value_low: float | None = None
    estimated_value_base: float | None = None
    estimated_value_high: float | None = None
    price_per_unit_at_base_value: float | None = None
    price_per_square_foot_at_base_value: float | None = None


def underwrite_commercial(
    inputs: CommercialMultifamilyInputs,
) -> CommercialMultifamilyResult:
    required = {
        name: getattr(inputs, name)
        for name in (
            "units",
            "current_noi",
            "gross_potential_rent",
            "market_vacancy_rate",
            "taxes_after_sale",
            "insurance",
            "management_rate",
            "utilities",
            "maintenance",
            "other_operating_expenses",
            "deferred_maintenance",
            "capital_expenditures",
            "market_cap_rate_low",
            "market_cap_rate_base",
            "market_cap_rate_high",
        )
    }
    missing = [name for name, value in required.items() if value is None]
    if inputs.units is not None and inputs.units < 5:
        missing.append("five_plus_unit_segment")
    if not inputs.public_unit_count_verified:
        missing.append("verified_public_unit_count")
    if (
        inputs.units is not None
        and inputs.advertised_units is not None
        and inputs.units != inputs.advertised_units
    ):
        missing.append("unit_count_disagreement")
    if inputs.current_noi is not None and inputs.current_noi <= 0:
        missing.append("positive_current_noi")
    cap_rates = (
        inputs.market_cap_rate_low,
        inputs.market_cap_rate_base,
        inputs.market_cap_rate_high,
    )
    if any(rate is not None and rate <= 0 for rate in cap_rates):
        missing.append("valid_cap_rate_support")
    if (
        inputs.gross_potential_rent is not None
        and inputs.market_vacancy_rate is not None
        and inputs.management_rate is not None
    ):
        effective_income = inputs.gross_potential_rent * (
            1 - inputs.market_vacancy_rate
        )
        known_expenses = sum(
            value or 0
            for value in (
                inputs.taxes_after_sale,
                inputs.insurance,
                effective_income * inputs.management_rate,
                inputs.utilities,
                inputs.maintenance,
                inputs.other_operating_expenses,
            )
        )
        if effective_income > 0 and known_expenses / effective_income < 0.15:
            missing.append("plausible_operating_expenses")
    if missing:
        return CommercialMultifamilyResult(
            "insufficient_data", tuple(dict.fromkeys(missing))
        )

    assert inputs.units is not None
    assert inputs.gross_potential_rent is not None
    assert inputs.market_vacancy_rate is not None
    assert inputs.management_rate is not None
    effective_income = inputs.gross_potential_rent * (1 - inputs.market_vacancy_rate)
    expenses = sum(
        (
            inputs.taxes_after_sale or 0,
            inputs.insurance or 0,
            effective_income * inputs.management_rate,
            inputs.utilities or 0,
            inputs.maintenance or 0,
            inputs.other_operating_expenses or 0,
        )
    )
    reconstructed_noi = inputs.gross_potential_rent - expenses
    stabilized_noi = effective_income - expenses
    if stabilized_noi <= 0:
        return CommercialMultifamilyResult(
            "insufficient_data", ("positive_stabilized_noi",),
            current_noi=inputs.current_noi,
            reconstructed_noi=round(reconstructed_noi, 2),
            stabilized_noi=round(stabilized_noi, 2),
        )
    assert inputs.market_cap_rate_low
    assert inputs.market_cap_rate_base
    assert inputs.market_cap_rate_high
    low = stabilized_noi / inputs.market_cap_rate_high
    base = stabilized_noi / inputs.market_cap_rate_base
    high = stabilized_noi / inputs.market_cap_rate_low
    return CommercialMultifamilyResult(
        status="complete",
        missing_data=(),
        current_noi=inputs.current_noi,
        reconstructed_noi=round(reconstructed_noi, 2),
        stabilized_noi=round(stabilized_noi, 2),
        estimated_value_low=round(low, 2),
        estimated_value_base=round(base, 2),
        estimated_value_high=round(high, 2),
        price_per_unit_at_base_value=round(base / inputs.units, 2),
        price_per_square_foot_at_base_value=(
            round(base / inputs.building_area, 2) if inputs.building_area else None
        ),
    )
