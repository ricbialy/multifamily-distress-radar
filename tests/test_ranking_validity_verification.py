import unittest

from distress_radar.ranking_validity_verification import (
    REQUIRED_TEST_MARKERS,
    evaluate_acceptance_output,
)


class RankingValidityHarnessTests(unittest.TestCase):
    def test_complete_harness_accepts_exactly_all_ten_gates(self) -> None:
        output = "\n".join(
            f"{marker}_scenario (test_real_pilot_02b.Acceptance) ... ok"
            for marker in REQUIRED_TEST_MARKERS.values()
        )
        output += "\n\nRan 10 tests in 1.000s\n\nOK\n"
        result = evaluate_acceptance_output(output, 0)
        self.assertEqual(result.status, "REAL-PILOT-02b: PASS")
        self.assertEqual(result.test_count, 10)
        self.assertTrue(all(gate.status == "PASS" for gate in result.gates))

    def test_complete_harness_fails_when_any_gate_is_missing(self) -> None:
        markers = list(REQUIRED_TEST_MARKERS.values())[:-1]
        output = "\n".join(
            f"{marker}_scenario (test_real_pilot_02b.Acceptance) ... ok"
            for marker in markers
        )
        output += "\n\nRan 9 tests in 1.000s\n\nOK\n"
        result = evaluate_acceptance_output(output, 0)
        self.assertEqual(result.status, "REAL-PILOT-02b: FAIL")
        self.assertEqual(result.gates[-1].status, "FAIL")


if __name__ == "__main__":
    unittest.main()
