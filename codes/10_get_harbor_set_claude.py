# This is the Claude-edited fork of 10_get_harbor_set.py.
# run_codex.sh stage 10h invokes THIS file; the unsuffixed 10_get_harbor_set.py
# is kept frozen as a reference. Keep edits here, not there.
"""Stage 10h - package current Paper2Code artifacts into Harbor tasks.

Sample usage:
  python3.10 codes/10_get_harbor_set_claude.py \
    --paper_name adaptive-pruning \
    --planned_file outputs/paperbench_log/adaptive-pruning/planning_response.json \
    --eval_plan_json tests/harbor/yantingchi/adaptive-pruning/eval_plan/eval_plan.json \
    --repo_dir outputs/paperbench_repos/adaptive-pruning_repo \
    --unit_test_dir outputs/paperbench_tests/adaptive-pruning_tests \
    --harbor_asset_dir tests/harbor/yantingchi/adaptive-pruning/harbor/asset \
    --harbor_output_dir outputs/harbor_tasks \
    --task_slug adaptive-pruning \
    --force

The current run_codex.sh workflow writes runnable stage-9b pytest files into
<generated_repo>/tests/{intermediate,comparison} and leaves test_specs.json in
<unit_test_dir>. This script turns those artifacts, the stage-5.1 eval plan,
and the stage-6 downloaded assets into a Harbor-compatible task directory.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


# User-changeable defaults and generated-file templates.
DEFAULT_HARBOR_OUTPUT_DIR = "./outputs/harbor_tasks"
DEFAULT_DIFFICULTY = "medium"
DEFAULT_AGENT_TIMEOUT_SEC = 1800
DEFAULT_VERIFIER_TIMEOUT_SEC = 600
TASK_CATEGORIES = ("benchmarks", "rivals", "metrics")
SKIP_COPY_PATTERNS = ("__pycache__", "*.pyc", ".git", ".venv", ".pytest_cache")

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
gpus = 0
allow_internet = true

[verifier.env]
PAPER2CODE_INSTALL_REQUIREMENTS = "{install_reqs_default}"

[solution.env]
"""

ASSET_CATEGORY_BLURBS = {
    "benchmarks": (
        "pre-downloaded datasets the paper evaluates on. Use them directly "
        "instead of re-downloading."
    ),
    "rivals": (
        "pre-fetched baseline or rival implementations. Use these instead "
        "of cloning external repositories when possible."
    ),
    "metrics": (
        "pre-fetched metric implementations, reports, formulas, or toy "
        "fixtures used by the generated tests."
    ),
}

DOCKERFILE = """FROM python:3.11-slim

# Harbor builds this image with the task environment directory as context.
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends \\
        git build-essential ca-certificates curl \\
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest

# Light codebase deps so the repo imports out of the box. Heavy wheels (torch,
# transformers, ...) are deferred to verifier-time (PAPER2CODE_INSTALL_REQUIREMENTS=1).
# Copied before the full tree so editing the codebase doesn't bust this layer.
COPY requirements-base.txt /tmp/requirements-base.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt \\
    || echo "[WARN] base requirements failed; deferring all installs to /tests/test.sh"

COPY . /workspace/environment

RUN mkdir -p /logs/verifier && chmod -R a+rX /workspace /logs

CMD ["bash"]
"""

SOLVE_SH = """#!/usr/bin/env bash
# Sample usage: bash solution/solve.sh
# This Harbor task has no oracle solution; agents repair environment/codebase.
echo "No oracle solution provided for this Paper2Code task."
exit 0
"""

