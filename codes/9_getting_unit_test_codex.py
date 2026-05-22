# 9_getting_unit_test.py — Generate pytest tests for a Paper2Code paper.
#
# Reads the paper2code rubric JSON (output of script 8) and the planned repo
# structure (output of script 1), then produces two tiers of pytest files for
# the "Code running agent" loop:
#   - intermediate-value tests: verify key paper computations (shapes,
#     normalisation invariants, paper-reported intermediate numbers).
#   - final-comparison tests:  verify our implementation outperforms a rival
#     baseline by a stated margin on a downloaded benchmark.
#
# Every rubric leaf gets exactly one test — no leaves are dropped.
# Each generated test function includes a comment block that links it back to
# its rubric entry (ID, weight, requirement text) so the correspondence is
# immediately visible.
#
# Outputs go to <output_dir>/tests/{intermediate,comparison}/ along with
# aggregate and per-stage manifests/spec files. A shared conftest.py hook writes
# per-test scores to stage-specific score files so the running agent has a
# structured signal for each stage.
#
# Example:
# python3.10 codes/9_getting_unit_test.py \
#   --rubric_json_path    outputs/paperbench_rubrics/adaptive-pruning_paper2code_rubric.json \
#   --paper_name          adaptive-pruning \
#   --repo_plan_path      outputs/paperbench_planning/adaptive-pruning/planning_response.json \
#   --generated_repo_path outputs/paperbench_repos/adaptive-pruning_repo \
#   --output_dir          outputs/adaptive-pruning_tests \
#   --gpt_version         gpt-5.4
#
# Dry-run (Pass 1 only — write specs only, skip pytest synthesis):
# python3.10 codes/9_getting_unit_test.py \
#   --rubric_json_path    ... \
#   --paper_name          adaptive-pruning \
#   --repo_plan_path      ... \
#   --generated_repo_path ... \
#   --output_dir          outputs/adaptive-pruning_tests \
#   --dry_run

import argparse
import ast
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai_client import create_openai_client
from utils import get_now_str, num_tokens_from_messages, print_log_cost
from _unit_test_utils import (
    build_repo_api_manifest as shared_build_repo_api_manifest,
    extract_api_from_doxygen as shared_extract_api_from_doxygen,
    extract_repo_symbols as shared_extract_repo_symbols,
    sanitize_generated_functions as shared_sanitize_generated_functions,
    validate_and_resolve_specs as shared_validate_and_resolve_specs,
    write_repo_api_manifest as shared_write_repo_api_manifest,
)


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PASS1_PROMPT_PATH = PROMPTS_DIR / "c8.1_getting_unit_test_pass1_prompt.txt"
PASS2_PROMPT_PATH = PROMPTS_DIR / "c8.1_getting_unit_test_pass2_prompt.txt"

PASS2_BATCH_SIZE = 5
PAPER_TEXT_CHAR_BUDGET = 60000
COLLECT_ONLY_TIMEOUT_SECONDS = 120

STAGE_INTERMEDIATE = "intermediate"
STAGE_COMPARISON = "comparison"
STAGE_NAMES = (STAGE_INTERMEDIATE, STAGE_COMPARISON)
STAGE_TEST_DIRNAMES = {
    STAGE_INTERMEDIATE: "intermediate",
    STAGE_COMPARISON: "comparison",
}
STAGE_SCORE_FILENAMES = {
    STAGE_INTERMEDIATE: "intermediate_scores.json",
    STAGE_COMPARISON: "comparison_scores.json",
}
STAGE_SPEC_FILENAMES = {
    STAGE_INTERMEDIATE: "test_specs_intermediate.json",
    STAGE_COMPARISON: "test_specs_comparison.json",
}
STAGE_MANIFEST_FILENAMES = {
    STAGE_INTERMEDIATE: "test_manifest_intermediate.json",
    STAGE_COMPARISON: "test_manifest_comparison.json",
}
STAGE_RUNNER_FILENAMES = {
    STAGE_INTERMEDIATE: "run_intermediate_tests.sh",
    STAGE_COMPARISON: "run_comparison_tests.sh",
}
ASSETS_ROOT_MODULE = "assets.rivals"
PLACEHOLDER_SKIP_REASONS = {
    "no_symbol",
    "invalid_api",
    "invalid_stage_payload",
    "invalid_import",
    "missing_generated_function",
}
REPO_IMPORT_ALLOWLIST_PREFIXES = ("assets",)

COMPARISON_KINDS = [
    "allclose_zero",
    "allclose_value",
    "shape_only",
    "sum_to_one",
    "monotonic",
    "constant_equals",
]


# --------------------------------------------------------------------------- #
# Argparse                                                                    #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate pytest test files (intermediate-value tier and "
            "final-comparison tier) from a paper2code rubric, the cleaned "
            "paper JSON, and the planned/generated repo's symbol map."
        )
    )
    parser.add_argument("--rubric_json_path", type=str, required=True)
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument("--repo_plan_path", type=str, required=True)
    parser.add_argument("--generated_repo_path", type=str, required=True)
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument(
        "--skip_audit",
        action="store_true",
        help="Skip the optional Pass 3 audit (AST parse + import resolution + paper-number check).",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Dump unprocessed LLM responses to <output_dir>/prompts/. Off by default.",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Stop after Pass 1 (write test_specs.json only; skip pytest synthesis).",
    )
    return parser.parse_args()


# --------------------------------------------------------------------------- #
# IO helpers                                                                  #
# --------------------------------------------------------------------------- #

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "branch"


# --------------------------------------------------------------------------- #
# Rubric flattening                                                           #
# --------------------------------------------------------------------------- #

