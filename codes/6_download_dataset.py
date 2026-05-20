# Run Codex asset extraction for Paper2Code reproduction setup.
# This script sends the reproduction setup prompt to `codex exec`, so Codex can
# inspect the paper JSON and generated repo, scaffold reproducibility support,
# and fetch, clone, or synthesize benchmark, metric, and rival assets.
#
# Example:
# python3.10 codes/c1_download_dataset.py \
#   --paper_json_path /mnt/blk1/Paper2Code/data/paperbench_jsons/adaptive-pruning/paper_cleaned.json \
#   --generated_repo_path /mnt/blk1/Paper2Code/outputs/paperbench_repos/adaptive-pruning_repo \
#   --gpt_version gpt-5.4
#
# Dry-run example:
# python3.10 codes/c1_download_dataset.py \
#   --paper_json_path paper.json \
#   --generated_repo_path generated_repo \
#   --dry-run
#
# Stored in /mnt/blk1/Paper2Code/tests/harbor/yantingchi/$papername/harbor/asset/

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "c1_download_dataset_prompt.txt"
DEFAULT_HARBOR_ROOT = REPO_ROOT / "tests" / "harbor" / "yantingchi"

ASSET_DIRS = {
    "benchmarks": Path("assets") / "benchmarks",
    "rivals": Path("assets") / "rivals",
    "metrics": Path("assets") / "metrics",
}

REQUIRED_REPO_FILES = [
    "reproduction_setup.md",
    "scripts/fetch_baselines.sh",
    "src/metrics.py",
    "configs/datasets.yaml",
    "configs/baselines.yaml",
    "tests/test_data_prep.py",
    "tests/test_metrics.py",
    "tests/test_baseline_configs.py",
]

DATA_PREP_ALTERNATIVES = [
    "scripts/prepare_data.py",
    "scripts/prepare_data.sh",
]


class ValidationReport(NamedTuple):
    ok: bool
    missing: List[str]


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Codex to extract a paper's reproduction setup and materialize "
            "benchmark, metric, and rival assets."
        )
    )
    parser.add_argument("--paper_json_path", type=str, required=True)
    parser.add_argument("--generated_repo_path", type=str, required=True)
    parser.add_argument(
        "--rubric_json_path",
        type=str,
        default="",
        help="Optional legacy rubric path kept for pipeline compatibility.",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="",
        help="Optional output directory. Defaults to the Harbor asset path.",
    )
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument(
        "--prompt_path",
        type=str,
        default=str(DEFAULT_PROMPT_PATH),
        help="Path to the reproduction setup prompt sent to Codex.",
    )
    parser.add_argument(
        "--codex_bin",
        type=str,
        default="codex",
        help="Codex CLI executable to run.",
    )
    parser.add_argument(
        "--codex_timeout",
        type=int,
        default=0,
        help="Optional timeout in seconds. Use 0 for no timeout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write prompt and metadata, but do not invoke Codex.",
    )
    parser.add_argument(
        "--call_model",
        action="store_true",
        help="Deprecated compatibility flag; Codex runs by default unless --dry-run is set.",
    )
    parser.add_argument(
        "--eval_info_json",
        type=str,
        default="",
        help=(
            "Optional path to the JSON produced by 5_eval_get_running_info.py. "
            "When provided, the 'Baseline compared with' list becomes the authoritative "
            "rival source and per-baseline retry is enabled."
        ),
    )
    parser.add_argument(
        "--eval_plan_json",
        type=str,
        default="",
        help=(
            "Optional path to the JSON produced by 5.1_get_evaluation_plan.py. "
            "When provided, its richer baseline plan (official_repo_url, "
            "implementation_type, hf_dataset_id …) supersedes --eval_info_json "
            "for the runtime context given to codex."
        ),
    )
    parser.add_argument(
        "--max_baseline_retries",
        type=int,
        default=1,
        help="How many focused codex retries to do per missing baseline after the batch call.",
    )
    parser.add_argument(
        "--max_download_gb",
        type=float,
        default=5.0,
        help=(
            "Per-paper soft cap on total bytes written under harbor_asset_output_dir, "
            "in GiB (default 5.0). Codex is told to self-limit; the python wrapper also "
            "produces a filesystem-grounded report after the run."
        ),
    )
    return parser.parse_args(argv)


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_optional_json(path: str | Path | None) -> dict:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return load_json(candidate)


