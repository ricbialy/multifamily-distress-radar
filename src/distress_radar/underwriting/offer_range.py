from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OfferInputs:
    stabilized_value_low: float | None = None
    stabilized_value_base: float | None = None
    stabilized_value_high: float | None = None
    required_margin_rate: float | None = None
    repairs: float | None = None
    capital_expenditures: float | None = None
    violation_permit_contingency: float | None = None
    closing_cost_rate: float | None = None
    financing_cost_rate: float | None = None
    insurance_flood_contingency: float | None = None
    data_uncertainty_rate: float | None = None


@dataclass(frozen=True)
class OfferRange:
    conservative: float | None
    base: float | None
    maximum: float | None
    status: str
    deductions: dict[str, float] = field(default_factory=dict)
    missing_data: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "conservative": self.conservative,
            "base": self.base,
            "maximum": self.maximum,
            "status": self.status,
            "deductions": dict(self.deductions),
            "missing_data": list(self.missing_data),
        }


def calculate_offer_range(inputs: OfferInputs) -> OfferRange:
    required = (
        "stabilized_value_low",
        "stabilized_value_base",
        "stabilized_value_high",
        "required_margin_rate",
        "repairs",
        "capital_expenditures",
        "violation_permit_contingency",
        "closing_cost_rate",
        "financing_cost_rate",
        "insurance_flood_contingency",
        "data_uncertainty_rate",
    )
    missing = tuple(name for name in required if getattr(inputs, name) is None)
    values = (
        inputs.stabilized_value_low,
        inputs.stabilized_value_base,
        inputs.stabilized_value_high,
    )
    if missing or any(value is not None and value <= 0 for value in values):
        if any(value is not None and value <= 0 for value in values):
            missing += ("positive_stabilized_values",)
        return OfferRange(None, None, None, "insufficient_data", missing_data=missing)
    assert all(value is not None for value in values)
    assert inputs.required_margin_rate is not None
    assert inputs.repairs is not None
    assert inputs.capital_expenditures is not None
    assert inputs.violation_permit_contingency is not None
    assert inputs.closing_cost_rate is not None
    assert inputs.financing_cost_rate is not None
    assert inputs.insurance_flood_contingency is not None
    assert inputs.data_uncertainty_rate is not None
    base_value = inputs.stabilized_value_base
    assert base_value is not None
    deductions = {
        "required_return_margin": base_value * inputs.required_margin_rate,
        "repairs": inputs.repairs,
        "capital_expenditures": inputs.capital_expenditures,
        "violation_permit_contingency": inputs.violation_permit_contingency,
        "closing_costs": base_value * inputs.closing_cost_rate,
        "financing_costs": base_value * inputs.financing_cost_rate,
        "insurance_flood_contingency": inputs.insurance_flood_contingency,
        "data_uncertainty_reserve": base_value * inputs.data_uncertainty_rate,
    }
    fixed_costs = (
        inputs.repairs
        + inputs.capital_expenditures
        + inputs.violation_permit_contingency
        + inputs.insurance_flood_contingency
    )
    rate = (
        inputs.required_margin_rate
        + inputs.closing_cost_rate
        + inputs.financing_cost_rate
        + inputs.data_uncertainty_rate
    )
    scenario_offers = tuple(max(0, value * (1 - rate) - fixed_costs) for value in values)
    return OfferRange(
        conservative=round(scenario_offers[0], 2),
        base=round(scenario_offers[1], 2),
        maximum=round(scenario_offers[2], 2),
        status="complete",
        deductions={name: round(amount, 2) for name, amount in deductions.items()},
    )