TEST_SH = """#!/usr/bin/env bash
# Sample usage: bash /tests/test.sh
# Runs the Paper2Code pytest verifier and writes Harbor reward diagnostics.
set -uo pipefail

mkdir -p /logs/verifier
rm -f /logs/verifier/scores.json

# Optional dependency installation is disabled by default because generated
# repos often request large GPU wheels. Set PAPER2CODE_INSTALL_REQUIREMENTS=1
# inside Harbor only when dependency installation is the failure under test.
if [ "${PAPER2CODE_INSTALL_REQUIREMENTS:-0}" = "1" ] && [ -f /workspace/environment/codebase/requirements.txt ]; then
    pip install -q -r /workspace/environment/codebase/requirements.txt || true
fi

# Stage-9b conftest.py respects these env vars.
export PAPER2CODE_REPO_PATH="/workspace/environment/codebase"
export PAPER2CODE_SCORE_PATH="/logs/verifier/scores.json"
export PYTHONPATH="/workspace/environment/codebase:/workspace/environment:${PYTHONPATH:-}"

cd /tests
pytest -q --tb=short -rA . > /logs/verifier/pytest.log 2>&1
pytest_status=$?

python /tests/compute_reward.py \\
    --specs /tests/test_specs.json \\
    --scores /logs/verifier/scores.json \\
    --output /logs/verifier/reward.txt

python /tests/analyze_failures.py \\
    --scores /logs/verifier/scores.json \\
    --pytest-log /logs/verifier/pytest.log \\
    --summary /logs/verifier/summary.txt \\
    --report /logs/verifier/failure_report.json \\
    --pytest-status "$pytest_status"

if [ ! -s /logs/verifier/reward.txt ]; then
    echo "0.0" > /logs/verifier/reward.txt
fi

cat /logs/verifier/reward.txt
exit 0
"""

COMPUTE_REWARD_PY = '''"""Compute weighted reward from stage-9b pytest score records.

Sample usage:
  python /tests/compute_reward.py --specs /tests/test_specs.json \\
    --scores /tests/scores/scores.json --output /logs/verifier/reward.txt
"""

import argparse
import json
from pathlib import Path


DEFAULT_WEIGHT = 1


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--specs", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    specs_doc = _load_json(Path(args.specs), {"tests": []})
    specs = specs_doc.get("tests", []) if isinstance(specs_doc, dict) else specs_doc
    scores = _load_json(Path(args.scores), [])
    passed_ids = {record.get("test_id") for record in scores if record.get("passed")}

    total_weight = sum(int(spec.get("weight", DEFAULT_WEIGHT) or DEFAULT_WEIGHT) for spec in specs)
    passed_weight = sum(
        int(spec.get("weight", DEFAULT_WEIGHT) or DEFAULT_WEIGHT)
        for spec in specs
        if spec.get("test_id") in passed_ids
    )
    reward = (passed_weight / total_weight) if total_weight > 0 else 0.0
    Path(args.output).write_text(f"{reward:.6f}\\n", encoding="utf-8")


if __name__ == "__main__":
    main()
'''

ANALYZE_FAILURES_PY = '''"""Summarize Paper2Code verifier failures for Harbor agents.

Sample usage:
  python /tests/analyze_failures.py --scores /tests/scores/scores.json \\
    --pytest-log /logs/verifier/pytest.log --summary /logs/verifier/summary.txt \\
    --report /logs/verifier/failure_report.json
"""

import argparse
import json
import re
from pathlib import Path


DEFAULT_PYTEST_STATUS = 0
LOG_TAIL_LINES = 80


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _load_scores(path: Path) -> list[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return doc if isinstance(doc, list) else []


def classify_failure(text: str, *, collection_failed: bool = False) -> str:
    lowered = text.lower()
    if collection_failed:
        return "collection_failure"
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        return "missing_module"
    if "attributeerror" in lowered or "has no attribute" in lowered:
        return "missing_api"
    if "filenotfounderror" in lowered or "no such file or directory" in lowered:
        return "missing_file_or_dataset"
    if "assertionerror" in lowered or re.search(r"\\bassert\\b", lowered):
        return "assertion_mismatch"
    if "runtimeerror" in lowered or "valueerror" in lowered or "typeerror" in lowered:
        return "runtime_failure"
    return "unknown_failure"


def build_report(scores: list[dict], pytest_log: str, pytest_status: int) -> dict:
    failures = []
    for record in scores:
        if record.get("passed"):
            continue
        detail = str(record.get("longrepr") or "")
        failures.append(
            {
                "test_id": record.get("test_id"),
                "rubric_id": record.get("rubric_id", ""),
                "nodeid": record.get("nodeid", ""),
                "cause": classify_failure(detail),
                "detail": detail,
            }
        )

    collection_failed = pytest_status != 0 and not scores
    if collection_failed:
        failures.append(
            {
                "test_id": "collection",
                "rubric_id": "",
                "nodeid": "",
                "cause": classify_failure(pytest_log, collection_failed=True),
                "detail": "\\n".join(pytest_log.splitlines()[-LOG_TAIL_LINES:]),
            }
        )

    return {
        "pytest_status": pytest_status,
        "failure_count": len(failures),
        "failures": failures,
    }


def write_summary(report: dict, summary_path: Path) -> None:
    lines = [
        "Paper2Code Harbor verifier summary",
        f"pytest_status={report['pytest_status']}",
        f"failure_count={report['failure_count']}",
        "",
    ]
    if not report["failures"]:
        lines.append("No failing score records were reported.")
    else:
        lines.append("Failing tests:")
        for item in report["failures"]:
            lines.append(
                f"- {item.get('test_id')} {item.get('nodeid')} cause={item.get('cause')}"
            )
            detail_lines = str(item.get("detail") or "").splitlines()
            if detail_lines:
                lines.extend(f"  {line}" for line in detail_lines[-8:])
    summary_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--pytest-log", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--pytest-status", type=int, default=DEFAULT_PYTEST_STATUS)
    args = parser.parse_args()

    scores = _load_scores(Path(args.scores))
    pytest_log = _read_text(Path(args.pytest_log))
    report = build_report(scores, pytest_log, args.pytest_status)

    report_path = Path(args.report)
    summary_path = Path(args.summary)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_summary(report, summary_path)


if __name__ == "__main__":
    main()
'''


