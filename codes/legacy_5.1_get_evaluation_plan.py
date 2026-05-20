import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from openai_client import create_openai_client
from utils import get_now_str, num_tokens_from_messages, print_log_cost


# Example invocations:
#
# python3.10 codes/5.1_get_evaluation_plan.py \
#   --paper_name adaptive-pruning \
#   --eval_info_json outputs/paperbench_repos/adaptive-pruning_repo/eval/adaptive-pruning_paper_only_eval_info_gpt-5.2.json \
#   --gpt_version gpt-5.2 \
#   --output_path outputs/paperbench_repos/adaptive-pruning_repo/eval/adaptive-pruning_eval_plan_gpt-5.2.json
#
# The --eval_info_json produced by 5_eval_get_running_info.py is the primary input.
# This script enriches it into a concrete download / implementation plan that
# 6_download_dataset.py can execute without re-reading the paper:
#
# Flow:  paper → [5] → eval_info.json → [5.1] → eval_plan.json → [6] → assets/
#
# For each baseline the plan records:
#   - official_repo_url  (from model training knowledge)
#   - huggingface_id     (model id if applicable)
#   - implementation_type: "download" | "implement" | "reference_only"
#   - priority: "main" | "ablation" | "appendix"
#   - estimated_size_mb
#
# For each dataset / benchmark the plan records:
#   - fetch_method: "hf_dataset" | "download" | "manual" | "generated" | "lm_eval_task"
#   - hf_dataset_id + hf_dataset_config  (for HuggingFace datasets)
#   - download_url                        (for direct-download datasets)
#   - splits_needed                       (e.g. ["train", "validation"])
#   - estimated_size_mb
#   - license


EVAL_PLAN_SCHEMA: Dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "baselines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "paper_citation": {"type": "string"},
                    "implementation_type": {
                        "type": "string",
                        "enum": ["download", "implement", "reference_only"],
                    },
                    "official_repo_url": {"type": "string"},
                    "huggingface_id": {"type": "string"},
                    "install_cmd": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["main", "ablation", "appendix"],
                    },
                    "estimated_size_mb": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "name",
                    "slug",
                    "paper_citation",
                    "implementation_type",
                    "official_repo_url",
                    "huggingface_id",
                    "install_cmd",
                    "priority",
                    "estimated_size_mb",
                    "notes",
                ],
            },
        },
        "datasets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "fetch_method": {
                        "type": "string",
                        "enum": [
                            "hf_dataset",
                            "download",
                            "manual",
                            "generated",
                            "lm_eval_task",
                        ],
                    },
                    "hf_dataset_id": {"type": "string"},
                    "hf_dataset_config": {"type": "string"},
                    "download_url": {"type": "string"},
                    "splits_needed": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "estimated_size_mb": {"type": "number"},
                    "license": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["main", "secondary"],
                    },
                    "notes": {"type": "string"},
                },
                "required": [
                    "name",
                    "slug",
                    "fetch_method",
                    "hf_dataset_id",
                    "hf_dataset_config",
                    "download_url",
                    "splits_needed",
                    "estimated_size_mb",
                    "license",
                    "priority",
                    "notes",
                ],
            },
        },
        "download_sequence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "total_estimated_size_mb": {"type": "number"},
        "notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "baselines",
        "datasets",
        "download_sequence",
        "total_estimated_size_mb",
        "notes",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Produce a detailed evaluation plan (baseline download/impl plan + "
            "dataset fetch plan) from the structured eval_info JSON output of "
            "5_eval_get_running_info.py, without re-reading the paper."
        )
    )
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument(
        "--eval_info_json",
        type=str,
        required=True,
        help="Path to the JSON produced by 5_eval_get_running_info.py.",
    )
    parser.add_argument("--gpt_version", type=str, default="o3-mini")
    parser.add_argument(
        "--output_path",
        type=str,
        default="",
        help="Output JSON path. Defaults to <eval_info_json dir>/<paper_name>_eval_plan_<model>_<ts>.json",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Save raw model response JSON in the output file.",
    )
    return parser.parse_args()


