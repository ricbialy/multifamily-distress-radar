from __future__ import annotations

import re


_REPLACEMENTS = {
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "road": "rd",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "terrace": "ter",
    "apartment": "unit",
    "apt": "unit",
    "suite": "unit",
}


def normalize_address(value: str | None) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold().replace("#", " "))
    return " ".join(_REPLACEMENTS.get(token, token) for token in tokens)
