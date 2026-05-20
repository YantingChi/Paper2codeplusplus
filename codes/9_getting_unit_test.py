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
# Outputs go to <output_dir>/tests/ along with test_manifest.json and
# test_specs.json. A conftest.py hook writes per-test scores to
# <output_dir>/scores/scores.json so the running agent has a structured signal.
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
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai_client import create_openai_client
from utils import get_now_str, num_tokens_from_messages, print_log_cost


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PASS1_PROMPT_PATH = PROMPTS_DIR / "c8.1_getting_unit_test_pass1_prompt.txt"
PASS2_PROMPT_PATH = PROMPTS_DIR / "c8.1_getting_unit_test_pass2_prompt.txt"

PASS2_BATCH_SIZE = 5
PAPER_TEXT_CHAR_BUDGET = 60000

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
    symbols: Dict[str, List[str]] = {}
    if not repo_root.exists():
        return symbols

    for py_path in repo_root.rglob("*.py"):
        if any(part.startswith(".") or part == "__pycache__" for part in py_path.parts):
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        names: List[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        names.append(target.id)

        if not names:
            continue

        rel = py_path.relative_to(repo_root).with_suffix("")
        module_path = ".".join(rel.parts)
        symbols[module_path] = names

    return symbols


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
    repo_symbols: Dict[str, List[str]],
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an expert at converting paper2code rubrics into structured pytest "
        "specifications. Return ONLY valid JSON matching the schema.\n"
        "IMPORTANT: You MUST generate exactly one test specification for EVERY rubric "
        "leaf provided in <rubric_leaves_json>. Do not skip any leaf. If a leaf has no "
        "traceable numeric value, use comparison_kind=shape_only and set "
        "skip_reason='qualitative'."
    )
    user_prompt = f"""{pass1_prompt}

<paper_name>
{paper_name}
</paper_name>

<repo_symbols_json>
{json.dumps(repo_symbols, indent=2, ensure_ascii=False)}
</repo_symbols_json>

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
    repo_symbols: Dict[str, List[str]],
    assets_root_module: str,
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an expert author of pytest function bodies. Return ONLY valid JSON "
        "matching the schema. Each test_code must start with 'def test_<name>():'."
    )
    user_prompt = f"""{pass2_prompt}

<repo_symbols_json>
{json.dumps(repo_symbols, indent=2, ensure_ascii=False)}
</repo_symbols_json>

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
) -> Tuple[List[Dict[str, Any]], float, List[Dict[str, Any]]]:
    """Returns (functions, accumulated_cost, raw_completions)."""
    all_functions: List[Dict[str, Any]] = []
    raw_completions: List[Dict[str, Any]] = []

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
                f"[c8.1 Pass2] batch {batch_idx // PASS2_BATCH_SIZE + 1}",
                str(output_dir),
                accumulated_cost,
            )
        except Exception as exc:
            print(f"[WARNING] Pass 2 cost logging skipped (batch {batch_idx}): {exc}")

    return all_functions, accumulated_cost, raw_completions


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
DEFAULT_SCORE_PATH = {score_path_literal}


def _resolve_repo_path() -> str:
    return os.environ.get("PAPER2CODE_REPO_PATH", DEFAULT_REPO_PATH)


def _resolve_score_path() -> Path:
    return Path(os.environ.get("PAPER2CODE_SCORE_PATH", DEFAULT_SCORE_PATH))


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

    score_path = _resolve_score_path()
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


def write_conftest(tests_dir: Path, repo_path: str, score_path: str) -> None:
    content = CONFTEST_TEMPLATE.format(
        repo_path_literal=repr(repo_path),
        score_path_literal=repr(score_path),
    )
    (tests_dir / "conftest.py").write_text(content, encoding="utf-8")


def write_pytest_ini(tests_dir: Path) -> None:
    (tests_dir / "pytest.ini").write_text(PYTEST_INI_TEMPLATE, encoding="utf-8")


def assemble_test_files(
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    tests_dir: Path,
) -> List[Dict[str, Any]]:
    """Group functions by branch_slug (intermediate) or comparisons file. Returns manifest entries."""
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
            manifest.append(_manifest_entry(fn, specs_by_id[fn["test_id"]], file_path.name))

    if comparison_funcs:
        file_path = tests_dir / "test_comparisons.py"
        _write_test_file(file_path, comparison_funcs, specs_by_id, include_slow_marker=True)
        for fn in comparison_funcs:
            manifest.append(_manifest_entry(fn, specs_by_id[fn["test_id"]], file_path.name))

    return manifest


