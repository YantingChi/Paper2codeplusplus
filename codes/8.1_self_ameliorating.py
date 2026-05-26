"""Self-ameliorating loop for the generated repo.

What this script does, in plain English:
  1. Read the rubric produced by 8_getting_paper2code_rubric.py and pull out
     only the "Code Development" leaves — i.e. the rubric items you can check
     by reading the code (Method Implementation + hyperparameter values).
     We deliberately skip "Code Execution" / "Result Analysis" leaves because
     those need to actually run the repo / a benchmark.
  2. Ask the LLM (with structured JSON output) to mark each leaf pass/fail
     based ONLY on the current code + planning context.
  3. For every fail, ask the LLM to produce SEARCH/REPLACE patches in the
     same format used by 4_debugging.py (`Filename: ... <<<<<<< SEARCH ...
     ======= ... >>>>>>> REPLACE`), then apply them with backups.
  4. Loop. Stop when all leaves pass OR after --max_iterations.

Outputs (under <output_dir>/ameliorating_artifacts/):
  - iter_NNN_check.json   structured pass/fail per leaf (machine-readable)
  - iter_NNN_check.txt    same info as a human-readable report
  - iter_NNN_patches.txt  raw LLM SEARCH/REPLACE response for that iter

Plus, in <output_dir>:
  - ameliorating_history.json   summary across all iterations + final status

Backup scheme: each modified file gets a `.<save_num>.bak` next to it, where
save_num = save_num_start + iter (default save_num_start=100, leaving 1..99
free for 4_debugging.py).
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

from openai_client import create_openai_client
from utils import (
    extract_planning,
    load_accumulated_cost,
    print_log_cost,
    print_response,
    read_python_files,
    save_accumulated_cost,
)


# ============================================================
# Rubric helpers
# ============================================================

def collect_code_dev_leaves(node: Dict, leaves: List[Dict]) -> None:
    """Walk the rubric tree and collect leaves whose task_category is
    'Code Development'. A leaf is any node with empty `sub_tasks`.

    Example node we KEEP:
        {"id": "implement_apt_forward_eq2",
         "task_category": "Code Development",
         "finegrained_task_category": "Method Implementation",
         "sub_tasks": []}
    Example node we SKIP (needs running the code):
        {"id": "obtain_glue_sst2_mnli",
         "task_category": "Code Execution", ...}
    """
    sub_tasks = node.get("sub_tasks", []) or []
    if not sub_tasks:
        if node.get("task_category") == "Code Development":
            leaves.append({
                "id": node.get("id", ""),
                "requirements": node.get("requirements", ""),
                "finegrained_task_category": node.get("finegrained_task_category", ""),
            })
        return
    for child in sub_tasks:
        collect_code_dev_leaves(child, leaves)


def load_rubric_leaves(rubric_path: str) -> List[Dict]:
    """Load the rubric file produced by 8_getting_paper2code_rubric.py and
    return the flat list of Code-Development leaves.
    """
    with open(rubric_path, "r", encoding="utf-8") as f:
        rubric_payload = json.load(f)

    rubric_tree = rubric_payload.get("paper2code_rubric")
    if rubric_tree is None:
        # Allow passing the rubric tree directly (without the wrapper).
        rubric_tree = rubric_payload

    leaves: List[Dict] = []
    collect_code_dev_leaves(rubric_tree, leaves)
    return leaves


# Turn an arbitrary name into a stable, id-safe slug.
def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:60] or "item"


def load_reproduction_rubric_leaves(repro_path: str) -> List[Dict]:
    """Convert the Pass-1 reproduction rubric (7_getting_rubric.py output) into extra
    code-checkable leaves. We fold in `methods` (algorithmic subroutines the code must
    implement) and `hyperparameters` flagged `required_for_reproduction` (exact values the
    code/config must set). `results_to_verify` and `assets` are skipped: they need the code
    to actually run, which the self-check (code-only) cannot judge.

    These items are paper-derived (independent of the grader) and finer-grained than the
    paper2code Code-Development leaves, giving the self-ameliorator better coverage.
    """
    with open(repro_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rubric = payload.get("reproduction_rubric", payload)

    extra: List[Dict] = []
    for m in (rubric.get("methods") or []):
        name = m.get("name", "")
        details = m.get("details", "")
        anchor = m.get("anchor", "")
        req = f"The code implements the method '{name}': {details}".strip()
        if anchor:
            req += f" (paper anchor: {anchor})"
        extra.append({
            "id": f"method_{_slug(name)}",
            "requirements": req,
            "finegrained_task_category": "Method Implementation",
        })
    for h in (rubric.get("hyperparameters") or []):
        # Only fold in hyperparameters the paper marks as required for reproduction.
        if not h.get("required_for_reproduction", False):
            continue
        name = h.get("name", "")
        value = h.get("value", "")
        scope = h.get("scope", "")
        anchor = h.get("anchor", "")
        req = (f"The code/config sets the hyperparameter '{name}' to the paper's value "
               f"'{value}'").strip()
        if scope:
            req += f" for {scope}"
        if anchor:
            req += f" (paper anchor: {anchor})"
        extra.append({
            "id": f"hparam_{_slug(name)}",
            "requirements": req,
            "finegrained_task_category": "Hyperparameter Value",
        })
    return extra


# ============================================================
# Codebase serialization
# ============================================================

def serialize_codebase(repo_dir: str) -> str:
    """Read every .py file under repo_dir plus config.yaml and emit one big
    Markdown-style code block string for the prompt.
    """
    py_files = read_python_files(repo_dir)
    blocks = []
    for relpath, content in sorted(py_files.items()):
        blocks.append(f"```python\n## File: {relpath}\n{content}\n```")

    config_path = os.path.join(repo_dir, "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            blocks.append(f"```yaml\n## File: config.yaml\n{f.read()}\n```")

    return "\n\n".join(blocks)


# ============================================================
# LLM check: does the codebase satisfy each rubric leaf?
# ============================================================

# Schema for OpenAI structured outputs. Forces the model to return one
# object per rubric item, with status="pass"|"fail" plus evidence/reason.
CHECK_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pass", "fail"]},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "status", "evidence", "reason"],
            },
        },
    },
    "required": ["results"],
}


def build_check_messages(
    planning_overview: str,
    planning_design: str,
    planning_tasks: str,
    leaves: List[Dict],
    codebase: str,
) -> List[Dict[str, str]]:
    leaves_json = json.dumps(leaves, indent=2, ensure_ascii=False)

    system = """You are a meticulous code reviewer. You will be shown a code repository plus a list of rubric items. For EACH item, decide whether the code visibly implements the requirement (PASS) or does not (FAIL).

