from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class SmallMultifamilyInputs:
    units: int | None
    current_monthly_rent: float | None
    market_monthly_rent: float | None
    taxes_after_sale: float | None
    insurance: float | None
    maintenance: float | None
    utilities: float | None
    management_rate: float | None
    vacancy_rate: float | None
    repairs: float | None
    sale_comparable_value: float | None
    rent_comparable_value: float | None
    legal_units_verified: bool
    building_area: float | None = None


@dataclass(frozen=True)
class SmallMultifamilyResult:
    status: str
    missing_data: tuple[str, ...]
    current_gross_income: float | None = None
    stabilized_gross_income: float | None = None
    stabilized_noi: float | None = None
    estimated_value_low: float | None = None
    estimated_value_base: float | None = None
    estimated_value_high: float | None = None
    price_per_unit_at_base_value: float | None = None
    price_per_square_foot_at_base_value: float | None = None


def _valid_decimal_rate(value: float | None) -> bool:
    return value is not None and isfinite(value) and 0 <= value <= 1


def underwrite_small_multifamily(
    inputs: SmallMultifamilyInputs,
) -> SmallMultifamilyResult:
    required = {
        "units": inputs.units,
        "current_monthly_rent": inputs.current_monthly_rent,
        "market_monthly_rent": inputs.market_monthly_rent,
        "taxes_after_sale": inputs.taxes_after_sale,
        "insurance": inputs.insurance,
        "maintenance": inputs.maintenance,
        "utilities": inputs.utilities,
        "management_rate": inputs.management_rate,
        "vacancy_rate": inputs.vacancy_rate,
        "sale_comparable_value": inputs.sale_comparable_value,
        "rent_comparable_value": inputs.rent_comparable_value,
    }
    missing = [name for name, value in required.items() if value is None]
    if not inputs.legal_units_verified:
        missing.append("legal_unit_verification")
    if inputs.units is not None and not 2 <= inputs.units <= 4:
        missing.append("two_to_four_unit_segment")
    if (
        inputs.vacancy_rate is not None
        and not _valid_decimal_rate(inputs.vacancy_rate)
    ):
        missing.append("valid_vacancy_rate")
    if (
        inputs.management_rate is not None
        and not _valid_decimal_rate(inputs.management_rate)
    ):
        missing.append("valid_management_rate")
    if missing:
        return SmallMultifamilyResult("insufficient_data", tuple(missing))

    assert inputs.units is not None
    assert inputs.current_monthly_rent is not None
    assert inputs.market_monthly_rent is not None
    assert inputs.vacancy_rate is not None
    assert inputs.management_rate is not None
    current_gross = inputs.current_monthly_rent * 12
    stabilized_gross = inputs.market_monthly_rent * 12 * (1 - inputs.vacancy_rate)
    operating_expenses = sum(
        (
            inputs.taxes_after_sale or 0,
            inputs.insurance or 0,
            inputs.maintenance or 0,
            inputs.utilities or 0,
            stabilized_gross * inputs.management_rate,
        )
    )
    stabilized_noi = stabilized_gross - operating_expenses
    comparable_values = sorted(
        (inputs.sale_comparable_value or 0, inputs.rent_comparable_value or 0)
    )
    base_value = sum(comparable_values) / 2
    return SmallMultifamilyResult(
        status="complete",
        missing_data=(),
        current_gross_income=round(current_gross, 2),
        stabilized_gross_income=round(stabilized_gross, 2),
        stabilized_noi=round(stabilized_noi, 2),
        estimated_value_low=round(comparable_values[0], 2),
        estimated_value_base=round(base_value, 2),
        estimated_value_high=round(comparable_values[1], 2),
        price_per_unit_at_base_value=round(base_value / inputs.units, 2),
        price_per_square_foot_at_base_value=(
            round(base_value / inputs.building_area, 2) if inputs.building_area else None
        ),
    )
