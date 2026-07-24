from __future__ import annotations

import re


def normalize_folio(value: str | None) -> str | None:
    normalized = "".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))
    return normalized or None
