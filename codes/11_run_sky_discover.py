"""Stage 11 — run SkyDiscover on a task produced by 10_get_skyDiscover.py."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


SEARCH_ALGORITHMS = (
    "adaevolve",
    "evox",
    "openevolve",
    "gepa",
    "shinkaevolve",
    "topk",
    "best_of_n",
    "beam_search",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skydiscover_output_dir",
        "--skydiscover-output-dir",
        default="./outputs/skydiscover_tasks",
        help="Root directory written by 10_get_skyDiscover.py.",
    )
    parser.add_argument(
        "--task_slug",
        "--task-slug",
        default="",
        help="Task subdirectory under skydiscover_output_dir, matching stage 10.",
    )
    parser.add_argument(
        "--task_dir",
        "--task-dir",
        default="",
        help="Direct SkyDiscover task directory. Overrides skydiscover_output_dir/task_slug.",
    )
    parser.add_argument("--search", default="adaevolve", choices=SEARCH_ALGORITHMS)
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument(
        "--output_dir",
        "--output-dir",
        default="",
        help="Directory for SkyDiscover run artifacts.",
    )
    parser.add_argument(
        "--cost_log",
        "--cost-log",
        default="",
        help="Path forwarded to evaluator.py as SKYDISCOVER_COST_LOG.",
    )
    return parser.parse_args(argv)


def resolve_task_dir(args: argparse.Namespace) -> Path:
    if args.task_dir:
        return Path(args.task_dir).expanduser().resolve()
    if not args.task_slug:
        raise SystemExit("[ERROR] --task_slug is required unless --task_dir is provided.")
    return (Path(args.skydiscover_output_dir).expanduser() / args.task_slug).resolve()


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"[ERROR] missing {label}: {path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    task_dir = resolve_task_dir(args)
    initial_program = task_dir / "initial_program.py"
    evaluator = task_dir / "evaluator.py"

    require_file(initial_program, "initial_program.py")
    require_file(evaluator, "evaluator.py")

    output_dir = str(Path(args.output_dir).expanduser().resolve()) if args.output_dir else None
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    if args.cost_log:
        cost_log = Path(args.cost_log).expanduser().resolve()
        cost_log.parent.mkdir(parents=True, exist_ok=True)
        os.environ["SKYDISCOVER_COST_LOG"] = str(cost_log)

    from skydiscover import run_discovery

    result = run_discovery(
        initial_program=str(initial_program),
        evaluator=str(evaluator),
        search=args.search,
        model=args.model,
        iterations=args.iterations,
        output_dir=output_dir,
        cleanup=output_dir is None,
    )

    print(result.best_score, result.best_solution)
    return 0


if __name__ == "__main__":
    sys.exit(main())
