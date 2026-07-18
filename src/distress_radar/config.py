from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SourceConfig:
    name: str
    adapter: str
    base_url: str | None
    public_search_url: str | None
    record_url_template: str | None
    page_size: int
    request_delay_seconds: float
    timeout_seconds: int
    max_retries: int
    active_statuses: tuple[str, ...]


@dataclass(frozen=True)
class PropertySourceConfig:
    name: str
    adapter: str
    layer_url: str
    dataset_url: str
    municipality: str
    min_units: int
    max_units: int
    dor_codes: tuple[str, ...]
    page_size: int
    request_delay_seconds: float
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True)
class OfficialRecordsSourceConfig:
    name: str
    adapter: str
    base_url: str
    documentation_url: str
    auth_key_env: str
    enabled: bool
    request_delay_seconds: float
    timeout_seconds: int
    max_retries: int
    notes: str | None


@dataclass(frozen=True)
class CityConfig:
    slug: str
    name: str
    state: str
    enabled: bool
    notes: str | None
    source: SourceConfig
    property_source: PropertySourceConfig | None
    official_records_source: OfficialRecordsSourceConfig | None


def default_config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "cities"


def load_city_config(slug: str, config_dir: Path | None = None) -> CityConfig:
    path = (config_dir or default_config_dir()) / f"{slug}.yaml"
    if not path.exists():
        available = ", ".join(p.stem for p in sorted(path.parent.glob("*.yaml")))
        raise ValueError(f"Unknown city '{slug}'. Available: {available or 'none'}")

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    source = raw.get("source") or {}
    required = ("name", "adapter", "page_size", "timeout_seconds", "max_retries")
    missing = [key for key in required if key not in source]
    if missing:
        raise ValueError(f"{path}: missing source keys: {', '.join(missing)}")

    property_source_raw = raw.get("property_source")
    property_source = None
    if property_source_raw:
        property_source = PropertySourceConfig(
            name=str(property_source_raw["name"]),
            adapter=str(property_source_raw["adapter"]),
            layer_url=str(property_source_raw["layer_url"]),
            dataset_url=str(property_source_raw["dataset_url"]),
            municipality=str(property_source_raw["municipality"]),
            min_units=int(property_source_raw["min_units"]),
            max_units=int(property_source_raw["max_units"]),
            dor_codes=tuple(str(value) for value in property_source_raw.get("dor_codes", [])),
            page_size=int(property_source_raw.get("page_size", 2000)),
            request_delay_seconds=float(
                property_source_raw.get("request_delay_seconds", 0.25)
            ),
            timeout_seconds=int(property_source_raw.get("timeout_seconds", 60)),
            max_retries=int(property_source_raw.get("max_retries", 3)),
        )

    official_raw = raw.get("official_records_source")
    official_records_source = None
    if official_raw:
        official_records_source = OfficialRecordsSourceConfig(
            name=str(official_raw["name"]),
            adapter=str(official_raw["adapter"]),
            base_url=str(official_raw["base_url"]),
            documentation_url=str(official_raw["documentation_url"]),
            auth_key_env=str(official_raw["auth_key_env"]),
            enabled=bool(official_raw.get("enabled", False)),
            request_delay_seconds=float(official_raw.get("request_delay_seconds", 0.25)),
            timeout_seconds=int(official_raw.get("timeout_seconds", 45)),
            max_retries=int(official_raw.get("max_retries", 3)),
            notes=official_raw.get("notes"),
        )

    return CityConfig(
        slug=str(raw["slug"]),
        name=str(raw["name"]),
        state=str(raw["state"]),
        enabled=bool(raw.get("enabled", False)),
        notes=raw.get("notes"),
        source=SourceConfig(
            name=str(source["name"]),
            adapter=str(source["adapter"]),
            base_url=source.get("base_url"),
            public_search_url=source.get("public_search_url"),
            record_url_template=source.get("record_url_template"),
            page_size=int(source["page_size"]),
            request_delay_seconds=float(source.get("request_delay_seconds", 0.25)),
            timeout_seconds=int(source["timeout_seconds"]),
            max_retries=int(source["max_retries"]),
            active_statuses=tuple(str(value) for value in source.get("active_statuses", [])),
        ),
        property_source=property_source,
        official_records_source=official_records_source,
    )
