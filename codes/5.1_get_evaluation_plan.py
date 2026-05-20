# Get a hardware-constrained evaluation plan for Paper2Code reproduction.
# Uses OpenAI API with structured JSON output to produce a feasibility-filtered
# plan that fits within GPU VRAM (≤6 GB) and dataset size (≤5 GB) constraints.
# Outputs three files: eval_plan.json, partial_scope.json, download_list.json.
#
# Example:
# python3.10 "codes/5.1 get_evaluation_plan.py" \
#   --paper_json_path data/paperbench_jsons/adaptive-pruning/paper_cleaned.json \
#   --eval_info_json results/adaptive-pruning_paper_only_eval_info_o3-mini_20250513.json \
#   --generated_repo_path outputs/paperbench_repos/adaptive-pruning_repo \
#   --output_dir tests/harbor/yantingchi/adaptive-pruning/eval_plan
#
# Dry-run (writes prompt but skips API call):
# python3.10 "codes/5.1 get_evaluation_plan.py" \
#   --paper_json_path paper.json \
#   --generated_repo_path generated_repo \
#   --dry-run
#
# Outputs go to: output_dir/eval_plan.json
#                output_dir/partial_scope.json
#                output_dir/download_list.json

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HARBOR_ROOT = REPO_ROOT / "tests" / "harbor" / "yantingchi"

DEFAULT_GPU_VRAM_LIMIT_GB = 6.0
DEFAULT_DATASET_SIZE_LIMIT_GB = 5.0

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openai_client import create_openai_client
from utils import num_tokens_from_messages, print_log_cost


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
                    "fits_in_vram": {"type": "boolean"},
                    "in_scope": {"type": "boolean"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "name", "slug", "paper_citation", "implementation_type",
                    "official_repo_url", "huggingface_id", "install_cmd",
                    "priority", "estimated_size_mb", "fits_in_vram", "in_scope", "notes",
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
                        "enum": ["hf_dataset", "download", "manual", "generated", "lm_eval_task"],
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
                    "fits_in_storage": {"type": "boolean"},
                    "in_scope": {"type": "boolean"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "name", "slug", "fetch_method", "hf_dataset_id", "hf_dataset_config",
                    "download_url", "splits_needed", "estimated_size_mb", "license",
                    "priority", "fits_in_storage", "in_scope", "notes",
                ],
            },
        },
        "download_sequence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "total_estimated_size_mb": {"type": "number"},
        "feasible_size_mb": {"type": "number"},
        "notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "baselines", "datasets", "download_sequence",
        "total_estimated_size_mb", "feasible_size_mb", "notes",
    ],
}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use OpenAI API to produce a hardware-constrained evaluation plan. "
            "Selects which datasets and baselines fit within GPU VRAM and size limits."
        )
    )
    parser.add_argument("--paper_json_path", type=str, required=True,
                        help="Path to the cleaned paper JSON.")
    parser.add_argument(
        "--eval_info_json",
        type=str,
        default="",
        help="Path to output of 5_eval_get_running_info.py (structured eval info).",
    )
    parser.add_argument("--generated_repo_path", type=str, required=True,
                        help="Path to the generated codebase for this paper.")
    parser.add_argument(
        "--output_dir",
        type=str,
        default="",
        help="Directory for output files. "
             "Defaults to tests/harbor/yantingchi/<paper_slug>/eval_plan.",
    )
    parser.add_argument("--gpt_version", type=str, default="gpt-4.1",
                        help="OpenAI model to use.")
    parser.add_argument(
        "--gpu_vram_limit_gb",
        type=float,
        default=DEFAULT_GPU_VRAM_LIMIT_GB,
        help=f"Maximum GPU VRAM in GB for training (default: {DEFAULT_GPU_VRAM_LIMIT_GB}).",
    )
    parser.add_argument(
        "--dataset_size_limit_gb",
        type=float,
        default=DEFAULT_DATASET_SIZE_LIMIT_GB,
        help=f"Maximum total dataset download in GB (default: {DEFAULT_DATASET_SIZE_LIMIT_GB}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write prompt but do not invoke the API.",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Save raw model response JSON in the output file.",
    )
    return parser.parse_args(argv)


