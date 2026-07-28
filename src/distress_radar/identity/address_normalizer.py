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

_COMPOUND_DIRECTIONS = {
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


def normalize_address(value: str | None) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold().replace("#", " "))
    replaced = [
        _COMPOUND_DIRECTIONS.get(token, _REPLACEMENTS.get(token, token))
        for token in tokens
    ]
    normalized: list[str] = []
    index = 0
    while index < len(replaced):
        token = replaced[index]
        if (
            token in {"n", "s"}
            and index + 1 < len(replaced)
            and replaced[index + 1] in {"e", "w"}
        ):
            normalized.append(token + replaced[index + 1])
            index += 2
            continue
        normalized.append(re.sub(r"^(\d+)(?:st|nd|rd|th)$", r"\1", token))
        index += 1
    return " ".join(normalized)
