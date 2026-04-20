import argparse
import json
import os
import re
import sys
from typing import Dict, List, Tuple

from openai_client import create_openai_client

from utils import get_now_str, num_tokens_from_messages, print_log_cost, read_all_files

#deprecated
DOCKER_REPRO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "goal": {"type": "string"},
        "docker_environment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "base_image": {"type": "string"},
                "system_packages": {"type": "array", "items": {"type": "string"}},
                "python_packages": {"type": "array", "items": {"type": "string"}},
                "environment_variables": {"type": "array", "items": {"type": "string"}},
                "dockerfile_snippet": {"type": "string"},
                "build_command": {"type": "string"},
                "run_command": {"type": "string"},
                "mount_instructions": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "base_image",
                "system_packages",
                "python_packages",
                "environment_variables",
                "dockerfile_snippet",
                "build_command",
                "run_command",
                "mount_instructions",
            ],
        },
        "dataset_download": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "required_datasets": {"type": "array", "items": {"type": "string"}},
                "download_commands": {"type": "array", "items": {"type": "string"}},
                "target_directories": {"type": "array", "items": {"type": "string"}},
                "integrity_checks": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "required_datasets",
                "download_commands",
                "target_directories",
                "integrity_checks",
            ],
        },
        "reproduction_workflow": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "preflight_checks": {"type": "array", "items": {"type": "string"}},
                "ordered_steps": {"type": "array", "items": {"type": "string"}},
                "run_commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "command": {"type": "string"},
                            "purpose": {"type": "string"},
                            "expected_output": {"type": "string"},
                        },
                        "required": ["command", "purpose", "expected_output"],
                    },
                },
            },
            "required": ["preflight_checks", "ordered_steps", "run_commands"],
        },
        "verification": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "metrics_to_verify": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "metric": {"type": "string"},
                            "target_or_reference": {"type": "string"},
                            "how_to_compute": {"type": "string"},
                            "pass_rule": {"type": "string"},
                        },
                        "required": [
                            "metric",
                            "target_or_reference",
                            "how_to_compute",
                            "pass_rule",
                        ],
                    },
                },
                "artifacts_to_collect": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["metrics_to_verify", "artifacts_to_collect"],
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "goal",
        "docker_environment",
        "dataset_download",
        "reproduction_workflow",
        "verification",
        "unknowns",
        "assumptions",
    ],
}


ALLOWED_REPO_EXTENSIONS = [
    ".py",
    ".yaml",
    ".yml",
    ".md",
    ".sh",
    ".bash",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".txt",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Docker-based experiment reproduction plan from an existing plan "
            "and repository code context."
        )
    )
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument(
        "--plan_path",
        type=str,
        required=True,
        help="Path to a previously generated plan json/text file.",
    )
    parser.add_argument(
        "--target_repo_dir",
        type=str,
        required=True,
        help="Path to target repository to inspect.",
    )
    parser.add_argument("--eval_result_dir", type=str, required=True)
    parser.add_argument("--gpt_version", type=str, default="o3-mini")
    parser.add_argument(
        "--max_plan_chars",
        type=int,
        default=120000,
        help="Maximum characters from plan context to include in prompt.",
    )
    parser.add_argument(
        "--max_repo_chars",
        type=int,
        default=180000,
        help="Maximum characters from repository context to include in prompt.",
    )
    parser.add_argument(
        "--max_repo_file_chars",
        type=int,
        default=12000,
        help="Maximum characters per repository file snippet in prompt.",
    )
    parser.add_argument(
        "--max_repo_files",
        type=int,
        default=80,
        help="Maximum number of repository files to include in prompt.",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="",
        help="Optional explicit output json path.",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Save raw model completion JSON in output.",
    )
    return parser.parse_args()


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