def load_json(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_optional_json(path) -> dict:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return load_json(candidate)


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return normalized.strip("_") or "unknown"


def resolve_paper_name(paper_payload: dict, paper_json_path=None) -> str:
    for key in ("title", "paper_title", "paper_name"):
        text = str(paper_payload.get(key, "")).strip()
        if text:
            return text
    if paper_json_path:
        return Path(paper_json_path).stem
    return "unknown"


def resolve_output_dir(args: argparse.Namespace, paper_name: str) -> Path:
    if getattr(args, "output_dir", ""):
        return Path(args.output_dir).resolve()
    return DEFAULT_HARBOR_ROOT / slugify(paper_name) / "eval_plan"


def extract_eval_info_summary(eval_info: dict) -> dict:
    extracted = eval_info.get("extracted_info", {})
    if not isinstance(extracted, dict):
        return {"benchmarks": [], "baselines": [], "training": {}, "hardware": []}
    setup = extracted.get("Evaluation Set-up", {})
    return {
        "benchmarks": extracted.get("Benchmark evaluated on", []),
        "baselines": extracted.get("Baseline compared with", []),
        "training": extracted.get("Training", {}),
        "hardware": setup.get("hardware", []) if isinstance(setup, dict) else [],
        "machine": setup.get("machine", []) if isinstance(setup, dict) else [],
    }


def build_messages(
    paper_payload: dict,
    eval_info: dict,
    gpu_vram_limit_gb: float,
    dataset_size_limit_gb: float,
) -> List[Dict[str, str]]:
    extracted = eval_info.get("extracted_info", eval_info) if eval_info else {}

    system_prompt = f"""You are an expert ML-reproducibility engineer.

Given a research paper JSON and its structured evaluation-info, produce a concrete,
hardware-constrained evaluation plan.

Hardware constraints:
- GPU VRAM limit: {gpu_vram_limit_gb} GB (training runs must fit within this)
- Dataset size limit: {dataset_size_limit_gb} GB total downloaded data

Your output has two arrays:

1. `baselines` — one entry per baseline the paper compares against.
   For each entry:
   - `implementation_type`:
       "download"        if a public GitHub / HuggingFace repo is known;
       "reference_only"  if trivial (e.g. vanilla fine-tuning, standard SGD);
       "implement"       if no public code exists.
   - `official_repo_url`: canonical GitHub URL or "Not specified".
   - `priority`: "main", "ablation", or "appendix".
   - `fits_in_vram`: true if the model/method fits within {gpu_vram_limit_gb} GB VRAM.
   - `in_scope`: true if this baseline should be included given hardware constraints.

2. `datasets` — one entry per dataset / benchmark the paper evaluates on.
   - `fetch_method`: "hf_dataset", "lm_eval_task", "download", "manual", or "generated".
   - Fill `hf_dataset_id` + `hf_dataset_config` for hf_dataset entries.
   - Fill `download_url` for download entries.
   - Use "Not specified" for fields that do not apply.
   - `fits_in_storage`: true if estimated_size_mb <= {dataset_size_limit_gb * 1024:.0f} MB.
   - `in_scope`: true if this dataset should be fetched given hardware/storage constraints.

3. `download_sequence`: flat list of in-scope dataset slugs in priority order.
4. `total_estimated_size_mb`: sum of ALL entries' estimated_size_mb.
5. `feasible_size_mb`: sum of only in-scope datasets' estimated_size_mb.
6. `notes`: list of strings explaining key feasibility decisions.

Constraints:
- Use only your training knowledge. Write "Not specified" for unknown URLs.
- `slug` must be lowercase_underscored (no spaces, no parens).
- Do not include entries for items not in the input.
"""

    paper_section = json.dumps(paper_payload, indent=2, ensure_ascii=False)
    eval_section = json.dumps(extracted, indent=2, ensure_ascii=False) if extracted else "(not provided)"

    user_prompt = f"""<task>
Produce a hardware-constrained evaluation plan from the paper and eval_info below.
</task>

<paper>
{paper_section}
</paper>

<eval_info>
{eval_section}
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
    if any(k in model_name for k in ("o1", "o3", "o4")):
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
        return json.loads(text[left: right + 1])
    raise ValueError("Could not parse JSON from model output.")


def derive_partial_scope(eval_plan: Dict) -> Dict:
    return {
        "in_scope_baselines": [b for b in eval_plan.get("baselines", []) if b.get("in_scope")],
        "in_scope_datasets": [d for d in eval_plan.get("datasets", []) if d.get("in_scope")],
        "feasible_size_mb": eval_plan.get("feasible_size_mb", 0),
        "notes": eval_plan.get("notes", []),
    }


def derive_download_list(eval_plan: Dict) -> List[Dict]:
    items = []
    for d in eval_plan.get("datasets", []):
        if not d.get("in_scope"):
            continue
        items.append({
            "slug": d.get("slug", ""),
            "name": d.get("name", ""),
            "fetch_method": d.get("fetch_method", ""),
            "hf_dataset_id": d.get("hf_dataset_id", ""),
            "hf_dataset_config": d.get("hf_dataset_config", ""),
            "download_url": d.get("download_url", ""),
            "splits_needed": d.get("splits_needed", []),
            "estimated_size_mb": d.get("estimated_size_mb", 0),
        })
    return items


def main(args: argparse.Namespace) -> None:
    paper_payload = load_json(args.paper_json_path)
    eval_info = load_optional_json(args.eval_info_json)
    paper_name = resolve_paper_name(paper_payload, args.paper_json_path)
    output_dir = resolve_output_dir(args, paper_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated_repo_path = Path(args.generated_repo_path).resolve()
    if not generated_repo_path.exists():
        raise FileNotFoundError(f"Generated repo path does not exist: {generated_repo_path}")

    messages = build_messages(
        paper_payload=paper_payload,
        eval_info=eval_info,
        gpu_vram_limit_gb=args.gpu_vram_limit_gb,
        dataset_size_limit_gb=args.dataset_size_limit_gb,
    )

    prompt_text = "\n\n---\n".join(m["content"] for m in messages)
    (output_dir / "c5.1_prompt.txt").write_text(prompt_text, encoding="utf-8")

    if args.dry_run:
        print(f"Dry run: prompt written to {output_dir / 'c5.1_prompt.txt'}")
        return

    token_count = -1
    try:
        token_count = num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")

    client = create_openai_client()
    request_json = build_request_json(args.gpt_version, messages)
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())

    output_content = completion_json["choices"][0]["message"]["content"]
    eval_plan = extract_json_from_content(output_content)

    (output_dir / "eval_plan.json").write_text(
        json.dumps(eval_plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "partial_scope.json").write_text(
        json.dumps(derive_partial_scope(eval_plan), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "download_list.json").write_text(
        json.dumps(derive_download_list(eval_plan), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    result_payload: Dict = {
        "paper_name": paper_name,
        "model": args.gpt_version,
        "paper_json_path": str(Path(args.paper_json_path).resolve()),
        "eval_info_json": str(Path(args.eval_info_json).resolve()) if args.eval_info_json else "",
        "generated_repo_path": str(generated_repo_path),
        "output_dir": str(output_dir),
        "gpu_vram_limit_gb": args.gpu_vram_limit_gb,
        "dataset_size_limit_gb": args.dataset_size_limit_gb,
        "prompt_token_estimate": token_count,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.save_raw_completion:
        result_payload["raw_completion"] = completion_json
    else:
        result_payload["raw_model_output"] = output_content

    (output_dir / "c5.1_result.json").write_text(
        json.dumps(result_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    n_baselines = len(eval_plan.get("baselines", []))
    n_datasets = len(eval_plan.get("datasets", []))
    n_in_scope_b = sum(1 for b in eval_plan.get("baselines", []) if b.get("in_scope"))
    n_in_scope_d = sum(1 for d in eval_plan.get("datasets", []) if d.get("in_scope"))
    total_mb = eval_plan.get("total_estimated_size_mb", 0)
    feasible_mb = eval_plan.get("feasible_size_mb", 0)

    print("=" * 60)
    print("Evaluation Plan Generation Summary")
    print(f"Paper name:             {paper_name}")
    print(f"Model:                  {args.gpt_version}")
    print(f"Prompt token estimate:  {token_count}")
    print(f"Baselines planned:      {n_baselines} (in scope: {n_in_scope_b})")
    print(f"Datasets planned:       {n_datasets} (in scope: {n_in_scope_d})")
    print(f"Total estimated size:   {total_mb:.0f} MB")
    print(f"Feasible size:          {feasible_mb:.0f} MB")
    print(f"Artifacts saved to:     {output_dir}")
    print("=" * 60)

    try:
        print_log_cost(
            completion_json,
            args.gpt_version,
            f"[EvalPlan] {paper_name}",
            str(output_dir),
            0,
        )
    except Exception as exc:
        print(f"[WARNING] Cost logging skipped: {exc}")


if __name__ == "__main__":
    try:
        main(parse_args())
    except Exception as error:
        print(f"[ERROR] {error}")
        sys.exit(1)
