from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OfferInputs:
    stabilized_value: float | None
    required_margin_rate: float = 0
    repairs: float = 0
    capital_expenditures: float = 0
    violation_permit_contingency: float = 0
    closing_cost_rate: float = 0
    financing_cost_rate: float = 0
    insurance_flood_contingency: float = 0
    data_uncertainty_rate: float = 0


@dataclass(frozen=True)
class OfferRange:
    conservative: float | None
    base: float | None
    maximum: float | None
    status: str
    deductions: dict[str, float] = field(default_factory=dict)
    missing_data: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, float | str | None]:
        return {
            "conservative": self.conservative,
            "base": self.base,
            "maximum": self.maximum,
            "status": self.status,
        }


def calculate_offer_range(inputs: OfferInputs) -> OfferRange:
    if inputs.stabilized_value is None or inputs.stabilized_value <= 0:
        return OfferRange(None, None, None, "insufficient_data", missing_data=("stabilized_value",))
    value = inputs.stabilized_value
    deductions = {
        "required_return_margin": value * inputs.required_margin_rate,
        "repairs": inputs.repairs,
        "capital_expenditures": inputs.capital_expenditures,
        "violation_permit_contingency": inputs.violation_permit_contingency,
        "closing_costs": value * inputs.closing_cost_rate,
        "financing_costs": value * inputs.financing_cost_rate,
        "insurance_flood_contingency": inputs.insurance_flood_contingency,
        "data_uncertainty_reserve": value * inputs.data_uncertainty_rate,
    }
    maximum = max(0, value - sum(deductions.values()))
    return OfferRange(
        conservative=round(maximum * 0.90, 2),
        base=round(maximum * 0.95, 2),
        maximum=round(maximum, 2),
        status="complete",
        deductions={name: round(amount, 2) for name, amount in deductions.items()},
    )
