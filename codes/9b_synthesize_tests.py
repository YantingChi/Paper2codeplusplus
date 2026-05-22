# 9b_synthesize_tests.py — Stage B of the split unit-test generator.
#
# What this file does:
#   Reads test_specs.json (produced by 9a_categorize_and_plan.py), filters the
#   specs by `--tier`, then runs Pass 2 (LLM converts each spec into pytest
#   code), assembles the resulting functions into per-tier subdirectories,
#   writes the shared conftest.py + pytest.ini, runs Pass 3 audit, and applies
#   Pass 4 repair if the audit flagged any blockers.
#
# Per-tier output layout:
#   <output_dir>/tests/intermediate/test_<branch_slug>.py
#   <output_dir>/tests/comparison/test_comparisons.py
#   <output_dir>/tests/conftest.py              (shared)
#   <output_dir>/tests/pytest.ini               (shared)
#   <output_dir>/test_manifest_<tier>.json
#   <output_dir>/audit_<tier>.json
#
# Usage example (both tiers in one run):
#   python3.10 codes/9b_synthesize_tests.py \
#     --specs_path          outputs/paperbench_tests/adaptive-pruning_tests/test_specs.json \
#     --generated_repo_path outputs/paperbench_repos/adaptive-pruning_repo \
#     --output_dir          outputs/paperbench_tests/adaptive-pruning_tests \
#     --tier                both
#
# Re-synthesize only comparison tests (intermediate dir is left intact):
#   python3.10 codes/9b_synthesize_tests.py \
#     --specs_path          outputs/paperbench_tests/adaptive-pruning_tests/test_specs.json \
#     --generated_repo_path outputs/paperbench_repos/adaptive-pruning_repo \
#     --output_dir          outputs/paperbench_tests/adaptive-pruning_tests \
#     --tier                comparison

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai_client import create_openai_client
from utils import get_now_str

from _unit_test_utils import (
    PASS2_PROMPT_PATH,
    assemble_test_files,
    audit_emitted_files,
    build_repo_api_manifest,
    collect_only_check,
    ensure_functions_for_specs,
    extract_api_from_doxygen,
    extract_repo_symbols,
    load_json,
    load_prompt,
    repair_broken_tests,
    remove_legacy_root_test_files,
    run_pass2_in_batches,
    sanitize_generated_functions,
    validate_and_resolve_specs,
    write_conftest,
    write_pytest_ini,
    write_repo_api_manifest,
)


# Parse command-line arguments for Stage B.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage B: read test_specs.json from Stage A, synthesize pytest "
            "function bodies, assemble per-tier test directories, audit, and "
            "repair broken tests."
        )
    )
    parser.add_argument("--specs_path", type=str, required=True,
                        help="Path to test_specs.json (output of 9a_categorize_and_plan.py).")
    parser.add_argument("--generated_repo_path", type=str, required=True)
    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument(
        "--tier",
        type=str,
        default="both",
        choices=["intermediate", "comparison", "both"],
        help="Which tier(s) to synthesize (default: both).",
    )
    parser.add_argument(
        "--skip_audit",
        action="store_true",
        help="Skip Pass 3 audit + Pass 4 repair.",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Dump unprocessed LLM responses to <output_dir>/prompts/.",
    )
    return parser.parse_args()


# Return the subset of specs whose `tier` matches `tier_filter`. `both` is identity.
def filter_specs_by_tier(
    specs: List[Dict[str, Any]],
    tier_filter: str,
) -> List[Dict[str, Any]]:
    if tier_filter == "both":
        return list(specs)
    return [s for s in specs if s.get("tier") == tier_filter]


# Audit + repair a single per-tier subdirectory. Returns the audit payload (or None).
# Keeping the audit per-tier means re-synthesizing one tier doesn't re-touch the other.
def audit_and_repair_tier(
    tier_dir: Path,
    specs_for_tier: List[Dict[str, Any]],
    flat_symbols: Dict[str, List[str]],
    repo_root: Path,
) -> Optional[Dict[str, Any]]:
    """Run audit_emitted_files + repair_broken_tests on a per-tier subdir."""
    if not tier_dir.exists():
        return None

    paper_numbers: set[str] = set()  # numeric cross-check not used today
    audit_payload = audit_emitted_files(
        tests_dir=tier_dir,
        specs=specs_for_tier,
        repo_symbols=flat_symbols,
        repo_root=repo_root,
        paper_numbers=paper_numbers,
    )

    repair_records: List[Dict[str, Any]] = []
    if audit_payload.get("blocker_count", 0) > 0:
        repair_records = repair_broken_tests(tier_dir, audit_payload)
        # Re-audit so the saved payload reflects post-repair state.
        audit_payload = audit_emitted_files(
            tests_dir=tier_dir,
            specs=specs_for_tier,
            repo_symbols=flat_symbols,
            repo_root=repo_root,
            paper_numbers=paper_numbers,
        )
        audit_payload["repair_records"] = repair_records

    return audit_payload


