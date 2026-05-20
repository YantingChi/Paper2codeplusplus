"""Stage 10 — Package the Paper2Code pipeline outputs into a Harbor benchmark task.

Bundles:
  - the post-debug generated repo (stage 4)        -> environment/codebase/
  - the cleaned paper JSON                         -> environment/paper.json
  - the planning trajectory from stage 1           -> environment/planning.json
  - the pytest tests + conftest from stage 9       -> tests/
  - (optional) the datasets / rivals / metrics
    materialized by stage 6                        -> environment/assets/
plus the five files Harbor requires (task.toml, instruction.md,
environment/Dockerfile, solution/solve.sh, tests/test.sh) and a small
weighted-scoring helper.

The verifier runs pytest inside the container; stage 9's conftest.py reads
PAPER2CODE_REPO_PATH and PAPER2CODE_SCORE_PATH env vars, so test.sh just
exports those — no patching of the copied conftest is needed. The reward is
sum(weight * passed) / sum(weight) over the test_specs.json from stage 9.

When --harbor_asset_dir is provided, the assets/{benchmarks,rivals,metrics}
sub-trees produced by 6_download_dataset.py are copied into
environment/assets/ so the agent can use the pre-fetched datasets and rival
implementations without internet access. The instruction.md is generated to
reflect whichever of those categories actually have content.
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "task"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paper_name", type=str, required=True)
    p.add_argument("--paper_json_path", type=str, required=True,
                   help="Cleaned paper JSON.")
    p.add_argument("--planning_dir", type=str, required=True,
                   help="Directory 1_planning.py wrote into (must contain planning_trajectories.json).")
    p.add_argument("--repo_dir", type=str, required=True,
                   help="Post-debug generated repo (stage 4 output).")
    p.add_argument("--unit_test_dir", type=str, required=True,
                   help="Directory 9_getting_unit_test.py wrote into.")
    p.add_argument("--harbor_asset_dir", type=str, default="",
                   help=("Directory 6_download_dataset.py wrote into (its "
                         "harbor_asset_output_dir). When provided, the "
                         "assets/{benchmarks,rivals,metrics} sub-trees are "
                         "copied into environment/assets/ and surfaced in "
                         "instruction.md. Optional — omit if stage 6 was not "
                         "run."))
    p.add_argument("--harbor_output_dir", type=str, default="./outputs/harbor_tasks")
    p.add_argument("--task_slug", type=str, default="")
    p.add_argument("--difficulty", type=str, default="medium",
                   choices=["easy", "medium", "hard"])
    p.add_argument("--agent_timeout_sec", type=int, default=1800)
    p.add_argument("--verifier_timeout_sec", type=int, default=600)
    p.add_argument("--force", action="store_true",
                   help="Overwrite the task directory if it already exists.")
    return p.parse_args()


def validate_inputs(paper_json: Path, planning_dir: Path, repo_dir: Path,
                    unit_test_dir: Path) -> Path:
    if not paper_json.is_file():
        sys.exit(f"[ERROR] paper_json_path is not a file: {paper_json}")

    planning_file = planning_dir / "planning_trajectories.json"
    if not planning_file.is_file():
        sys.exit(f"[ERROR] missing planning file: {planning_file}")

    if not repo_dir.is_dir() or not any(repo_dir.iterdir()):
        sys.exit(f"[ERROR] repo_dir is not a non-empty directory: {repo_dir}")

    test_specs = unit_test_dir / "test_specs.json"
    if not test_specs.is_file():
        sys.exit(f"[ERROR] missing test_specs.json: {test_specs}")

    src_tests = unit_test_dir / "tests"
    if not src_tests.is_dir():
        sys.exit(f"[ERROR] missing tests dir: {src_tests}")
    if not (src_tests / "conftest.py").is_file():
        sys.exit(f"[ERROR] missing conftest.py in {src_tests}")
    if not list(src_tests.glob("test_*.py")):
        sys.exit(f"[ERROR] no test_*.py files in {src_tests}")

    return planning_file


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

TASK_TOML = """version = "1.0"

[metadata]
author_name = "Paper2Code"
author_email = "local@paper2code"
difficulty = "{difficulty}"
category = "paper_reproduction"
tags = ["paper_reproduction", "{task_slug}", "code_repair"]

[agent]
timeout_sec = {agent_timeout_sec}.0

