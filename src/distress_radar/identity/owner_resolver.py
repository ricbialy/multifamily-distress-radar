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
    conflicting: bool = False
    candidate_owner_ids: tuple[str, ...] = ()
    beneficial_ownership_confirmed: bool = False


class OwnerResolver:
    def __init__(self, owners: tuple[CanonicalOwner, ...]) -> None:
        owner_ids_by_alias: dict[str, set[str]] = {}
        for owner in owners:
            owner_ids_by_alias.setdefault(
                normalize_owner_name(owner.display_name), set()
            ).add(owner.owner_id)
        self._owners = {
            alias: tuple(sorted(owner_ids))
            for alias, owner_ids in owner_ids_by_alias.items()
        }

    def resolve(self, name: str | None) -> OwnerResolution:
        owner_ids = self._owners.get(normalize_owner_name(name), ())
        if len(owner_ids) == 1:
            return OwnerResolution(
                owner_id=owner_ids[0],
                alias_match=True,
                candidate_owner_ids=owner_ids,
            )
        if len(owner_ids) > 1:
            return OwnerResolution(
                owner_id=None,
                alias_match=True,
                conflicting=True,
                candidate_owner_ids=owner_ids,
            )
        return OwnerResolution(owner_id=None, alias_match=False)
