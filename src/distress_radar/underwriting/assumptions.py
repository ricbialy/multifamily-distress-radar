from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnderwritingAssumptions:
    vacancy_rate: float
    management_rate: float
    closing_cost_rate: float
    financing_cost_rate: float
    required_margin_rate: float
    data_uncertainty_rate: float

    def __post_init__(self) -> None:
        for value in (
            self.vacancy_rate,
            self.management_rate,
            self.closing_cost_rate,
            self.financing_cost_rate,
            self.required_margin_rate,
            self.data_uncertainty_rate,
        ):
            if not 0 <= value <= 1:
                raise ValueError("rate assumptions must be between zero and one")