[verifier]
timeout_sec = {verifier_timeout_sec}.0

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 8192
storage_mb = 16384
"""

ASSET_CATEGORY_BLURBS = {
    "benchmarks": (
        "pre-downloaded datasets the paper evaluates on. Each subdir has a "
        "`manifest.json` with provenance; use them directly instead of "
        "re-downloading."
    ),
    "rivals": (
        "pre-fetched baseline / rival implementations (one subdir per "
        "baseline). Use these instead of running "
        "`environment/codebase/scripts/fetch_baselines.sh`."
    ),
    "metrics": (
        "pre-fetched metric implementations (one subdir per metric). Wire "
        "them in via `environment/codebase/src/metrics.py`."
    ),
}


def build_instruction_md(repro_files: dict, asset_counts: dict) -> str:
    repro_lines: list[str] = []
    if repro_files.get("reproduction_setup.md"):
        repro_lines.append(
            "- `environment/codebase/reproduction_setup.md` — overview of "
            "the paper's reproduction stack (datasets, baselines, metrics), "
            "produced by stage 6. Read after `planning.json`."
        )
    if repro_files.get("configs/datasets.yaml"):
        repro_lines.append(
            "- `environment/codebase/configs/datasets.yaml` — dataset config "
            "wired up by stage 6; paths point into `environment/assets/benchmarks/`."
        )
    if repro_files.get("configs/baselines.yaml"):
        repro_lines.append(
            "- `environment/codebase/configs/baselines.yaml` — baseline / "
            "rival config wired up by stage 6; paths point into "
            "`environment/assets/rivals/`."
        )
    if repro_files.get("scripts/fetch_baselines.sh"):
        repro_lines.append(
            "- `environment/codebase/scripts/fetch_baselines.sh` — script "
            "that originally fetched the rival implementations. The outputs "
            "are already in `environment/assets/rivals/`; you should not "
            "need to rerun it."
        )
    if repro_files.get("scripts/prepare_data"):
        prep_name = repro_files["scripts/prepare_data"]
        repro_lines.append(
            f"- `environment/codebase/scripts/{prep_name}` — dataset "
            "preparation script. Outputs are already in "
            "`environment/assets/benchmarks/`."
        )

    asset_lines: list[str] = []
    for category in ("benchmarks", "rivals", "metrics"):
        n = asset_counts.get(category, 0)
        if n > 0:
            asset_lines.append(
                f"- `environment/assets/{category}/` ({n} subdir"
                f"{'s' if n != 1 else ''}) — {ASSET_CATEGORY_BLURBS[category]}"
            )

    repro_block = ("\n" + "\n".join(repro_lines)) if repro_lines else ""
    asset_block = ("\n" + "\n".join(asset_lines)) if asset_lines else ""

    has_assets = bool(asset_lines)
    asset_rule = (
        "\n- You may NOT modify anything under `environment/assets/` — "
        "those are the ground-truth datasets / rivals / metrics."
        if has_assets else ""
    )
    internet_clause = (
        "Internet access is not guaranteed; rely on the pre-bundled "
        "`environment/assets/` for datasets, baselines, and metrics."
        if has_assets
        else "Internet access is not guaranteed."
    )

    return (
        "The workdir is `/workspace`.\n"
        "\n"
        "## What you have\n"
        "- `environment/codebase/` — a generated implementation of a "
        "research paper. It may not run, and even if it runs it may not "
        "match the paper's claims.\n"
        "- `environment/planning.json` — **READ THIS FIRST.** It is the "
        "structured plan derived from the paper (overall plan, "
        "architecture, logic design, config). The assistant turns inside "
        "the JSON contain the actionable content.\n"
        "- `environment/paper.json` — the original paper text, only as "
        "backup if planning.json is unclear."
        f"{repro_block}{asset_block}\n"
        "\n"
        "## Your task\n"
        "1. Make `environment/codebase/` runnable. "
        "`bash environment/codebase/run.sh` (if present) should exit "
        "cleanly.\n"
        "2. Make as many of the hidden unit tests pass as possible. They "
        "check correctness against the paper's specification.\n"
        "\n"
        "## Rules\n"
        "- You may modify any file under `environment/codebase/`.\n"
        "- You may NOT modify anything under `/tests/`."
        f"{asset_rule}\n"
        f"- {internet_clause}\n"
        "\n"
        "## Scoring\n"
        "Your reward is a weighted pass rate over the hidden pytest suite, "
        "with weights drawn from the paper-reproduction rubric. "
        "Higher = better.\n"
    )

DOCKERFILE = """FROM python:3.11-slim