# Argument parsing and path resolution.
def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "task"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument("--planned_file", type=str, default="", help="planning_response.json from stage 1.")
    parser.add_argument("--eval_plan_json", type=str, default="", help="eval_plan.json from stage 5.1.")
    parser.add_argument("--paper_json_path", type=str, default="", help="Optional cleaned paper JSON.")
    parser.add_argument(
        "--planning_dir",
        type=str,
        default="",
        help="Legacy fallback directory containing planning_trajectories.json.",
    )
    parser.add_argument("--repo_dir", type=str, required=True, help="Generated repo directory.")
    parser.add_argument(
        "--unit_test_dir",
        type=str,
        required=True,
        help="Stage-9 sidecar directory containing test_specs.json.",
    )
    parser.add_argument(
        "--harbor_asset_dir",
        type=str,
        default="",
        help="Stage-6 asset output root containing assets/{benchmarks,rivals,metrics}.",
    )
    parser.add_argument("--harbor_output_dir", type=str, default=DEFAULT_HARBOR_OUTPUT_DIR)
    parser.add_argument("--task_slug", type=str, default="")
    parser.add_argument("--difficulty", type=str, default=DEFAULT_DIFFICULTY, choices=["easy", "medium", "hard"])
    parser.add_argument("--agent_timeout_sec", type=int, default=DEFAULT_AGENT_TIMEOUT_SEC)
    parser.add_argument("--verifier_timeout_sec", type=int, default=DEFAULT_VERIFIER_TIMEOUT_SEC)
    parser.add_argument("--force", action="store_true", help="Overwrite existing task directory.")
    return parser.parse_args()


def resolve_planned_file(planned_file_arg: str, planning_dir_arg: str) -> Path:
    if planned_file_arg:
        planned_file = Path(planned_file_arg).resolve()
    elif planning_dir_arg:
        planned_file = (Path(planning_dir_arg).resolve() / "planning_trajectories.json")
    else:
        sys.exit("[ERROR] --planned_file is required (or legacy --planning_dir with planning_trajectories.json).")
    if not planned_file.is_file():
        sys.exit(f"[ERROR] planned_file is not a file: {planned_file}")
    return planned_file


