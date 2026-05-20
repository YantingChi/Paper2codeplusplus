# =============================================================================
# harbor_serial_runner.py — Serial iterative harbor trial orchestrator.
#
# Runs harbor trials one at a time, using the best codebase from previous
# trials as the starting point for the next, and injecting failed-test hints
# into instruction.md so the agent knows exactly what to fix.
#
# Usage example:
#   python3 codes/harbor_serial_runner.py \
    #   --task_path  outputs/harbor_tasks/adaptive-pruning \
    #   --agent      codex \
    #   --model      gpt-5.2 \
    #   --jobs_dir   outputs/harbor_jobs \
    #   --job_name   adaptive-pruning \
    #   --max_iterations 5
# =============================================================================

import argparse
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 in some local test environments.
    tomllib = None

# Sentinel that marks the auto-generated hints block in instruction.md.
# Everything from this line onward is replaced on each iteration.
_HINTS_SENTINEL = "<!-- HARBOR_SERIAL_HINTS_START -->"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run harbor trials serially, carrying forward the best codebase."
    )
    p.add_argument("--task_path", required=True,
                   help="Path to the harbor task bundle directory.")
    p.add_argument("--agent", default="codex",
                   help="Harbor agent name (default: codex).")
    p.add_argument("--model", default="gpt-5.2",
                   help="Model name passed to the agent (default: gpt-5.2).")
    p.add_argument("--jobs_dir", required=True,
                   help="Root directory for harbor job outputs.")
    p.add_argument("--job_name", default="",
                   help="Job name prefix (default: basename of task_path).")
    p.add_argument("--max_iterations", type=int, default=5,
                   help="Maximum number of serial trials (default: 5).")
    p.add_argument("--max_no_improvement", type=int, default=3,
                   help="Stop after this many consecutive non-improving trials (default: 3; 0 disables).")
    p.add_argument("--no_stop_on_perfect", action="store_true",
                   help="Keep running even after score reaches 1.0.")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Harbor invocation
# ---------------------------------------------------------------------------

def allocate_run_root(jobs_dir: Path, job_name: str) -> Path:
    """Return a fresh root directory for this serial-run invocation."""
    base = jobs_dir / f"{job_name}_serial"
    if not base.exists():
        return base

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for suffix in range(1000):
        suffix_text = "" if suffix == 0 else f"_{suffix}"
        candidate = jobs_dir / f"{job_name}_serial_{timestamp}{suffix_text}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not allocate a fresh run directory under {jobs_dir}")


def should_force_build_after_carry_forward(task_path: Path) -> bool:
    """Force rebuilds only when Harbor would otherwise use a prebuilt image."""
    task_file = task_path / "task.toml"
    if not task_file.exists():
        return False
    task_text = task_file.read_text(encoding="utf-8")
    if tomllib is None:
        in_environment_section = False
        for raw_line in task_text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                in_environment_section = line.strip("[]").strip() == "environment"
                continue
            if in_environment_section and line.startswith("docker_image") and "=" in line:
                value = line.split("=", 1)[1].strip().strip("\"'")
                return bool(value)
        return False
    try:
        data = tomllib.loads(task_text)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(f"  [WARN] Could not parse {task_file}; not forcing rebuild: {exc}")
        return False
    environment = data.get("environment", {})
    return bool(isinstance(environment, dict) and environment.get("docker_image"))


def should_stop_after_no_improvement(streak: int, threshold: int) -> bool:
    """Return True when a non-improvement streak reaches the stop threshold."""
    return int(threshold) > 0 and int(streak) >= int(threshold)