def load_baseline_list(eval_info_path: str | Path) -> List[Dict[str, str]]:
    # Read 5_eval_get_running_info.py output and return a normalized baseline list.
    # Each item dict has: name, paper_citation, download_link, short_description, slug.
    if not eval_info_path:
        return []
    payload = load_optional_json(eval_info_path)
    if not payload:
        return []
    extracted = payload.get("extracted_info", {})
    raw_list = extracted.get("Baseline compared with", []) if isinstance(extracted, dict) else []
    normalized: List[Dict[str, str]] = []
    seen_slugs: set[str] = set()
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        slug = slugify(name)
        # Disambiguate slug collisions (e.g., two entries normalize to the same slug).
        unique_slug = slug
        suffix_idx = 2
        while unique_slug in seen_slugs:
            unique_slug = f"{slug}_{suffix_idx}"
            suffix_idx += 1
        seen_slugs.add(unique_slug)
        normalized.append({
            "name": name,
            "paper_citation": str(entry.get("paper_citation", "")).strip(),
            "download_link": str(entry.get("download_link", "")).strip(),
            "short_description": str(entry.get("short_description", "")).strip(),
            "slug": unique_slug,
        })
    return normalized


def load_eval_plan(eval_plan_path: str | Path) -> Dict[str, object] | None:
    # Read 5.1_get_evaluation_plan.py output. Returns the inner `eval_plan` dict,
    # or None if the file is absent / malformed.
    if not eval_plan_path:
        return None
    payload = load_optional_json(eval_plan_path)
    if not payload:
        return None
    plan = payload.get("eval_plan", payload)
    if not isinstance(plan, dict):
        return None
    return plan


def baselines_from_eval_plan(
    eval_plan: Dict[str, object],
    output_dir: str | Path,
) -> List[Dict[str, str]]:
    # Extract and normalise the baseline list from the 5.1 evaluation plan.
    # Each returned item has: name, slug, paper_citation, implementation_type,
    # official_repo_url, huggingface_id, install_cmd, priority,
    # estimated_size_mb, notes — plus expected_rival_dir.
    raw_list = eval_plan.get("baselines", [])
    if not isinstance(raw_list, list):
        return []
    rivals_root = Path(output_dir).resolve() / ASSET_DIRS["rivals"]
    seen_slugs: set[str] = set()
    normalized: List[Dict[str, str]] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        # Prefer the slug the model assigned; fall back to slugify(name).
        slug = str(entry.get("slug", "")).strip() or slugify(name)
        slug = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_") or "unknown"
        # Disambiguate slug collisions.
        unique_slug = slug
        idx = 2
        while unique_slug in seen_slugs:
            unique_slug = f"{slug}_{idx}"
            idx += 1
        seen_slugs.add(unique_slug)
        item: Dict[str, str] = {
            "name": name,
            "slug": unique_slug,
            "paper_citation": str(entry.get("paper_citation", "")).strip(),
            "implementation_type": str(entry.get("implementation_type", "implement")).strip(),
            "official_repo_url": str(entry.get("official_repo_url", "")).strip(),
            "huggingface_id": str(entry.get("huggingface_id", "")).strip(),
            "install_cmd": str(entry.get("install_cmd", "")).strip(),
            "priority": str(entry.get("priority", "main")).strip(),
            "estimated_size_mb": str(entry.get("estimated_size_mb", 0)),
            "notes": str(entry.get("notes", "")).strip(),
            # Legacy keys for backward-compat with existing validation helpers.
            "download_link": str(entry.get("official_repo_url", "")).strip(),
            "short_description": str(entry.get("notes", "")).strip(),
            "expected_rival_dir": str((rivals_root / unique_slug).resolve()),
        }
        normalized.append(item)
    return normalized


def datasets_from_eval_plan(eval_plan: Dict[str, object]) -> List[Dict[str, str]]:
    # Extract and normalise the dataset list from the 5.1 evaluation plan.
    raw_list = eval_plan.get("datasets", [])
    if not isinstance(raw_list, list):
        return []
    seen_slugs: set[str] = set()
    normalized: List[Dict[str, str]] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        slug = str(entry.get("slug", "")).strip() or slugify(name)
        slug = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_") or "unknown"
        unique_slug = slug
        idx = 2
        while unique_slug in seen_slugs:
            unique_slug = f"{slug}_{idx}"
            idx += 1
        seen_slugs.add(unique_slug)
        normalized.append({
            "name": name,
            "slug": unique_slug,
            "fetch_method": str(entry.get("fetch_method", "hf_dataset")).strip(),
            "hf_dataset_id": str(entry.get("hf_dataset_id", "")).strip(),
            "hf_dataset_config": str(entry.get("hf_dataset_config", "")).strip(),
            "download_url": str(entry.get("download_url", "")).strip(),
            "splits_needed": [str(s) for s in (entry.get("splits_needed") or [])],
            "estimated_size_mb": str(entry.get("estimated_size_mb", 0)),
            "license": str(entry.get("license", "")).strip(),
            "priority": str(entry.get("priority", "main")).strip(),
            "notes": str(entry.get("notes", "")).strip(),
        })
    return normalized