def validate_inputs(args: argparse.Namespace) -> dict[str, Path | None]:
    planned_file = resolve_planned_file(args.planned_file, args.planning_dir)
    eval_plan = Path(args.eval_plan_json).resolve() if args.eval_plan_json else None
    if eval_plan is None or not eval_plan.is_file():
        sys.exit(f"[ERROR] eval_plan_json is not a file: {eval_plan or args.eval_plan_json}")

    paper_json = Path(args.paper_json_path).resolve() if args.paper_json_path else None
    if paper_json is not None and not paper_json.is_file():
        sys.exit(f"[ERROR] paper_json_path is not a file: {paper_json}")

    repo_dir = Path(args.repo_dir).resolve()
    if not repo_dir.is_dir() or not any(repo_dir.iterdir()):
        sys.exit(f"[ERROR] repo_dir is not a non-empty directory: {repo_dir}")

    unit_test_dir = Path(args.unit_test_dir).resolve()
    test_specs = unit_test_dir / "test_specs.json"
    if not test_specs.is_file():
        sys.exit(f"[ERROR] missing test_specs.json: {test_specs}")

    tests_dir = resolve_tests_source(repo_dir, unit_test_dir)

    asset_dir = Path(args.harbor_asset_dir).resolve() if args.harbor_asset_dir else None
    if asset_dir is not None and not asset_dir.is_dir():
        sys.exit(f"[ERROR] --harbor_asset_dir is not a directory: {asset_dir}")

    return {
        "planned_file": planned_file,
        "eval_plan": eval_plan,
        "paper_json": paper_json,
        "repo_dir": repo_dir,
        "unit_test_dir": unit_test_dir,
        "tests_dir": tests_dir,
        "asset_dir": asset_dir,
    }


# Test discovery and copy helpers.
def _has_runnable_tests(tests_dir: Path) -> bool:
    return (
        tests_dir.is_dir()
        and (tests_dir / "conftest.py").is_file()
        and (tests_dir / "pytest.ini").is_file()
        and any(tests_dir.rglob("test_*.py"))
    )


def resolve_tests_source(repo_dir: Path, unit_test_dir: Path) -> Path:
    repo_tests = repo_dir / "tests"
    if _has_runnable_tests(repo_tests):
        return repo_tests

    legacy_tests = unit_test_dir / "tests"
    if _has_runnable_tests(legacy_tests):
        return legacy_tests

    sys.exit(
        "[ERROR] missing runnable stage-9b tests: expected conftest.py, pytest.ini, "
        f"and test_*.py under {repo_tests} or {legacy_tests}"
    )


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def remove_tree(path: Path) -> None:
    def _chmod_and_retry(function, failed_path, exc_info) -> None:
        failed = Path(failed_path)
        try:
            if failed.parent.exists():
                os.chmod(failed.parent, 0o700)
            if failed.exists():
                os.chmod(failed, 0o700)
            function(failed_path)
        except Exception:
            raise exc_info[1]

    shutil.rmtree(path, onerror=_chmod_and_retry)


def clear_existing_task_dir(task_dir: Path) -> None:
    try:
        remove_tree(task_dir)
        return
    except OSError as error:
        stale_root = task_dir.parent.with_name(f"{task_dir.parent.name}_stale")
        stale_root.mkdir(parents=True, exist_ok=True)
        base_name = f"{task_dir.name}-{os.getpid()}"
        stale_dir = stale_root / base_name
        suffix = 1
        while stale_dir.exists():
            stale_dir = stale_root / f"{base_name}-{suffix}"
            suffix += 1
        try:
            task_dir.rename(stale_dir)
        except OSError:
            sys.exit(
                "[ERROR] task dir could not be removed or moved aside: "
                f"{task_dir}\n        original error: {error}"
            )
        print(
            f"[warn] moved protected stale task dir to {stale_dir}",
            file=sys.stderr,
        )


def copy_codebase(repo_dir: Path, dst: Path) -> None:
    # Drop the repo's OWN top-level tests/ dir so the held-out verifier never
    # lands under environment/codebase/tests/ where the agent could read it.
    # Nested tests dirs (e.g. src/foo/tests) are preserved — we only skip the
    # tests dir that sits at the repo root.
    repo_root_str = str(repo_dir)

    def _ignore(src: str, names: list[str]) -> set[str]:
        ignored = set(shutil.ignore_patterns(*SKIP_COPY_PATTERNS)(src, names))
        if os.path.realpath(src) == os.path.realpath(repo_root_str) and "tests" in names:
            ignored.add("tests")
        return ignored

    shutil.copytree(repo_dir, dst, ignore=_ignore)


