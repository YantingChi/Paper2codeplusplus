import argparse
import json
import os
import re
import sys
from typing import Dict, List, Tuple

from openai_client import create_openai_client

from utils import get_now_str, num_tokens_from_messages, print_log_cost


PAPER_ONLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "Evaluation Set-up": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "hardware": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "machine": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "docker_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "other_runtime_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "hardware",
                "machine",
                "docker_requirements",
                "other_runtime_requirements",
            ],
        },
        "Benchmark evaluated on": {
            "type": "array",
            "items": {"type": "string"},
        },
        "Baseline compared with": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "paper_citation": {"type": "string"},
                    "download_link": {"type": "string"},
                    "short_description": {"type": "string"},
                },
                "required": [
                    "name",
                    "paper_citation",
                    "download_link",
                    "short_description",
                ],
            },
        },
        "Training": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "is_training_required": {
                    "type": "string",
                },
                "hyperparameters": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "random_seed": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "is_training_required",
                "hyperparameters",
                "random_seed",
            ],
        },
        "Results": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "metrics_used": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "statistical_results": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "figure_results": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "metrics_used",
                "statistical_results",
                "figure_results",
            ],
        },
        "Expected Results": {
            "type": "array",
            "items": {"type": "string"},
        },
        "Notes": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "Evaluation Set-up",
        "Benchmark evaluated on",
        "Baseline compared with",
        "Training",
        "Results",
        "Expected Results",
        "Notes",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract evaluation setup, benchmark, training, and results from paper only."
        )
    )
    parser.add_argument("--paper_name", type=str, required=True)

    parser.add_argument(
        "--paper_format",
        type=str,
        default="JSON",
        choices=["JSON", "LaTeX"],
    )
    parser.add_argument("--pdf_json_path", type=str, default="")
    parser.add_argument("--pdf_latex_path", type=str, default="")

    parser.add_argument("--gpt_version", type=str, default="o3-mini")
    parser.add_argument(
        "--max_paper_chars",
        type=int,
        default=180000,
        help="Maximum characters from paper context to include in prompt.",
    )
    parser.add_argument(
        "--eval_result_dir",
        type=str,
        default="",
        help="Optional output directory for generated eval artifacts and logs.",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="",
        help="Optional output json path or output directory.",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Save raw model response json in the output file.",
    )
    return parser.parse_args()


def load_paper_context(args: argparse.Namespace) -> Tuple[str, Dict[str, object]]:
    if args.paper_format == "JSON":
        if len(args.pdf_json_path.strip()) == 0:
            raise ValueError("`--pdf_json_path` is required when paper_format is JSON.")
        with open(args.pdf_json_path, "r", encoding="utf-8") as f:
            paper_obj = json.load(f)
        paper_text = json.dumps(paper_obj, ensure_ascii=False)
        source_path = args.pdf_json_path
    else:
        if len(args.pdf_latex_path.strip()) == 0:
            raise ValueError("`--pdf_latex_path` is required when paper_format is LaTeX.")
        with open(args.pdf_latex_path, "r", encoding="utf-8") as f:
            paper_text = f.read()
        source_path = args.pdf_latex_path

    original_chars = len(paper_text)
    truncated = original_chars > args.max_paper_chars
    if truncated:
        paper_text = paper_text[: args.max_paper_chars]
        paper_text += "\n...[TRUNCATED PAPER CONTEXT]..."

    meta = {
        "source_path": source_path,
        "paper_format": args.paper_format,
        "original_chars": original_chars,
        "used_chars": len(paper_text),
        "truncated": truncated,
    }
    return paper_text, meta


