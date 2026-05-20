"""Stage 10 (SkyDiscover variant) — Package Paper2Code pipeline outputs into
SkyDiscover's iterative-optimization format.

Produces:
  initial_program.py   — the generated codebase serialized as a Python dict
                          (CODEBASE = {relative_path: content}).  SkyDiscover
                          evolves this file across iterations.
  evaluator.py         — evaluate(program_path) -> {"combined_score": float}.
                          Extracts the codebase, runs the pytest suite from
                          stage 9, and returns the weighted pass-rate reward.
  test_specs.json      — copied from unit_test_dir (used by evaluator.py).
  tests/               — conftest.py, test_*.py, and compute_reward.py
                          (same scoring logic as the Harbor variant).

Substitute for 10_get_harbor_set.py when running SkyDiscover in Stage 11
instead of `harbor run`.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #

def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "task"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paper_name", required=True)
    p.add_argument("--paper_json_path", required=True,
                   help="Cleaned paper JSON.")
    p.add_argument("--planning_dir", required=True,
                   help="Directory 1_planning.py wrote into (must contain "
                        "planning_trajectories.json).")
    p.add_argument("--repo_dir", required=True,
                   help="Post-debug generated repo (stage 4 output).")
    p.add_argument("--unit_test_dir", required=True,
                   help="Directory 9_getting_unit_test.py wrote into.")
    p.add_argument("--harbor_asset_dir", default="",
                   help="(Optional) Directory 6_download_dataset.py wrote "
                        "into. Assets are noted in initial_program.py "
                        "PLANNING_CONTEXT but not copied (SkyDiscover runs "
                        "locally, not in Docker).")
    p.add_argument("--skydiscover_output_dir", default="./outputs/skydiscover_tasks",
                   help="Root output directory for SkyDiscover tasks.")
    p.add_argument("--task_slug", default="",
                   help="Subdirectory name inside skydiscover_output_dir. "
                        "Defaults to slugified paper_name.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite the task directory if it already exists.")
    return p.parse_args()


# --------------------------------------------------------------------------- #
# Input validation (mirrors 10_get_harbor_set.py)
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Codebase serialization
# --------------------------------------------------------------------------- #

_SKIP_PATTERNS = frozenset({"__pycache__", ".git", ".venv", ".mypy_cache"})
_SKIP_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".pyd",
    # binary ML model / data formats — contain null bytes that break Python source files
    ".safetensors", ".pt", ".pth", ".pkl", ".ckpt",
    ".bin", ".h5", ".hdf5", ".npz", ".npy",
})


def serialize_codebase(repo_dir: Path) -> dict[str, str]:
    """Walk repo_dir and return {relative_path: content} for text files."""
    entries: dict[str, str] = {}
    for fp in sorted(repo_dir.rglob("*")):
        if fp.is_dir():
            continue
        if any(part in _SKIP_PATTERNS for part in fp.parts):
            continue
        if fp.suffix in _SKIP_SUFFIXES:
            continue
        rel = fp.relative_to(repo_dir).as_posix()
        try:
            entries[rel] = fp.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
        except Exception:
            pass  # skip unreadable binary files silently
    return entries


def _escape(s: str) -> str:
    """Escape backslashes and triple-quotes so content embeds safely."""
    return s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

INITIAL_PROGRAM_TEMPLATE = '''\
"""Auto-generated by 10_get_skyDiscover.py.

SkyDiscover will iteratively evolve CODEBASE to maximise combined_score
as reported by evaluator.py.

CODEBASE is a dict mapping relative file paths to their text content.
The evaluator extracts these files into a temporary directory, installs
dependencies, and runs the stage-9 pytest suite to compute a weighted
reward.

Do NOT edit this file by hand — regenerate via 10_get_skyDiscover.py.
"""

PAPER_NAME = {paper_name!r}

PLANNING_CONTEXT = {planning_context!r}

CODEBASE = {{
{codebase_entries}
}}
'''

EVALUATOR_PY = '''\
"""evaluate(program_path) -> {"combined_score": float}