def load_plan_context(args: argparse.Namespace) -> Tuple[str, Dict[str, object]]:
    with open(args.plan_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    plan_obj = None
    try:
        plan_obj = json.loads(raw_text)
    except Exception:
        plan_obj = None

    if plan_obj is None:
        plan_text = raw_text
    else:
        plan_text = json.dumps(plan_obj, indent=2, ensure_ascii=False)

    original_chars = len(plan_text)
    truncated = original_chars > args.max_plan_chars
    if truncated:
        plan_text = plan_text[: args.max_plan_chars]
        plan_text += "\n...[TRUNCATED PLAN CONTEXT]..."

    meta = {
        "source_path": args.plan_path,
        "original_chars": original_chars,
        "used_chars": len(plan_text),
        "truncated": truncated,
    }
    return plan_text, meta


def get_file_priority(path: str) -> Tuple[int, int, str]:
    basename = os.path.basename(path).lower()
    ext = os.path.splitext(path)[1].lower()

    priority = 10
    if basename in {"main.py", "app.py", "run.py", "train.py", "evaluate.py", "evaluation.py"}:
        priority = 0
    elif basename.startswith("config") or ext in {".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        priority = 1
    elif "docker" in basename or basename in {"reproduce.sh", "run.sh"}:
        priority = 2
    elif "eval" in basename or "metric" in basename:
        priority = 3
    elif "train" in basename or "test" in basename:
        priority = 4
    elif basename.startswith("readme") or ext == ".md":
        priority = 5
    elif ext in {".py", ".sh", ".bash"}:
        priority = 6

    return priority, len(path), path


def load_repo_context(args: argparse.Namespace) -> Tuple[str, Dict[str, object]]:
    all_files_dict = read_all_files(
        args.target_repo_dir,
        allowed_ext=ALLOWED_REPO_EXTENSIONS,
        is_print=False,
    )

    if len(all_files_dict) == 0:
        raise ValueError(
            f"No readable repository files were found in: {args.target_repo_dir}"
        )

    sorted_items = sorted(all_files_dict.items(), key=lambda item: get_file_priority(item[0]))

    parts: List[str] = []
    selected_paths: List[str] = []
    selected_chars = 0
    truncated_files = 0

    for relative_path, content in sorted_items:
        if len(selected_paths) >= args.max_repo_files:
            break

        file_content = content
        if len(file_content) > args.max_repo_file_chars:
            file_content = file_content[: args.max_repo_file_chars]
            file_content += "\n...[TRUNCATED FILE CONTENT]..."
            truncated_files += 1

        file_block = f"### File: {relative_path}\n```\n{file_content}\n```\n"
        if selected_chars + len(file_block) > args.max_repo_chars:
            break

        parts.append(file_block)
        selected_paths.append(relative_path)
        selected_chars += len(file_block)

    if len(parts) == 0:
        raise ValueError(
            "Repository context budget is too small and produced empty prompt context. "
            "Increase --max_repo_chars or --max_repo_file_chars."
        )

    repo_text = "\n".join(parts)
    meta = {
        "repo_dir": os.path.abspath(args.target_repo_dir),
        "candidate_files": len(all_files_dict),
        "selected_files": len(selected_paths),
        "selected_paths": selected_paths,
        "selected_chars": selected_chars,
        "truncated_files": truncated_files,
        "max_repo_files": args.max_repo_files,
        "max_repo_chars": args.max_repo_chars,
        "max_repo_file_chars": args.max_repo_file_chars,
    }
    return repo_text, meta


def build_messages(
    plan_text: str,
    plan_meta: Dict[str, object],
    repo_text: str,
    repo_meta: Dict[str, object],
) -> List[Dict[str, str]]:
    system_prompt = """You are an expert ML reproducibility engineer.

You will receive:
1) an existing experiment/evaluation plan, and
2) repository code context.

Your task is to produce a practical Docker-based reproduction plan that covers:
1) create a docker environment
2) mount the code folder
3) download dataset
4) reproduce the experiment

Rules:
- Use only provided context.
- Be concrete with commands and expected outputs.
- If a required detail is missing, explicitly add it to unknowns and assumptions.
- Do not invent dataset URLs or secret credentials.
- Return only valid JSON matching the required schema.
- Do not output chain-of-thought.
"""

    user_prompt = f"""<task>
Based on the plan and the repository, create a Docker-first reproduction plan.
</task>

<success_criteria>
- Docker environment instructions include Dockerfile snippet, build command, run command, and mount guidance.
- Dataset section includes download commands and integrity checks.
- Reproduction section includes ordered steps and exact commands where possible.
- Verification section maps expected metrics/results to concrete checks.
- Unknowns and assumptions capture missing information clearly.
</success_criteria>

<plan_meta>
{json.dumps(plan_meta, ensure_ascii=False)}
</plan_meta>

<repo_meta>
{json.dumps(repo_meta, ensure_ascii=False)}
</repo_meta>

<plan_context>
{plan_text}
</plan_context>

<repo_context>
{repo_text}
</repo_context>
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_request_json(
    model_name: str, messages: List[Dict[str, str]]
) -> Dict[str, object]:
    request_json: Dict[str, object] = {
        "model": model_name,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "docker_reproduction_plan",
                "schema": DOCKER_REPRO_SCHEMA,
                "strict": True,
            },
        },
    }

    if "o3" in model_name or "o4" in model_name:
        request_json["reasoning_effort"] = "high"
    else:
        request_json["temperature"] = 0

    return request_json


def main(args: argparse.Namespace) -> None:
    client = create_openai_client()

    plan_text, plan_meta = load_plan_context(args)
    repo_text, repo_meta = load_repo_context(args)
    messages = build_messages(plan_text, plan_meta, repo_text, repo_meta)

    token_count = -1
    try:
        token_count = num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")

    request_json = build_request_json(args.gpt_version, messages)
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())

    output_content = completion_json["choices"][0]["message"]["content"]
    docker_plan = extract_json_from_content(output_content)

    output_payload: Dict[str, object] = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "plan_meta": plan_meta,
        "repo_meta": repo_meta,
        "prompt_token_estimate": token_count,
        "docker_reproduction_plan": docker_plan,
    }

    if args.save_raw_completion:
        output_payload["raw_completion"] = completion_json
    else:
        output_payload["raw_model_output"] = output_content

    os.makedirs(args.eval_result_dir, exist_ok=True)
    if len(args.output_path.strip()) > 0:
        save_path = args.output_path
    else:
        now_str = get_now_str()
        save_path = os.path.join(
            args.eval_result_dir,
            f"{args.paper_name}_docker_repro_plan_{args.gpt_version}_{now_str}.json",
        )

    save_parent = os.path.dirname(os.path.abspath(save_path))
    if len(save_parent) > 0:
        os.makedirs(save_parent, exist_ok=True)

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print("Docker Reproduction Plan Summary")
    print(f"Paper name: {args.paper_name}")
    print(f"Model: {args.gpt_version}")
    print(f"Prompt token estimate: {token_count}")
    print(f"Saved to: {save_path}")
    print("=" * 50)

    try:
        print_log_cost(
            completion_json,
            args.gpt_version,
            f"[DockerReproPlan] {args.paper_name}",
            args.eval_result_dir,
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