# Rewrite the stage-9b conftest's hardcoded host paths to file-relative anchors.
# The generated conftest bakes DEFAULT_REPO_PATH / DEFAULT_SCORE_PATH as absolute
# host paths (e.g. /mnt/blk1/Paper2Code/...), which are invalid once the task is
# moved or run on another host. We anchor them on the conftest's own location so
# `pytest tests/` works directly on the bundle; inside Harbor the env vars set by
# test.sh (PAPER2CODE_REPO_PATH / PAPER2CODE_SCORE_PATH) still override these.
def _rewrite_conftest_defaults(src_path: Path, dst_path: Path) -> None:
    text = src_path.read_text(encoding="utf-8")

    text, repo_subs = re.subn(
        r"^DEFAULT_REPO_PATH\s*=\s*['\"][^'\"]*['\"]",
        "DEFAULT_REPO_PATH = str(Path(__file__).resolve().parent.parent / 'environment' / 'codebase')",
        text,
        flags=re.M,
    )
    text, score_subs = re.subn(
        r"^DEFAULT_SCORE_PATH\s*=\s*['\"][^'\"]*['\"]",
        "DEFAULT_SCORE_PATH = str(Path(__file__).resolve().parent.parent / 'logs' / 'verifier' / 'scores.json')",
        text,
        flags=re.M,
    )
    if repo_subs < 1 or score_subs < 1:
        sys.exit(
            "[ERROR] could not rewrite conftest defaults "
            f"(repo_subs={repo_subs}, score_subs={score_subs}); "
            f"expected DEFAULT_REPO_PATH and DEFAULT_SCORE_PATH assignments in {src_path}"
        )
    dst_path.write_text(text, encoding="utf-8")


def copy_tests(src_tests: Path, dst_tests: Path) -> None:
    _rewrite_conftest_defaults(src_tests / "conftest.py", dst_tests / "conftest.py")
    shutil.copy2(src_tests / "pytest.ini", dst_tests / "pytest.ini")

    for test_file in sorted(src_tests.glob("test_*.py")):
        shutil.copy2(test_file, dst_tests / test_file.name)

    for subdir_name in ("intermediate", "comparison"):
        src_subdir = src_tests / subdir_name
        if not src_subdir.is_dir():
            continue
        dst_subdir = dst_tests / subdir_name
        shutil.copytree(
            src_subdir,
            dst_subdir,
            ignore=shutil.ignore_patterns(*SKIP_COPY_PATTERNS),
        )


def copy_assets(asset_dir: Path | None, env_dir: Path) -> dict[str, int]:
    asset_counts = {category: 0 for category in TASK_CATEGORIES}
    if asset_dir is None:
        return asset_counts

    dst_root = env_dir / "assets"
    for category in TASK_CATEGORIES:
        src = asset_dir / "assets" / category
        if not src.is_dir():
            continue
        if not any(src.iterdir()):
            continue
        dst = dst_root / category
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(*SKIP_COPY_PATTERNS),
        )
        asset_counts[category] = len([path for path in dst.iterdir() if path.is_dir()])
    return asset_counts


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def top_level_contents(path: Path) -> str:
    if not path.exists():
        return "(none)"
    names = [item.name + ("/" if item.is_dir() else "") for item in sorted(path.iterdir())]
    return ", ".join(names[:20]) if names else "(empty)"


# Generated documentation.
def detect_repro_files(codebase_dir: Path) -> dict[str, Any]:
    repro_files: dict[str, Any] = {
        "reproduction_setup.md": (codebase_dir / "reproduction_setup.md").is_file(),
        "configs/datasets.yaml": (codebase_dir / "configs" / "datasets.yaml").is_file(),
        "configs/baselines.yaml": (codebase_dir / "configs" / "baselines.yaml").is_file(),
        "scripts/fetch_baselines.sh": (codebase_dir / "scripts" / "fetch_baselines.sh").is_file(),
    }
    for prep in ("prepare_data.py", "prepare_data.sh"):
        if (codebase_dir / "scripts" / prep).is_file():
            repro_files["scripts/prepare_data"] = prep
            break
    return repro_files