def run_harbor_trial(
    task_path: Path,
    agent: str,
    model: str,
    iter_jobs_dir: Path,
    job_name: str,
    force_build: bool,
) -> Path:
    """Run one harbor trial and return the trial directory."""
    iter_jobs_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "harbor", "run",
        "--path",      str(task_path),
        "--agent",     agent,
        "--model",     model,
        "--jobs-dir",  str(iter_jobs_dir),
        "--job-name",  job_name,
        "--n-attempts", "1",
        "--artifact",  "/workspace/environment/codebase",
        "--artifact",  "/tests/scores/scores.json",
    ]
    if force_build:
        cmd.append("--force-build")

    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # Find the trial directory: <iter_jobs_dir>/<job_name>/<task_slug>__<suffix>/
    job_dir = iter_jobs_dir / job_name
    candidates = sorted(
        [d for d in job_dir.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        sys.exit(f"[ERROR] No trial directory found under {job_dir}")
    return candidates[0]


# ---------------------------------------------------------------------------
# Result reading
# ---------------------------------------------------------------------------

def read_score(trial_dir: Path) -> float:
    """Read the float reward from verifier/reward.txt (0.0 if missing)."""
    reward_file = trial_dir / "verifier" / "reward.txt"
    if not reward_file.exists():
        print(f"  [WARN] reward.txt not found at {reward_file}, assuming 0.0")
        return 0.0
    try:
        return float(reward_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return 0.0


def read_artifact_manifest(trial_dir: Path) -> list[dict]:
    """Read Harbor's artifact manifest, returning an empty list if unavailable."""
    manifest_file = trial_dir / "artifacts" / "manifest.json"
    if not manifest_file.exists():
        return []
    try:
        doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  [WARN] Could not parse artifact manifest: {exc}")
        return []
    return doc if isinstance(doc, list) else []


def resolve_artifact_path(
    trial_dir: Path,
    container_path: str,
    fallbacks: list[Path],
) -> Path | None:
    """Resolve an artifact path using Harbor's manifest and known layouts."""
    for entry in read_artifact_manifest(trial_dir):
        if entry.get("source") != container_path or entry.get("status") != "ok":
            continue
        destination = entry.get("destination")
        if not destination:
            continue
        candidate = trial_dir / destination
        if candidate.exists():
            return candidate

    for candidate in fallbacks:
        if candidate.exists():
            return candidate
    return None


def read_scores_json(trial_dir: Path) -> list[dict]:
    """Read test-level results from the downloaded scores.json artifact."""
    scores_file = resolve_artifact_path(
        trial_dir,
        "/tests/scores/scores.json",
        [
            # Current Harbor stores artifacts by basename plus manifest.
            trial_dir / "artifacts" / "scores.json",
            # Older local assumption: mirror absolute container path.
            trial_dir / "artifacts" / "tests" / "scores" / "scores.json",
        ],
    )
    if scores_file is None:
        print(f"  [WARN] scores.json artifact not found under {trial_dir / 'artifacts'}")
        return []
    try:
        return json.loads(scores_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  [WARN] Could not parse scores.json: {exc}")
        return []


def official_failed_tests(scores_data: list[dict], specs: dict[str, dict]) -> list[dict]:
    """Return failed score entries that correspond to official rubric specs."""
    if not specs:
        seen_ids = set()
        failed = []
        for result in scores_data:
            test_id = result.get("test_id")
            if result.get("passed", True) or test_id in seen_ids:
                continue
            seen_ids.add(test_id)
            failed.append(result)
        return failed

    passed_ids = {
        result.get("test_id")
        for result in scores_data
        if result.get("test_id") in specs and result.get("passed")
    }
    seen_failed_ids = set()
    failed = []
    for result in scores_data:
        test_id = result.get("test_id")
        if (
            test_id not in specs
            or test_id in passed_ids
            or result.get("passed", True)
            or test_id in seen_failed_ids
        ):
            continue
        seen_failed_ids.add(test_id)
        failed.append(result)
    return failed


def trial_failed_during_environment_build(trial_dir: Path) -> bool:
    """Return True when Harbor created an exception trial during Docker build."""
    exception_file = trial_dir / "exception.txt"
    if not exception_file.exists():
        return False
    text = exception_file.read_text(encoding="utf-8", errors="replace").lower()
    return (
        "docker compose command failed" in text
        and "build" in text
        and (
            "no space left on device" in text
            or "failed for environment" in text
        )
    )


# ---------------------------------------------------------------------------
# Test specs lookup
# ---------------------------------------------------------------------------

def load_test_specs(task_path: Path) -> dict[str, dict]:
    """Return a dict mapping test_id → spec dict from test_specs.json."""
    specs_file = task_path / "tests" / "test_specs.json"
    if not specs_file.exists():
        return {}
    doc = json.loads(specs_file.read_text(encoding="utf-8"))
    tests = doc.get("tests", []) if isinstance(doc, dict) else doc
    return {s["test_id"]: s for s in tests if "test_id" in s}


# ---------------------------------------------------------------------------
# Codebase carry-forward
# ---------------------------------------------------------------------------

def copy_codebase_from_trial(trial_dir: Path, task_path: Path) -> bool:
    """Replace task codebase with the agent-modified version from this trial."""
    src = resolve_artifact_path(
        trial_dir,
        "/workspace/environment/codebase",
        [
            # Current Harbor stores directory artifacts by basename plus manifest.
            trial_dir / "artifacts" / "codebase",
            # Older local assumption: mirror absolute container path.
            trial_dir / "artifacts" / "workspace" / "environment" / "codebase",
        ],
    )
    dst = task_path / "environment" / "codebase"

    if src is None:
        print(f"  [WARN] Codebase artifact not found under {trial_dir / 'artifacts'}, skipping carry-forward.")
        return False

    # Replace dst with a fresh copy of src.
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv"),
    )
    print(f"  [OK] Codebase updated from trial artifact.")
    return True


# ---------------------------------------------------------------------------
# Hint injection
# ---------------------------------------------------------------------------

def build_hints_block(
    iteration: int,
    last_score: float,
    best_score: float,
    total_tests: int,
    failed_tests: list[dict],
    specs: dict[str, dict],
) -> str:
    """Build the markdown hints block to append to instruction.md."""
    last_passed = round(last_score * total_tests)
    best_passed = round(best_score * total_tests)

    lines = [
        _HINTS_SENTINEL,
        "",
        "---",
        "## Hints from Previous Attempts (auto-generated — do not edit below this line)",
        "",
        f"**Iteration completed**: {iteration}  ",
        f"**Last attempt score**: {last_passed}/{total_tests} ({last_score * 100:.1f}%)  ",
        f"**Best score so far**: {best_passed}/{total_tests} ({best_score * 100:.1f}%)  ",
        "",
        f"### Still failing — fix these {len(failed_tests)} test(s):",
        "",
    ]

    for entry in failed_tests:
        test_id = entry.get("test_id", "?")
        rubric_id = entry.get("rubric_id", "")
        spec = specs.get(test_id, {})
        rubric_text = spec.get("rubric_text", entry.get("rubric_text", "(no description)"))
        longrepr = entry.get("longrepr") or ""

        lines.append(f"- **{test_id}** (`{rubric_id}`): {rubric_text}")
        if longrepr:
            # Indent the error snippet for readability.
            short_err = longrepr.strip().splitlines()
            short_err = short_err[-5:] if len(short_err) > 5 else short_err
            lines.append("  ```")
            for line in short_err:
                lines.append(f"  {line}")
            lines.append("  ```")

    lines.append("")
    return "\n".join(lines)


def inject_hints(task_path: Path, hints_block: str) -> None:
    """Write hints into instruction.md, replacing any previous hints block."""
    instr_file = task_path / "instruction.md"
    original = instr_file.read_text(encoding="utf-8")

    # Strip any previous hints block (everything from sentinel onward).
    if _HINTS_SENTINEL in original:
        base = original[: original.index(_HINTS_SENTINEL)].rstrip()
    else:
        base = original.rstrip()

    instr_file.write_text(base + "\n\n" + hints_block + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def backup_originals(task_path: Path) -> None:
    """Save pristine copies of instruction.md and environment/codebase/ once."""
    instr_orig = task_path / "instruction.md.orig"
    codebase_orig = task_path / "environment" / "codebase.orig"

    if not instr_orig.exists():
        shutil.copy2(task_path / "instruction.md", instr_orig)
        print("  [OK] Backed up instruction.md → instruction.md.orig")

    if not codebase_orig.exists():
        shutil.copytree(
            task_path / "environment" / "codebase",
            codebase_orig,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
        )
        print("  [OK] Backed up environment/codebase → environment/codebase.orig")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    task_path = Path(args.task_path).resolve()
    if not task_path.is_dir():
        sys.exit(f"[ERROR] task_path does not exist: {task_path}")

    jobs_dir = Path(args.jobs_dir).resolve()
    job_name = args.job_name or task_path.name
    specs = load_test_specs(task_path)
    total_tests = len(specs) or 1  # avoid division by zero

    backup_originals(task_path)

    best_score = 0.0
    codebase_carried_forward = False
    no_improvement_streak = 0
    history: list[dict] = []
    run_root = allocate_run_root(jobs_dir, job_name)
    run_root.mkdir(parents=True, exist_ok=False)
    force_build_supported = should_force_build_after_carry_forward(task_path)

    print(f"\n{'='*60}")
    print(f"  Serial harbor trials")
    print(f"  Task      : {task_path}")
    print(f"  Agent     : {args.agent}  |  Model: {args.model}")
    print(f"  Run dir   : {run_root}")
    print(
        f"  Max iters : {args.max_iterations}  |  "
        f"Max no-improve: {args.max_no_improvement}  |  Tests: {total_tests}"
    )
    if not force_build_supported:
        print("  Force build retries disabled: task does not configure a prebuilt image.")
    print(f"{'='*60}\n")

    for iteration in range(1, args.max_iterations + 1):
        print(f"\n{'─'*60}")
        print(f"  Iteration {iteration}/{args.max_iterations}")
        print(f"{'─'*60}")

        iter_jobs_dir = run_root / f"iter_{iteration}"
        force_build = (
            iteration > 1
            and codebase_carried_forward
            and force_build_supported
        )
        trial_dir = None
        build_retry_count = 0

        while True:
            try:
                trial_dir = run_harbor_trial(
                    task_path=task_path,
                    agent=args.agent,
                    model=args.model,
                    iter_jobs_dir=iter_jobs_dir,
                    job_name=job_name,
                    force_build=force_build,
                )
            except subprocess.CalledProcessError as exc:
                if force_build and build_retry_count == 0:
                    build_retry_count += 1
                    retry_jobs_dir = run_root / f"iter_{iteration}_retry_{build_retry_count}"
                    print(
                        "  [WARN] harbor run failed during forced rebuild "
                        f"(exit {exc.returncode}); retrying without --force-build in {retry_jobs_dir}."
                    )
                    iter_jobs_dir = retry_jobs_dir
                    force_build = False
                    continue
                print(f"  [ERROR] harbor run failed (exit {exc.returncode}). Stopping.")
                break

            if (
                trial_failed_during_environment_build(trial_dir)
                and force_build
                and build_retry_count == 0
            ):
                build_retry_count += 1
                retry_jobs_dir = run_root / f"iter_{iteration}_retry_{build_retry_count}"
                print(
                    "  [WARN] Forced Docker rebuild failed during environment build; "
                    f"retrying without --force-build in {retry_jobs_dir}."
                )
                iter_jobs_dir = retry_jobs_dir
                force_build = False
                continue

            break

        if trial_dir is None:
            break

        score = read_score(trial_dir)
        scores_data = read_scores_json(trial_dir)

        failed = official_failed_tests(scores_data, specs)
        passed_count = round(score * total_tests)

        delta = score - best_score
        improved = score > best_score

        print(f"\n  Score     : {passed_count}/{total_tests} ({score * 100:.1f}%)")
        print(f"  Delta     : {'+' if delta >= 0 else ''}{delta * 100:.1f}%")
        print(f"  Failing   : {len(failed)} test(s)")

        history.append({
            "iteration": iteration,
            "score": score,
            "passed": passed_count,
            "total": total_tests,
            "failed_count": len(failed),
            "trial_dir": str(trial_dir),
        })

        if improved:
            print(f"  → New best! Updating codebase base.")
            best_score = score
            no_improvement_streak = 0
            codebase_carried_forward = copy_codebase_from_trial(trial_dir, task_path)
        else:
            no_improvement_streak += 1
            print(f"  → No improvement (best still {round(best_score * total_tests)}/{total_tests}). Keeping best codebase.")

        if not args.no_stop_on_perfect and score >= 1.0:
            print("\n  [DONE] Perfect score achieved — stopping early.")
            break

        if should_stop_after_no_improvement(
            no_improvement_streak,
            args.max_no_improvement,
        ):
            print(
                "\n  [DONE] No score improvement for "
                f"{no_improvement_streak} consecutive iteration(s) — stopping early."
            )
            break

        # Always inject hints for the next iteration (unless this is the last).
        if iteration < args.max_iterations and failed:
            hints = build_hints_block(
                iteration=iteration,
                last_score=score,
                best_score=best_score,
                total_tests=total_tests,
                failed_tests=failed,
                specs=specs,
            )
            inject_hints(task_path, hints)
            print(f"  → Injected {len(failed)} failing-test hints into instruction.md.")

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("  Final Summary")
    print(f"{'='*60}")
    print(f"  {'Iter':>4}  {'Score':>8}  {'Passed':>8}  {'Failed':>8}")
    print(f"  {'----':>4}  {'-----':>8}  {'------':>8}  {'------':>8}")
    for h in history:
        marker = " ← best" if h["score"] == best_score else ""
        print(
            f"  {h['iteration']:>4}  "
            f"{h['score'] * 100:>7.1f}%  "
            f"{h['passed']:>5}/{h['total']:<2}  "
            f"{h['failed_count']:>8}{marker}"
        )
    best_passed = round(best_score * total_tests)
    print(f"\n  Best score: {best_passed}/{total_tests} ({best_score * 100:.1f}%)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
