#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True, type=str)
    parser.add_argument("--cases", required=True, type=str)
    parser.add_argument("--expected", required=False, type=str)
    parser.add_argument("--output", required=True, type=str)
    return parser.parse_args()


def import_repo_modules(repo_dir: Path):
    for module_name in ["config", "utils", "benchmark", "evaluation"]:
        if module_name in sys.modules:
            del sys.modules[module_name]
    sys.path.insert(0, str(repo_dir))
    benchmark_mod = importlib.import_module("benchmark")
    evaluation_mod = importlib.import_module("evaluation")
    import torch
    return benchmark_mod, evaluation_mod, torch


def finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def compare_float(left: float | None, right: float | None, tol: float) -> bool:
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= tol


def compare_results(actual: dict[str, Any], expected: dict[str, Any], tol: float) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    actual_problems = actual.get("problems", {})
    expected_problems = expected.get("problems", {})

    if set(actual_problems) != set(expected_problems):
        mismatches.append("problem set mismatch")
        return False, mismatches

    for problem_name, expected_problem in expected_problems.items():
        actual_problem = actual_problems.get(problem_name, {})
        actual_cases = actual_problem.get("cases", [])
        expected_cases = expected_problem.get("cases", [])
        if len(actual_cases) != len(expected_cases):
            mismatches.append(f"{problem_name}: case count mismatch")
            continue

        for index, (actual_case, expected_case) in enumerate(zip(actual_cases, expected_cases), start=1):
            if actual_case.get("candidate") != expected_case.get("candidate"):
                mismatches.append(f"{problem_name}: candidate mismatch at case {index}")
            if bool(actual_case.get("feasible")) != bool(expected_case.get("feasible")):
                mismatches.append(f"{problem_name}: feasibility mismatch at case {index}")
            if not compare_float(actual_case.get("objective"), expected_case.get("objective"), tol):
                mismatches.append(f"{problem_name}: objective mismatch at case {index}")

        actual_metrics = actual_problem.get("metrics", {})
        expected_metrics = expected_problem.get("metrics", {})
        for metric_name in ["best_objective", "feasibility_ratio"]:
            if not compare_float(actual_metrics.get(metric_name), expected_metrics.get(metric_name), tol):
                mismatches.append(f"{problem_name}: metric mismatch for {metric_name}")
        if int(actual_metrics.get("total_evaluations", -1)) != int(expected_metrics.get("total_evaluations", -2)):
            mismatches.append(f"{problem_name}: total_evaluations mismatch")

    return len(mismatches) == 0, mismatches


def main() -> int:
    args = parse_args()
    repo_dir = Path(args.repo_dir).resolve()
    cases_path = Path(args.cases).resolve()
    output_path = Path(args.output).resolve()

    result: dict[str, Any] = {
        "runnable": False,
        "result_same": False,
        "comparison_tolerance": None,
        "problems": {},
        "error": None,
        "mismatches": []
    }

    try:
        benchmark_mod, evaluation_mod, torch = import_repo_modules(repo_dir)
        cases_payload = load_json(cases_path)
        tolerance = float(cases_payload.get("comparison_tolerance", 1e-6))
        result["comparison_tolerance"] = tolerance

        problems_output: dict[str, Any] = {}
        for problem_name, problem_payload in cases_payload["problems"].items():
            benchmark = benchmark_mod.Benchmark({"problem_name": problem_name})
            evaluations: list[dict[str, Any]] = []
            for index, point in enumerate(problem_payload["points"], start=1):
                point_tensor = torch.tensor(point, dtype=torch.float32)
                objective, feasible = benchmark.evaluate(point_tensor)
                evaluations.append(
                    {
                        "iteration": index,
                        "candidate": [float(value) for value in point],
                        "feasible": bool(feasible),
                        "objective": finite_or_none(objective)
                    }
                )

            metrics = evaluation_mod.Evaluation({"evaluations": evaluations}).compute_metrics()
            problems_output[problem_name] = {
                "cases": evaluations,
                "metrics": {
                    "best_objective": finite_or_none(metrics.get("best_objective")),
                    "feasibility_ratio": finite_or_none(metrics.get("feasibility_ratio")),
                    "total_evaluations": int(metrics.get("total_evaluations", len(evaluations)))
                }
            }

        result["runnable"] = True
        result["problems"] = problems_output

        if args.expected:
            expected_payload = load_json(Path(args.expected).resolve())
            ok, mismatches = compare_results(result, expected_payload, tolerance)
            result["result_same"] = ok
            result["mismatches"] = mismatches

        write_json(output_path, result)
        if result["result_same"]:
            return 0
        return 2

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        write_json(output_path, result)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