def build_instruction_md(repro_files: dict[str, Any], asset_counts: dict[str, int]) -> str:
    repro_lines: list[str] = []
    if repro_files.get("reproduction_setup.md"):
        repro_lines.append("- `environment/codebase/reproduction_setup.md` - reproduction setup overview.")
    if repro_files.get("configs/datasets.yaml"):
        repro_lines.append("- `environment/codebase/configs/datasets.yaml` - dataset configuration.")
    if repro_files.get("configs/baselines.yaml"):
        repro_lines.append("- `environment/codebase/configs/baselines.yaml` - baseline/rival configuration.")
    if repro_files.get("scripts/fetch_baselines.sh"):
        repro_lines.append("- `environment/codebase/scripts/fetch_baselines.sh` - original baseline fetch script.")
    if repro_files.get("scripts/prepare_data"):
        prep_name = repro_files["scripts/prepare_data"]
        repro_lines.append(f"- `environment/codebase/scripts/{prep_name}` - dataset preparation script.")

    asset_lines = [
        f"- `environment/assets/{category}/` ({count} subdir{'s' if count != 1 else ''}) - "
        f"{ASSET_CATEGORY_BLURBS[category]}"
        for category, count in asset_counts.items()
        if count > 0
    ]
    asset_rule = (
        "- You may NOT modify anything under `environment/assets/`; those are ground-truth assets.\n"
        if asset_lines
        else ""
    )
    internet_clause = (
        "Internet access is not guaranteed; rely on `environment/assets/`."
        if asset_lines
        else "Internet access is not guaranteed."
    )

    return (
        "The workdir is `/workspace`.\n\n"
        "## What you have\n"
        "- `environment/codebase/` - generated implementation of a research paper.\n"
        "- `environment/planning.json` - READ THIS FIRST. It is the implementation plan/context.\n"
        "- `environment/eval_plan.json` - hardware-constrained evaluation plan and asset scope.\n"
        "- `/tests/` - hidden verifier-style pytest tests generated by Paper2Code.\n"
        + ("\n".join(repro_lines) + "\n" if repro_lines else "")
        + ("\n".join(asset_lines) + "\n" if asset_lines else "")
        + "\n"
        "## Your workflow\n"
        "1. Run `bash /tests/test.sh` first.\n"
        "2. Read `/logs/verifier/summary.txt`, `/logs/verifier/failure_report.json`, and "
        "`/logs/verifier/pytest.log`.\n"
        "3. Synthesize the root cause from the failed tests.\n"
        "4. Fix only files under `/workspace/environment/codebase/`.\n"
        "5. Rerun targeted pytest commands, then rerun `bash /tests/test.sh`.\n\n"
        "## Rules\n"
        "- You may modify any file under `environment/codebase/`.\n"
        "- You may NOT modify anything under `/tests/`.\n"
        f"{asset_rule}"
        f"- {internet_clause}\n\n"
        "## Scoring\n"
        "Reward is a weighted pass rate over the pytest suite using `test_specs.json` weights.\n"
    )


def build_bundle_log(
    sources: dict[str, Path | None],
    task_dir: Path,
) -> str:
    env_dir = task_dir / "environment"
    codebase_dir = env_dir / "codebase"
    assets_dir = env_dir / "assets"
    tests_dir = task_dir / "tests"
    solution_dir = task_dir / "solution"
    tests_source = sources["tests_dir"]
    unit_source = sources["unit_test_dir"]
    assets_source = sources["asset_dir"]

    return (
        "# Harbor Bundle Log\n\n"
        "## environment/\n"
        f"Source: generated from {sources['repo_dir']}, {sources['planned_file']}, and {sources['eval_plan']}.\n"
        "Purpose: Harbor runtime environment.\n"
        f"Contents: {top_level_contents(env_dir)}.\n"
        f"File count: {count_files(env_dir)}.\n\n"
        "## environment/codebase/\n"
        f"Source: {sources['repo_dir']}.\n"
        "Purpose: generated repo the agent may repair.\n"
        f"File count: {count_files(codebase_dir)}.\n\n"
        "## environment/assets/\n"
        f"Source: {assets_source if assets_source is not None else '(none supplied)'}.\n"
        "Purpose: downloaded datasets, rivals, and metrics.\n"
        f"Top-level contents: {top_level_contents(assets_dir)}.\n"
        f"File count: {count_files(assets_dir)}.\n\n"
        "## tests/\n"
        f"Source: runnable tests from {tests_source}; specs from {unit_source / 'test_specs.json'}.\n"
        "Purpose: stage-9b pytest verifier.\n"
        f"Contents: {top_level_contents(tests_dir)}.\n"
        f"File count: {count_files(tests_dir)}.\n\n"
        "## solution/\n"
        "Source: generated placeholder.\n"
        "Purpose: no oracle solution for Paper2Code repair tasks.\n"
        f"Contents: {top_level_contents(solution_dir)}.\n"
    )