def _manifest_entry(fn: Dict[str, Any], spec: Dict[str, Any], file_name: str) -> Dict[str, Any]:
    return {
        "test_id": fn["test_id"],
        "rubric_id": spec.get("rubric_id", ""),
        "tier": fn["tier"],
        "weight": spec.get("weight", 1),
        "branch_slug": fn.get("branch_slug", ""),
        "file": file_name,
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
            f"# ── Rubric link {'─' * 52}\n"
            f"# ID     : {rubric_id}\n"
            f"# Weight : {weight}  |  Tier: {tier_label}\n"
            f"# Req    : {rubric_text}\n"
            f"# {'─' * 64}\n"
        )
        body_blocks.append(
            rubric_comment
            + "\n".join(decorators)
            + "\n"
            + fn["test_code"].rstrip()
            + "\n"
        )

    file_text = (
        '"""Auto-generated by c8.1_getting_unit_test.py — do not edit by hand."""\n'
        + "\n".join(seen_imports)
        + "\n\n\n"
        + "\n\n".join(body_blocks)
    )
    file_path.write_text(file_text, encoding="utf-8")


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

    for py_path in sorted(tests_dir.glob("test_*.py")):
        try:
            ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            issues.append({
                "file": py_path.name,
                "kind": "syntax_error",
                "detail": f"{exc.msg} at line {exc.lineno}",
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
        "checked_files": [p.name for p in sorted(tests_dir.glob("test_*.py"))],
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
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", str(tests_dir)],
            capture_output=True,
            text=True,
            timeout=120,
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
    scores_dir = output_dir / "scores"
    raw_prompts_dir = output_dir / "prompts"
    output_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / ".gitkeep").touch()
    if args.save_raw_completion:
        raw_prompts_dir.mkdir(parents=True, exist_ok=True)

    rubric_payload = load_json(args.rubric_json_path)
    rubric_root = rubric_payload.get("paper2code_rubric") or rubric_payload.get("reproduction_rubric") or rubric_payload
    repo_plan_payload = load_json(args.repo_plan_path)  # noqa: F841 — included for future use
    repo_symbols = extract_repo_symbols(Path(args.generated_repo_path).resolve())

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

    specs: List[Dict[str, Any]] = list(pass1_parsed.get("tests", []))
    spec_payload = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "rubric_json_path": os.path.abspath(args.rubric_json_path),
        "generated_repo_path": os.path.abspath(args.generated_repo_path),
        "repo_symbols": repo_symbols,
        "prompt_token_estimate_pass1": pass1_token_count,
        "tests": specs,
    }
    (output_dir / "test_specs.json").write_text(
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
        print(f"  {len(leaves)} rubric leaves total, {len(uncovered)} uncovered.")
        return

    # ---------- Pass 2 ---------- #
    assets_root_module = "assets.rivals"
    functions, accumulated_cost, raw_completions = run_pass2_in_batches(
        client=client,
        pass2_prompt=pass2_prompt,
        specs=specs,
        repo_symbols=repo_symbols,
        assets_root_module=assets_root_module,
        model_name=args.gpt_version,
        output_dir=output_dir,
        save_raw_completion=args.save_raw_completion,
        accumulated_cost=accumulated_cost,
    )
    if args.save_raw_completion:
        (raw_prompts_dir / "pass2_raw.json").write_text(
            json.dumps(raw_completions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Assemble files ---------- #
    specs_by_id = {s["test_id"]: s for s in specs}
    manifest = assemble_test_files(functions, specs_by_id, tests_dir)
    write_conftest(
        tests_dir=tests_dir,
        repo_path=str(Path(args.generated_repo_path).resolve()),
        score_path=str(scores_dir / "scores.json"),
    )
    write_pytest_ini(tests_dir)

    manifest_payload = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "generated_at": get_now_str(),
        "rubric_json_path": os.path.abspath(args.rubric_json_path),
        "tests_dir": str(tests_dir),
        "scores_path": str(scores_dir / "scores.json"),
        "tests": manifest,
    }
    (output_dir / "test_manifest.json").write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ---------- Pass 3 audit (optional) ---------- #
    audit_payload: Optional[Dict[str, Any]] = None
    if not args.skip_audit:
        paper_numbers: set[str] = set()  # paper text not loaded; numeric cross-check skipped
        audit_payload = audit_emitted_files(
            tests_dir=tests_dir,
            specs=specs,
            repo_symbols=repo_symbols,
            paper_numbers=paper_numbers,
        )
        (output_dir / "audit.json").write_text(
            json.dumps(audit_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------- Self-checks ---------- #
    collect_ok, collect_log = collect_only_check(tests_dir)

    print("=" * 60)
    print(f"c8.1_getting_unit_test summary — {args.paper_name}")
    print(f"  Pass 1 specs:     {len(specs)}")
    print(f"  Pass 2 functions: {len(functions)}")
    print(f"  Manifest entries: {len(manifest)}")
    print(f"  Tests dir:        {tests_dir}")
    print(f"  pytest --collect-only: {'OK' if collect_ok else 'FAILED'}")
    if not collect_ok:
        print("  collect-only output (tail):")
        for line in collect_log.splitlines()[-20:]:
            print(f"    {line}")
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