def load_eval_info(eval_info_path: str) -> Dict:
    with open(eval_info_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_messages(eval_info: Dict) -> List[Dict[str, str]]:
    extracted = eval_info.get("extracted_info", eval_info)

    system_prompt = """You are an expert ML-reproducibility engineer.

Given a structured evaluation-info JSON extracted from a research paper, produce
a concrete evaluation plan describing EXACTLY how to obtain each baseline and
each benchmark dataset.

Your output has two arrays:

1. `baselines` — one entry per baseline the paper compares against.
   For each entry decide:
   - `implementation_type`:
       "download"        if a public GitHub / HuggingFace repo is known;
       "reference_only"  if the baseline is a trivial procedure (e.g. vanilla
                         fine-tuning, standard SGD) that needs no separate repo;
       "implement"       if no public code exists and a minimal implementation
                         must be written from the paper's description.
   - `official_repo_url`: the canonical GitHub URL (best-effort from your
     training knowledge). Write "Not specified" if genuinely unknown.
   - `priority`: "main" if the baseline appears in the primary result tables;
     "ablation" if it is an ablation variant; "appendix" if results are
     reported only in the appendix.
   - `estimated_size_mb`: rough size of the repo in MB (0 for reference_only).

2. `datasets` — one entry per dataset / benchmark the paper evaluates on.
   For each entry decide the `fetch_method`:
       "hf_dataset"   — available via `datasets.load_dataset()`;
       "lm_eval_task" — evaluated using lm-evaluation-harness task name;
       "download"     — direct URL download from an official source;
       "manual"       — requires license acceptance or user registration;
       "generated"    — synthetic data the code generates itself.
   Fill `hf_dataset_id` + `hf_dataset_config` for hf_dataset entries.
   Fill `download_url` for download entries.
   Use "Not specified" for fields that do not apply.

Critical constraints:
- Use only your training knowledge. Do not guess URLs you are not confident
  about — write "Not specified" instead.
- `slug` must be a lowercase-underscored identifier (no spaces, no parens).
- `download_sequence` is a flat list of slugs in priority order (most
  important first, smallest datasets before largest of equal priority).
- `total_estimated_size_mb` is the sum of all entries' `estimated_size_mb`.
- Do not include entries for items not in the input.
"""

    user_prompt = f"""<task>
Produce a concrete evaluation plan from the eval_info below.
</task>

<eval_info>
{json.dumps(extracted, indent=2, ensure_ascii=False)}
</eval_info>
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_request_json(model_name: str, messages: List[Dict]) -> Dict:
    request: Dict = {
        "model": model_name,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "evaluation_plan",
                "schema": EVAL_PLAN_SCHEMA,
                "strict": True,
            },
        },
    }
    if "o3" in model_name or "o4" in model_name:
        request["reasoning_effort"] = "high"
    else:
        request["temperature"] = 0
    return request


def extract_json_from_content(content: str) -> Dict:
    text = content.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        return json.loads(text[left : right + 1])
    raise ValueError("Could not parse JSON from model output.")


def resolve_output_path(args: argparse.Namespace, eval_info_path: str) -> str:
    if args.output_path.strip():
        return args.output_path.strip()
    out_dir = os.path.dirname(os.path.abspath(eval_info_path))
    now_str = get_now_str()
    return os.path.join(
        out_dir,
        f"{args.paper_name}_eval_plan_{args.gpt_version}_{now_str}.json",
    )


def main(args: argparse.Namespace) -> None:
    client = create_openai_client()

    eval_info = load_eval_info(args.eval_info_json)
    messages = build_messages(eval_info)

    token_count = -1
    try:
        token_count = num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")

    request_json = build_request_json(args.gpt_version, messages)
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())

    output_content = completion_json["choices"][0]["message"]["content"]
    eval_plan = extract_json_from_content(output_content)

    output_payload: Dict = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "eval_info_json": os.path.abspath(args.eval_info_json),
        "prompt_token_estimate": token_count,
        "eval_plan": eval_plan,
    }
    if args.save_raw_completion:
        output_payload["raw_completion"] = completion_json
    else:
        output_payload["raw_model_output"] = output_content

    save_path = resolve_output_path(args, args.eval_info_json)
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print("Evaluation Plan Generation Summary")
    print(f"Paper name:             {args.paper_name}")
    print(f"Model:                  {args.gpt_version}")
    print(f"Prompt token estimate:  {token_count}")
    n_baselines = len(eval_plan.get("baselines", []))
    n_datasets = len(eval_plan.get("datasets", []))
    total_mb = eval_plan.get("total_estimated_size_mb", 0)
    print(f"Baselines planned:      {n_baselines}")
    print(f"Datasets planned:       {n_datasets}")
    print(f"Total estimated size:   {total_mb:.0f} MB")
    print(f"Saved to:               {save_path}")
    print("=" * 50)

    out_dir = os.path.dirname(os.path.abspath(save_path))
    try:
        print_log_cost(
            completion_json,
            args.gpt_version,
            f"[EvalPlan] {args.paper_name}",
            out_dir,
            0,
        )
    except Exception as exc:
        print(f"[WARNING] Cost logging skipped: {exc}")


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        main(cli_args)
    except Exception as error:
        print(f"[ERROR] {error}")
        sys.exit(1)
