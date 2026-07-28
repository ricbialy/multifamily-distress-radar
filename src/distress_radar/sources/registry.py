from __future__ import annotations

from distress_radar.sources.base import SourceSpec


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, SourceSpec] = {}

    def register(self, spec: SourceSpec) -> None:
        if spec.name in self._sources:
            raise ValueError(f"source already registered: {spec.name}")
        self._sources[spec.name] = spec

    def get(self, name: str) -> SourceSpec:
        return self._sources[name]

    def all(self) -> tuple[SourceSpec, ...]:
        return tuple(self._sources.values())
