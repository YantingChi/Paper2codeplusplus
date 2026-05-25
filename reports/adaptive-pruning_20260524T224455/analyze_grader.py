#!/usr/bin/env python3
# analyze_grader.py — Parse a PaperBench grader_output.json, list failing leaves
# (score < threshold), pull the "# Reality" block from each judge response, and
# print a weight-sorted summary to drive prompt-improvement decisions.
#
# Usage:
#   python analyze_grader.py <grader_output.json> [--threshold 0.8] [--full <leaf_id>]

import json
import re
import sys
import argparse


# Recursively walk graded_task_tree, returning only leaf nodes (no sub_tasks).
def collect_leaves(node, out):
    subs = node.get("sub_tasks") or []
    if not subs:
        out.append(node)
        return
    for s in subs:
        collect_leaves(s, out)


# Pull the "# Reality" section text out of a full_judge_response markdown blob.
def extract_reality(full_resp):
    if not full_resp:
        return ""
    m = re.search(r"#\s*Reality\s*\n(.*?)(?:\n#\s|\Z)", full_resp, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--full", default=None, help="print full judge response for this leaf id")
    args = ap.parse_args()

    with open(args.path) as f:
        data = json.load(f)

    print(f"AGGREGATE score={data.get('score'):.4f}  leaves={data.get('num_leaf_nodes')}")

    leaves = []
    collect_leaves(data["graded_task_tree"], leaves)

    if args.full:
        for lf in leaves:
            if lf.get("id") == args.full:
                jm = lf.get("judge_metadata") or {}
                print(jm.get("full_judge_response", "<no judge response>"))
                return
        print(f"leaf id {args.full} not found")
        return

    failing = [lf for lf in leaves if (lf.get("score") or 0) < args.threshold]
    # Sort by weight descending so highest-impact failures surface first.
    failing.sort(key=lambda x: x.get("weight", 0), reverse=True)

    total_w = sum(lf.get("weight", 0) for lf in leaves)
    fail_w = sum(lf.get("weight", 0) for lf in failing)
    print(f"FAILING (<{args.threshold}): {len(failing)}/{len(leaves)} leaves | "
          f"failing weight {fail_w:.3f} of {total_w:.3f}\n")

    for lf in failing:
        req = (lf.get("requirements") or "").replace("\n", " ")
        reality = extract_reality((lf.get("judge_metadata") or {}).get("full_judge_response", ""))
        reality_short = reality.replace("\n", " ")[:300]
        print(f"--- id={lf.get('id')} | weight={lf.get('weight'):.4f} | score={lf.get('score')}")
        print(f"    REQ: {req[:200]}")
        print(f"    REALITY: {reality_short}")
        print()


if __name__ == "__main__":
    main()