Called by SkyDiscover after each iteration to score the evolved codebase.
Extracts the CODEBASE dict from the candidate program, writes it to a
temp directory, runs pytest with the stage-9 test suite, then computes the
weighted pass-rate reward via compute_reward.py.

Cost log: each call appends one JSON line to the file pointed to by
SKYDISCOVER_COST_LOG (env var) or cost_log.jsonl next to this file.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TESTS_DIR = Path(__file__).parent / "tests"
_SPECS_FILE = Path(__file__).parent / "test_specs.json"
_COST_LOG = Path(os.environ.get("SKYDISCOVER_COST_LOG",
                                str(Path(__file__).parent / "cost_log.jsonl")))
_ITER_FILE = Path(__file__).parent / "_eval_count.json"


def _next_iteration() -> int:
    """Increment and return a persistent per-run iteration counter."""
    try:
        n = json.loads(_ITER_FILE.read_text()) + 1
    except Exception:
        n = 1
    _ITER_FILE.write_text(json.dumps(n))
    return n


def _estimate_tokens(codebase: dict) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return max(1, sum(len(v) for v in codebase.values()) // 4)


def _log_cost(iteration: int, score: float, duration_s: float, tokens_est: int) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "iteration": iteration,
        "score": round(score, 6),
        "duration_s": round(duration_s, 2),
        "codebase_tokens_est": tokens_est,
    }
    _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _COST_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\\n")


def evaluate(program_path: str) -> dict:
    t0 = time.monotonic()
    program_path = str(program_path)

    # ---- load the evolved program ---------------------------------------- #
    spec = importlib.util.spec_from_file_location("_candidate", program_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"[evaluator] failed to load {program_path}: {exc}", file=sys.stderr)
        return {"combined_score": 0.0}

    codebase: dict = getattr(module, "CODEBASE", {})
    if not codebase:
        print("[evaluator] CODEBASE is empty — returning 0.0", file=sys.stderr)
        return {"combined_score": 0.0}

    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)
        codebase_dir = tmp / "codebase"
        scores_dir = tmp / "scores"
        codebase_dir.mkdir()
        scores_dir.mkdir()

        # ---- write codebase files ---------------------------------------- #
        for rel_path, content in codebase.items():
            dst = codebase_dir / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                dst.write_text(content, encoding="utf-8")
            except Exception:
                pass

        # ---- install requirements ---------------------------------------- #
        req_file = codebase_dir / "requirements.txt"
        if req_file.is_file():
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
                check=False,
            )

        # ---- build environment matching test.sh -------------------------- #
        scores_json = scores_dir / "scores.json"
        reward_txt = tmp / "reward.txt"
        env = {
            **os.environ,
            "PAPER2CODE_REPO_PATH": str(codebase_dir),
            "PAPER2CODE_SCORE_PATH": str(scores_json),
            "PYTHONPATH": ":".join(filter(None, [
                str(codebase_dir),
                str(tmp),
                os.environ.get("PYTHONPATH", ""),
            ])),
        }

        # ---- run pytest -------------------------------------------------- #
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no", str(_TESTS_DIR)],
            env=env,
            check=False,
        )

        # ---- compute reward ---------------------------------------------- #
        subprocess.run(
            [
                sys.executable,
                str(_TESTS_DIR / "compute_reward.py"),
                "--specs", str(_SPECS_FILE),
                "--scores", str(scores_json),
                "--output", str(reward_txt),
            ],
            env=env,
            check=False,
        )

        try:
            score = float(reward_txt.read_text().strip())
        except Exception:
            score = 0.0

    # ---- cost logging ---------------------------------------------------- #
    iteration = _next_iteration()
    duration_s = time.monotonic() - t0
    tokens_est = _estimate_tokens(codebase)
    _log_cost(iteration, score, duration_s, tokens_est)
    print(
        f"[evaluator] iter={iteration:>3}  score={score:.4f}"
        f"  dur={duration_s:.1f}s  tokens_est≈{tokens_est:,}",
        flush=True,
    )

    return {"combined_score": score}


