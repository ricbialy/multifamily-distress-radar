from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RankingGate:
    gate: str
    status: str
    evidence: str


@dataclass(frozen=True)
class RankingVerification:
    status: str
    test_count: int
    gates: tuple[RankingGate, ...]
    command: tuple[str, ...]


REQUIRED_TEST_MARKERS = {
    "R1": "test_r1_honest_queue",
    "R2": "test_r2_rank_explanation",
    "R3": "test_r3_each_collector_outage",
    "R4": "test_r4_healthy_disappearance",
    "R5": "test_r5_tokenized_hazard",
    "R6": "test_r6_never_enriched",
    "R7": "test_r7_fifty_economics",
    "R8": "test_r8_one_production_scoring",
    "R9": "test_r9_action_semantics",
    "R10": "test_r10_same_and_next_day",
}


def evaluate_acceptance_output(output: str, returncode: int) -> RankingVerification:
    count_match = re.search(r"Ran (\d+) tests?", output)
    test_count = int(count_match.group(1)) if count_match else 0
    gates = tuple(
        RankingGate(
            gate,
            (
                "PASS"
                if re.search(
                    rf"^{re.escape(marker)}.*\.\.\. ok$",
                    output,
                    flags=re.MULTILINE,
                )
                else "FAIL"
            ),
            next(
                (line for line in output.splitlines() if line.startswith(marker)),
                f"{marker} result not found",
            ),
        )
        for gate, marker in REQUIRED_TEST_MARKERS.items()
    )
    passed = (
        returncode == 0
        and test_count == len(REQUIRED_TEST_MARKERS)
        and all(gate.status == "PASS" for gate in gates)
    )
    return RankingVerification(
        status="REAL-PILOT-02b: PASS" if passed else "REAL-PILOT-02b: FAIL",
        test_count=test_count,
        gates=gates,
        command=(),
    )


def run_ranking_validity_acceptance(
    repository: Path, output_dir: Path
) -> RankingVerification:
    command = (
        str(repository / ".venv/bin/python"),
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_real_pilot_02b.py",
        "-v",
    )
    process = subprocess.run(
        command,
        cwd=repository,
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
        check=False,
    )
    raw_output = process.stdout + process.stderr
    evaluated = evaluate_acceptance_output(raw_output, process.returncode)
    result = RankingVerification(
        status=evaluated.status,
        test_count=evaluated.test_count,
        gates=evaluated.gates,
        command=command,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "R1-R10-test-results.txt").write_text(raw_output, encoding="utf-8")
    (output_dir / "R1-R10-acceptance.json").write_text(
        json.dumps(asdict(result), indent=2) + "\n",
        encoding="utf-8",
    )
    rows = "\n".join(
        f"| {gate.gate} | {gate.status} | {gate.evidence} |" for gate in result.gates
    )
    (output_dir / "R1-R10-acceptance.md").write_text(
        "# REAL-PILOT-02b R1–R10 acceptance\n\n"
        "| Gate | Result | Evidence |\n"
        "|---|---|---|\n"
        f"{rows}\n\n"
        f"Overall: **{result.status}**\n",
        encoding="utf-8",
    )
    return result
