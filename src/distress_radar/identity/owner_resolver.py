from __future__ import annotations

import re
from dataclasses import dataclass

from distress_radar.domain.owner import CanonicalOwner


_SUFFIXES = {"llc", "lc", "inc", "corp", "corporation", "ltd", "lp", "llp"}
_DOTTED_SUFFIXES = (("l", "l", "c"), ("l", "l", "p"), ("i", "n", "c"))


def normalize_owner_name(value: str | None) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").casefold())
    for suffix in _DOTTED_SUFFIXES:
        if tuple(tokens[-len(suffix) :]) == suffix:
            del tokens[-len(suffix) :]
            break
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


@dataclass(frozen=True)
class OwnerResolution:
    owner_id: str | None
    alias_match: bool
    beneficial_ownership_confirmed: bool = False


class OwnerResolver:
    def __init__(self, owners: tuple[CanonicalOwner, ...]) -> None:
        self._owners = {
            normalize_owner_name(owner.display_name): owner.owner_id for owner in owners
        }

    def resolve(self, name: str | None) -> OwnerResolution:
        owner_id = self._owners.get(normalize_owner_name(name))
        return OwnerResolution(owner_id=owner_id, alias_match=owner_id is not None)