if __name__ == "__main__":
    # Quick smoke-test: score the initial_program.py sitting next to this file.
    initial = Path(__file__).parent / "initial_program.py"
    if not initial.is_file():
        print("Usage: python evaluator.py  (run from the task directory)")
        sys.exit(1)
    result = evaluate(str(initial))
    print(f"Initial score: {result[\'combined_score\']:.4f}")
'''

COMPUTE_REWARD_PY = '''\
import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--specs", required=True)
ap.add_argument("--scores", required=True)
ap.add_argument("--output", required=True)
args = ap.parse_args()

specs_doc = json.loads(Path(args.specs).read_text(encoding="utf-8"))
specs = specs_doc.get("tests", []) if isinstance(specs_doc, dict) else specs_doc

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
    out_root = Path(args.skydiscover_output_dir).resolve()

    planning_file = validate_inputs(paper_json, planning_dir, repo_dir, unit_test_dir)

    task_slug = args.task_slug.strip() or slugify(args.paper_name)
    task_dir = out_root / task_slug

    if task_dir.exists():
        if not args.force:
            sys.exit(
                f"[ERROR] task dir already exists: {task_dir}\n"
                f"        use --force to overwrite."
            )
        shutil.rmtree(task_dir, ignore_errors=True)

    task_dir.mkdir(parents=True, exist_ok=True)
    tests_out = task_dir / "tests"
    tests_out.mkdir()

    # ---- build initial_program.py --------------------------------------- #
    print("[10_get_skyDiscover] serializing codebase …")
    codebase = serialize_codebase(repo_dir)
    if not codebase:
        sys.exit(f"[ERROR] no readable files found in repo_dir: {repo_dir}")

    planning_context = planning_file.read_text(encoding="utf-8", errors="replace")

    codebase_lines = []
    for rel, content in codebase.items():
        escaped = _escape(content)
        codebase_lines.append(f'    {rel!r}: """{escaped}""",')
    codebase_entries = "\n".join(codebase_lines)

    initial_program_text = INITIAL_PROGRAM_TEMPLATE.format(
        paper_name=args.paper_name,
        planning_context=planning_context,
        codebase_entries=codebase_entries,
    )
    (task_dir / "initial_program.py").write_text(initial_program_text, encoding="utf-8")

    # ---- write evaluator.py --------------------------------------------- #
    (task_dir / "evaluator.py").write_text(EVALUATOR_PY, encoding="utf-8")

    # ---- copy test artifacts -------------------------------------------- #
    shutil.copy2(unit_test_dir / "test_specs.json", task_dir / "test_specs.json")

    src_tests = unit_test_dir / "tests"
    for fp in sorted(src_tests.iterdir()):
        if fp.is_file() and (
            fp.name.startswith("test_")
            or fp.name in {"conftest.py", "pytest.ini"}
        ):
            shutil.copy2(fp, tests_out / fp.name)
    (tests_out / "compute_reward.py").write_text(COMPUTE_REWARD_PY, encoding="utf-8")

    # ---- summary -------------------------------------------------------- #
    n_files = len(codebase)
    print(f"[OK] SkyDiscover task created at: {task_dir}")
    print(f"     Serialized {n_files} codebase file{'s' if n_files != 1 else ''}.")
    print()
    print("Smoke test (from task directory):")
    print(f"  cd {task_dir}")
    print(f"  python evaluator.py   # scores initial_program.py without evolution")
    print()
    print("Run with SkyDiscover:")
    print(
        f"  skydiscover-run {task_dir}/initial_program.py "
        f"{task_dir}/evaluator.py "
        f"--search adaevolve "
        f"--model <model> "
        f"--iterations 10 "
        f"--output <jobs_dir>/{task_slug}"
    )


if __name__ == "__main__":
    main()
