from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def collect_paginated(
    fetch_page: Callable[[str | None], tuple[tuple[T, ...], str | None]],
) -> tuple[T, ...]:
    token: str | None = None
    seen_tokens: set[str] = set()
    records: list[T] = []
    while True:
        page, next_token = fetch_page(token)
        records.extend(page)
        if next_token is None:
            return tuple(records)
        if next_token in seen_tokens:
            raise RuntimeError(f"pagination token repeated: {next_token}")
        seen_tokens.add(next_token)
        token = next_token
