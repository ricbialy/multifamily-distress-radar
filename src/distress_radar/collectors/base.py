from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from distress_radar.models import CollectionResult


class Collector(ABC):
    @abstractmethod
    def list_statuses(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def collect(
        self,
        statuses: Sequence[str],
        *,
        max_pages: int | None = None,
        fetch_details: bool = True,
        include_violations: bool = False,
    ) -> CollectionResult:
        raise NotImplementedError
