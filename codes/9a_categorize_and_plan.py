# 9a_categorize_and_plan.py — Stage A of the split unit-test generator.
#
# What this file does:
#   Reads the paper2code rubric (output of script 8) and the generated repo's
#   public API, then:
#     1) flattens the rubric into one entry per leaf,
#     2) deterministically assigns a `tier` ("intermediate" | "comparison") to
#        every leaf using the lookup table in codes/_unit_test_utils.py,
#     3) writes the audit-trail file `categorized_rubric.json`, and
#     4) (unless --dry_run) calls the Pass 1 LLM to produce `test_specs.json`
#        whose `tier` field is forced to match the deterministic categorization.
#
# Stage B (codes/9b_synthesize_tests.py) consumes test_specs.json and turns
# the specs into actual pytest files, one tier at a time.
#
# Usage example (categorization-only, no LLM call):
#   python3.10 codes/9a_categorize_and_plan.py \
#     --rubric_json_path    outputs/paperbench_repos/adaptive-pruning_repo/eval/adaptive-pruning_paper2code_rubric_gpt-5.2.json \
#     --paper_name          adaptive-pruning \
#     --repo_plan_path      outputs/paperbench_planning/adaptive-pruning/planning_response.json \
#     --generated_repo_path outputs/paperbench_repos/adaptive-pruning_repo \
#     --output_dir          outputs/paperbench_tests/adaptive-pruning_tests \
#     --dry_run
#
# Full run (includes the LLM call):
#   python3.10 codes/9a_categorize_and_plan.py \
#     --rubric_json_path    ... \
#     --paper_name          adaptive-pruning \
#     --repo_plan_path      ... \
#     --generated_repo_path outputs/paperbench_repos/adaptive-pruning_repo \
#     --output_dir          outputs/paperbench_tests/adaptive-pruning_tests \
#     --gpt_version         gpt-5.4

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from openai_client import create_openai_client
from utils import print_log_cost

from _unit_test_utils import (
    PASS1_PROMPT_PATH,
    PASS1_RESPONSE_SCHEMA,
    apply_tier_to_leaves,
    assign_tier,
    build_repo_api_manifest,
    build_pass1_messages,
    build_request_json,
    call_model,
    estimate_tokens,
    flatten_rubric_leaves,
    load_json,
    load_prompt,
    tier_counts,
    validate_and_resolve_specs,
    write_repo_api_manifest,
)


# Parse command-line arguments for Stage A.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage A: deterministically categorize rubric leaves into "
            "intermediate/comparison tiers and (optionally) generate the "
            "Pass 1 test_specs.json that Stage B consumes."
        )
    )
    parser.add_argument("--rubric_json_path", type=str, required=True)
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument("--repo_plan_path", type=str, required=True)
    parser.add_argument("--generated_repo_path", type=str, required=True)
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Dump unprocessed LLM responses to <output_dir>/prompts/.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Stop after writing categorized_rubric.json (no LLM call).",
    )
    return parser.parse_args()


# Reconcile LLM-emitted tiers against the deterministic categorization.
# If the LLM returned a different tier than assign_tier() decided, log a
# warning and overwrite with the deterministic value. This is the guard rail
# that makes categorization reproducible even if a prompt regression slips.
def enforce_deterministic_tiers(
    specs: List[Dict[str, Any]],
    leaves_by_rubric_id: Dict[str, Dict[str, Any]],
) -> int:
    """Returns the number of corrections applied."""
    corrected = 0
    for spec in specs:
        rid = spec.get("rubric_id", "")
        leaf = leaves_by_rubric_id.get(rid)
        if leaf is None:
            continue
        deterministic = assign_tier(leaf)
        if spec.get("tier") != deterministic:
            print(
                f"[WARNING] Tier override for {spec.get('test_id', '?')} (rubric {rid}): "
                f"LLM returned {spec.get('tier')!r}, forcing {deterministic!r}."
            )
            spec["tier"] = deterministic
            # Mismatched tier may also mean the wrong block was populated — flag it.
            if deterministic == "intermediate" and spec.get("intermediate") is None:
                print(
                    f"  [WARN] spec {spec.get('test_id')} now intermediate but has no "
                    "intermediate block; downstream Pass 2 will likely emit a smoke check."
                )
            if deterministic == "comparison" and spec.get("comparison") is None:
                print(
                    f"  [WARN] spec {spec.get('test_id')} now comparison but has no "
                    "comparison block; downstream Pass 2 will likely emit a smoke check."
                )
            corrected += 1
    return corrected