# Harbor builds this image with the task's `environment/` directory as the
# build context (docker compose passes --project-directory <task>/environment),
# so we copy from `.` into /workspace/environment, NOT from `environment/`.
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends \\
        git build-essential ca-certificates curl \\
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest

COPY . /workspace/environment

RUN if [ -f /workspace/environment/codebase/requirements.txt ]; then \\
        pip install --no-cache-dir -r /workspace/environment/codebase/requirements.txt || true; \\
    fi

RUN mkdir -p /logs/verifier && chmod -R a+rX /workspace /logs

CMD ["bash"]
"""

SOLVE_SH = """#!/usr/bin/env bash
echo "No oracle solution provided for this Paper2Code task."
exit 0
"""

TEST_SH = """#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier /tests/scores

# Re-install agent's deps in case they edited requirements.txt.
if [ -f /workspace/environment/codebase/requirements.txt ]; then
    pip install -q -r /workspace/environment/codebase/requirements.txt || true
fi

# Stage-9 conftest.py respects these env vars.
export PAPER2CODE_REPO_PATH="/workspace/environment/codebase"
export PAPER2CODE_SCORE_PATH="/tests/scores/scores.json"
# Both the codebase and the bundled assets must be importable: the codebase
# at the top (so `from src...` works) and /workspace/environment (so
# `from assets.rivals.<slug> import ...` works in the generated tests).
export PYTHONPATH="/workspace/environment/codebase:/workspace/environment:${PYTHONPATH:-}"

cd /tests
pytest -q --tb=no || true

python /tests/compute_reward.py \\
    --specs /tests/test_specs.json \\
    --scores /tests/scores/scores.json \\
    --output /logs/verifier/reward.txt

