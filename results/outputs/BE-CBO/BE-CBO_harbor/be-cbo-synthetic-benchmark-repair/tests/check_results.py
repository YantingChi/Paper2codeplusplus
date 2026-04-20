#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True, type=str)
    parser.add_argument("--verification-output", required=True, type=str)
    parser.add_argument("--agent-output", required=True, type=str)
    parser.add_argument("--runner-exit", required=True, type=int)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def compare_float(left: float | None, right: float | None, tol: float) -> bool:
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= tol


def compare_problem_payload(actual: dict[str, Any], expected: dict[str, Any], tol: float) -> bool:
    actual_cases = actual.get("cases", [])
    expected_cases = expected.get("cases", [])
    if len(actual_cases) != len(expected_cases):
        return False

    for actual_case, expected_case in zip(actual_cases, expected_cases):
        if actual_case.get("candidate") != expected_case.get("candidate"):
            return False
        if bool(actual_case.get("feasible")) != bool(expected_case.get("feasible")):
            return False
        if not compare_float(actual_case.get("objective"), expected_case.get("objective"), tol):
            return False

    actual_metrics = actual.get("metrics", {})
    expected_metrics = expected.get("metrics", {})
    for metric_name in ["best_objective", "feasibility_ratio"]:
        if not compare_float(actual_metrics.get(metric_name), expected_metrics.get(metric_name), tol):
            return False
    return int(actual_metrics.get("total_evaluations", -1)) == int(expected_metrics.get("total_evaluations", -2))


def compare_problems(actual: dict[str, Any], expected: dict[str, Any], tol: float) -> bool:
    if set(actual) != set(expected):
        return False
    for problem_name, expected_problem in expected.items():
        if not compare_problem_payload(actual.get(problem_name, {}), expected_problem, tol):
            return False
    return True


def main() -> int:
    args = parse_args()
    expected = read_json(Path(args.expected))
    verification = read_json(Path(args.verification_output))
    agent_output = read_json(Path(args.agent_output))
    tolerance = 1e-6
    if expected is not None:
        tolerance = float(expected.get("comparison_tolerance", 1e-6))

    reasons: list[str] = []

    if expected is None:
        reasons.append("expected results file missing")
    if verification is None:
        reasons.append("verification output missing")
    if agent_output is None:
        reasons.append("agent output missing")
    if args.runner_exit != 0:
        reasons.append(f"benchmark runner exit code was {args.runner_exit}")

    if verification is not None:
        if verification.get("runnable") is not True:
            reasons.append("verification run was not runnable")
        if verification.get("result_same") is not True:
            reasons.append("verification run did not match expected results")

    if agent_output is not None:
        if agent_output.get("runnable") is not True:
            reasons.append("agent output does not mark runnable=true")
        if agent_output.get("result_same") is not True:
            reasons.append("agent output does not mark result_same=true")
        if expected is not None and not compare_problems(agent_output.get("problems", {}), expected.get("problems", {}), tolerance):
            reasons.append("agent output problems payload does not match expected results")

    reward = 1.0 if not reasons else 0.0
    Path("/logs/verifier").mkdir(parents=True, exist_ok=True)
    Path("/logs/verifier/reward.txt").write_text(f"{reward}\n", encoding="utf-8")
    write_json(
        Path("/logs/verifier/result.json"),
        {
          "reward": reward,
          "passed": reward == 1.0,
          "reasons": reasons,
          "verification_output_present": verification is not None,
          "agent_output_present": agent_output is not None
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