# Stage A entrypoint: categorize + (optional) Pass 1 LLM call.
def main(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir).resolve()
    raw_prompts_dir = output_dir / "prompts"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.save_raw_completion:
        raw_prompts_dir.mkdir(parents=True, exist_ok=True)

    # ---------- Load rubric and flatten ----------
    rubric_payload = load_json(args.rubric_json_path)
    rubric_root = (
        rubric_payload.get("paper2code_rubric")
        or rubric_payload.get("reproduction_rubric")
        or rubric_payload
    )
    leaves = flatten_rubric_leaves(rubric_root)
    if not leaves:
        print("[ERROR] No rubric leaves found — check --rubric_json_path.")
        sys.exit(1)

    # ---------- Deterministic categorization ----------
    apply_tier_to_leaves(leaves)
    counts = tier_counts(leaves)
    print(
        f"[c9a categorize] {len(leaves)} rubric leaves → "
        f"intermediate={counts.get('intermediate', 0)}, "
        f"comparison={counts.get('comparison', 0)}"
    )

    # Audit trail — readable independently of Pass 1.
    categorized_path = output_dir / "categorized_rubric.json"
    categorized_payload = {
        "paper_name": args.paper_name,
        "rubric_json_path": os.path.abspath(args.rubric_json_path),
        "tier_counts": counts,
        "leaves": leaves,
    }
    categorized_path.write_text(
        json.dumps(categorized_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[c9a categorize] wrote {categorized_path}")

    if args.dry_run:
        print("[DRY RUN] Categorization-only; skipping Pass 1 LLM call.")
        return

    # ---------- Pass 1: LLM generates per-leaf spec details ----------
    repo_root = Path(args.generated_repo_path).resolve()
    if not repo_root.exists():
        print(f"[ERROR] Generated repo path does not exist: {repo_root}")
        sys.exit(1)
    api_manifest = build_repo_api_manifest(repo_root)
    repo_symbols = api_manifest["api_symbols"]
    manifest_path = write_repo_api_manifest(output_dir, api_manifest)
    print(f"[c9a categorize] wrote {manifest_path}")

    pass1_prompt = load_prompt(PASS1_PROMPT_PATH)
    pass1_messages = build_pass1_messages(
        pass1_prompt=pass1_prompt,
        paper_name=args.paper_name,
        leaves=leaves,
        repo_symbols=repo_symbols,
    )
    pass1_token_count = estimate_tokens(pass1_messages)
    pass1_request = build_request_json(
        args.gpt_version, pass1_messages, "test_specs", PASS1_RESPONSE_SCHEMA
    )

    client = create_openai_client()
    try:
        pass1_completion, _pass1_raw, pass1_parsed = call_model(client, pass1_request)
    except Exception as exc:
        print(f"[ERROR] Pass 1 LLM call failed: {exc}")
        sys.exit(1)

    specs: List[Dict[str, Any]] = list(pass1_parsed.get("tests", []))

    # Guard rail: force deterministic tier on every spec.
    leaves_by_rubric_id = {leaf["rubric_id"]: leaf for leaf in leaves}
    corrected = enforce_deterministic_tiers(specs, leaves_by_rubric_id)
    if corrected:
        print(f"[c9a categorize] forced {corrected} tier override(s) to match deterministic lookup.")
    specs = validate_and_resolve_specs(specs, api_manifest)

    spec_payload = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "rubric_json_path": os.path.abspath(args.rubric_json_path),
        "generated_repo_path": os.path.abspath(args.generated_repo_path),
        "repo_api_manifest_path": str(manifest_path),
        "repo_symbols": repo_symbols,
        "prompt_token_estimate_pass1": pass1_token_count,
        "tier_counts": counts,
        "tests": specs,
    }
    specs_path = output_dir / "test_specs.json"
    specs_path.write_text(
        json.dumps(spec_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if args.save_raw_completion:
        (raw_prompts_dir / "pass1_messages.json").write_text(
            json.dumps(pass1_messages, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (raw_prompts_dir / "pass1_completion.json").write_text(
            json.dumps(pass1_completion, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    try:
        print_log_cost(
            pass1_completion,
            args.gpt_version,
            f"[c9a Pass1] {args.paper_name}",
            str(output_dir),
            0.0,
        )
    except Exception as exc:
        print(f"[WARNING] Pass 1 cost logging skipped: {exc}")

    # Warn about leaves not covered by any spec.
    leaf_ids = {leaf["rubric_id"] for leaf in leaves}
    spec_rubric_ids = {s["rubric_id"] for s in specs}
    uncovered = leaf_ids - spec_rubric_ids
    if uncovered:
        print(
            f"[WARNING] {len(uncovered)} rubric leaves have no test spec: "
            + ", ".join(sorted(uncovered)[:5])
            + ("..." if len(uncovered) > 5 else "")
        )

    spec_tier_counts: Dict[str, int] = {"intermediate": 0, "comparison": 0}
    for s in specs:
        spec_tier_counts[s.get("tier", "intermediate")] = (
            spec_tier_counts.get(s.get("tier", "intermediate"), 0) + 1
        )

    print("=" * 60)
    print(f"c9a_categorize_and_plan summary — {args.paper_name}")
    print(f"  Leaves total:        {len(leaves)}")
    print(f"  Leaves intermediate: {counts.get('intermediate', 0)}")
    print(f"  Leaves comparison:   {counts.get('comparison', 0)}")
    print(f"  Specs emitted:       {len(specs)}")
    print(f"  Specs intermediate:  {spec_tier_counts.get('intermediate', 0)}")
    print(f"  Specs comparison:    {spec_tier_counts.get('comparison', 0)}")
    print(f"  Uncovered leaves:    {len(uncovered)}")
    print(f"  Tier corrections:    {corrected}")
    print(f"  Output:              {specs_path}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        main(cli_args)
    except Exception as error:
        print(f"[ERROR] {error}")
        sys.exit(1)