def flatten_rubric_leaves(
    rubric_root: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return one entry per leaf with branch_slug derived from its top-level ancestor."""
    leaves: List[Dict[str, Any]] = []

    top_branches = rubric_root.get("sub_tasks") or []
    for branch in top_branches:
        branch_slug = slugify(branch.get("requirements", "")[:60])
        _walk_leaves(branch, branch_slug, [branch.get("requirements", "")], leaves)

    return leaves


def _walk_leaves(
    node: Dict[str, Any],
    branch_slug: str,
    ancestor_path: List[str],
    out: List[Dict[str, Any]],
) -> None:
    sub_tasks = node.get("sub_tasks") or []
    if not sub_tasks:
        out.append({
            "rubric_id": node.get("id", ""),
            "rubric_text": node.get("requirements", ""),
            "weight": int(node.get("weight", 1) or 1),
            "task_category": node.get("task_category"),
            "finegrained_task_category": node.get("finegrained_task_category"),
            "branch_slug": branch_slug,
            "ancestor_path": list(ancestor_path),
        })
        return
    for child in sub_tasks:
        _walk_leaves(
            child,
            branch_slug,
            ancestor_path + [child.get("requirements", "")],
            out,
        )


def cap_leaves_per_branch(
    leaves: List[Dict[str, Any]],
    max_per_branch: int,
) -> List[Dict[str, Any]]:
    """Keep the top-N leaves by weight (desc, ties stable) per branch_slug."""
    by_branch: Dict[str, List[Dict[str, Any]]] = {}
    for leaf in leaves:
        by_branch.setdefault(leaf["branch_slug"], []).append(leaf)

    kept: List[Dict[str, Any]] = []
    for slug, entries in by_branch.items():
        sorted_entries = sorted(
            enumerate(entries),
            key=lambda pair: (-pair[1]["weight"], pair[0]),
        )
        kept.extend(entry for _, entry in sorted_entries[:max_per_branch])
    return kept


# --------------------------------------------------------------------------- #
# Repo symbol extraction (AST)                                                #
# --------------------------------------------------------------------------- #

def extract_repo_symbols(repo_root: Path) -> Dict[str, List[str]]:
    """Walk the repo, AST-parse each .py, return {module.path: [TopLevelName, ...]}."""
    return shared_extract_repo_symbols(repo_root)


def extract_api_from_doxygen(repo_root: Path) -> Dict[str, Any]:
    """Return the shared AST-backed API manifest view used by Stage 9."""
    return shared_extract_api_from_doxygen(repo_root)


# --------------------------------------------------------------------------- #
# Paper text extraction                                                       #
# --------------------------------------------------------------------------- #

def extract_paper_text(paper_payload: Dict[str, Any], char_budget: int) -> str:
    """Concatenate body_text into one searchable string, truncated to char_budget."""
    chunks: List[str] = []
    title = paper_payload.get("title") or paper_payload.get("paper_title") or ""
    if title:
        chunks.append(f"# {title}")
    abstract = paper_payload.get("abstract") or ""
    if abstract:
        chunks.append(f"## Abstract\n{abstract}")

    body = (paper_payload.get("pdf_parse") or {}).get("body_text") or paper_payload.get("body_text") or []
    for entry in body:
        section = entry.get("section", "")
        sec_num = entry.get("sec_num", "")
        text = entry.get("text", "")
        if not text:
            continue
        header = f"## {sec_num} {section}".strip()
        chunks.append(f"{header}\n{text}")

    full = "\n\n".join(chunks)
    if len(full) > char_budget:
        full = full[:char_budget] + "\n\n[... truncated ...]"
    return full


def find_numbers_in_paper(paper_text: str) -> set[str]:
    """Return the set of numeric tokens that appear in the paper (string-matched)."""
    return set(re.findall(r"-?\d+\.?\d*(?:[eE]-?\d+)?", paper_text))


# --------------------------------------------------------------------------- #
# JSON schemas                                                                #
# --------------------------------------------------------------------------- #

INTERMEDIATE_SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "what_to_compute": {"type": "string"},
        "input_recipe": {"type": "string"},
        "expected_value": {"type": ["number", "null"]},
        "expected_shape": {
            "anyOf": [
                {"type": "array", "items": {"type": "integer"}},
                {"type": "null"},
            ]
        },
        "tolerance": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "atol": {"type": "number"},
                "rtol": {"type": "number"},
            },
            "required": ["atol", "rtol"],
        },
        "comparison_kind": {"type": "string", "enum": COMPARISON_KINDS},
    },
    "required": [
        "what_to_compute",
        "input_recipe",
        "expected_value",
        "expected_shape",
        "tolerance",
        "comparison_kind",
    ],
}

COMPARISON_SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "our_call": {"type": "string"},
        "rival_call": {"type": "string"},
        "metric": {"type": "string"},
        "comparison_op": {"type": "string", "enum": ["lt", "gt"]},
        "margin": {"type": "number"},
        "paper_table_ref": {"type": "string"},
    },
    "required": ["our_call", "rival_call", "metric", "comparison_op", "margin", "paper_table_ref"],
}

TEST_SPEC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "test_id": {"type": "string"},
        "rubric_id": {"type": "string"},
        "rubric_text": {"type": "string"},
        "weight": {"type": "integer", "minimum": 1},
        "tier": {"type": "string", "enum": ["intermediate", "comparison"]},
        "branch_slug": {"type": "string"},
        "paper_section_ref": {"type": "string"},
        "imports": {"type": "array", "items": {"type": "string"}},
        "api_refs": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "intermediate": {
            "anyOf": [INTERMEDIATE_SPEC_SCHEMA, {"type": "null"}],
        },
        "comparison": {
            "anyOf": [COMPARISON_SPEC_SCHEMA, {"type": "null"}],
        },
        "skip_reason": {"type": ["string", "null"]},
    },
    "required": [
        "test_id",
        "rubric_id",
        "rubric_text",
        "weight",
        "tier",
        "branch_slug",
        "paper_section_ref",
        "imports",
        "api_refs",
        "rationale",
        "intermediate",
        "comparison",
        "skip_reason",
    ],
}

PASS1_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paper_name": {"type": "string"},
        "tests": {"type": "array", "items": TEST_SPEC_SCHEMA},
    },
    "required": ["paper_name", "tests"],
}

PASS2_FUNCTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "test_id": {"type": "string"},
        "branch_slug": {"type": "string"},
        "tier": {"type": "string", "enum": ["intermediate", "comparison"]},
        "function_name": {"type": "string"},
        "extra_imports": {"type": "array", "items": {"type": "string"}},
        "test_code": {"type": "string"},
    },
    "required": ["test_id", "branch_slug", "tier", "function_name", "extra_imports", "test_code"],
}

PASS2_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "functions": {"type": "array", "items": PASS2_FUNCTION_SCHEMA},
    },
    "required": ["functions"],
}


# --------------------------------------------------------------------------- #
# LLM call plumbing                                                           #
# --------------------------------------------------------------------------- #

def extract_json_from_content(content: str) -> Any:
    text = content.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        return json.loads(text[left:right + 1])

    raise ValueError("Could not parse JSON from model output.")


def build_request_json(
    model_name: str,
    messages: List[Dict[str, str]],
    schema_name: str,
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    request: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": schema,
                "strict": True,
            },
        },
    }
    if "o3" in model_name or "o4" in model_name:
        request["reasoning_effort"] = "high"
    else:
        request["temperature"] = 0
    return request


def call_model(
    client: Any,
    request_json: Dict[str, Any],
) -> Tuple[Dict[str, Any], str, Any]:
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())
    raw_output = completion_json["choices"][0]["message"]["content"]
    parsed = extract_json_from_content(raw_output)
    return completion_json, raw_output, parsed


def estimate_tokens(messages: List[Dict[str, str]]) -> int:
    try:
        return num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")
        return -1


# --------------------------------------------------------------------------- #
# Pass 1 — Test-spec planning                                                 #
# --------------------------------------------------------------------------- #

def build_pass1_messages(
    pass1_prompt: str,
    paper_name: str,
    leaves: List[Dict[str, Any]],
    repo_symbols: Dict[str, Any],
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an expert at converting paper2code rubrics into structured pytest "
        "specifications. Return ONLY valid JSON matching the schema.\n"
        "IMPORTANT: You MUST generate exactly one test specification for EVERY rubric "
        "leaf provided in <rubric_leaves_json>. Do not skip any leaf. If a leaf has no "
        "traceable numeric value, use comparison_kind=shape_only and set "
        "skip_reason='qualitative'.\n"
        "Only reference class constructors, method names, and function argument names "
        "that are EXPLICITLY listed in the api_docs_json provided. Each entry includes "
        "'signature' (the exact def line), 'params' (kwarg names and types), and for "
        "classes: 'init_signature' and 'methods'. Never invent or guess API names. "
        "When in doubt, emit skip_reason='qualitative'. If no exact symbol exists "
        "in api_docs_json, set skip_reason='no_symbol' and leave imports empty; "
        "never provide best-guess imports.\n"
        "For each repo API used by a spec, emit an `api_refs` item in the exact "
        "`module.path:QualifiedName` form from api_docs_json. Treat `imports` as "
        "secondary; the static validator will rewrite imports from api_refs.\n"
        "NEVER add an import that points to a `tests.*` module — repo test files are not "
        "a reusable library. If a key in api_docs_json contains a dot "
        "(e.g. 'DistillationManager.TrTransform'), the symbol is a nested class: import "
        "the outer class only, never write a flat `from <module> import InnerClass`."
    )
    user_prompt = f"""{pass1_prompt}

<paper_name>
{paper_name}
</paper_name>

<api_docs_json>
{json.dumps(repo_symbols, indent=2, ensure_ascii=False)}
</api_docs_json>

<rubric_leaves_json>
{json.dumps(leaves, indent=2, ensure_ascii=False)}
</rubric_leaves_json>
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# --------------------------------------------------------------------------- #
# Pass 2 — Pytest synthesis                                                   #
# --------------------------------------------------------------------------- #

def build_pass2_messages(
    pass2_prompt: str,
    specs_batch: List[Dict[str, Any]],
    repo_symbols: Dict[str, Any],
    assets_root_module: str,
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an expert author of pytest function bodies. Return ONLY valid JSON "
        "matching the schema. Each test_code must start with 'def test_<name>():'."
        " Use ONLY constructors, method names, and kwarg names that appear in "
        "api_docs_json. Never invent API names. For unknown constructors use "
        "`assert ClassName is not None` as a minimal smoke check. For specs with "
        "skip_reason='no_symbol' or skip_reason='invalid_api', keep extra_imports empty "
        "and emit a pytest.skip placeholder.\n"
        "HARD CONSTRAINTS (these will be enforced by an automated audit; violations "
        "result in the test being replaced with a skip stub):\n"
        " - extra_imports MUST NOT contain any `from tests.* import ...` — repo test "
        "files are not a reusable library.\n"
        " - extra_imports MUST NOT do flat imports of nested classes. If api_docs_json "
        "lists a symbol under a dotted key like 'DistillationManager.TrTransform', "
        "import only the outer class (`from src.algorithms.distillation import "
        "DistillationManager`) and reference the inner class as "
        "`DistillationManager.TrTransform` in the body.\n"
        " - For every `from <module> import <name>` in extra_imports, BOTH the module "
        "and the name MUST appear in api_docs_json. If they don't, do not invent — emit "
        "`assert ClassName is not None` or `pytest.skip(...)`."
    )
    user_prompt = f"""{pass2_prompt}

<api_docs_json>
{json.dumps(repo_symbols, indent=2, ensure_ascii=False)}
</api_docs_json>

<assets_root_module>
{assets_root_module}
</assets_root_module>

<specs_json>
{json.dumps(specs_batch, indent=2, ensure_ascii=False)}
</specs_json>
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def run_pass2_in_batches(
    client: Any,
    pass2_prompt: str,
    specs: List[Dict[str, Any]],
    repo_symbols: Dict[str, List[str]],
    assets_root_module: str,
    model_name: str,
    output_dir: Path,
    save_raw_completion: bool,
    accumulated_cost: float,
    stage_name: str,
) -> Tuple[List[Dict[str, Any]], float, List[Dict[str, Any]]]:
    """Returns (functions, accumulated_cost, raw_completions)."""
    all_functions: List[Dict[str, Any]] = []
    raw_completions: List[Dict[str, Any]] = []

    if not specs:
        return all_functions, accumulated_cost, raw_completions

    for batch_idx in range(0, len(specs), PASS2_BATCH_SIZE):
        batch = specs[batch_idx : batch_idx + PASS2_BATCH_SIZE]
        messages = build_pass2_messages(pass2_prompt, batch, repo_symbols, assets_root_module)
        request = build_request_json(model_name, messages, "pytest_functions", PASS2_RESPONSE_SCHEMA)
        completion_json, raw_output, parsed = call_model(client, request)

        all_functions.extend(parsed.get("functions", []))
        raw_completions.append({
            "batch_index": batch_idx,
            "completion": completion_json if save_raw_completion else None,
            "raw_output": raw_output,
        })

        try:
            accumulated_cost = print_log_cost(
                completion_json,
                model_name,
                f"[c8.1 Pass2:{stage_name}] batch {batch_idx // PASS2_BATCH_SIZE + 1}",
                str(output_dir),
                accumulated_cost,
            )
        except Exception as exc:
            print(f"[WARNING] Pass 2 cost logging skipped (batch {batch_idx}): {exc}")

    return all_functions, accumulated_cost, raw_completions


# --------------------------------------------------------------------------- #
# Stage categorisation and generation safety                                  #
# --------------------------------------------------------------------------- #

def split_specs_by_stage(
    specs: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Normalise Pass-1 specs into separate intermediate and comparison lists."""
    by_stage: Dict[str, List[Dict[str, Any]]] = {
        STAGE_INTERMEDIATE: [],
        STAGE_COMPARISON: [],
    }

    for original in specs:
        spec = dict(original)
        tier = spec.get("tier")
        if tier not in STAGE_NAMES:
            tier = STAGE_INTERMEDIATE
            spec["tier"] = tier
            _mark_spec_for_placeholder(spec, "invalid_stage_payload")

        if tier == STAGE_INTERMEDIATE:
            spec["comparison"] = None
            if spec.get("intermediate") is None:
                _mark_spec_for_placeholder(spec, "invalid_stage_payload")
        else:
            spec["intermediate"] = None
            if spec.get("comparison") is None:
                _mark_spec_for_placeholder(spec, "invalid_stage_payload")

        if spec.get("skip_reason") == "no_symbol":
            spec["imports"] = []

        by_stage[tier].append(spec)

    return by_stage[STAGE_INTERMEDIATE], by_stage[STAGE_COMPARISON]


def run_pass2_stage(
    client: Any,
    pass2_prompt: str,
    specs: List[Dict[str, Any]],
    repo_symbols: Dict[str, Any],
    assets_root_module: str,
    model_name: str,
    output_dir: Path,
    save_raw_completion: bool,
    accumulated_cost: float,
    stage_name: str,
) -> Tuple[List[Dict[str, Any]], float, List[Dict[str, Any]]]:
    """Generate test functions for one stage while preserving skip placeholders."""
    placeholder_functions = [
        _placeholder_function(spec)
        for spec in specs
        if _is_placeholder_spec(spec)
    ]
    model_specs = [spec for spec in specs if not _is_placeholder_spec(spec)]

    generated_functions, accumulated_cost, raw_completions = run_pass2_in_batches(
        client=client,
        pass2_prompt=pass2_prompt,
        specs=model_specs,
        repo_symbols=repo_symbols,
        assets_root_module=assets_root_module,
        model_name=model_name,
        output_dir=output_dir,
        save_raw_completion=save_raw_completion,
        accumulated_cost=accumulated_cost,
        stage_name=stage_name,
    )

    return placeholder_functions + generated_functions, accumulated_cost, raw_completions


def validate_generated_imports(
    import_statements: List[str],
    repo_symbols: Dict[str, Any],
) -> List[str]:
    """Return validation errors for repo-local imports that cannot resolve."""
    errors: List[str] = []
    repo_modules = set(repo_symbols.keys())
    repo_roots = _repo_top_level_packages(repo_modules)

    for statement in import_statements:
        stripped = statement.strip()
        if not stripped or stripped == "import pytest":
            continue
        error = _validate_import_statement(stripped, repo_symbols, repo_modules, repo_roots)
        if error:
            errors.append(error)
    return errors


def sanitize_generated_functions(
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    repo_symbols: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Replace functions with invalid repo imports by safe pytest.skip placeholders."""
    return shared_sanitize_generated_functions(functions, specs_by_id, repo_symbols)


def filter_functions_for_specs(
    functions: List[Dict[str, Any]],
    specs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep only functions whose test_id belongs to the current stage."""
    allowed_ids = {spec.get("test_id") for spec in specs}
    return [fn for fn in functions if fn.get("test_id") in allowed_ids]


def ensure_functions_for_specs(
    functions: List[Dict[str, Any]],
    specs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Guarantee every spec has one emitted pytest function."""
    emitted_ids = {fn.get("test_id") for fn in functions}
    completed = list(functions)
    for spec in specs:
        if spec.get("test_id") not in emitted_ids:
            _mark_spec_for_placeholder(spec, "missing_generated_function")
            completed.append(_placeholder_function(spec))
    return completed


def write_spec_payloads(
    output_dir: Path,
    paper_name: str,
    model_name: str,
    rubric_json_path: str,
    generated_repo_path: str,
    repo_symbols: Dict[str, Any],
    prompt_token_estimate_pass1: int,
    stage_specs: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Write aggregate and per-stage test specification payloads."""
    all_specs = stage_specs[STAGE_INTERMEDIATE] + stage_specs[STAGE_COMPARISON]
    base_payload = {
        "paper_name": paper_name,
        "model": model_name,
        "rubric_json_path": os.path.abspath(rubric_json_path),
        "generated_repo_path": os.path.abspath(generated_repo_path),
        "repo_api_manifest_path": str(output_dir / "repo_api_manifest.json"),
        "repo_symbols": repo_symbols,
        "prompt_token_estimate_pass1": prompt_token_estimate_pass1,
        "stage_counts": {stage: len(specs) for stage, specs in stage_specs.items()},
    }

    aggregate_payload = dict(base_payload)
    aggregate_payload["tests"] = all_specs
    (output_dir / "test_specs.json").write_text(
        json.dumps(aggregate_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for stage_name, specs in stage_specs.items():
        stage_payload = dict(base_payload)
        stage_payload["stage"] = stage_name
        stage_payload["tests"] = specs
        (output_dir / STAGE_SPEC_FILENAMES[stage_name]).write_text(
            json.dumps(stage_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _mark_spec_for_placeholder(spec: Dict[str, Any], reason: str) -> None:
    spec["skip_reason"] = reason
    spec["imports"] = []


def _is_placeholder_spec(spec: Dict[str, Any]) -> bool:
    return spec.get("skip_reason") in PLACEHOLDER_SKIP_REASONS


def _placeholder_function(spec: Dict[str, Any], detail: Optional[str] = None) -> Dict[str, Any]:
    test_id = str(spec.get("test_id") or "unknown")
    tier = spec.get("tier") if spec.get("tier") in STAGE_NAMES else STAGE_INTERMEDIATE
    base = spec.get("rubric_id") or test_id
    function_name = f"test_{slugify(str(base))}_placeholder"
    reason = detail or spec.get("skip_reason") or "test generation placeholder"
    return {
        "test_id": test_id,
        "branch_slug": spec.get("branch_slug") or "misc",
        "tier": tier,
        "function_name": function_name,
        "extra_imports": [],
        "test_code": (
            f"def {function_name}():\n"
            f"    pytest.skip({reason!r})\n"
        ),
    }


def _validate_import_statement(
    statement: str,
    repo_symbols: Dict[str, Any],
    repo_modules: set[str],
    repo_roots: set[str],
) -> Optional[str]:
    from_match = re.match(r"^\s*from\s+([\w\.]+)\s+import\s+(.+)$", statement)
    if from_match:
        module = from_match.group(1)
        imported_symbols = _split_imported_symbols(from_match.group(2))
        return _validate_from_import(statement, module, imported_symbols, repo_symbols, repo_modules, repo_roots)

    import_match = re.match(r"^\s*import\s+(.+)$", statement)
    if import_match:
        for module in _split_imported_symbols(import_match.group(1)):
            error = _validate_plain_import(statement, module, repo_modules, repo_roots)
            if error:
                return error
    return None


def _validate_from_import(
    statement: str,
    module: str,
    imported_symbols: List[str],
    repo_symbols: Dict[str, Any],
    repo_modules: set[str],
    repo_roots: set[str],
) -> Optional[str]:
    if module.split(".")[0] in REPO_IMPORT_ALLOWLIST_PREFIXES:
        return None
    if not _is_repo_related_module(module, repo_modules, repo_roots):
        return None
    if module not in repo_modules:
        return f"unresolved repo module in import {statement!r}"
    if "*" in imported_symbols:
        return f"wildcard import is not allowed in {statement!r}"

    known_symbols = _symbols_for_module(repo_symbols, module)
    for symbol in imported_symbols:
        if symbol in known_symbols:
            continue
        if f"{module}.{symbol}" in repo_modules:
            continue
        return f"unresolved symbol {symbol!r} in import {statement!r}"
    return None


def _validate_plain_import(
    statement: str,
    module: str,
    repo_modules: set[str],
    repo_roots: set[str],
) -> Optional[str]:
    if module.split(".")[0] in REPO_IMPORT_ALLOWLIST_PREFIXES:
        return None
    if not _is_repo_related_module(module, repo_modules, repo_roots):
        return None
    if module in repo_modules:
        return None
    if any(known.startswith(module + ".") for known in repo_modules):
        return None
    return f"unresolved repo module in import {statement!r}"


def _split_imported_symbols(import_list: str) -> List[str]:
    cleaned = import_list.strip().strip("()")
    symbols: List[str] = []
    for part in cleaned.split(","):
        symbol = part.split("#", 1)[0].strip()
        if " as " in symbol:
            symbol = symbol.split(" as ", 1)[0].strip()
        if symbol:
            symbols.append(symbol)
    return symbols


def _is_repo_related_module(
    module: str,
    repo_modules: set[str],
    repo_roots: set[str],
) -> bool:
    root = module.split(".")[0]
    return root in repo_roots or module in repo_modules


def _repo_top_level_packages(repo_modules: set[str]) -> set[str]:
    return {module.split(".")[0] for module in repo_modules if module}


def _symbols_for_module(repo_symbols: Dict[str, Any], module: str) -> set[str]:
    entry = repo_symbols.get(module)
    if isinstance(entry, list):
        return set(entry)
    if isinstance(entry, dict):
        symbols = {name for name in entry if name != "_names_only"}
        names_only = entry.get("_names_only")
        if isinstance(names_only, list):
            symbols.update(str(name) for name in names_only)
        return symbols
    return set()


# --------------------------------------------------------------------------- #
# File assembly                                                               #
# --------------------------------------------------------------------------- #

CONFTEST_TEMPLATE = '''import json
import os
import random
import sys
from pathlib import Path

import pytest

DEFAULT_REPO_PATH = {repo_path_literal}
DEFAULT_SCORE_ROOT = Path({score_root_literal})
DEFAULT_SCORE_FILENAMES = {score_filenames_literal}


def _resolve_repo_path() -> str:
    return os.environ.get("PAPER2CODE_REPO_PATH", DEFAULT_REPO_PATH)


def _resolve_score_path(tier: str) -> Path:
    env_path = os.environ.get("PAPER2CODE_SCORE_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_SCORE_ROOT / DEFAULT_SCORE_FILENAMES.get(tier, "scores.json")


repo_path = _resolve_repo_path()
if repo_path and repo_path not in sys.path:
    sys.path.insert(0, repo_path)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "rubric(test_id, rubric_id, weight, tier): Paper2Code rubric metadata.",
    )
    config.addinivalue_line(
        "markers",
        "slow: comparison-tier tests that run rivals live.",
    )


@pytest.fixture(autouse=True)
def _seed_rng():
    random.seed(0)
    try:
        import numpy as np
        np.random.seed(0)
    except ImportError:
        pass
    try:
        import torch
        torch.manual_seed(0)
    except ImportError:
        pass


def _read_rubric_marker(item):
    marker = item.get_closest_marker("rubric")
    if not marker:
        return {{}}
    return dict(marker.kwargs)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    rubric_meta = _read_rubric_marker(item)
    record = {{
        "test_id": rubric_meta.get("test_id", item.nodeid),
        "rubric_id": rubric_meta.get("rubric_id", ""),
        "weight": rubric_meta.get("weight", 1),
        "tier": rubric_meta.get("tier", "intermediate"),
        "nodeid": item.nodeid,
        "passed": report.passed,
        "xfailed": bool(getattr(report, "wasxfail", False)),
        "duration_s": report.duration,
        "longrepr": str(report.longrepr) if report.failed else None,
    }}

    score_path = _resolve_score_path(record["tier"])
    score_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if score_path.exists():
        try:
            existing = json.loads(score_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    existing.append(record)
    score_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
'''

PYTEST_INI_TEMPLATE = """[pytest]
markers =
    rubric(test_id, rubric_id, weight, tier): Paper2Code rubric metadata
    slow: comparison-tier tests that run rivals live
"""


def write_conftest(tests_dir: Path, repo_path: str, score_root: str) -> None:
    content = CONFTEST_TEMPLATE.format(
        repo_path_literal=repr(repo_path),
        score_root_literal=repr(score_root),
        score_filenames_literal=repr(STAGE_SCORE_FILENAMES),
    )
    (tests_dir / "conftest.py").write_text(content, encoding="utf-8")


def write_pytest_ini(tests_dir: Path) -> None:
    (tests_dir / "pytest.ini").write_text(PYTEST_INI_TEMPLATE, encoding="utf-8")


def assemble_test_files(
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    tests_dir: Path,
    manifest_prefix: str = "",
) -> List[Dict[str, Any]]:
    """Group functions by branch_slug (intermediate) or comparisons file. Returns manifest entries."""
    tests_dir.mkdir(parents=True, exist_ok=True)
    intermediate_by_branch: Dict[str, List[Dict[str, Any]]] = {}
    comparison_funcs: List[Dict[str, Any]] = []

    for fn in functions:
        spec = specs_by_id.get(fn["test_id"])
        if spec is None:
            continue
        if fn["tier"] == "comparison":
            comparison_funcs.append(fn)
        else:
            intermediate_by_branch.setdefault(fn["branch_slug"] or "misc", []).append(fn)

    manifest: List[Dict[str, Any]] = []

    for branch_slug, fns in intermediate_by_branch.items():
        file_path = tests_dir / f"test_{branch_slug}.py"
        _write_test_file(file_path, fns, specs_by_id, include_slow_marker=False)
        for fn in fns:
            manifest.append(_manifest_entry(fn, specs_by_id[fn["test_id"]], file_path.name, manifest_prefix))

    if comparison_funcs:
        file_path = tests_dir / "test_comparisons.py"
        _write_test_file(file_path, comparison_funcs, specs_by_id, include_slow_marker=True)
        for fn in comparison_funcs:
            manifest.append(_manifest_entry(fn, specs_by_id[fn["test_id"]], file_path.name, manifest_prefix))

    return manifest


def _manifest_entry(
    fn: Dict[str, Any],
    spec: Dict[str, Any],
    file_name: str,
    manifest_prefix: str,
) -> Dict[str, Any]:
    relative_file = f"{manifest_prefix}/{file_name}" if manifest_prefix else file_name
    return {
        "test_id": fn["test_id"],
        "rubric_id": spec.get("rubric_id", ""),
        "stage": fn["tier"],
        "tier": fn["tier"],
        "weight": spec.get("weight", 1),
        "branch_slug": fn.get("branch_slug", ""),
        "file": relative_file,
        "function": fn["function_name"],
        "skip_reason": spec.get("skip_reason"),
    }


def _write_test_file(
    file_path: Path,
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    include_slow_marker: bool,
) -> None:
    seen_imports: List[str] = []
    seen_set: set[str] = set()
    seen_imports.append("import pytest")
    seen_set.add("import pytest")
    for fn in functions:
        for imp in fn.get("extra_imports", []):
            if imp not in seen_set:
                seen_imports.append(imp)
                seen_set.add(imp)

    body_blocks: List[str] = []
    for fn in functions:
        spec = specs_by_id[fn["test_id"]]
        decorators: List[str] = []
        if include_slow_marker:
            decorators.append("@pytest.mark.slow")
        if spec.get("skip_reason") == "qualitative":
            decorators.append('@pytest.mark.xfail(strict=False, reason="qualitative requirement; smoke test")')
        decorators.append(
            '@pytest.mark.rubric(test_id={tid!r}, rubric_id={rid!r}, weight={w}, tier={tier!r})'.format(
                tid=fn["test_id"],
                rid=spec.get("rubric_id", ""),
                w=spec.get("weight", 1),
                tier=fn["tier"],
            )
        )
        # Build a rubric-link comment block so the correspondence between each
        # test function and its rubric entry is immediately visible in the file.
        rubric_id = spec.get("rubric_id", "")
        rubric_text = (spec.get("rubric_text", "") or "")[:160]
        tier_label = spec.get("tier", "")
        weight = spec.get("weight", 1)
        rubric_comment = (
            f"# -- Rubric link {'-' * 52}\n"
            f"# ID     : {rubric_id}\n"
            f"# Weight : {weight}  |  Tier: {tier_label}\n"
            f"# Req    : {rubric_text}\n"
            f"# {'-' * 64}\n"
        )
        body_blocks.append(
            rubric_comment
            + "\n".join(decorators)
            + "\n"
            + fn["test_code"].rstrip()
            + "\n"
        )

    file_text = (
        '"""Auto-generated by c8.1_getting_unit_test.py; do not edit by hand."""\n'
        + "\n".join(seen_imports)
        + "\n\n\n"
        + "\n\n".join(body_blocks)
    )
    file_path.write_text(file_text, encoding="utf-8")


def write_stage_runner(
    output_dir: Path,
    stage_name: str,
    tests_dir: Path,
    repo_path: str,
    score_path: Path,
) -> None:
    """Write a shell runner for one generated pytest stage."""
    runner_path = output_dir / STAGE_RUNNER_FILENAMES[stage_name]
    title = f"{stage_name} Paper2Code tests"
    content = f'''#!/usr/bin/env bash
# {runner_path.name} - Run {title}.
#
# Usage:
#   ./{runner_path.name}
#   ./{runner_path.name} -k "T-0001"
#   ./{runner_path.name} -v
#
# Extra arguments are forwarded directly to pytest.

set -euo pipefail

REPO_DIR={shlex.quote(repo_path)}
TESTS_DIR={shlex.quote(str(tests_dir))}
SCORES_PATH={shlex.quote(str(score_path))}

mkdir -p "$(dirname "$SCORES_PATH")"

export PAPER2CODE_REPO_PATH="$REPO_DIR"
export PAPER2CODE_SCORE_PATH="$SCORES_PATH"

echo "=== Running {title} ==="
echo "  Repo  : $PAPER2CODE_REPO_PATH"
echo "  Tests : $TESTS_DIR"
echo "  Scores: $PAPER2CODE_SCORE_PATH"
echo ""

if ! command -v pytest &>/dev/null; then
    echo "ERROR: pytest not found. Install it with: pip install pytest" >&2
    exit 1
fi

if [ -z "$(find "$TESTS_DIR" -name 'test_*.py' -print -quit)" ]; then
    echo "No {stage_name} tests were generated."
    exit 0
fi

EXIT_CODE=0
pytest "$TESTS_DIR" \\
    --tb=short \\
    -p no:cacheprovider \\
    "$@" || EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "All {stage_name} tests passed."
else
    echo ""
    echo "Some {stage_name} tests failed (exit code $EXIT_CODE)."
fi

exit $EXIT_CODE
'''
    runner_path.write_text(content, encoding="utf-8")
    os.chmod(runner_path, 0o755)


# --------------------------------------------------------------------------- #
# Pass 3 — Audit (optional)                                                   #
# --------------------------------------------------------------------------- #

def audit_emitted_files(
    tests_dir: Path,
    specs: List[Dict[str, Any]],
    repo_symbols: Dict[str, List[str]],
    paper_numbers: set[str],
) -> Dict[str, Any]:
    issues: List[Dict[str, str]] = []

    for py_path in sorted(tests_dir.rglob("test_*.py")):
        try:
            file_text = py_path.read_text(encoding="utf-8")
            ast.parse(file_text)
        except SyntaxError as exc:
            issues.append({
                "file": str(py_path.relative_to(tests_dir)),
                "kind": "syntax_error",
                "detail": f"{exc.msg} at line {exc.lineno}",
            })
            continue

        emitted_imports = [
            line
            for line in file_text.splitlines()
            if line.startswith("import ") or line.startswith("from ")
        ]
        for detail in validate_generated_imports(emitted_imports, repo_symbols):
            issues.append({
                "file": str(py_path.relative_to(tests_dir)),
                "kind": "unresolved_import",
                "detail": detail,
            })

    known_module_prefixes = set(repo_symbols.keys())
    known_module_prefixes.add("assets")

    for spec in specs:
        for imp in spec.get("imports", []) or []:
            target_module = _import_target_module(imp)
            if target_module and not _module_matches_known(target_module, known_module_prefixes):
                issues.append({
                    "file": "test_specs.json",
                    "kind": "unresolved_import",
                    "detail": f"test_id={spec.get('test_id')} import={imp!r}",
                })

        intermediate = spec.get("intermediate")
        if intermediate and intermediate.get("expected_value") is not None:
            value_str = _strip_trailing_zero(intermediate["expected_value"])
            if value_str not in paper_numbers and "0" != value_str:
                issues.append({
                    "file": "test_specs.json",
                    "kind": "expected_value_not_in_paper",
                    "detail": f"test_id={spec.get('test_id')} value={value_str}",
                })

    return {
        "passes": len(issues) == 0,
        "issues": issues,
        "checked_files": [str(p.relative_to(tests_dir)) for p in sorted(tests_dir.rglob("test_*.py"))],
    }


def _import_target_module(import_statement: str) -> Optional[str]:
    m = re.match(r"^\s*from\s+([\w\.]+)\s+import\s+", import_statement)
    if m:
        return m.group(1)
    m = re.match(r"^\s*import\s+([\w\.]+)", import_statement)
    if m:
        return m.group(1)
    return None


def _module_matches_known(module: str, known_prefixes: set[str]) -> bool:
    if module in known_prefixes:
        return True
    for prefix in known_prefixes:
        if module.startswith(prefix + "."):
            return True
        if prefix.startswith(module + "."):
            return True
    return False


def _strip_trailing_zero(value: float) -> str:
    text = repr(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------- #
# Self-checks                                                                 #
# --------------------------------------------------------------------------- #

def collect_only_check(tests_dir: Path) -> Tuple[bool, str]:
    """Run `pytest --collect-only` against the emitted tests directory."""
    import subprocess
    if not any(tests_dir.rglob("test_*.py")):
        return True, "no test files generated for this stage"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", str(tests_dir)],
            capture_output=True,
            text=True,
            timeout=COLLECT_ONLY_TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode == 0, (result.stdout + result.stderr)[-2000:]
    except FileNotFoundError:
        return False, "pytest not installed"
    except subprocess.TimeoutExpired:
        return False, "pytest --collect-only timed out"


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main(args: argparse.Namespace) -> None:
    """Generate pytest tests for a Paper2Code paper.

    Reads the cleaned paper JSON, the paper2code rubric JSON, and the planned
    repo structure, then produces two tiers of pytest files (intermediate-value
    and final-comparison) along with a manifest and Pass-1 specs.
    """
    output_dir = Path(args.output_dir).resolve()
    tests_dir = output_dir / "tests"
    stage_tests_dirs = {
        stage_name: tests_dir / dirname
        for stage_name, dirname in STAGE_TEST_DIRNAMES.items()
    }
    scores_dir = output_dir / "scores"
    stage_score_paths = {
        stage_name: scores_dir / filename
        for stage_name, filename in STAGE_SCORE_FILENAMES.items()
    }
    raw_prompts_dir = output_dir / "prompts"
    output_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    for stage_dir in stage_tests_dirs.values():
        stage_dir.mkdir(parents=True, exist_ok=True)
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / ".gitkeep").touch()
    if args.save_raw_completion:
        raw_prompts_dir.mkdir(parents=True, exist_ok=True)

    rubric_payload = load_json(args.rubric_json_path)
    rubric_root = rubric_payload.get("paper2code_rubric") or rubric_payload.get("reproduction_rubric") or rubric_payload
    repo_plan_payload = load_json(args.repo_plan_path)  # noqa: F841 — included for future use
    repo_root = Path(args.generated_repo_path).resolve()
    api_manifest = shared_build_repo_api_manifest(repo_root)
    repo_symbols = api_manifest["api_symbols"]
    flat_symbols = shared_extract_repo_symbols(repo_root)
    manifest_path = shared_write_repo_api_manifest(output_dir, api_manifest)

    leaves = flatten_rubric_leaves(rubric_root)
    # All rubric leaves are included — no cap applied.
    # (cap_leaves_per_branch is kept for reference but not called here.)

    pass1_prompt = load_prompt(PASS1_PROMPT_PATH)
    pass2_prompt = load_prompt(PASS2_PROMPT_PATH)

    client = create_openai_client()
    accumulated_cost = 0.0

    # ---------- Pass 1 ---------- #
    pass1_messages = build_pass1_messages(
        pass1_prompt=pass1_prompt,
        paper_name=args.paper_name,
        leaves=leaves,
        repo_symbols=repo_symbols,
    )
    pass1_token_count = estimate_tokens(pass1_messages)
    pass1_request = build_request_json(args.gpt_version, pass1_messages, "test_specs", PASS1_RESPONSE_SCHEMA)
    pass1_completion, _pass1_raw, pass1_parsed = call_model(client, pass1_request)

    raw_specs: List[Dict[str, Any]] = list(pass1_parsed.get("tests", []))
    raw_specs = shared_validate_and_resolve_specs(raw_specs, api_manifest)
    intermediate_specs, comparison_specs = split_specs_by_stage(raw_specs)
    stage_specs = {
        STAGE_INTERMEDIATE: intermediate_specs,
        STAGE_COMPARISON: comparison_specs,
    }
    specs: List[Dict[str, Any]] = intermediate_specs + comparison_specs
    write_spec_payloads(
        output_dir=output_dir,
        paper_name=args.paper_name,
        model_name=args.gpt_version,
        rubric_json_path=args.rubric_json_path,
        generated_repo_path=args.generated_repo_path,
        repo_symbols=repo_symbols,
        prompt_token_estimate_pass1=pass1_token_count,
        stage_specs=stage_specs,
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
        accumulated_cost = print_log_cost(
            pass1_completion,
            args.gpt_version,
            f"[c8.1 Pass1] {args.paper_name}",
            str(output_dir),
            accumulated_cost,
        )
    except Exception as exc:
        print(f"[WARNING] Pass 1 cost logging skipped: {exc}")

    # Warn about any rubric leaves that received no test spec
    leaf_ids = {leaf["rubric_id"] for leaf in leaves}
    spec_rubric_ids = {s["rubric_id"] for s in specs}
    uncovered = leaf_ids - spec_rubric_ids
    if uncovered:
        print(
            f"[WARNING] {len(uncovered)} rubric leaves have no test spec: "
            + ", ".join(sorted(uncovered)[:5])
            + ("..." if len(uncovered) > 5 else "")
        )

    if args.dry_run:
        print(f"[DRY RUN] Pass 1 specs written to {output_dir / 'test_specs.json'}.")
        print(f"  {len(specs)} specs planned across {len({s['branch_slug'] for s in specs})} branches.")
        print(f"  {len(intermediate_specs)} intermediate specs, {len(comparison_specs)} comparison specs.")
        print(f"  {len(leaves)} rubric leaves total, {len(uncovered)} uncovered.")
        return

    # ---------- Pass 2 ---------- #
    specs_by_id = {s["test_id"]: s for s in specs}
    functions_by_stage: Dict[str, List[Dict[str, Any]]] = {}
    raw_completions_by_stage: Dict[str, List[Dict[str, Any]]] = {}

    for stage_name in STAGE_NAMES:
        stage_functions, accumulated_cost, raw_completions = run_pass2_stage(
            client=client,
            pass2_prompt=pass2_prompt,
            specs=stage_specs[stage_name],
            repo_symbols=repo_symbols,
            assets_root_module=ASSETS_ROOT_MODULE,
            model_name=args.gpt_version,
            output_dir=output_dir,
            save_raw_completion=args.save_raw_completion,
            accumulated_cost=accumulated_cost,
            stage_name=stage_name,
        )
        stage_functions = filter_functions_for_specs(stage_functions, stage_specs[stage_name])
        stage_functions = sanitize_generated_functions(stage_functions, specs_by_id, api_manifest)
        stage_functions = ensure_functions_for_specs(stage_functions, stage_specs[stage_name])
        functions_by_stage[stage_name] = stage_functions
        raw_completions_by_stage[stage_name] = raw_completions

    functions = functions_by_stage[STAGE_INTERMEDIATE] + functions_by_stage[STAGE_COMPARISON]
    write_spec_payloads(
        output_dir=output_dir,
        paper_name=args.paper_name,
        model_name=args.gpt_version,
        rubric_json_path=args.rubric_json_path,
        generated_repo_path=args.generated_repo_path,
        repo_symbols=repo_symbols,
        prompt_token_estimate_pass1=pass1_token_count,
        stage_specs=stage_specs,
    )
    if args.save_raw_completion:
        (raw_prompts_dir / "pass2_raw.json").write_text(
            json.dumps(raw_completions_by_stage, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Assemble files ---------- #
    manifest_by_stage: Dict[str, List[Dict[str, Any]]] = {}
    for stage_name in STAGE_NAMES:
        manifest_by_stage[stage_name] = assemble_test_files(
            functions=functions_by_stage[stage_name],
            specs_by_id=specs_by_id,
            tests_dir=stage_tests_dirs[stage_name],
            manifest_prefix=STAGE_TEST_DIRNAMES[stage_name],
        )
    manifest = manifest_by_stage[STAGE_INTERMEDIATE] + manifest_by_stage[STAGE_COMPARISON]
    write_conftest(
        tests_dir=tests_dir,
        repo_path=str(Path(args.generated_repo_path).resolve()),
        score_root=str(scores_dir),
    )
    write_pytest_ini(tests_dir)
    for stage_name in STAGE_NAMES:
        write_stage_runner(
            output_dir=output_dir,
            stage_name=stage_name,
            tests_dir=stage_tests_dirs[stage_name],
            repo_path=str(Path(args.generated_repo_path).resolve()),
            score_path=stage_score_paths[stage_name],
        )

    manifest_payload = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "generated_at": get_now_str(),
        "rubric_json_path": os.path.abspath(args.rubric_json_path),
        "tests_dir": str(tests_dir),
        "stage_tests_dirs": {stage: str(path) for stage, path in stage_tests_dirs.items()},
        "score_paths": {stage: str(path) for stage, path in stage_score_paths.items()},
        "stage_counts": {stage: len(stage_specs[stage]) for stage in STAGE_NAMES},
        "tests": manifest,
    }
    (output_dir / "test_manifest.json").write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for stage_name in STAGE_NAMES:
        stage_manifest_payload = dict(manifest_payload)
        stage_manifest_payload["stage"] = stage_name
        stage_manifest_payload["tests_dir"] = str(stage_tests_dirs[stage_name])
        stage_manifest_payload["score_path"] = str(stage_score_paths[stage_name])
        stage_manifest_payload["tests"] = manifest_by_stage[stage_name]
        (output_dir / STAGE_MANIFEST_FILENAMES[stage_name]).write_text(
            json.dumps(stage_manifest_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Pass 3 audit (optional) ---------- #
    audit_payload: Optional[Dict[str, Any]] = None
    if not args.skip_audit:
        paper_numbers: set[str] = set()  # paper text not loaded; numeric cross-check skipped
        audit_payload = audit_emitted_files(
            tests_dir=tests_dir,
            specs=specs,
            repo_symbols=flat_symbols,
            paper_numbers=paper_numbers,
        )
        (output_dir / "audit.json").write_text(
            json.dumps(audit_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Self-checks ---------- #
    collect_results: Dict[str, Tuple[bool, str]] = {
        stage_name: collect_only_check(stage_tests_dirs[stage_name])
        for stage_name in STAGE_NAMES
    }
    collect_ok = all(ok for ok, _ in collect_results.values())

    print("=" * 60)
    print(f"c8.1_getting_unit_test summary — {args.paper_name}")
    print(f"  Pass 1 specs:     {len(specs)}")
    print(f"    intermediate:   {len(intermediate_specs)} specs, {len(functions_by_stage[STAGE_INTERMEDIATE])} functions")
    print(f"    comparison:     {len(comparison_specs)} specs, {len(functions_by_stage[STAGE_COMPARISON])} functions")
    print(f"  Pass 2 functions: {len(functions)}")
    print(f"  Manifest entries: {len(manifest)}")
    print(f"  Tests dir:        {tests_dir}")
    for stage_name in STAGE_NAMES:
        stage_ok, stage_log = collect_results[stage_name]
        print(f"  pytest --collect-only {stage_name}: {'OK' if stage_ok else 'FAILED'}")
        if not stage_ok:
            print(f"  {stage_name} collect-only output (tail):")
            for line in stage_log.splitlines()[-20:]:
                print(f"    {line}")
    print(f"  Overall collect-only: {'OK' if collect_ok else 'FAILED'}")
    if audit_payload is not None:
        print(f"  Audit: {'PASS' if audit_payload['passes'] else 'ISSUES'} ({len(audit_payload['issues'])} issues)")
    print(f"  Accumulated cost: ${accumulated_cost:.6f}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        main(cli_args)
    except Exception as error:
        print(f"[ERROR] {error}")
        sys.exit(1)
