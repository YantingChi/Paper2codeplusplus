# =============================================================================
# 3.5_repair.py  —  Targeted, feedback-driven repair stage for Paper2Code.
#
# WHAT THIS DOES
#   Stage 3 (`3_coding.py`) regenerates the WHOLE repo from scratch on every run,
#   which is stochastic: re-running it flips ~5-6 rubric leaves in each direction
#   purely from LLM variance, swamping the (small) effect of any single prompt
#   improvement. This stage removes that variance. It reads a PaperBench
#   grader_output.json, finds the rubric leaves that FAILED, maps each failing
#   leaf to the repo file(s) the judge actually inspected, and regenerates ONLY
#   those files — leaving every passing file frozen. For each repaired file the
#   prompt lists both the failing requirements (to fix) and the requirements that
#   file already satisfies (to preserve), so fixing one leaf is far less likely to
#   break a neighbour in the same file.
#
#   Leaf -> file mapping is taken from the judge's own per-leaf logs
#   (`<eval_dir>/<leaf_id>.log`, line "Model file selection raw output:"), so no
#   extra LLM calls are spent on mapping and the mapping matches what the judge
#   reads.
#
# WHAT THIS DOES NOT DO
#   It never edits the rubric, the grader, or the paper. It only rewrites code
#   files inside the submission repo. The fixes target faithfulness to the PAPER
#   (the leaf requirements are paper-derived); the judge's "# Reality" critique is
#   passed only as a secondary "here is what is currently wrong" hint.
#
# USAGE
#   python codes/3.5_repair.py \
#       --paper_name adaptive-pruning \
#       --pdf_json_path data/paperbench_jsons/adaptive-pruning/paper_cleaned.json \
#       --output_dir outputs/paperbench_log/adaptive-pruning \
#       --output_repo_dir outputs/paperbench_repos/adaptive-pruning_repo \
#       --grader_output outputs/paperbench_eval/adaptive-pruning/iter2proxy_XXprovide/grader_output.json \
#       --gpt_version gpt-5.2
#   (then re-grade the repo with scripts/grade_paper.sh JUDGE=simple to verify)
# =============================================================================

import argparse
import copy
import json
import os
import re
import sys

from utils import (
    extract_code_from_content,
    print_response,
    print_log_cost,
    load_accumulated_cost,
    save_accumulated_cost,
)
from openai_client import create_openai_client


# Parse CLI args. output_dir = stage-1/2 log dir; output_repo_dir = the repo to repair.
def parse_args():
    p = argparse.ArgumentParser(description="Targeted feedback-driven repair of a Paper2Code repo.")
    p.add_argument('--paper_name', type=str, required=True)
    p.add_argument('--gpt_version', type=str, default="gpt-5.2")
    p.add_argument('--paper_format', type=str, default="JSON", choices=["JSON", "LaTeX"])
    p.add_argument('--pdf_json_path', type=str)
    p.add_argument('--pdf_latex_path', type=str)
    p.add_argument('--output_dir', type=str, required=True, help="stage log dir (for cost log + artifacts)")
    p.add_argument('--output_repo_dir', type=str, required=True, help="the submission repo to repair in place")
    p.add_argument('--grader_output', type=str, required=True, help="path to grader_output.json to repair against")
    p.add_argument('--eval_dir', type=str, default="", help="dir holding per-leaf <id>.log files (default: grader_output's dir)")
    p.add_argument('--score_threshold', type=float, default=0.8, help="leaves scoring below this are 'failing'")
    p.add_argument('--max_files', type=int, default=0, help="cap number of files to repair (0 = no cap)")
    p.add_argument('--use_reality', action='store_true', default=True, help="include judge '# Reality' critique as a hint")
    return p.parse_args()


# Load the paper text in the requested format; exit nonzero with a clear message on failure.
def load_paper(paper_format, pdf_json_path, pdf_latex_path):
    try:
        if paper_format == "JSON":
            with open(pdf_json_path) as f:
                return json.load(f)
        with open(pdf_latex_path) as f:
            return f.read()
    except Exception as e:
        print(f"[ERROR][repair] could not load paper ({paper_format}): {e}", file=sys.stderr)
        sys.exit(1)


