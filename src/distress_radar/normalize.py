from __future__ import annotations

import re


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def normalize_parcel(value: object) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    normalized = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    return normalized or None


def first_present(*values: object) -> str | None:
    for value in values:
        cleaned = clean_text(value)
        if cleaned is not None:
            return cleaned
    return None