cat /logs/verifier/reward.txt 2>/dev/null || echo "0.0" > /logs/verifier/reward.txt
exit 0
"""

COMPUTE_REWARD_PY = '''import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--specs", required=True)
ap.add_argument("--scores", required=True)
ap.add_argument("--output", required=True)
args = ap.parse_args()

# test_specs.json: {"tests": [{"test_id": ..., "weight": int, ...}, ...]}
specs_doc = json.loads(Path(args.specs).read_text(encoding="utf-8"))
specs = specs_doc.get("tests", []) if isinstance(specs_doc, dict) else specs_doc

# scores.json (written by stage-9 conftest): list of {"test_id", "weight", "passed", ...}.
scores = []
sp = Path(args.scores)
if sp.exists():
    try:
        scores = json.loads(sp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        scores = []

passed_ids = {r.get("test_id") for r in scores if r.get("passed")}

total_w = sum(int(s.get("weight", 1) or 1) for s in specs)
got_w = sum(int(s.get("weight", 1) or 1) for s in specs if s.get("test_id") in passed_ids)

reward = (got_w / total_w) if total_w > 0 else 0.0
Path(args.output).write_text(f"{reward:.6f}\\n", encoding="utf-8")
'''


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    args = parse_args()

    paper_json = Path(args.paper_json_path).resolve()
    planning_dir = Path(args.planning_dir).resolve()
    repo_dir = Path(args.repo_dir).resolve()
    unit_test_dir = Path(args.unit_test_dir).resolve()
    harbor_root = Path(args.harbor_output_dir).resolve()
    harbor_asset_dir = Path(args.harbor_asset_dir).resolve() if args.harbor_asset_dir else None

    if harbor_asset_dir is not None and not harbor_asset_dir.is_dir():
        sys.exit(f"[ERROR] --harbor_asset_dir is not a directory: {harbor_asset_dir}")

    planning_file = validate_inputs(paper_json, planning_dir, repo_dir, unit_test_dir)

    task_slug = args.task_slug.strip() or slugify(args.paper_name)
    task_dir = harbor_root / task_slug

    if task_dir.exists():
        if not args.force:
            sys.exit(f"[ERROR] task dir already exists: {task_dir}\n"
                     f"        use --force to overwrite.")
        shutil.rmtree(task_dir, ignore_errors=True)
        if task_dir.exists():
            import subprocess
            subprocess.run(["rm", "-rf", str(task_dir)], check=False)

    env_dir = task_dir / "environment"
    sol_dir = task_dir / "solution"
    tst_dir = task_dir / "tests"
    for d in (env_dir, sol_dir, tst_dir):
        d.mkdir(parents=True, exist_ok=True)

    # ---- copy bundled artifacts ------------------------------------------ #
    shutil.copytree(
        repo_dir,
        env_dir / "codebase",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv"),
    )
    shutil.copy2(paper_json, env_dir / "paper.json")
    shutil.copy2(planning_file, env_dir / "planning.json")

    shutil.copy2(unit_test_dir / "test_specs.json", tst_dir / "test_specs.json")
    src_tests = unit_test_dir / "tests"
    for fp in sorted(src_tests.iterdir()):
        if fp.is_file() and (fp.name.startswith("test_") or fp.name in {"conftest.py", "pytest.ini"}):
            shutil.copy2(fp, tst_dir / fp.name)

    # ---- copy stage-6 assets (datasets / rivals / metrics) --------------- #
    asset_counts: dict[str, int] = {"benchmarks": 0, "rivals": 0, "metrics": 0}
    if harbor_asset_dir is not None:
        asset_dst_root = env_dir / "assets"
        for category in asset_counts:
            src = harbor_asset_dir / "assets" / category
            if not src.is_dir():
                continue
            subdirs = [p for p in src.iterdir() if p.is_dir()]
            if not subdirs and not any(p.is_file() for p in src.iterdir()):
                continue
            dst = asset_dst_root / category
            shutil.copytree(
                src, dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv"),
            )
            asset_counts[category] = len([p for p in dst.iterdir() if p.is_dir()])

    # Detect which stage-6 reproduction-setup artifacts ended up inside the
    # bundled codebase, so the generated instruction.md can reference them.
    codebase_dir = env_dir / "codebase"
    repro_files = {
        "reproduction_setup.md": (codebase_dir / "reproduction_setup.md").is_file(),
        "configs/datasets.yaml": (codebase_dir / "configs" / "datasets.yaml").is_file(),
        "configs/baselines.yaml": (codebase_dir / "configs" / "baselines.yaml").is_file(),
        "scripts/fetch_baselines.sh": (codebase_dir / "scripts" / "fetch_baselines.sh").is_file(),
    }
    for prep in ("prepare_data.py", "prepare_data.sh"):
        if (codebase_dir / "scripts" / prep).is_file():
            repro_files["scripts/prepare_data"] = prep
            break

    # ---- generate harbor-required files ---------------------------------- #
    (task_dir / "task.toml").write_text(
        TASK_TOML.format(
            difficulty=args.difficulty,
            task_slug=task_slug,
            agent_timeout_sec=args.agent_timeout_sec,
            verifier_timeout_sec=args.verifier_timeout_sec,
        ),
        encoding="utf-8",
    )
    instruction_md = build_instruction_md(repro_files, asset_counts)
    (task_dir / "instruction.md").write_text(instruction_md, encoding="utf-8")
    (env_dir / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")

    write_executable(sol_dir / "solve.sh", SOLVE_SH)
    write_executable(tst_dir / "test.sh", TEST_SH)
    (tst_dir / "compute_reward.py").write_text(COMPUTE_REWARD_PY, encoding="utf-8")

    # ---- summary --------------------------------------------------------- #
    print(f"[OK] Harbor task created at: {task_dir}")
    if any(asset_counts.values()):
        print(f"     Bundled assets: " + ", ".join(
            f"{cat}={n}" for cat, n in asset_counts.items() if n
        ))
    elif harbor_asset_dir is not None:
        print(f"     [warn] --harbor_asset_dir given but no asset categories had content: {harbor_asset_dir}")
    print()
    # The Dockerfile uses the env_dir as build context (matches what Harbor's
    # docker-compose template does via --project-directory <task>/environment).
    print("Smoke test:")
    print(f"  docker build -t p2c-{task_slug} {env_dir}")
    print(f"  docker run --rm \\")
    print(f"    -v {tst_dir}:/tests \\")
    print(f"    -v /tmp/p2c-logs:/logs \\")
    print(f"    p2c-{task_slug} bash /tests/test.sh")
    print(f"  cat /tmp/p2c-logs/verifier/reward.txt")
    print()
    print("Or via Harbor:")
    print(f"  harbor run -p {task_dir} -a <agent> -m <model>")


if __name__ == "__main__":
    main()