# Recursively collect leaf nodes (no sub_tasks) from a graded_task_tree.
def collect_leaves(node, out):
    subs = node.get("sub_tasks") or []
    if not subs:
        out.append(node)
        return
    for s in subs:
        collect_leaves(s, out)


# Pull the "# Reality" section out of a judge full_judge_response markdown blob.
def extract_reality(full_resp):
    if not full_resp:
        return ""
    m = re.search(r"#\s*Reality\s*\n(.*?)(?:\n#\s|\Z)", full_resp, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


# Read the judge's per-leaf log and return the repo-relative file paths it selected.
# The judge logs lines like "Model file selection raw output:\n<path>\n<path>...".
def files_selected_for_leaf(eval_dir, leaf_id, repo_dir):
    log_path = os.path.join(eval_dir, f"{leaf_id}.log")
    if not os.path.isfile(log_path):
        return []
    try:
        with open(log_path, errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return []
    selected = []
    for i, line in enumerate(lines):
        if "Model file selection raw output:" in line:
            # The selected paths follow on subsequent lines until the next log line.
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if not s or s.startswith("20") and "[info" in nxt:  # next structured log line
                    break
                # keep only things that look like a relative file path and exist in the repo
                cand = s.strip().strip("/")
                if "/" in cand or cand.endswith((".py", ".yaml", ".yml", ".sh", ".json", ".md", ".txt", ".cfg", ".ini", ".toml")):
                    if os.path.isfile(os.path.join(repo_dir, cand)):
                        selected.append(cand)
            break
    # de-dup preserving order
    seen = set()
    return [x for x in selected if not (x in seen or seen.add(x))]


# Build a repair message for one file: fix the failing requirements while preserving the passing ones.
def build_repair_msg(paper_content, paper_format, rel_path, current_code, failing_items, passing_reqs, use_reality):
    fixes = ""
    for idx, (req, reality) in enumerate(failing_items, 1):
        fixes += f"\n{idx}. REQUIREMENT (from the paper / rubric): {req}\n"
        if use_reality and reality:
            fixes += f"   CURRENT SHORTCOMING (what the code does now, to be corrected): {reality[:700]}\n"

    preserve = ""
    if passing_reqs:
        preserve = "\n".join(f"- {r}" for r in passing_reqs[:25])

    system = {
        "role": "system",
        "content": (
            "You are an expert ML reproducibility engineer. You are given ONE source file from a "
            "paper-reproduction repository, plus the paper as ground truth. The file currently fails to "
            "faithfully implement some requirements from the paper. Your job is to return a CORRECTED, "
            "COMPLETE version of this one file that implements those requirements faithfully, while keeping "
            "everything else in the file working and unchanged in interface.\n\n"
            "Hard rules:\n"
            "1. Implement the ACTUAL algorithm/method from the paper. NEVER write a placeholder, stub, "
            "'proxy', 'simplified', or 'we do not vendor' implementation.\n"
            "2. Implement equations EXACTLY as written, including every coefficient, scaling factor, and "
            "constant (e.g. EMA decay weights, loss-term weights, a leading factor of 2, summation ranges).\n"
            "3. Preserve the precise semantics (e.g. distinguish pruning of attention heads vs neurons vs "
            "hidden dimensions; do not collapse distinct mask types).\n"
            "4. Perform each operation at the pipeline point the paper specifies (e.g. merge adapters BEFORE "
            "inference/evaluation, not only at export).\n"
            "5. Keep exact paper hyperparameters resolvable (learning rate, batch size, epochs, splits, "
            "sparsity schedule) and report the exact metrics the paper specifies.\n"
            "6. DO NOT change the file's public classes/functions/signatures or break imports other files rely "
            "on. Change only what is needed for fidelity. Keep all currently-correct behaviour intact.\n"
            "7. Output the COMPLETE corrected file inside a single ```python code block, nothing else."
        ),
    }
    user = {
        "role": "user",
        "content": f"""## Paper ({paper_format})
{paper_content}

-----

## File to repair: {rel_path}
Current contents:
```python
{current_code}
```

-----

## Requirements this file currently FAILS — fix each one faithfully to the paper:
{fixes}

-----

## Behaviours this file already implements correctly — DO NOT break these:
{preserve if preserve else "(none recorded)"}

-----

## Output
Return the COMPLETE corrected `{rel_path}` inside one ```python block. Change only what is necessary to
satisfy the failing requirements while preserving the rest of the file's behaviour and interface.""",
    }
    return [system, user]


def main():
    args = parse_args()
    client = create_openai_client()
    gpt_version = args.gpt_version

    eval_dir = args.eval_dir or os.path.dirname(args.grader_output)
    repo_dir = args.output_repo_dir

    if not os.path.isdir(repo_dir):
        print(f"[ERROR][repair] repo dir not found: {repo_dir}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.grader_output):
        print(f"[ERROR][repair] grader_output.json not found: {args.grader_output}", file=sys.stderr)
        sys.exit(1)

    paper_content = load_paper(args.paper_format, args.pdf_json_path, args.pdf_latex_path)

    with open(args.grader_output) as f:
        grader = json.load(f)
    leaves = []
    collect_leaves(grader["graded_task_tree"], leaves)

    # Build file -> {failing:[(req,reality)], passing:[req]} from the judge's own per-leaf file selection.
    file_failing = {}
    file_passing = {}
    unmapped_failing = 0
    for lf in leaves:
        score = lf.get("score") or 0
        req = (lf.get("requirements") or "").replace("\n", " ").strip()
        reality = extract_reality((lf.get("judge_metadata") or {}).get("full_judge_response", ""))
        sel = files_selected_for_leaf(eval_dir, lf.get("id", ""), repo_dir)
        if score < args.score_threshold:
            if not sel:
                unmapped_failing += 1
                continue
            # attribute a failing requirement to its top selected file
            target = sel[0]
            file_failing.setdefault(target, []).append((req, reality))
        else:
            for s in sel:
                file_passing.setdefault(s, []).append(req)

    targets = list(file_failing.keys())
    if args.max_files and len(targets) > args.max_files:
        # repair the files with the most failing requirements first
        targets = sorted(targets, key=lambda t: len(file_failing[t]), reverse=True)[: args.max_files]

    print(f"[repair] failing leaves mapped to {len(file_failing)} files; "
          f"{unmapped_failing} failing leaves had no resolvable file (skipped).")
    print(f"[repair] repairing {len(targets)} files: {targets}")
    if not targets:
        print("[repair] nothing to repair (no failing leaf mapped to an existing file).")
        return

    artifact_dir = os.path.join(args.output_dir, "repair_artifacts")
    os.makedirs(artifact_dir, exist_ok=True)
    total_cost = load_accumulated_cost(f"{args.output_dir}/accumulated_cost.json")

    for rel_path in targets:
        full_path = os.path.join(repo_dir, rel_path)
        try:
            with open(full_path, errors="ignore") as f:
                current_code = f.read()
        except Exception as e:
            print(f"[ERROR][repair] cannot read {full_path}: {e}", file=sys.stderr)
            continue

        stage = f"[REPAIR] {rel_path}"
        print(stage)
        msg = build_repair_msg(
            paper_content, args.paper_format, rel_path, current_code,
            file_failing[rel_path], file_passing.get(rel_path, []), args.use_reality,
        )

        # Reuse the same completion pattern as 3_coding.py.
        if "o3-mini" in gpt_version:
            completion = client.chat.completions.create(model=gpt_version, reasoning_effort="high", messages=msg)
        else:
            completion = client.chat.completions.create(model=gpt_version, messages=msg)

        completion_json = json.loads(completion.model_dump_json())
        print_response(completion_json)
        total_cost = print_log_cost(completion_json, gpt_version, stage, args.output_dir, total_cost)

        content = completion.choices[0].message.content
        safe_name = rel_path.replace("/", "_")
        with open(os.path.join(artifact_dir, f"{safe_name}_repair.txt"), 'w') as f:
            f.write(content)

        code = extract_code_from_content(content)
        if len(code) == 0:
            # If the model didn't fence the code, skip overwriting rather than corrupt the file.
            print(f"[WARN][repair] no fenced code block returned for {rel_path}; leaving file unchanged.")
            continue

        with open(full_path, 'w') as f:
            f.write(code)
        print(f"[repair] rewrote {rel_path} ({len(code)} chars)")

    save_accumulated_cost(f"{args.output_dir}/accumulated_cost.json", total_cost)
    print(f"[repair] done. Repaired {len(targets)} files. Re-grade with JUDGE=simple to verify.")


if __name__ == "__main__":
    main()