# Lightweight runtime deps safe to bake into the image at build time. Heavy
# wheels (torch, transformers, lm-eval, accelerate, ...) are intentionally left
# out so the image build stays fast and we don't silently bake a CPU-only torch
# when a GPU build is wanted; those are installed at verifier-time when
# PAPER2CODE_INSTALL_REQUIREMENTS=1 (set via task.toml [verifier.env]).
LIGHT_DEP_WHITELIST = {
    "numpy", "pandas", "pyyaml", "yaml", "tqdm", "packaging", "scipy",
    "matplotlib", "scikit-learn", "sklearn", "datasets", "omegaconf", "requests",
    "filelock", "regex", "typing-extensions", "click", "rich", "joblib",
}

# Match a requirement line's base package name, e.g. "torch>=2.1.0" -> "torch".
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


# Read the test specs array (stage-9b) so we can branch on whether a comparison
# (reproduction) tier exists. Returns [] if the file is missing or malformed.
def load_test_specs(unit_test_dir: Path) -> list[dict]:
    specs_path = unit_test_dir / "test_specs.json"
    try:
        doc = json.loads(specs_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    tests = doc.get("tests", []) if isinstance(doc, dict) else doc
    return tests if isinstance(tests, list) else []


# True if any spec is in the comparison (reproduction) tier.
def has_comparison_tier(specs: list[dict]) -> bool:
    return any(spec.get("tier") == "comparison" for spec in specs)


# True if any comparison test file imports the rival package namespace. We grep
# the raw text (cheap) instead of parsing — c6.5 does the real AST work.
def comparison_uses_rivals(comparison_dir: Path) -> bool:
    if not comparison_dir.is_dir():
        return False
    for test_file in comparison_dir.rglob("test_*.py"):
        try:
            if "assets.rivals." in test_file.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


# Scaffold assets/rivals/<slug> packages inside the bundled codebase so the
# comparison tests' `assets.rivals.*` imports resolve. c6.5 sys.exits(1) when
# tests/comparison/ is absent, so we run with check=False and only call it when
# we already detected rival imports.
def maybe_install_rival_stubs(codebase_dir: Path) -> None:
    script = Path(__file__).resolve().parent / "c6.5_install_rival_stubs.py"
    if not script.is_file():
        print(f"[warn] rival-stub script not found: {script}", file=sys.stderr)
        return
    result = subprocess.run(
        [sys.executable, str(script), "--repo", str(codebase_dir)],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"[warn] c6.5 rival-stub bootstrap returned {result.returncode}; "
            "comparison tests may fail to import assets.rivals.*",
            file=sys.stderr,
        )


# Write environment/requirements-base.txt: the subset of the codebase's
# requirements.txt whose base package name is in LIGHT_DEP_WHITELIST. Always
# writes the file (possibly empty) so the Dockerfile's COPY/install step has a
# target. Returns the number of lines written.
def write_requirements_base(codebase_dir: Path, env_dir: Path) -> int:
    req_path = codebase_dir / "requirements.txt"
    kept: list[str] = []
    if req_path.is_file():
        for raw in req_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = _REQ_NAME_RE.match(line)
            if match and match.group(1).lower() in LIGHT_DEP_WHITELIST:
                kept.append(line)
    (env_dir / "requirements-base.txt").write_text(
        ("\n".join(kept) + "\n") if kept else "", encoding="utf-8"
    )
    return len(kept)


# Main packaging workflow.
def create_task(args: argparse.Namespace, sources: dict[str, Path | None]) -> Path:
    harbor_root = Path(args.harbor_output_dir).resolve()
    task_slug = args.task_slug.strip() or slugify(args.paper_name)
    task_dir = harbor_root / task_slug

    if task_dir.exists():
        if not args.force:
            sys.exit(f"[ERROR] task dir already exists: {task_dir}\n        use --force to overwrite.")
        clear_existing_task_dir(task_dir)

    env_dir = task_dir / "environment"
    solution_dir = task_dir / "solution"
    tests_dir = task_dir / "tests"
    for directory in (env_dir, solution_dir, tests_dir):
        directory.mkdir(parents=True, exist_ok=True)

    copy_codebase(sources["repo_dir"], env_dir / "codebase")  # type: ignore[arg-type]
    shutil.copy2(sources["planned_file"], env_dir / "planning.json")
    shutil.copy2(sources["eval_plan"], env_dir / "eval_plan.json")
    if sources["paper_json"] is not None:
        shutil.copy2(sources["paper_json"], env_dir / "paper.json")

    shutil.copy2(sources["unit_test_dir"] / "test_specs.json", tests_dir / "test_specs.json")  # type: ignore[operator]
    copy_tests(sources["tests_dir"], tests_dir)  # type: ignore[arg-type]
    asset_counts = copy_assets(sources["asset_dir"], env_dir)  # type: ignore[arg-type]

    # Branch on whether the suite has a comparison (reproduction) tier: it drives
    # timeouts, the default for verifier-time heavy-dep install, and rival stubs.
    specs = load_test_specs(sources["unit_test_dir"])  # type: ignore[arg-type]
    comparison_present = has_comparison_tier(specs)

    codebase_dir = env_dir / "codebase"
    if comparison_present and comparison_uses_rivals(codebase_dir / "tests" / "comparison"):
        maybe_install_rival_stubs(codebase_dir)

    n_base = write_requirements_base(codebase_dir, env_dir)
    print(f"[info] requirements-base.txt: {n_base} light dep(s) baked into image.")

    # Bump timeouts for reproduction runs, but only if the caller left the
    # module defaults untouched (don't override an explicit --*_timeout_sec).
    agent_timeout = args.agent_timeout_sec
    verifier_timeout = args.verifier_timeout_sec
    if comparison_present:
        if agent_timeout == DEFAULT_AGENT_TIMEOUT_SEC:
            agent_timeout = 7200
        if verifier_timeout == DEFAULT_VERIFIER_TIMEOUT_SEC:
            verifier_timeout = 3600
    install_reqs_default = "1" if comparison_present else "0"

    repro_files = detect_repro_files(codebase_dir)
    (task_dir / "task.toml").write_text(
        TASK_TOML.format(
            difficulty=args.difficulty,
            task_slug=task_slug,
            agent_timeout_sec=agent_timeout,
            verifier_timeout_sec=verifier_timeout,
            install_reqs_default=install_reqs_default,
        ),
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text(build_instruction_md(repro_files, asset_counts), encoding="utf-8")
    (env_dir / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")

    write_executable(solution_dir / "solve.sh", SOLVE_SH)
    write_executable(tests_dir / "test.sh", TEST_SH)
    (tests_dir / "compute_reward.py").write_text(COMPUTE_REWARD_PY, encoding="utf-8")
    (tests_dir / "analyze_failures.py").write_text(ANALYZE_FAILURES_PY, encoding="utf-8")
    (task_dir / "bundle_log.md").write_text(build_bundle_log(sources, task_dir), encoding="utf-8")

    return task_dir


def print_summary(task_dir: Path, args: argparse.Namespace) -> None:
    env_dir = task_dir / "environment"
    tests_dir = task_dir / "tests"
    task_slug = task_dir.name
    print(f"[OK] Harbor task created at: {task_dir}")
    print(f"     Bundle log: {task_dir / 'bundle_log.md'}")
    print()
    print("Smoke test:")
    print(f"  docker build -t p2c-{task_slug} {env_dir}")
    print(f"  docker run --rm -v {tests_dir}:/tests -v /tmp/p2c-{task_slug}-logs:/logs p2c-{task_slug} bash /tests/test.sh")
    print(f"  cat /tmp/p2c-{task_slug}-logs/verifier/summary.txt")
    print()
    harbor_root = Path(args.harbor_output_dir).resolve()
    print("Validate + boot via Harbor:")
    print(f"  harbor tasks check {task_dir} -m sonnet -o /tmp/{task_slug}-check.json")
    print(f"  harbor tasks start-env -p {task_dir} -e docker --non-interactive")
    print(f"  harbor run -p {harbor_root} -t {task_slug} -a openhands -m gpt-5 --debug")


def main() -> None:
    args = parse_args()
    sources = validate_inputs(args)
    task_dir = create_task(args, sources)
    print_summary(task_dir, args)


if __name__ == "__main__":
    main()