Rules:
- Judge ONLY from the code shown. Do NOT assume "it might be implemented elsewhere".
- A function name alone is not enough — the function body must show the required logic.
- For hyperparameter items, you must find the actual numeric value in the code or in config.yaml.
- Output strict JSON matching the required schema. No markdown. No commentary outside the JSON."""

    user = f"""## Planning context

### Overview
{planning_overview}

### Design
{planning_design}

### Tasks
{planning_tasks}

----

## Code repository
{codebase}

----

## Rubric items to check
{leaves_json}

----

## Instruction
For each rubric item id above, return:
- status: "pass" or "fail"
- evidence: the file path (and function name or approximate line range if you can) where the implementation is (or where it should have been but isn't). If "fail", say "not found".
- reason: one sentence explaining the verdict.

Return JSON in this exact shape:
{{"results": [{{"id": "...", "status": "pass|fail", "evidence": "...", "reason": "..."}}, ...]}}"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_check(
    client: Any,
    gpt_version: str,
    messages: List[Dict[str, str]],
) -> Tuple[Dict, Dict]:
    """Returns (parsed_results_dict, completion_json)."""
    request: Dict[str, object] = {
        "model": gpt_version,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "rubric_check_results",
                "schema": CHECK_SCHEMA,
                "strict": True,
            },
        },
    }
    if "o3" in gpt_version or "o4" in gpt_version:
        request["reasoning_effort"] = "high"
    else:
        request["temperature"] = 0

    completion = client.chat.completions.create(**request)
    completion_json = json.loads(completion.model_dump_json())
    content = completion_json["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return parsed, completion_json


def write_check_report(
    iter_idx: int,
    leaves: List[Dict],
    check_result: Dict,
    artifacts_dir: str,
) -> Tuple[List[str], int, int]:
    """Save iter_NNN_check.json (structured) AND iter_NNN_check.txt (human-readable).
    Returns (failed_ids, num_pass, num_fail).
    """
    os.makedirs(artifacts_dir, exist_ok=True)

    leaf_by_id = {leaf["id"]: leaf for leaf in leaves}
    results = check_result.get("results", [])

    # Structured JSON for the loop.
    json_path = os.path.join(artifacts_dir, f"iter_{iter_idx:03d}_check.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(check_result, f, indent=2, ensure_ascii=False)

    # Human-readable text. Failed items first so they're easy to scan.
    pass_lines: List[str] = []
    fail_lines: List[str] = []
    failed_ids: List[str] = []
    for r in results:
        rid = r.get("id", "")
        leaf = leaf_by_id.get(rid, {})
        req = leaf.get("requirements", "<requirement text not in original rubric>")
        block = (
            f"[{r.get('status', '?').upper()}] {rid}\n"
            f"  Requirement: {req}\n"
            f"  Evidence:    {r.get('evidence', '')}\n"
            f"  Reason:      {r.get('reason', '')}"
        )
        if r.get("status") == "pass":
            pass_lines.append(block)
        else:
            fail_lines.append(block)
            failed_ids.append(rid)

    txt_lines = [
        f"# Ameliorating iteration {iter_idx}",
        f"Total items checked: {len(results)}",
        f"Passed: {len(pass_lines)}",
        f"Failed: {len(fail_lines)}",
        "",
        "## Failed items",
        "",
    ]
    txt_lines.extend(fail_lines if fail_lines else ["(none)"])
    txt_lines.append("")
    txt_lines.append("## Passed items")
    txt_lines.append("")
    txt_lines.extend(pass_lines if pass_lines else ["(none)"])

    txt_path = os.path.join(artifacts_dir, f"iter_{iter_idx:03d}_check.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(txt_lines))

    return failed_ids, len(pass_lines), len(fail_lines)


# ============================================================
# LLM patch step (SEARCH/REPLACE, mirrors 4_debugging.py)
# ============================================================

def build_patch_messages(
    planning_overview: str,
    planning_design: str,
    planning_tasks: str,
    failed_leaves: List[Dict],
    failed_reasons: List[Dict],
    codebase: str,
) -> List[Dict[str, str]]:
    failed_text = ""
    for leaf, r in zip(failed_leaves, failed_reasons):
        failed_text += (
            f"\n--- {leaf['id']} ---\n"
            f"Requirement: {leaf['requirements']}\n"
            f"Why it failed: {r.get('reason', '')}\n"
            f"(Reviewer evidence: {r.get('evidence', '')})\n"
        )

    system = """You are a code editor. You will be given a code repository, planning context, and a list of rubric requirements that the current code does NOT satisfy. Produce minimal SEARCH/REPLACE edits that make the code satisfy each requirement.

Rules:
- Use the EXACT format below. Do not output anything else.
- Each edit must include `Filename: <relative path>` then a SEARCH block, ======= separator, REPLACE block, then the closing line.
- Keep edits minimal and surgical. Do not rewrite whole files.
- Do not rename existing functions/classes. Add new code where needed.
- The SEARCH text must be an EXACT substring of the current file (whitespace matters).
- Whenever you add or modify a function or class, include or update its Doxygen comment block immediately before the def/class line:
    ## @brief One-line description.
    # @param paramName Type Description.
    # @return Type Description.
- When adding a new function, always precede it with a complete Doxygen block covering all parameters.
- When modifying a function's signature (adding/removing parameters), update its @param lines to match."""

    user = f"""## Planning context

### Overview
{planning_overview}

### Design
{planning_design}

### Tasks
{planning_tasks}

----

## Current code repository
{codebase}

----

## Failed rubric items
{failed_text}

----

## Format example
Filename: train.py
<<<<<<< SEARCH
result = model.predict(input_data)
=======
result = model(input_data)
>>>>>>> REPLACE

----

## Answer"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def call_patch(
    client: Any,
    gpt_version: str,
    messages: List[Dict[str, str]],
) -> Tuple[str, Dict]:
    request: Dict[str, object] = {
        "model": gpt_version,
        "messages": messages,
    }
    if "o3" in gpt_version or "o4" in gpt_version:
        request["reasoning_effort"] = "high"

    completion = client.chat.completions.create(**request)
    completion_json = json.loads(completion.model_dump_json())
    content = completion_json["choices"][0]["message"]["content"]
    return content, completion_json


def parse_and_apply_changes(
    responses: List[str],
    debug_dir: str,
    save_num: int = 1,
) -> int:
    """Apply SEARCH/REPLACE edits to files in debug_dir.

    This is a copy of `parse_and_apply_changes` in 4_debugging.py. Keeping
    a copy here (rather than importing) because module names starting with
    a digit can't be imported with a normal `import 4_debugging`.

    Returns the number of files that were actually modified.
    """
    files_modified = 0
    for response in responses:
        file_blocks = re.split(r"Filename:\s*([^\n]+)", response)

        if len(file_blocks) < 3:
            print(f"❌ No filename patterns found in response:\n{response[:200]}...\n")
            continue

        for i in range(1, len(file_blocks), 2):
            filename = file_blocks[i].strip()
            file_content_block = file_blocks[i + 1]
            filepath = os.path.join(debug_dir, filename)

            search_replace_pattern = (
                r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
            )
            matches = re.findall(search_replace_pattern, file_content_block, re.DOTALL)

            if not matches:
                print(f"❌ No SEARCH/REPLACE patterns found for file: {filename}\n")
                continue

            if not os.path.exists(filepath):
                print(f"❌ File does not exist: {filepath}\n")
                continue

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    file_content = f.read()
            except Exception as e:
                print(f"❌ Error reading file {filepath}: {e}\n")
                continue

            modified = False
            for idx, (search_text, replace_text) in enumerate(matches, 1):
                search_text = search_text.strip()
                replace_text = replace_text.strip()
                if search_text in file_content:
                    file_content = file_content.replace(search_text, replace_text)
                    modified = True
                    print(f"✅ {filename}: Modification {idx} applied")
                else:
                    print(
                        f"❌ {filename}: Search text for modification {idx} not found:\n"
                        f"{search_text[:200]}...\n"
                    )

            if modified:
                backup_path = f"{filepath}.{save_num:03d}.bak"
                try:
                    os.rename(filepath, backup_path)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(file_content)
                    print(f"💾 {filename}: File saved. Backup: {backup_path}\n")
                    files_modified += 1
                except Exception as e:
                    print(f"❌ Error saving file {filepath}: {e}\n")
            else:
                print(f"ℹ️ {filename}: No modifications applied\n")

    return files_modified


# ============================================================
# CLI + main loop
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Self-ameliorating loop: keep patching the generated repo until every "
            "Code-Development rubric item visibly appears in the source."
        )
    )
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Root directory containing planning_trajectories.json (output of 1_planning.py).",
    )
    parser.add_argument(
        "--output_repo_dir",
        type=str,
        required=True,
        help="Directory holding the generated codebase to ameliorate.",
    )
    parser.add_argument(
        "--rubric_path",
        type=str,
        required=True,
        help="Path to the paper2code rubric JSON (output of 8_getting_paper2code_rubric.py).",
    )
    parser.add_argument(
        "--reproduction_rubric_path",
        type=str,
        default="",
        help=(
            "Optional path to the Pass-1 reproduction rubric JSON (output of "
            "7_getting_rubric.py). Its `methods` and required `hyperparameters` are folded in "
            "as additional, finer-grained code-checkable items. If empty, auto-detected as the "
            "newest *_reproduction_rubric_*.json next to --rubric_path."
        ),
    )
    parser.add_argument(
        "--max_iterations",
        type=int,
        default=5,
        help="Stop after this many ameliorate cycles even if items still fail.",
    )
    parser.add_argument(
        "--save_num_start",
        type=int,
        default=100,
        help=(
            "Backup index for the first iteration. Iter i creates "
            "<file>.<save_num_start+i:03d>.bak. Default 100 leaves 1..99 free for 4_debugging.py."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = create_openai_client()

    # ---- Load planning context (overview, design, tasks) ----
    planning_traj_path = os.path.join(args.output_dir, "planning_trajectories.json")
    if not os.path.exists(planning_traj_path):
        print(f"❌ Planning trajectories not found: {planning_traj_path}", file=sys.stderr)
        sys.exit(1)
    context_lst = extract_planning(planning_traj_path)
    while len(context_lst) < 3:
        context_lst.append("")
    planning_overview, planning_design, planning_tasks = context_lst[0], context_lst[1], context_lst[2]

    # ---- Load rubric leaves (filter to Code Development only) ----
    leaves = load_rubric_leaves(args.rubric_path)

    # ---- Fold in finer-grained paper-derived items from the Pass-1 reproduction rubric ----
    # (methods + required hyperparameters). This widens coverage beyond the coarse
    # paper2code Code-Development leaves, which alone let the self-check converge too early.
    repro_path = args.reproduction_rubric_path
    if not repro_path:
        # Auto-detect: newest *_reproduction_rubric_*.json next to the paper2code rubric.
        import glob
        cands = sorted(
            glob.glob(os.path.join(os.path.dirname(args.rubric_path), "*_reproduction_rubric_*.json")),
            key=os.path.getmtime,
        )
        repro_path = cands[-1] if cands else ""
    if repro_path and os.path.isfile(repro_path):
        seen_ids = {lf.get("id") for lf in leaves}
        extra = [lf for lf in load_reproduction_rubric_leaves(repro_path) if lf["id"] not in seen_ids]
        leaves.extend(extra)
        print(f"Folded in {len(extra)} extra items from reproduction rubric: {os.path.basename(repro_path)}")
    else:
        print("No reproduction rubric found; checking paper2code Code-Development leaves only.")

    if not leaves:
        print("⚠️ No 'Code Development' leaves found in the rubric. Nothing to check.")
        sys.exit(0)
    print(f"Loaded {len(leaves)} rubric leaves to check (paper2code + reproduction).")

    # ---- Prepare output dirs ----
    artifacts_dir = os.path.join(args.output_dir, "ameliorating_artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)

    total_accumulated_cost = load_accumulated_cost(f"{args.output_dir}/accumulated_cost.json")
    history: List[Dict] = []
    final_status = "max_iter_reached"

    for iter_idx in range(args.max_iterations):
        print("=" * 60)
        print(f"[AMELIORATE] Iteration {iter_idx} / {args.max_iterations - 1}")
        print("=" * 60)

        # Re-read codebase fresh each iteration — it changes between iters.
        codebase = serialize_codebase(args.output_repo_dir)

        # ---- Check ----
        check_messages = build_check_messages(
            planning_overview, planning_design, planning_tasks, leaves, codebase
        )
        check_stage = f"[AMELIORATE][CHECK iter={iter_idx}] {args.paper_name}"
        print(check_stage)
        check_result, check_completion = call_check(client, args.gpt_version, check_messages)
        print_response(check_completion)
        total_accumulated_cost = print_log_cost(
            check_completion, args.gpt_version, check_stage, args.output_dir, total_accumulated_cost
        )

        failed_ids, n_pass, n_fail = write_check_report(
            iter_idx, leaves, check_result, artifacts_dir
        )
        history.append({
            "iter": iter_idx,
            "total": len(leaves),
            "pass": n_pass,
            "fail": n_fail,
            "failed_ids": failed_ids,
        })
        print(f"[AMELIORATE] iter {iter_idx}: {n_pass} pass, {n_fail} fail")

        if n_fail == 0:
            print(f"✅ All {n_pass} rubric items pass at iteration {iter_idx}. Stopping.")
            final_status = "all_pass"
            break

        # ---- Patch ----
        leaf_by_id = {leaf["id"]: leaf for leaf in leaves}
        results_by_id = {r["id"]: r for r in check_result["results"]}
        failed_leaves = [leaf_by_id[fid] for fid in failed_ids if fid in leaf_by_id]
        failed_reasons = [results_by_id[fid] for fid in failed_ids if fid in results_by_id]

        patch_messages = build_patch_messages(
            planning_overview, planning_design, planning_tasks,
            failed_leaves, failed_reasons, codebase,
        )
        patch_stage = f"[AMELIORATE][PATCH iter={iter_idx}] {args.paper_name}"
        print(patch_stage)
        patch_text, patch_completion = call_patch(client, args.gpt_version, patch_messages)
        total_accumulated_cost = print_log_cost(
            patch_completion, args.gpt_version, patch_stage, args.output_dir, total_accumulated_cost
        )

        with open(
            os.path.join(artifacts_dir, f"iter_{iter_idx:03d}_patches.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(patch_text)

        save_num = args.save_num_start + iter_idx
        files_modified = parse_and_apply_changes(
            [patch_text], args.output_repo_dir, save_num=save_num
        )
        if files_modified == 0:
            print(
                f"⚠️ No files modified at iteration {iter_idx}. "
                "Likely the LLM produced unparseable patches or all SEARCH blocks missed. Stopping."
            )
            final_status = "no_progress"
            break

    # ---- Save history + cost ----
    history_path = os.path.join(args.output_dir, "ameliorating_history.json")
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump({
            "paper_name": args.paper_name,
            "model": args.gpt_version,
            "max_iterations": args.max_iterations,
            "save_num_start": args.save_num_start,
            "rubric_path": os.path.abspath(args.rubric_path),
            "rubric_leaves_checked": len(leaves),
            "final_status": final_status,
            "iterations": history,
        }, f, indent=2, ensure_ascii=False)
    print(f"💾 Saved history to {history_path}")

    save_accumulated_cost(f"{args.output_dir}/accumulated_cost.json", total_accumulated_cost)


if __name__ == "__main__":
    main()