# Stage B entrypoint: load specs, synthesize per-tier, assemble, audit, repair.
def main(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir).resolve()
    tests_dir = output_dir / "tests"
    scores_dir = output_dir / "scores"
    raw_prompts_dir = output_dir / "prompts"
    output_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / ".gitkeep").touch()
    if args.save_raw_completion:
        raw_prompts_dir.mkdir(parents=True, exist_ok=True)

    # ---------- Load specs (Stage A's output) ----------
    specs_payload = load_json(args.specs_path)
    all_specs: List[Dict[str, Any]] = specs_payload.get("tests", [])
    if not all_specs:
        print(f"[ERROR] No specs found in {args.specs_path}.")
        sys.exit(1)

    # repo_symbols was cached by Stage A; fall back to recomputing if missing.
    repo_root = Path(args.generated_repo_path).resolve()
    if not repo_root.exists():
        print(f"[ERROR] Generated repo path does not exist: {repo_root}")
        sys.exit(1)
    manifest_path_value = specs_payload.get("repo_api_manifest_path")
    api_manifest: Dict[str, Any]
    if manifest_path_value and Path(manifest_path_value).is_file():
        api_manifest = load_json(str(manifest_path_value))
    else:
        api_manifest = build_repo_api_manifest(repo_root)
        manifest_path = write_repo_api_manifest(output_dir, api_manifest)
        specs_payload["repo_api_manifest_path"] = str(manifest_path)
    repo_symbols = api_manifest.get("api_symbols") or specs_payload.get("repo_symbols")
    if not repo_symbols:
        repo_symbols = extract_api_from_doxygen(repo_root)
    flat_symbols = extract_repo_symbols(repo_root)

    # ---------- Filter by tier ----------
    all_specs = validate_and_resolve_specs(all_specs, api_manifest)
    specs_payload["tests"] = all_specs
    specs_payload["repo_symbols"] = repo_symbols
    Path(args.specs_path).write_text(
        json.dumps(specs_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    selected = filter_specs_by_tier(all_specs, args.tier)
    if not selected:
        print(f"[WARNING] No specs match --tier {args.tier!r}; nothing to synthesize.")
        sys.exit(0)
    print(
        f"[c9b synthesize] tier={args.tier} → {len(selected)} specs "
        f"(out of {len(all_specs)} total)"
    )

    # ---------- Pass 2: synthesize pytest function bodies ----------
    pass2_prompt = load_prompt(PASS2_PROMPT_PATH)
    client = create_openai_client()
    paper_name = specs_payload.get("paper_name", "unknown")
    assets_root_module = "assets.rivals"
    functions, accumulated_cost, raw_completions = run_pass2_in_batches(
        client=client,
        pass2_prompt=pass2_prompt,
        specs=selected,
        repo_symbols=repo_symbols,
        assets_root_module=assets_root_module,
        model_name=args.gpt_version,
        output_dir=output_dir,
        save_raw_completion=args.save_raw_completion,
        accumulated_cost=0.0,
        cost_log_label=f"[c9b Pass2 {args.tier}]",
    )
    selected_specs_by_id = {s["test_id"]: s for s in selected}
    functions = sanitize_generated_functions(functions, selected_specs_by_id, api_manifest)
    functions = ensure_functions_for_specs(functions, selected)
    specs_by_id = {s["test_id"]: s for s in all_specs}
    if args.save_raw_completion:
        (raw_prompts_dir / f"pass2_raw_{args.tier}.json").write_text(
            json.dumps(raw_completions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Assemble per-tier files ----------
    removed_legacy_tests = remove_legacy_root_test_files(tests_dir)
    if removed_legacy_tests:
        print(
            f"[c9b synthesize] removed {len(removed_legacy_tests)} legacy root-level test file(s)."
        )
    tier_filter_arg = None if args.tier == "both" else args.tier
    manifest = assemble_test_files(
        functions, specs_by_id, tests_dir, tier_filter=tier_filter_arg
    )

    write_conftest(
        tests_dir=tests_dir,
        repo_path=str(repo_root),
        score_path=str(scores_dir / "scores.json"),
    )
    write_pytest_ini(tests_dir)

    # Per-tier manifest(s) — one file per tier so partial re-runs don't clobber the other.
    tiers_touched: List[str] = (
        ["intermediate", "comparison"] if args.tier == "both" else [args.tier]
    )
    for tier in tiers_touched:
        tier_entries = [m for m in manifest if m["tier"] == tier]
        if not tier_entries:
            continue
        manifest_payload = {
            "paper_name": paper_name,
            "tier": tier,
            "model": args.gpt_version,
            "generated_at": get_now_str(),
            "specs_path": os.path.abspath(args.specs_path),
            "repo_api_manifest_path": specs_payload.get("repo_api_manifest_path"),
            "tests_dir": str(tests_dir / tier),
            "scores_path": str(scores_dir / "scores.json"),
            "tests": tier_entries,
        }
        manifest_path = output_dir / f"test_manifest_{tier}.json"
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Pass 3 audit + Pass 4 repair, per-tier ----------
    audit_summary: Dict[str, Optional[Dict[str, Any]]] = {}
    if not args.skip_audit:
        for tier in tiers_touched:
            tier_dir = tests_dir / tier
            tier_specs = [s for s in selected if s.get("tier") == tier]
            payload = audit_and_repair_tier(tier_dir, tier_specs, flat_symbols, repo_root)
            audit_summary[tier] = payload
            if payload is not None:
                (output_dir / f"audit_{tier}.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

    # ---------- Final self-check ----------
    collect_ok, collect_log = collect_only_check(tests_dir)
    if not collect_ok and not args.skip_audit:
        print("[c9b synthesize] collect-only failed; running one more audit/repair pass.")
        for tier in tiers_touched:
            tier_dir = tests_dir / tier
            tier_specs = [s for s in selected if s.get("tier") == tier]
            payload = audit_and_repair_tier(tier_dir, tier_specs, flat_symbols, repo_root)
            audit_summary[tier] = payload
            if payload is not None:
                (output_dir / f"audit_{tier}.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        collect_ok, collect_log = collect_only_check(tests_dir)

    print("=" * 60)
    print(f"c9b_synthesize_tests summary — {paper_name} (tier={args.tier})")
    print(f"  Specs in:           {len(selected)}")
    print(f"  Pass 2 functions:   {len(functions)}")
    print(f"  Manifest entries:   {len(manifest)}")
    print(f"  Tests dir:          {tests_dir}")
    for tier, payload in audit_summary.items():
        if payload is None:
            continue
        blockers = payload.get("blocker_count", 0)
        warnings = payload.get("warning_count", 0)
        verdict = "PASS" if payload.get("passes") else "BLOCKERS"
        repair_records = payload.get("repair_records") or []
        print(f"  Audit ({tier:13s}): {verdict} ({blockers} blockers, {warnings} warnings)")
        if repair_records:
            n_files = len(repair_records)
            n_funcs = sum(len(r.get("skipped_functions", [])) for r in repair_records)
            print(f"    Repair: rewrote {n_files} file(s); replaced {n_funcs} hallucinated test(s)")
    print(f"  pytest --collect-only: {'OK' if collect_ok else 'FAILED'}")
    if not collect_ok:
        print("  collect-only output (tail):")
        for line in collect_log.splitlines()[-20:]:
            print(f"    {line}")
    print(f"  Accumulated cost: ${accumulated_cost:.6f}")
    print("=" * 60)

    # Non-zero exit only if a real pipeline bug remains AFTER repair.
    for tier, payload in audit_summary.items():
        if payload is not None and not payload.get("passes", True):
            print(f"[ERROR] {tier} audit still has blockers after repair; see audit_{tier}.json.")
            sys.exit(1)
    if not collect_ok:
        print("[ERROR] pytest --collect-only still failed after repair; unresolved blocker listed above.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        main(cli_args)
    except Exception as error:
        print(f"[ERROR] {error}")
        sys.exit(1)
