import unittest

from distress_radar.orchestration.retries import RetryPolicy, run_with_retry
from distress_radar.orchestration.jobs import collect_paginated


class RetryAndPaginationTests(unittest.TestCase):
    def test_retries_transient_failure_with_bounded_backoff(self) -> None:
        attempts = []
        sleeps = []

        def operation() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise TimeoutError("temporary")
            return "ok"

        result = run_with_retry(
            operation,
            RetryPolicy(max_attempts=3, base_delay_seconds=1, max_delay_seconds=10),
            sleep=sleeps.append,
            random_value=lambda: 0,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleeps, [1, 2])

    def test_non_retryable_error_is_not_retried(self) -> None:
        attempts = []

        def operation() -> None:
            attempts.append(1)
            raise ValueError("schema")

        with self.assertRaises(ValueError):
            run_with_retry(operation, RetryPolicy(max_attempts=4), sleep=lambda _: None)
        self.assertEqual(len(attempts), 1)

    def test_pagination_reads_until_next_token_is_absent(self) -> None:
        pages = {
            None: ((1, 2), "page-2"),
            "page-2": ((3,), None),
        }
        records = collect_paginated(lambda token: pages[token])
        self.assertEqual(records, (1, 2, 3))

    def test_repeated_pagination_token_fails_loudly(self) -> None:
        with self.assertRaises(RuntimeError):
            collect_paginated(lambda token: ((1,), "same"))


if __name__ == "__main__":
    unittest.main()
