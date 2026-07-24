from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1
    max_delay_seconds: float = 30
    jitter_seconds: float = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")


def run_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> T:
    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except (TimeoutError, ConnectionError, OSError):
            if attempt + 1 >= policy.max_attempts:
                raise
            delay = min(
                policy.max_delay_seconds,
                policy.base_delay_seconds * (2**attempt)
                + policy.jitter_seconds * random_value(),
            )
            sleep(delay)
    raise RuntimeError("unreachable")