def build_messages(paper_text: str, paper_meta: Dict[str, object]) -> List[Dict[str, str]]:
    system_prompt = """You are an expert research-paper analyst.

Extract ONLY the following information from the provided paper context:
1) Evaluation Set-up
2) Benchmark evaluated on
3) Baseline compared with
4) Training
5) Expected Results

Critical constraints:
- Use only information from the paper text (including appendix content if present in the input).
- Do NOT use repository/code assumptions or external knowledge.
- If a detail is missing, write \"Not specified\".
- Return only valid JSON matching the provided schema.
- Do not output chain-of-thought.
"""

    user_prompt = f"""<task>
Extract the required fields from the paper.
</task>

<success_criteria>
- Evaluation Set-up:
  - hardware: CPU/GPU/TPU details if available.
  - machine: machine/server model, cloud type, or cluster details if available.
  - docker_requirements: what is needed to run in Docker (base image, CUDA/toolkit, runtime constraints) if available.
- Benchmark evaluated on:
  - list datasets/benchmarks/tasks used by this paper.
- Baseline compared with:
  - For each baseline method this paper compares against, list:
    - name: the baseline method name as written in the paper.
    - paper_citation: the citation string as printed in the paper (e.g., "Smith et al., 2022").
    - download_link: a URL to the baseline's code/repo/page if explicitly given in the paper; otherwise "Not specified". Do not invent URLs.
    - short_description: one sentence describing what the baseline is, drawn only from this paper's text.
  - If the paper does not compare against any baselines, return an empty array.
- Training:
  - is_training_required: whether a new model needs training.
  - hyperparameters: training hyperparameters (LR, batch size, epochs, etc.).
  - random_seed: any seed settings.
- Results:
  - metrics_used: metric names.
  - statistical_results: numeric/statistical outcomes reported for this work.
  - figure_results: findings tied to figures/tables for this work.
- List benchmark names/datasets/tasks explicitly.
- Summarize expected results exactly as stated in paper text; do not invent numbers.
</success_criteria>

<input_meta>
{json.dumps(paper_meta, ensure_ascii=False)}
</input_meta>

<paper>
{paper_text}
</paper>
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def extract_json_from_content(content: str) -> Dict[str, object]:
    text = content.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    fenced_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced_match:
        return json.loads(fenced_match.group(1))

    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        return json.loads(text[left : right + 1])

    raise ValueError("Could not parse JSON from model output.")


def build_request_json(
    model_name: str, messages: List[Dict[str, str]]
) -> Dict[str, object]:
    request_json: Dict[str, object] = {
        "model": model_name,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "paper_eval_info_extraction",
                "schema": PAPER_ONLY_SCHEMA,
                "strict": True,
            },
        },
    }

    if "o3" in model_name or "o4" in model_name:
        request_json["reasoning_effort"] = "high"
    else:
        request_json["temperature"] = 0

    return request_json


def resolve_output_paths(args: argparse.Namespace) -> Tuple[str, str]:
    output_path = args.output_path.strip()
    eval_result_dir = args.eval_result_dir.strip()

    if output_path:
        if output_path.lower().endswith(".json"):
            save_path = output_path
            output_dir = os.path.dirname(os.path.abspath(save_path))
        else:
            output_dir = output_path
            now_str = get_now_str()
            save_path = os.path.join(
                output_dir,
                f"{args.paper_name}_paper_only_eval_info_{args.gpt_version}_{now_str}.json",
            )
        return save_path, output_dir

    if eval_result_dir:
        output_dir = eval_result_dir
    else:
        output_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "results")
        )

    now_str = get_now_str()
    save_path = os.path.join(
        output_dir,
        f"{args.paper_name}_paper_only_eval_info_{args.gpt_version}_{now_str}.json",
    )
    return save_path, output_dir


def main(args: argparse.Namespace) -> None:
    client = create_openai_client()

    paper_text, paper_meta = load_paper_context(args)
    messages = build_messages(paper_text=paper_text, paper_meta=paper_meta)

    token_count = -1
    try:
        token_count = num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")

    request_json = build_request_json(args.gpt_version, messages)
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())

    output_content = completion_json["choices"][0]["message"]["content"]
    extracted_info = extract_json_from_content(output_content)

    output_payload: Dict[str, object] = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "paper_meta": paper_meta,
        "prompt_token_estimate": token_count,
        "extracted_info": extracted_info,
    }

    if args.save_raw_completion:
        output_payload["raw_completion"] = completion_json
    else:
        output_payload["raw_model_output"] = output_content

    save_path, output_dir = resolve_output_paths(args)
    os.makedirs(output_dir, exist_ok=True)

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print("Paper-Only Evaluation Info Extraction Summary")
    print(f"Paper name: {args.paper_name}")
    print(f"Model: {args.gpt_version}")
    print(f"Prompt token estimate: {token_count}")
    print(f"Saved to: {save_path}")
    print("=" * 50)

    try:
        print_log_cost(
            completion_json,
            args.gpt_version,
            f"[PaperOnlyEvalInfo] {args.paper_name}",
            output_dir,
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