def load_prompt(path: str | Path = DEFAULT_PROMPT_PATH) -> str:
    return Path(path).read_text(encoding="utf-8")


def normalize_text(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("_", " ")
    normalized = normalized.replace("/", " ")
    normalized = normalized.replace("–", "-")
    normalized = normalized.replace("−", "-")
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def slugify(value: str) -> str:
    slug = normalize_text(value).replace(" ", "_")
    return slug or "unknown"


def resolve_paper_name(
    paper_payload: dict,
    rubric_payload: dict | None = None,
    paper_json_path: str | Path | None = None,
) -> str:
    candidates: List[Any] = [
        paper_payload.get("title"),
        paper_payload.get("paper_title"),
        paper_payload.get("paper_name"),
    ]

    rubric_payload = rubric_payload or {}
    reproduction_rubric = rubric_payload.get("reproduction_rubric", {})
    candidates.extend(
        [
            rubric_payload.get("paper_name"),
            rubric_payload.get("paper_title"),
            rubric_payload.get("title"),
        ]
    )
    if isinstance(reproduction_rubric, dict):
        candidates.extend(
            [
                reproduction_rubric.get("paper_name"),
                reproduction_rubric.get("paper_title"),
                reproduction_rubric.get("title"),
            ]
        )

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text

    if paper_json_path:
        return Path(paper_json_path).stem
    return "unknown"


def resolve_output_dir(args: argparse.Namespace, paper_name: str) -> Path:
    if getattr(args, "output_dir", ""):
        return Path(args.output_dir).resolve()
    return DEFAULT_HARBOR_ROOT / slugify(paper_name) / "harbor" / "asset"


def build_asset_paths(output_dir: str | Path) -> Dict[str, str]:
    root = Path(output_dir).resolve()
    return {name: str((root / relative).resolve()) for name, relative in ASSET_DIRS.items()}


def ensure_asset_dirs(output_dir: str | Path) -> None:
    root = Path(output_dir)
    for relative in ASSET_DIRS.values():
        (root / relative).mkdir(parents=True, exist_ok=True)


def build_runtime_context(
    paper_json_path: str | Path,
    generated_repo_path: str | Path,
    rubric_json_path: str | Path | None,
    output_dir: str | Path,
    paper_name: str = "",
    baselines: List[Dict[str, str]] | None = None,
    eval_info_json_path: str | Path | None = None,
    max_download_gb: float = 5.0,
    datasets_plan: List[Dict[str, str]] | None = None,
    eval_plan_json_path: str | Path | None = None,
) -> str:
    output_root = Path(output_dir).resolve()
    context: Dict[str, object] = {
        "paper_name": paper_name,
        "paper_json_path": str(Path(paper_json_path).resolve()),
        "generated_repo_path": str(Path(generated_repo_path).resolve()),
        "rubric_json_path": str(Path(rubric_json_path).resolve()) if rubric_json_path else "",
        "harbor_asset_output_dir": str(output_root),
        "required_asset_dirs": build_asset_paths(output_root),
        "required_repo_outputs": {
            "report": "reproduction_setup.md",
            "data_prep": DATA_PREP_ALTERNATIVES,
            "baseline_fetch": "scripts/fetch_baselines.sh",
            "metrics": "src/metrics.py",
            "dataset_config": "configs/datasets.yaml",
            "baseline_config": "configs/baselines.yaml",
            "tests": [
                "tests/test_data_prep.py",
                "tests/test_metrics.py",
                "tests/test_baseline_configs.py",
            ],
        },
    }
    if baselines:
        rivals_root = output_root / ASSET_DIRS["rivals"]
        # expected_rival_dir is already set when baselines come from eval_plan;
        # for eval_info baselines we compute it here.
        baselines_with_paths = [
            (
                b
                if "expected_rival_dir" in b
                else {**b, "expected_rival_dir": str((rivals_root / b["slug"]).resolve())}
            )
            for b in baselines
        ]
        if eval_plan_json_path:
            context["baselines_from_eval_plan"] = baselines_with_paths
            context["eval_plan_json_path"] = str(Path(eval_plan_json_path).resolve())
        else:
            context["baselines_from_eval_info"] = baselines_with_paths
        context["eval_info_json_path"] = (
            str(Path(eval_info_json_path).resolve()) if eval_info_json_path else ""
        )
    if datasets_plan:
        benchmarks_root = output_root / ASSET_DIRS["benchmarks"]
        datasets_with_paths = [
            {**d, "expected_benchmark_dir": str((benchmarks_root / d["slug"]).resolve())}
            for d in datasets_plan
        ]
        context["datasets_from_eval_plan"] = datasets_with_paths
    context["max_download_gb"] = float(max_download_gb)
    context["max_download_bytes"] = int(float(max_download_gb) * (1024 ** 3))
    return (
        "Runtime context for Codex asset extraction. Use these exact paths.\n"
        + json.dumps(context, indent=2, ensure_ascii=False)
        + "\n\nDo not stop after writing a plan. Modify the generated repo and "
        "place real downloaded, cloned, or synthesized assets under the required "
        "assets/benchmarks, assets/rivals, and assets/metrics directories."
    )


def build_full_prompt(prompt_text: str, runtime_context: str) -> str:
    return (
        prompt_text.rstrip()
        + "\n\n---\n"
        + runtime_context.strip()
        + "\n\nYou are running in non-interactive Codex. Complete the work end to end, "
        "run the lightweight verification commands you add, and summarize what was "
        "created in your final response."
    )


def build_codex_command(
    codex_bin: str,
    generated_repo_path: str | Path,
    output_dir: str | Path,
    model_name: str,
    last_message_path: str | Path,
) -> List[str]:
    return [
        codex_bin,
        "exec",
        "--cd",
        str(Path(generated_repo_path).resolve()),
        "--add-dir",
        str(Path(output_dir).resolve()),
        "--sandbox",
        "danger-full-access",
        # codex CLI v0.125.0 dropped `--ask-for-approval` and `--search` as exec flags.
        # `--sandbox danger-full-access` already implies approval=never (verified via
        # `codex exec --sandbox danger-full-access ...`). Web search is now opt-in via
        # the feature flag below; we want it on so codex can find baseline source URLs
        # and dataset/license info that aren't in the paper.
        "--enable",
        "web_search",
        "--json",
        "--skip-git-repo-check",
        "-o",
        str(Path(last_message_path).resolve()),
        "-m",
        model_name,
        "-",
    ]


def _has_any_file(path: Path) -> bool:
    return path.exists() and any(candidate.is_file() for candidate in path.rglob("*"))


def validate_generated_artifacts(
    generated_repo_path: str | Path,
    output_dir: str | Path,
) -> ValidationReport:
    repo = Path(generated_repo_path)
    asset_root = Path(output_dir)
    missing: List[str] = []

    for relative in REQUIRED_REPO_FILES:
        if not (repo / relative).is_file():
            missing.append(relative)

    if not any((repo / relative).is_file() for relative in DATA_PREP_ALTERNATIVES):
        missing.append("scripts/prepare_data.py or scripts/prepare_data.sh")

    for name, relative in ASSET_DIRS.items():
        asset_dir = asset_root / relative
        if not asset_dir.is_dir():
            missing.append(str(relative))
        elif not _has_any_file(asset_dir):
            missing.append(f"{relative} must contain at least one file or manifest")

    return ValidationReport(ok=len(missing) == 0, missing=missing)


def validate_baselines(
    output_dir: str | Path,
    baselines: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    # Return the subset of baselines whose assets/rivals/<slug>/ dir is missing or empty.
    rivals_root = Path(output_dir) / ASSET_DIRS["rivals"]
    missing: List[Dict[str, str]] = []
    for baseline in baselines:
        target = rivals_root / baseline["slug"]
        if not target.is_dir() or not _has_any_file(target):
            missing.append(baseline)
    return missing


def _format_bytes(num_bytes: int) -> str:
    # Human-readable size, binary units (matches `du -h`).
    step = 1024.0
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(num_bytes) < step:
            return f"{num_bytes:,.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= step
    return f"{num_bytes:,.1f} PiB"


def _walk_size(path: Path) -> tuple[int, int]:
    # Returns (total_bytes, file_count) under path, ignoring symlinks.
    total = 0
    files = 0
    if not path.exists():
        return 0, 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            try:
                total += entry.stat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def _read_manifest_fields(item_dir: Path) -> Dict[str, str]:
    # Pull a few fields from the item's manifest.json if present. Best-effort; missing
    # fields just become empty strings. Distinguishes a real download from a stub.
    fields = {"fetch_method": "", "source_url": "", "name": ""}
    manifest = item_dir / "manifest.json"
    if not manifest.is_file():
        return fields
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return fields
    if isinstance(data, dict):
        for key in fields:
            value = data.get(key, "")
            if isinstance(value, str):
                fields[key] = value
    has_budget_skip = (item_dir / "BUDGET_SKIPPED.md").is_file()
    if has_budget_skip:
        fields["fetch_method"] = fields["fetch_method"] or "budget_skipped"
    return fields


def compute_download_summary(
    output_dir: str | Path,
    baselines: List[Dict[str, str]] | None,
    max_download_gb: float,
) -> Dict[str, object]:
    # Filesystem-grounded report. Walks each category dir, sums bytes, reads manifests.
    output_root = Path(output_dir)
    max_bytes = int(max_download_gb * (1024 ** 3))
    summary: Dict[str, object] = {
        "output_dir": str(output_root.resolve()),
        "budget": {
            "max_gb": float(max_download_gb),
            "max_bytes": max_bytes,
            "used_bytes": 0,
            "remaining_bytes": max_bytes,
            "exceeded": False,
        },
        "categories": {},
        "items": [],
        "expected_baselines": {
            "total": len(baselines or []),
            "present": 0,
            "missing_slugs": [],
        },
    }

    for category, relative in ASSET_DIRS.items():
        cat_dir = output_root / relative
        cat_bytes = 0
        cat_files = 0
        cat_subdirs: List[Path] = []
        if cat_dir.is_dir():
            cat_subdirs = sorted([p for p in cat_dir.iterdir() if p.is_dir()])
            # Files directly under the category dir (e.g. datasets_inventory.json) count too.
            for direct in cat_dir.iterdir():
                if direct.is_file() and not direct.is_symlink():
                    try:
                        cat_bytes += direct.stat().st_size
                        cat_files += 1
                    except OSError:
                        pass
        for sub in cat_subdirs:
            sub_bytes, sub_files = _walk_size(sub)
            fields = _read_manifest_fields(sub)
            summary["items"].append({
                "category": category,
                "name": sub.name,
                "path": str(sub.resolve()),
                "bytes": sub_bytes,
                "bytes_human": _format_bytes(sub_bytes),
                "files": sub_files,
                "fetch_method": fields["fetch_method"],
                "source_url": fields["source_url"],
            })
            cat_bytes += sub_bytes
            cat_files += sub_files
        summary["categories"][category] = {
            "bytes": cat_bytes,
            "bytes_human": _format_bytes(cat_bytes),
            "files": cat_files,
            "subdirs": len(cat_subdirs),
        }

    if baselines:
        rivals_root = output_root / ASSET_DIRS["rivals"]
        present_slugs = {p.name for p in rivals_root.iterdir() if p.is_dir()} if rivals_root.is_dir() else set()
        expected_slugs = [b["slug"] for b in baselines]
        summary["expected_baselines"]["present"] = sum(1 for s in expected_slugs if s in present_slugs)
        summary["expected_baselines"]["missing_slugs"] = [s for s in expected_slugs if s not in present_slugs]

    used = sum(c["bytes"] for c in summary["categories"].values())
    summary["budget"]["used_bytes"] = used
    summary["budget"]["used_human"] = _format_bytes(used)
    summary["budget"]["remaining_bytes"] = max(0, max_bytes - used)
    summary["budget"]["remaining_human"] = _format_bytes(max(0, max_bytes - used))
    summary["budget"]["exceeded"] = used > max_bytes
    return summary


def print_download_report(summary: Dict[str, object]) -> None:
    # Human-readable summary printed to stdout at the end of main().
    print()
    print("=" * 72)
    print("Download report (filesystem-grounded)")
    print("=" * 72)
    budget = summary["budget"]
    cap_marker = "  EXCEEDED" if budget["exceeded"] else ""
    print(
        f"Budget: {_format_bytes(budget['used_bytes'])} used / "
        f"{_format_bytes(budget['max_bytes'])} cap"
        f"  (remaining: {_format_bytes(budget['remaining_bytes'])}){cap_marker}"
    )
    print("-" * 72)
    print(f"{'category':<12s} {'subdirs':>8s} {'files':>8s} {'on-disk':>14s}")
    for category, info in summary["categories"].items():
        print(f"{category:<12s} {info['subdirs']:>8d} {info['files']:>8d} "
              f"{info['bytes_human']:>14s}")
    print("-" * 72)
    if summary["items"]:
        print(f"{'category/name':<40s} {'fetch_method':<18s} {'size':>10s} {'files':>7s}")
        for item in sorted(summary["items"], key=lambda x: -x["bytes"]):
            label = f"{item['category']}/{item['name']}"
            method = item["fetch_method"] or "?"
            print(f"{label:<40s} {method:<18s} {item['bytes_human']:>10s} {item['files']:>7d}")
    eb = summary["expected_baselines"]
    if eb["total"]:
        print("-" * 72)
        print(f"Expected baselines from eval_info: {eb['present']}/{eb['total']} present")
        if eb["missing_slugs"]:
            print(f"  Missing slugs: {', '.join(eb['missing_slugs'])}")
    print("=" * 72)


def write_download_summary(summary: Dict[str, object], output_dir: str | Path) -> Path:
    out_path = Path(output_dir) / "c1_download_dataset_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def build_baseline_retry_prompt(
    prompt_template: str,
    paper_json_path: str | Path,
    generated_repo_path: str | Path,
    rubric_json_path: str | Path | None,
    output_dir: str | Path,
    paper_name: str,
    baseline: Dict[str, str],
    eval_info_json_path: str | Path | None,
    max_download_gb: float = 5.0,
    eval_plan_json_path: str | Path | None = None,
) -> str:
    # Reuse build_runtime_context but with a single-baseline list, then prepend a banner
    # so codex understands this is a focused retry call.
    runtime_context = build_runtime_context(
        paper_json_path=paper_json_path,
        generated_repo_path=generated_repo_path,
        rubric_json_path=rubric_json_path,
        output_dir=output_dir,
        paper_name=paper_name,
        baselines=[baseline],
        eval_info_json_path=eval_info_json_path,
        max_download_gb=max_download_gb,
        eval_plan_json_path=eval_plan_json_path,
    )
    retry_banner = (
        f"FOCUSED RETRY: only materialize the single rival '{baseline['name']}' "
        f"into '{(Path(output_dir) / ASSET_DIRS['rivals'] / baseline['slug']).resolve()}'. "
        "The previous batch run did not produce that directory or left it empty. "
        "Skip benchmarks and metrics this time; focus exclusively on this baseline.\n\n"
    )
    return build_full_prompt(prompt_template, retry_banner + runtime_context)


def run_baseline_retry(
    baseline: Dict[str, str],
    prompt_template: str,
    paper_json_path: str | Path,
    generated_repo_path: str | Path,
    rubric_json_path: str | Path | None,
    output_dir: str | Path,
    paper_name: str,
    eval_info_json_path: str | Path | None,
    codex_bin: str,
    model_name: str,
    timeout_seconds: int,
    max_download_gb: float = 5.0,
    eval_plan_json_path: str | Path | None = None,
) -> Dict[str, object]:
    # One focused codex call for a single missing baseline.
    output_root = Path(output_dir)
    slug = baseline["slug"]
    last_message_path = output_root / f"c1_download_dataset_last_message_retry_{slug}.txt"
    full_prompt = build_baseline_retry_prompt(
        prompt_template=prompt_template,
        paper_json_path=paper_json_path,
        generated_repo_path=generated_repo_path,
        rubric_json_path=rubric_json_path,
        output_dir=output_dir,
        paper_name=paper_name,
        baseline=baseline,
        eval_info_json_path=eval_info_json_path,
        max_download_gb=max_download_gb,
        eval_plan_json_path=eval_plan_json_path,
    )
    # Persist the per-retry prompt so failed retries are debuggable.
    (output_root / f"c1_download_dataset_prompt_retry_{slug}.txt").write_text(
        full_prompt, encoding="utf-8"
    )
    codex_command = build_codex_command(
        codex_bin=codex_bin,
        generated_repo_path=generated_repo_path,
        output_dir=output_dir,
        model_name=model_name,
        last_message_path=last_message_path,
    )
    run_result = run_codex_command(
        command=codex_command,
        prompt_text=full_prompt,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        artifact_suffix=f"retry_{slug}",
    )
    return {
        "baseline_name": baseline["name"],
        "baseline_slug": slug,
        "last_message_path": str(last_message_path.resolve()),
        **run_result,
    }


def build_metadata(
    args: argparse.Namespace,
    paper_name: str,
    output_dir: Path,
    prompt_path: Path,
    runtime_context: str,
    codex_command: List[str],
    baselines: List[Dict[str, str]] | None = None,
) -> Dict[str, object]:
    return {
        "paper_name": paper_name,
        "paper_slug": slugify(paper_name),
        "model": args.gpt_version,
        "paper_json_path": str(Path(args.paper_json_path).resolve()),
        "generated_repo_path": str(Path(args.generated_repo_path).resolve()),
        "rubric_json_path": str(Path(args.rubric_json_path).resolve()) if args.rubric_json_path else "",
        "eval_info_json": str(Path(args.eval_info_json).resolve()) if args.eval_info_json else "",
        "output_dir": str(output_dir.resolve()),
        "asset_paths": build_asset_paths(output_dir),
        "prompt_path": str(prompt_path.resolve()),
        "codex_command": codex_command,
        "dry_run": bool(args.dry_run),
        "max_baseline_retries": int(args.max_baseline_retries),
        "max_download_gb": float(args.max_download_gb),
        "eval_plan_json": str(Path(args.eval_plan_json).resolve()) if args.eval_plan_json else "",
        "baselines_from_eval_info": baselines or [],
        "runtime_context": runtime_context,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_prompt_and_metadata(
    prompt_text: str,
    metadata: Dict[str, object],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "c1_download_dataset_prompt.txt").write_text(prompt_text, encoding="utf-8")
    (output_dir / "c1_download_dataset_inputs.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _stringify_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_codex_command(
    command: List[str],
    prompt_text: str,
    output_dir: str | Path,
    timeout_seconds: int = 0,
    artifact_suffix: str = "",
) -> Dict[str, object]:
    output_root = Path(output_dir)
    suffix = f"_{artifact_suffix}" if artifact_suffix else ""
    stdout_path = output_root / f"c1_download_dataset_codex_stdout{suffix}.jsonl"
    stderr_path = output_root / f"c1_download_dataset_codex_stderr{suffix}.txt"
    timeout = timeout_seconds if timeout_seconds > 0 else None

    try:
        completed = subprocess.run(
            command,
            input=prompt_text,
            text=True,
            capture_output=True,
            cwd=REPO_ROOT,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = _stringify_subprocess_output(exc.stdout)
        stderr = _stringify_subprocess_output(exc.stderr)
        returncode = 124
        timed_out = True
    except OSError as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
        returncode = 127
        timed_out = False

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    return {
        "codex_returncode": returncode,
        "timed_out": timed_out,
        "stdout_path": str(stdout_path.resolve()),
        "stderr_path": str(stderr_path.resolve()),
    }


def write_result(output_dir: str | Path, result_payload: Dict[str, object]) -> None:
    result_path = Path(output_dir) / "c1_download_dataset_result.json"
    result_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(args: argparse.Namespace) -> None:
    prompt_path = Path(args.prompt_path)
    prompt_template = load_prompt(prompt_path)
    paper_payload = load_json(args.paper_json_path)
    rubric_payload = load_optional_json(args.rubric_json_path)

    # Prefer the richer eval_plan (5.1) over eval_info (5) when both are provided.
    eval_plan = load_eval_plan(args.eval_plan_json) if args.eval_plan_json else None
    if eval_plan is not None:
        baselines = baselines_from_eval_plan(eval_plan, output_dir="")  # dir resolved below
        datasets_plan = datasets_from_eval_plan(eval_plan)
        print(f"[5.1] Using eval_plan: {len(baselines)} baselines, {len(datasets_plan)} datasets")
    else:
        baselines = load_baseline_list(args.eval_info_json)
        datasets_plan = None

    paper_name = resolve_paper_name(
        paper_payload=paper_payload,
        rubric_payload=rubric_payload,
        paper_json_path=args.paper_json_path,
    )
    output_dir = resolve_output_dir(args, paper_name)
    generated_repo_path = Path(args.generated_repo_path).resolve()
    if not generated_repo_path.exists():
        raise FileNotFoundError(f"Generated repo path does not exist: {generated_repo_path}")

    # Re-resolve baselines now that output_dir is known (expected_rival_dir uses it).
    if eval_plan is not None:
        baselines = baselines_from_eval_plan(eval_plan, output_dir=output_dir)

    ensure_asset_dirs(output_dir)
    runtime_context = build_runtime_context(
        paper_json_path=args.paper_json_path,
        generated_repo_path=generated_repo_path,
        rubric_json_path=args.rubric_json_path,
        output_dir=output_dir,
        paper_name=paper_name,
        baselines=baselines,
        eval_info_json_path=args.eval_info_json,
        max_download_gb=args.max_download_gb,
        datasets_plan=datasets_plan,
        eval_plan_json_path=args.eval_plan_json if eval_plan is not None else None,
    )
    full_prompt = build_full_prompt(prompt_template, runtime_context)
    last_message_path = output_dir / "c1_download_dataset_last_message.txt"
    codex_command = build_codex_command(
        codex_bin=args.codex_bin,
        generated_repo_path=generated_repo_path,
        output_dir=output_dir,
        model_name=args.gpt_version,
        last_message_path=last_message_path,
    )
    metadata = build_metadata(
        args=args,
        paper_name=paper_name,
        output_dir=output_dir,
        prompt_path=prompt_path,
        runtime_context=runtime_context,
        codex_command=codex_command,
        baselines=baselines,
    )
    write_prompt_and_metadata(
        prompt_text=full_prompt,
        metadata=metadata,
        output_dir=output_dir,
    )

    if args.dry_run:
        print(f"Dry run wrote c1_download_dataset prompt artifacts to: {output_dir.resolve()}")
        return

    run_result = run_codex_command(
        command=codex_command,
        prompt_text=full_prompt,
        output_dir=output_dir,
        timeout_seconds=args.codex_timeout,
    )
    validation = validate_generated_artifacts(generated_repo_path, output_dir)

    # Per-baseline retry loop. Only active when --eval_info_json is provided.
    baseline_retry_results: List[Dict[str, object]] = []
    still_missing_baselines: List[Dict[str, str]] = []
    if baselines and run_result["codex_returncode"] == 0:
        missing_baselines = validate_baselines(output_dir, baselines)
        for attempt in range(max(0, int(args.max_baseline_retries))):
            if not missing_baselines:
                break
            for baseline in missing_baselines:
                retry_result = run_baseline_retry(
                    baseline=baseline,
                    prompt_template=prompt_template,
                    paper_json_path=args.paper_json_path,
                    generated_repo_path=generated_repo_path,
                    rubric_json_path=args.rubric_json_path,
                    output_dir=output_dir,
                    paper_name=paper_name,
                    eval_info_json_path=args.eval_info_json,
                    codex_bin=args.codex_bin,
                    model_name=args.gpt_version,
                    timeout_seconds=args.codex_timeout,
                    max_download_gb=args.max_download_gb,
                    eval_plan_json_path=args.eval_plan_json if eval_plan is not None else None,
                )
                retry_result["attempt"] = attempt + 1
                baseline_retry_results.append(retry_result)
            missing_baselines = validate_baselines(output_dir, baselines)
        still_missing_baselines = missing_baselines
        # Re-run the standard validation in case retries created some required dirs/files.
        validation = validate_generated_artifacts(generated_repo_path, output_dir)

    # Filesystem-grounded download report. Run unconditionally so the user gets a
    # report even when codex returncode != 0 or some baselines are missing.
    download_summary = compute_download_summary(
        output_dir=output_dir,
        baselines=baselines,
        max_download_gb=args.max_download_gb,
    )
    summary_path = write_download_summary(download_summary, output_dir)
    print_download_report(download_summary)

    result_payload: Dict[str, object] = {
        **run_result,
        "validation": {
            "ok": validation.ok,
            "missing": validation.missing,
        },
        "last_message_path": str(last_message_path.resolve()),
        "baseline_retry": baseline_retry_results,
        "baselines_still_missing": [b["slug"] for b in still_missing_baselines],
        "download_summary_path": str(summary_path.resolve()),
        "download_budget": download_summary["budget"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    write_result(output_dir, result_payload)

    if run_result["codex_returncode"] != 0:
        raise RuntimeError(
            f"Codex asset extraction failed with exit code {run_result['codex_returncode']}. "
            f"See {run_result['stderr_path']}"
        )
    if not validation.ok:
        raise RuntimeError(
            "Codex asset extraction finished but required outputs are missing: "
            + "; ".join(validation.missing)
        )
    if still_missing_baselines:
        raise RuntimeError(
            "Codex finished but the following baseline rivals are still missing or empty "
            "after retries: "
            + ", ".join(b["slug"] for b in still_missing_baselines)
        )

    print(f"Saved Codex asset extraction artifacts to: {output_dir.resolve()}")
    print(f"Download summary: {summary_path.resolve()}")


if __name__ == "__main__":
    main(parse_args())
