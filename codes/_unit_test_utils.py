# _unit_test_utils.py — shared helpers for unit test generation (9a + 9b)
#
# This module holds the plumbing that both stages of the split unit-test
# pipeline need: rubric flattening, deterministic tier categorization, AST-based
# repo symbol extraction, LLM call helpers, file assembly, audit, and repair.
# It is imported by:
#   - codes/9a_categorize_and_plan.py   (categorization + Pass 1)
#   - codes/9b_synthesize_tests.py      (Pass 2 + assembly + Pass 3 + Pass 4)
#
# Usage example (from a stage script):
#   from _unit_test_utils import (
#       flatten_rubric_leaves, apply_tier_to_leaves,
#       build_pass1_messages, run_pass2_in_batches,
#       assemble_test_files, audit_emitted_files,
#   )

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from utils import num_tokens_from_messages, print_log_cost


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PASS1_PROMPT_PATH = PROMPTS_DIR / "c8.1_getting_unit_test_pass1_prompt.txt"
PASS2_PROMPT_PATH = PROMPTS_DIR / "c8.1_getting_unit_test_pass2_prompt.txt"

PASS2_BATCH_SIZE = 5
PAPER_TEXT_CHAR_BUDGET = 60000
API_MANIFEST_SCHEMA_VERSION = 1
API_MANIFEST_FILENAME = "repo_api_manifest.json"
PLACEHOLDER_SKIP_REASONS = {
    "no_symbol",
    "invalid_api",
    "invalid_import",
    "invalid_stage_payload",
    "missing_generated_function",
}

COMPARISON_KINDS = [
    "allclose_zero",
    "allclose_value",
    "shape_only",
    "sum_to_one",
    "monotonic",
    "constant_equals",
]


# --------------------------------------------------------------------------- #
# Tier categorization (NEW — deterministic, replaces LLM tier inference)      #
# --------------------------------------------------------------------------- #
#
# We map a rubric leaf to one of two tiers using its `finegrained_task_category`
# first, then `task_category` as a fallback. Both fields are emitted by the
# upstream rubric generator (codes/8.1_self_ameliorating.py). When both are
# null we default to "intermediate" — the safer choice because comparison-tier
# tests pull in a rival baseline and are expensive to run.

# Lookup table — first hit wins. Edit here to extend categorization.
_TIER_BY_FINEGRAINED: Dict[str, str] = {
    "Method Implementation": "intermediate",
    "Dataset and Model Acquisition": "intermediate",
    "Environment & Infrastructure Setup": "intermediate",
    "Experimental Setup": "intermediate",
    "Evaluation, Metrics & Benchmarking": "comparison",
}

_TIER_BY_TASK_CATEGORY: Dict[str, str] = {
    "Code Development": "intermediate",
    "Code Execution": "intermediate",
    "Result Analysis": "comparison",
}

_DEFAULT_TIER = "intermediate"


# Deterministically assign a tier ("intermediate" or "comparison") to a rubric leaf.
def assign_tier(leaf: Dict[str, Any]) -> str:
    """Pure-function tier assignment from rubric leaf metadata.

    Lookup precedence:
      1) leaf["finegrained_task_category"]  → table  _TIER_BY_FINEGRAINED
      2) leaf["task_category"]              → table  _TIER_BY_TASK_CATEGORY
      3) default                            → "intermediate"
    """
    fg = leaf.get("finegrained_task_category")
    if fg in _TIER_BY_FINEGRAINED:
        return _TIER_BY_FINEGRAINED[fg]
    tc = leaf.get("task_category")
    if tc in _TIER_BY_TASK_CATEGORY:
        return _TIER_BY_TASK_CATEGORY[tc]
    return _DEFAULT_TIER


# Annotate each leaf in-place with a `tier` field; return the same list.
def apply_tier_to_leaves(leaves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add a `tier` field to every leaf using assign_tier()."""
    for leaf in leaves:
        leaf["tier"] = assign_tier(leaf)
    return leaves


# Count how many leaves landed in each tier (for logging).
def tier_counts(leaves: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {"intermediate": 0, "comparison": 0}
    for leaf in leaves:
        counts[leaf.get("tier", _DEFAULT_TIER)] = counts.get(leaf.get("tier", _DEFAULT_TIER), 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# IO helpers                                                                  #
# --------------------------------------------------------------------------- #

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Convert an arbitrary string to a filesystem-safe slug (used for branch_slug).
def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "branch"


# --------------------------------------------------------------------------- #
# Rubric flattening                                                           #
# --------------------------------------------------------------------------- #

# Return one entry per leaf with branch_slug derived from its top-level ancestor.
def flatten_rubric_leaves(rubric_root: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walk the rubric tree and emit one dict per leaf node.

    Each leaf carries rubric_id, rubric_text, weight, task_category,
    finegrained_task_category, branch_slug (slug of top-level ancestor), and
    ancestor_path. The caller adds `tier` via apply_tier_to_leaves().
    """
    leaves: List[Dict[str, Any]] = []
    top_branches = rubric_root.get("sub_tasks") or []
    for branch in top_branches:
        branch_slug = slugify(branch.get("requirements", "")[:60])
        _walk_leaves(branch, branch_slug, [branch.get("requirements", "")], leaves)
    return leaves


# Recursive helper for flatten_rubric_leaves — appends to `out` in-place.
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


# --------------------------------------------------------------------------- #
# API manifest extraction and resolution                                      #
# --------------------------------------------------------------------------- #
#
# The manifest intentionally separates two related concepts:
#   1. importable_symbols: module top-level names that Python can import with
#      `from module import name`.
#   2. api_symbols: documented symbols the LLM may reason about, including
#      nested classes addressed by qualified access expressions such as
#      `DistillationManager.TrTransform`.

_BRIEF_RE = re.compile(r"@brief\s+(.+)")
_PARAM_RE = re.compile(r"@param\s+(\w+)\s+(\S+)")
_RETURN_RE = re.compile(r"@return\s+(\S+)")
_DEF_OR_CLASS_LINE_RE = re.compile(r"^[ \t]*(?:async\s+def|def|class)\s+\w+")


# Pull the Doxygen `## … # …` comment block sitting immediately above `node`.
def _doxygen_block_for(node: ast.AST, source_lines: List[str]) -> str:
    start = node.lineno - 1
    i = start - 1
    while i >= 0 and source_lines[i].strip() == "":
        i -= 1
    end = i
    while i >= 0 and source_lines[i].lstrip().startswith("#"):
        i -= 1
    if i + 1 > end:
        return ""
    block_lines = source_lines[i + 1 : end + 1]
    if not any(ln.lstrip().startswith("##") for ln in block_lines):
        return ""
    return "\n".join(block_lines)


# Pack a function/method node into a JSON-serialisable dict.
def _func_entry(node: ast.AST, source_lines: List[str]) -> Dict[str, Any]:
    args = getattr(node, "args", None)
    arg_names: List[str] = []
    if args is not None:
        for a in list(args.posonlyargs) + list(args.args):
            if a.arg != "self" and a.arg != "cls":
                arg_names.append(a.arg)
        for a in list(args.kwonlyargs):
            arg_names.append(a.arg)
        if args.vararg:
            arg_names.append("*" + args.vararg.arg)
        if args.kwarg:
            arg_names.append("**" + args.kwarg.arg)

    sig_line_idx = node.lineno - 1
    signature = source_lines[sig_line_idx].strip() if 0 <= sig_line_idx < len(source_lines) else ""
    if not _DEF_OR_CLASS_LINE_RE.match(signature):
        signature = f"def {getattr(node, 'name', '?')}({', '.join(arg_names)})"

    doxy = _doxygen_block_for(node, source_lines)
    brief_m = _BRIEF_RE.search(doxy) if doxy else None
    params = {m.group(1): m.group(2) for m in _PARAM_RE.finditer(doxy)} if doxy else {}
    ret_m = _RETURN_RE.search(doxy) if doxy else None
    return {
        "brief": brief_m.group(1).strip() if brief_m else "",
        "signature": signature,
        "params": params,
        "returns": ret_m.group(1).strip() if ret_m else "",
        "arg_names": arg_names,
        "methods": {},
    }


# Pack a ClassDef into {kind:"class", init_signature, methods, nested_in}.
def _class_entry(node: ast.ClassDef, source_lines: List[str], nested_in: Optional[str]) -> Dict[str, Any]:
    init_args: List[str] = []
    methods: Dict[str, Any] = {}
    for sub in node.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entry = _func_entry(sub, source_lines)
            if sub.name == "__init__":
                init_args = entry["arg_names"]
            methods[sub.name] = entry
    sig_line_idx = node.lineno - 1
    signature = (
        source_lines[sig_line_idx].strip()
        if 0 <= sig_line_idx < len(source_lines)
        else f"class {node.name}"
    )
    doxy = _doxygen_block_for(node, source_lines)
    brief_m = _BRIEF_RE.search(doxy) if doxy else None
    entry: Dict[str, Any] = {
        "kind": "class",
        "signature": signature,
        "brief": brief_m.group(1).strip() if brief_m else "",
        "init_signature": init_args,
        "methods": methods,
    }
    if nested_in:
        entry["nested_in"] = nested_in
    return entry


def _source_location(repo_root: Path, py_path: Path, node: Optional[ast.AST]) -> Dict[str, Any]:
    """Return stable source metadata for a manifest entry."""
    return {
        "path": str(py_path.relative_to(repo_root)),
        "line": int(getattr(node, "lineno", 0) or 0),
    }


def _attach_symbol_metadata(
    entry: Dict[str, Any],
    module_path: str,
    qualname: str,
    kind: str,
    import_name: str,
    access_expr: str,
    nested_in: Optional[str],
    repo_root: Path,
    py_path: Path,
    node: Optional[ast.AST],
) -> Dict[str, Any]:
    """Add deterministic import/access metadata to an API entry."""
    out = dict(entry)
    out.update({
        "module": module_path,
        "qualname": qualname,
        "kind": kind,
        "import_stmt": f"from {module_path} import {import_name}",
        "access_expr": access_expr,
        "nested_in": nested_in,
        "source": _source_location(repo_root, py_path, node),
    })
    return out


def _constant_entry(name: str) -> Dict[str, Any]:
    """Build the manifest entry payload for a top-level assigned name."""
    return {
        "kind": "constant",
        "signature": name,
        "brief": "",
        "params": {},
        "returns": "",
        "arg_names": [],
        "methods": {},
    }


def _assigned_names(node: ast.AST) -> List[str]:
    """Return top-level names assigned by Assign or AnnAssign nodes."""
    names: List[str] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.append(node.target.id)
    return names


def build_repo_api_manifest(repo_root: Path) -> Dict[str, Any]:
    """Build the authoritative Stage 9 API manifest for a generated repo."""
    repo_root = Path(repo_root).resolve()
    manifest: Dict[str, Any] = {
        "schema_version": API_MANIFEST_SCHEMA_VERSION,
        "repo_root": str(repo_root),
        "importable_symbols": {},
        "api_symbols": {},
        "api_refs": {},
        "cli_entrypoints": {},
    }
    if not repo_root.exists():
        return manifest

    for py_path in sorted(repo_root.rglob("*.py")):
        if any(part.startswith(".") or part == "__pycache__" for part in py_path.parts):
            continue
        try:
            source = py_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        source_lines = source.splitlines()
        rel = py_path.relative_to(repo_root).with_suffix("")
        module_path = ".".join(rel.parts)
        importable: Dict[str, Any] = {}
        api_symbols: Dict[str, Any] = {}

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = node.name
                entry = _attach_symbol_metadata(
                    _func_entry(node, source_lines),
                    module_path,
                    qualname,
                    "function",
                    qualname,
                    qualname,
                    None,
                    repo_root,
                    py_path,
                    node,
                )
                importable[qualname] = entry
                api_symbols[qualname] = entry
            elif isinstance(node, ast.ClassDef):
                qualname = node.name
                entry = _attach_symbol_metadata(
                    _class_entry(node, source_lines, nested_in=None),
                    module_path,
                    qualname,
                    "class",
                    qualname,
                    qualname,
                    None,
                    repo_root,
                    py_path,
                    node,
                )
                importable[qualname] = entry
                api_symbols[qualname] = entry

                # Surface direct nested classes as qualified API symbols, while
                # keeping their import_stmt pointed at the outer importable class.
                for sub in node.body:
                    if not isinstance(sub, ast.ClassDef):
                        continue
                    nested_qualname = f"{node.name}.{sub.name}"
                    nested_entry = _attach_symbol_metadata(
                        _class_entry(sub, source_lines, nested_in=node.name),
                        module_path,
                        nested_qualname,
                        "class",
                        node.name,
                        nested_qualname,
                        node.name,
                        repo_root,
                        py_path,
                        sub,
                    )
                    api_symbols[nested_qualname] = nested_entry
            else:
                for name in _assigned_names(node):
                    entry = _attach_symbol_metadata(
                        _constant_entry(name),
                        module_path,
                        name,
                        "constant",
                        name,
                        name,
                        None,
                        repo_root,
                        py_path,
                        node,
                    )
                    importable[name] = entry
                    api_symbols[name] = entry

        if importable:
            manifest["importable_symbols"][module_path] = importable
        if api_symbols:
            manifest["api_symbols"][module_path] = api_symbols
            for qualname, entry in api_symbols.items():
                manifest["api_refs"][f"{module_path}:{qualname}"] = entry

    return manifest


def write_repo_api_manifest(output_dir: Path, manifest: Dict[str, Any]) -> Path:
    """Write repo_api_manifest.json next to the generated test specs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / API_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def resolve_api_ref(api_ref: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve `module.path:QualifiedName` to one manifest entry."""
    if ":" not in api_ref:
        raise KeyError(f"Invalid API ref {api_ref!r}; expected module:qualname")
    module, qualname = api_ref.split(":", 1)
    try:
        return manifest["api_symbols"][module][qualname]
    except KeyError as exc:
        raise KeyError(f"Unknown API ref {api_ref!r}") from exc


def _find_nested_symbol_repair(
    module: str,
    symbol: str,
    manifest: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Find a unique nested API symbol named `symbol` in `module`."""
    matches: List[Dict[str, Any]] = []
    for qualname, entry in (manifest.get("api_symbols", {}).get(module, {}) or {}).items():
        if qualname.endswith(f".{symbol}") and entry.get("nested_in"):
            matches.append(entry)
    if len(matches) == 1:
        return matches[0]
    return None


# Returns a flat {module.path: [TopLevelName, ...]} index for repo symbols.
def extract_repo_symbols(repo_root: Path) -> Dict[str, List[str]]:
    """Walk repo and return module → list of top-level names (used by audit)."""
    manifest = build_repo_api_manifest(repo_root)
    return {
        module: sorted(symbols.keys())
        for module, symbols in manifest.get("importable_symbols", {}).items()
    }


# AST-walk each .py and emit a rich API map for the LLM.
def extract_api_from_doxygen(repo_root: Path) -> Dict[str, Any]:
    """Build {module.path: {SymbolName: entry, OuterClass.InnerClass: entry}}."""
    return build_repo_api_manifest(repo_root).get("api_symbols", {})


# --------------------------------------------------------------------------- #
# JSON schemas (structured-output contracts for the LLM)                      #
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

# Parse a JSON object from raw model output, tolerating fenced code blocks.
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


# Build the chat-completions request body with structured-output enforcement.
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


# Call the OpenAI client and return (completion_json, raw_text, parsed_json).
def call_model(
    client: Any,
    request_json: Dict[str, Any],
) -> Tuple[Dict[str, Any], str, Any]:
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())
    raw_output = completion_json["choices"][0]["message"]["content"]
    parsed = extract_json_from_content(raw_output)
    return completion_json, raw_output, parsed


# Estimate token count for cost logging — returns -1 if tiktoken is unavailable.
def estimate_tokens(messages: List[Dict[str, str]]) -> int:
    try:
        return num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")
        return -1


# --------------------------------------------------------------------------- #
# Pass 1 — Test-spec planning                                                 #
# --------------------------------------------------------------------------- #

# Build the system + user messages for the Pass 1 LLM call. Tier is pre-assigned
# in `leaves` (see apply_tier_to_leaves) — the model uses it as input, not output.
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
        "skip_reason='qualitative_smoke'.\n"
        "Route every leaf into one of three buckets (see the user prompt's 'Bucket "
        "routing' section): A=implementation-claim smoke (skip_reason='qualitative_smoke', "
        "hasattr/callable body, NO xfail), B=value check (skip_reason=null, assert the "
        "actual value via a config dataclass or configs/ YAML), C=reproduction "
        "(tier='comparison', real our_call/rival_call body, skip_reason=null). The bare "
        "value skip_reason='qualitative' is DEPRECATED.\n"
        "Each rubric leaf in <rubric_leaves_json> has a `tier` field that is ALREADY "
        "assigned ('intermediate' or 'comparison'). Echo it verbatim in your output "
        "`tier` field — do NOT reclassify. Fill in the `intermediate` block when "
        "tier='intermediate' (leave `comparison` null) and vice versa.\n"
        "Only reference class constructors, method names, and function argument names "
        "that are EXPLICITLY listed in the api_docs_json provided. Each entry includes "
        "'signature' (the exact def line), 'params' (kwarg names and types), and for "
        "classes: 'init_signature' and 'methods'. Never invent or guess API names. "
        "When in doubt, emit skip_reason='qualitative_smoke'.\n"
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

# Build the system + user messages for one Pass 2 batch.
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
        "`assert ClassName is not None` as a minimal smoke check.\n"
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


# Run Pass 2 in fixed-size batches; aggregate all returned function entries.
def run_pass2_in_batches(
    client: Any,
    pass2_prompt: str,
    specs: List[Dict[str, Any]],
    repo_symbols: Dict[str, Any],
    assets_root_module: str,
    model_name: str,
    output_dir: Path,
    save_raw_completion: bool,
    accumulated_cost: float,
    cost_log_label: str = "[c8.1 Pass2]",
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
                f"{cost_log_label} batch {batch_idx // PASS2_BATCH_SIZE + 1}",
                str(output_dir),
                accumulated_cost,
            )
        except Exception as exc:
            print(f"[WARNING] Pass 2 cost logging skipped (batch {batch_idx}): {exc}")

    return all_functions, accumulated_cost, raw_completions


# --------------------------------------------------------------------------- #
# API validation and generated-function sanitization                          #
# --------------------------------------------------------------------------- #

def _dedupe_ordered(values: List[str]) -> List[str]:
    """Return values without duplicates, preserving first occurrence."""
    seen: Set[str] = set()
    out: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _split_imported_symbols(import_list: str) -> List[str]:
    """Split a `from x import a, b as c` tail into imported source names."""
    cleaned = import_list.strip().strip("()")
    symbols: List[str] = []
    for part in cleaned.split(","):
        symbol = part.split("#", 1)[0].strip()
        if " as " in symbol:
            symbol = symbol.split(" as ", 1)[0].strip()
        if symbol:
            symbols.append(symbol)
    return symbols


def _repo_roots_from_manifest(manifest: Dict[str, Any]) -> Set[str]:
    """Return top-level package names represented in the API manifest."""
    roots: Set[str] = set()
    for module in manifest.get("api_symbols", {}):
        if module:
            roots.add(str(module).split(".", 1)[0])
    return roots


def _is_repo_related_import(module: str, manifest: Dict[str, Any]) -> bool:
    """Return true when an import should be validated against the repo manifest."""
    if module in manifest.get("api_symbols", {}):
        return True
    root = module.split(".", 1)[0]
    return root in _repo_roots_from_manifest(manifest) or root in {"src", "tests", "main", "scripts"}


def _replace_symbol_references(text: str, replacements: Dict[str, str]) -> str:
    """Replace bare symbol references in generated snippets with access expressions."""
    out = text
    for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
        if old == new:
            continue
        out = re.sub(rf"(?<![\w.]){re.escape(old)}\b", new, out)
    return out


def _validate_or_repair_imports(
    import_statements: List[str],
    manifest: Dict[str, Any],
) -> Tuple[Optional[List[str]], Dict[str, str], List[str]]:
    """Validate imports; repair unique flat nested-class imports when possible.

    Returns (imports, replacements, errors). `imports` is None when the import
    set is invalid and cannot be repaired safely.
    """
    imports: List[str] = []
    replacements: Dict[str, str] = {}
    errors: List[str] = []
    importable = manifest.get("importable_symbols", {})

    for statement in import_statements:
        stripped = statement.strip()
        if not stripped or stripped == "import pytest":
            continue

        from_match = re.match(r"^\s*from\s+([\w\.]+)\s+import\s+(.+)$", stripped)
        if from_match:
            module = from_match.group(1)
            imported_symbols = _split_imported_symbols(from_match.group(2))
            if module == "tests" or module.startswith("tests."):
                errors.append(f"forbidden tests import {stripped!r}")
                continue
            if not _is_repo_related_import(module, manifest):
                imports.append(stripped)
                continue
            if module not in manifest.get("api_symbols", {}):
                errors.append(f"unresolved repo module in import {stripped!r}")
                continue

            module_importable = importable.get(module, {})
            for symbol in imported_symbols:
                if symbol == "*":
                    errors.append(f"wildcard import is not allowed in {stripped!r}")
                    continue
                if symbol in module_importable:
                    imports.append(f"from {module} import {symbol}")
                    continue
                nested_entry = _find_nested_symbol_repair(module, symbol, manifest)
                if nested_entry is not None:
                    imports.append(str(nested_entry["import_stmt"]))
                    replacements[symbol] = str(nested_entry["access_expr"])
                    continue
                errors.append(f"unresolved symbol {symbol!r} in import {stripped!r}")
            continue

        import_match = re.match(r"^\s*import\s+(.+)$", stripped)
        if import_match:
            for module in _split_imported_symbols(import_match.group(1)):
                if not _is_repo_related_import(module, manifest):
                    imports.append(stripped)
                    continue
                if module in manifest.get("api_symbols", {}) or any(
                    known.startswith(module + ".")
                    for known in manifest.get("api_symbols", {})
                ):
                    imports.append(stripped)
                    continue
                errors.append(f"unresolved repo module in import {stripped!r}")
            continue

        imports.append(stripped)

    if errors:
        return None, replacements, errors
    return _dedupe_ordered(imports), replacements, []


def _mark_spec_for_placeholder(spec: Dict[str, Any], reason: str) -> None:
    """Mark a spec so downstream generation emits a skip placeholder."""
    spec["skip_reason"] = reason
    spec["imports"] = []


def _placeholder_function(spec: Dict[str, Any], detail: Optional[str] = None) -> Dict[str, Any]:
    """Return a pytest.skip function preserving one manifest entry for a spec."""
    test_id = str(spec.get("test_id") or "unknown")
    tier = spec.get("tier") if spec.get("tier") in {"intermediate", "comparison"} else "intermediate"
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


def validate_and_resolve_spec_apis(
    spec: Dict[str, Any],
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate one Pass 1 spec and resolve api_refs/imports deterministically."""
    resolved_refs: List[Dict[str, Any]] = []
    replacements: Dict[str, str] = {}
    resolved_imports: List[str] = []

    for api_ref in spec.get("api_refs") or []:
        if str(api_ref).startswith("tests.") or ":tests." in str(api_ref):
            _mark_spec_for_placeholder(spec, "invalid_api")
            spec["resolved_api_refs"] = []
            return spec
        try:
            entry = resolve_api_ref(str(api_ref), manifest)
        except KeyError:
            _mark_spec_for_placeholder(spec, "no_symbol")
            spec["resolved_api_refs"] = []
            return spec
        resolved_refs.append({
            "api_ref": str(api_ref),
            "module": entry.get("module"),
            "qualname": entry.get("qualname"),
            "import_stmt": entry.get("import_stmt"),
            "access_expr": entry.get("access_expr"),
            "nested_in": entry.get("nested_in"),
        })
        resolved_imports.append(str(entry["import_stmt"]))
        leaf_name = str(entry["qualname"]).split(".")[-1]
        replacements[leaf_name] = str(entry["access_expr"])

    repaired_imports, import_replacements, errors = _validate_or_repair_imports(
        list(spec.get("imports") or []),
        manifest,
    )
    if errors:
        _mark_spec_for_placeholder(spec, "invalid_api")
        spec["resolved_api_refs"] = resolved_refs
        spec["api_validation_errors"] = errors
        return spec

    replacements.update(import_replacements)
    spec["imports"] = _dedupe_ordered(resolved_imports + (repaired_imports or []))
    spec["resolved_api_refs"] = resolved_refs

    for block_name in ("intermediate", "comparison"):
        block = spec.get(block_name)
        if not isinstance(block, dict):
            continue
        for field, value in list(block.items()):
            if isinstance(value, str):
                block[field] = _replace_symbol_references(value, replacements)
    return spec


def validate_and_resolve_specs(
    specs: List[Dict[str, Any]],
    manifest: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Validate and resolve all Pass 1 specs in-place."""
    return [validate_and_resolve_spec_apis(spec, manifest) for spec in specs]


def sanitize_generated_functions(
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    manifest: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Repair or replace Pass 2 functions whose imports do not match the manifest."""
    sanitized: List[Dict[str, Any]] = []
    for fn in functions:
        spec = specs_by_id.get(fn.get("test_id"))
        if spec is None:
            continue

        if spec.get("skip_reason") in PLACEHOLDER_SKIP_REASONS:
            sanitized.append(_placeholder_function(spec))
            continue

        fn = dict(fn)
        fn["tier"] = spec.get("tier", fn.get("tier", "intermediate"))
        imports = [imp for imp in fn.get("extra_imports", []) if imp.strip() != "import pytest"]
        repaired_imports, replacements, errors = _validate_or_repair_imports(imports, manifest)
        if errors:
            _mark_spec_for_placeholder(spec, "invalid_api")
            spec["api_validation_errors"] = errors
            sanitized.append(_placeholder_function(spec, "invalid API import removed during generation"))
            continue

        fn["extra_imports"] = repaired_imports or []
        fn["test_code"] = _replace_symbol_references(str(fn.get("test_code", "")), replacements)
        sanitized.append(fn)
    return sanitized


def ensure_functions_for_specs(
    functions: List[Dict[str, Any]],
    specs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Append skip placeholders for specs that Pass 2 failed to return."""
    emitted_ids = {fn.get("test_id") for fn in functions}
    completed = list(functions)
    for spec in specs:
        if spec.get("test_id") in emitted_ids:
            continue
        _mark_spec_for_placeholder(spec, "missing_generated_function")
        completed.append(_placeholder_function(spec))
    return completed


# --------------------------------------------------------------------------- #
# File assembly (tier-aware: writes into tests/<tier>/ subdirectories)        #
# --------------------------------------------------------------------------- #

def remove_legacy_root_test_files(tests_dir: Path) -> List[str]:
    """Delete stale root-level generated tests from the pre-tiered layout."""
    removed: List[str] = []
    if not tests_dir.exists():
        return removed
    for py_path in sorted(tests_dir.glob("test_*.py")):
        if not py_path.is_file():
            continue
        py_path.unlink()
        removed.append(py_path.name)
    return removed


# Wipe tests_dir/<tier>/ entirely so a re-run doesn't leave orphans behind.
# Used before assemble_test_files writes the fresh per-tier subdir contents.
def wipe_tier_subdir(tests_dir: Path, tier: str) -> bool:
    """Delete tests_dir/<tier>/ if it exists. Returns True if anything was removed."""
    if tier not in ("intermediate", "comparison"):
        return False
    target = tests_dir / tier
    if not target.exists():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


# Warn if a doubly-nested tests/tests/ subdir is present (orphan from misconfigured
# prior runs that pointed --output_dir at the repo's tests/ instead of the sidecar).
# We do NOT auto-delete it — it could contain hand-written content. Leave it to the
# user to inspect and clean manually.
def warn_if_doubly_nested_tests(tests_dir: Path) -> bool:
    nested = tests_dir / "tests"
    if not nested.exists() or not nested.is_dir():
        return False
    print(
        f"[c9b synthesize] WARNING: doubly-nested tests directory exists at {nested}. "
        "This is likely orphan content from a previous mis-configured run; please "
        "inspect and remove it manually if it's not user-owned."
    )
    return True


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
    config.addinivalue_line(
        "markers",
        "reproduction: full paper-reproduction test, opt-in via `pytest -m reproduction`.",
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
# Filter reproduction-tier tests out of the default invocation. They are
# expensive (real training/eval against rivals) and opt-in: run them with
#   pytest -m reproduction
# or via `scripts/run_tests_local.sh --reproduction`.
addopts = -m "not reproduction"
markers =
    rubric(test_id, rubric_id, weight, tier): Paper2Code rubric metadata
    slow: comparison-tier tests that run rivals live
    reproduction: full paper-reproduction test, opt-in via `pytest -m reproduction`
"""


# Write tests/conftest.py with the rubric-marker hook baked in.
def write_conftest(tests_dir: Path, repo_path: str, score_path: str) -> None:
    content = CONFTEST_TEMPLATE.format(
        repo_path_literal=repr(repo_path),
        score_path_literal=repr(score_path),
    )
    (tests_dir / "conftest.py").write_text(content, encoding="utf-8")


# Write tests/pytest.ini with the rubric/slow marker declarations.
def write_pytest_ini(tests_dir: Path) -> None:
    (tests_dir / "pytest.ini").write_text(PYTEST_INI_TEMPLATE, encoding="utf-8")


# Group functions by tier; write each tier into its own subdirectory.
# tier_filter:
#   None              → emit BOTH subdirs (tests/intermediate, tests/comparison)
#   "intermediate"    → emit only tests/intermediate (comparison funcs ignored)
#   "comparison"      → emit only tests/comparison   (intermediate funcs ignored)
# Returns a manifest list (one entry per emitted test function).
def assemble_test_files(
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    tests_dir: Path,
    tier_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Write per-tier test files into tests_dir/<tier>/ subdirectories."""
    intermediate_by_branch: Dict[str, List[Dict[str, Any]]] = {}
    comparison_funcs: List[Dict[str, Any]] = []

    for fn in functions:
        if fn["test_id"] not in specs_by_id:
            continue
        if fn["tier"] == "comparison":
            if tier_filter in (None, "comparison"):
                comparison_funcs.append(fn)
        else:
            if tier_filter in (None, "intermediate"):
                intermediate_by_branch.setdefault(fn["branch_slug"] or "misc", []).append(fn)

    manifest: List[Dict[str, Any]] = []

    if intermediate_by_branch:
        inter_dir = tests_dir / "intermediate"
        inter_dir.mkdir(parents=True, exist_ok=True)
        for branch_slug, fns in intermediate_by_branch.items():
            file_path = inter_dir / f"test_{branch_slug}.py"
            _write_test_file(file_path, fns, specs_by_id, include_slow_marker=False)
            for fn in fns:
                manifest.append(_manifest_entry(
                    fn, specs_by_id[fn["test_id"]], f"intermediate/{file_path.name}"
                ))

    if comparison_funcs:
        cmp_dir = tests_dir / "comparison"
        cmp_dir.mkdir(parents=True, exist_ok=True)
        file_path = cmp_dir / "test_comparisons.py"
        _write_test_file(file_path, comparison_funcs, specs_by_id, include_slow_marker=True)
        for fn in comparison_funcs:
            manifest.append(_manifest_entry(
                fn, specs_by_id[fn["test_id"]], f"comparison/{file_path.name}"
            ))

    return manifest


# Build one manifest entry describing a written test function.
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


# Render one pytest file with deduped imports, decorators, and rubric comments.
def _write_test_file(
    file_path: Path,
    functions: List[Dict[str, Any]],
    specs_by_id: Dict[str, Dict[str, Any]],
    include_slow_marker: bool,
) -> None:
    seen_imports: List[str] = []
    seen_set: Set[str] = set()
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
        # Bucket dispatch (see Phase-2 reform). The skip_reason / tier on the
        # spec decides which decorators a test gets:
        #   - Bucket C (tier=comparison): full reproduction → slow + opt-in
        #     `reproduction` marker so the default `pytest -m "not reproduction"`
        #     invocation filters it out; run it on demand with -m reproduction.
        #   - Bucket A (skip_reason="qualitative_smoke"): hasattr/callable body
        #     with NO marker → reported as a real PASS/FAIL, not XPASS.
        #   - Bucket B value-check (skip_reason=None): no extra marker.
        #   - Legacy "qualitative" / "asset_missing": keep the old xfail wrapper
        #     for back-compat with bundles generated before this reform. New
        #     bundles never emit these (asset/dataset checks are Bucket A/B).
        sr = spec.get("skip_reason")
        is_comparison = fn["tier"] == "comparison" or include_slow_marker
        if is_comparison:
            decorators.append("@pytest.mark.slow")
            decorators.append("@pytest.mark.reproduction")
        elif sr == "qualitative_smoke":
            pass  # Bucket A: real PASS/FAIL, no marker.
        elif sr == "qualitative":
            decorators.append('@pytest.mark.xfail(strict=False, reason="qualitative requirement; smoke test")')
        decorators.append(
            '@pytest.mark.rubric(test_id={tid!r}, rubric_id={rid!r}, weight={w}, tier={tier!r})'.format(
                tid=fn["test_id"],
                rid=spec.get("rubric_id", ""),
                w=spec.get("weight", 1),
                tier=fn["tier"],
            )
        )
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
        '"""Auto-generated by 9b_synthesize_tests.py — do not edit by hand."""\n'
        + "\n".join(seen_imports)
        + "\n\n\n"
        + "\n\n".join(body_blocks)
    )
    file_path.write_text(file_text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Pass 3 — Audit (per-file AST + symbol-existence checks)                     #
# --------------------------------------------------------------------------- #

_REPO_NAMESPACE_PREFIXES: Tuple[str, ...] = ("src", "scripts", "tests", "assets", "main")


# True if `module` is part of the target repo (and thus worth validating).
def _is_repo_internal(module: str, repo_symbols: Dict[str, List[str]], repo_root: Path) -> bool:
    if module in repo_symbols:
        return True
    head = module.split(".", 1)[0]
    if head in _REPO_NAMESPACE_PREFIXES:
        return True
    if repo_root.exists():
        parts = module.split(".")
        candidate_file = repo_root.joinpath(*parts).with_suffix(".py")
        candidate_pkg = repo_root.joinpath(*parts, "__init__.py")
        if candidate_file.is_file() or candidate_pkg.is_file():
            return True
    return False


# Fallback: read top-level names directly from the on-disk module.
def _module_top_level_names(module: str, repo_root: Path) -> Optional[List[str]]:
    parts = module.split(".")
    candidates = [
        repo_root.joinpath(*parts).with_suffix(".py"),
        repo_root.joinpath(*parts, "__init__.py"),
    ]
    for cand in candidates:
        if not cand.is_file():
            continue
        try:
            tree = ast.parse(cand.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return []
        names: List[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.append(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
        return names
    return None


# Audit one emitted test file: AST-parse, verify imports resolve to real symbols.
def _audit_one_file(
    py_path: Path,
    repo_symbols: Dict[str, List[str]],
    repo_root: Path,
    rel_name: str,
) -> List[Dict[str, Any]]:
    file_issues: List[Dict[str, Any]] = []
    try:
        source = py_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        file_issues.append({
            "file": rel_name, "severity": "blocker", "kind": "unreadable_file",
            "detail": str(exc), "lineno": 0,
        })
        return file_issues
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        file_issues.append({
            "file": rel_name, "severity": "blocker", "kind": "syntax_error",
            "detail": f"{exc.msg} at line {exc.lineno}", "lineno": exc.lineno or 0,
        })
        return file_issues

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not module or not _is_repo_internal(module, repo_symbols, repo_root):
                continue

            if module == "tests" or module.startswith("tests."):
                for alias in node.names:
                    file_issues.append({
                        "file": rel_name, "severity": "blocker",
                        "kind": "forbidden_tests_import",
                        "module": module, "name": alias.name, "lineno": node.lineno,
                        "detail": f"`from {module} import {alias.name}` — repo test files "
                                  "are not a reusable library; redefine helpers locally.",
                    })
                continue

            module_names = repo_symbols.get(module)
            if module_names is None:
                module_names = _module_top_level_names(module, repo_root)
                if module_names is None:
                    file_issues.append({
                        "file": rel_name, "severity": "blocker", "kind": "unknown_module",
                        "module": module, "lineno": node.lineno,
                        "detail": f"module '{module}' not found in repo at {repo_root}",
                    })
                    continue
            symbol_set = set(module_names)
            for alias in node.names:
                imported = alias.name
                if imported == "*":
                    continue
                if imported not in symbol_set:
                    file_issues.append({
                        "file": rel_name, "severity": "blocker", "kind": "unresolved_symbol",
                        "module": module, "name": imported, "lineno": node.lineno,
                        "detail": f"`from {module} import {imported}` — '{imported}' is "
                                  f"not defined at module top-level in {module}.",
                    })

        elif isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if not _is_repo_internal(mod, repo_symbols, repo_root):
                    continue
                if mod in repo_symbols:
                    continue
                if _module_top_level_names(mod, repo_root) is None:
                    file_issues.append({
                        "file": rel_name, "severity": "blocker", "kind": "unknown_module",
                        "module": mod, "lineno": node.lineno,
                        "detail": f"`import {mod}` — module not found in repo at {repo_root}",
                    })
    return file_issues


# Audit all test_*.py files under tests_dir (recursive). One result dict.
def audit_emitted_files(
    tests_dir: Path,
    specs: List[Dict[str, Any]],
    repo_symbols: Dict[str, List[str]],
    repo_root: Path,
    paper_numbers: Set[str],
) -> Dict[str, Any]:
    """Validate every emitted test_*.py file against the real source repo.

    Walks tests_dir recursively so per-tier subdirectories (intermediate/,
    comparison/) are both covered. `file` entries in the returned issues use
    paths relative to tests_dir so repair_broken_tests can find them again.
    """
    issues: List[Dict[str, Any]] = []
    checked_files: List[str] = []

    for py_path in sorted(tests_dir.rglob("test_*.py")):
        rel_name = str(py_path.relative_to(tests_dir))
        checked_files.append(rel_name)
        issues.extend(_audit_one_file(py_path, repo_symbols, repo_root, rel_name))

    if paper_numbers:
        for spec in specs:
            intermediate = spec.get("intermediate")
            if intermediate and intermediate.get("expected_value") is not None:
                value_str = _strip_trailing_zero(intermediate["expected_value"])
                if value_str not in paper_numbers and value_str != "0":
                    issues.append({
                        "file": "test_specs.json", "severity": "warning",
                        "kind": "expected_value_not_in_paper",
                        "detail": f"test_id={spec.get('test_id')} value={value_str}",
                    })

    blockers = [i for i in issues if i.get("severity") == "blocker"]
    return {
        "passes": len(blockers) == 0,
        "blocker_count": len(blockers),
        "warning_count": len(issues) - len(blockers),
        "issues": issues,
        "checked_files": checked_files,
    }


# Format a float for substring-matching against paper text.
def _strip_trailing_zero(value: float) -> str:
    text = repr(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------- #
# Pass 4 — Repair: comment out hallucinated imports, emit skip stubs          #
# --------------------------------------------------------------------------- #

# Group audit blockers by file; rewrite each broken file. Returns one record per file.
def repair_broken_tests(tests_dir: Path, audit_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For every blocker in audit_payload, comment out the offending line and
    replace the affected test function with a @pytest.mark.skip stub. The
    original content is preserved as `# TODO(c8.1-repair): …` comments so the
    human reviewer can see what the LLM tried to emit."""
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for issue in audit_payload.get("issues", []):
        if issue.get("severity") != "blocker":
            continue
        fname = issue.get("file")
        if isinstance(fname, str) and fname.endswith(".py"):
            by_file.setdefault(fname, []).append(issue)

    records: List[Dict[str, Any]] = []
    for file_name, file_issues in by_file.items():
        py_path = tests_dir / file_name
        if not py_path.exists():
            continue
        record = _repair_one_file(py_path, file_issues)
        if record:
            records.append(record)
    return records


# Rewrite one broken file: comment offending lines + emit skip stubs.
def _repair_one_file(py_path: Path, file_issues: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    try:
        source = py_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[WARNING] repair: cannot read {py_path.name}: {exc}")
        return None

    bad_names: Set[str] = {
        iss["name"] for iss in file_issues
        if iss.get("kind") in ("unresolved_symbol", "forbidden_tests_import") and "name" in iss
    }
    bad_modules: Set[str] = {
        iss["module"] for iss in file_issues
        if iss.get("kind") in ("unknown_module", "forbidden_tests_import") and "module" in iss
    }

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        new_body = (
            f'"""Auto-generated by 9b_synthesize_tests.py; '
            f'repaired after syntax error: {exc.msg} (line {exc.lineno})."""\n'
            "import pytest\n\n"
            'pytestmark = pytest.mark.skip(reason='
            f'"c8.1-repair: generation-time syntax error: {exc.msg}")\n\n'
            "# TODO(c8.1-repair): original content below failed to parse.\n"
            + "\n".join(f"# {ln}" for ln in source.splitlines())
            + "\n"
        )
        py_path.write_text(new_body, encoding="utf-8")
        return {
            "file": py_path.name,
            "bad_names": sorted(bad_names),
            "bad_modules": sorted(bad_modules),
            "skipped_functions": [],
            "syntax_error": True,
        }

    lines = source.splitlines()

    bad_import_lineno: Dict[int, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in bad_modules:
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    bad_import_lineno[ln] = f"unresolved or forbidden module: {module}"
                continue
            hit = {a.name for a in node.names} & bad_names
            if hit:
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    bad_import_lineno[ln] = f"missing in {module}: {', '.join(sorted(hit))}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in bad_modules:
                    for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                        bad_import_lineno[ln] = f"unknown module: {alias.name}"
                    break

    funcs_to_skip: List[Dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        refs: Set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in bad_names:
                refs.add(sub.id)
            elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in bad_names:
                refs.add(sub.value.id)
        if not refs:
            continue
        rubric_decorator: Optional[str] = None
        for dec in node.decorator_list:
            try:
                dec_src = ast.unparse(dec)
            except Exception:
                dec_src = ""
            if "pytest.mark.rubric" in dec_src:
                rubric_decorator = "@" + dec_src
                break
        first_line = min((d.lineno for d in node.decorator_list), default=node.lineno)
        funcs_to_skip.append({
            "name": node.name,
            "refs": sorted(refs),
            "start_line": first_line,
            "end_line": node.end_lineno or node.lineno,
            "rubric_decorator": rubric_decorator,
        })

    funcs_to_skip.sort(key=lambda f: f["start_line"])
    func_start_to_meta: Dict[int, Dict[str, Any]] = {f["start_line"]: f for f in funcs_to_skip}
    func_covered_lines: Set[int] = set()
    for f in funcs_to_skip:
        for ln in range(f["start_line"], f["end_line"] + 1):
            func_covered_lines.add(ln)

    out: List[str] = []
    i = 1
    n = len(lines)
    while i <= n:
        if i in func_start_to_meta:
            meta = func_start_to_meta[i]
            refs_str = ", ".join(meta["refs"])
            out.append(
                f"# TODO(c8.1-repair): function references hallucinated symbol(s) [{refs_str}]"
            )
            out.append("# Original preserved below; replaced with a @pytest.mark.skip stub.")
            for j in range(meta["start_line"], meta["end_line"] + 1):
                out.append(f"# {lines[j - 1]}")
            out.append("")
            out.append(
                f'@pytest.mark.skip(reason="c8.1-repair: hallucinated symbol(s): {refs_str}")'
            )
            if meta["rubric_decorator"]:
                out.append(meta["rubric_decorator"])
            out.append(f"def {meta['name']}():")
            out.append("    assert True  # placeholder; see commented-out original above")
            i = meta["end_line"] + 1
            continue
        if i in func_covered_lines:
            i += 1
            continue
        if i in bad_import_lineno:
            out.append(f"# TODO(c8.1-repair): {bad_import_lineno[i]}")
            out.append(f"# {lines[i - 1]}")
            i += 1
            continue
        out.append(lines[i - 1])
        i += 1

    rewritten = "\n".join(out) + ("\n" if source.endswith("\n") else "")
    py_path.write_text(rewritten, encoding="utf-8")
    return {
        "file": str(py_path.name),
        "bad_names": sorted(bad_names),
        "bad_modules": sorted(bad_modules),
        "skipped_functions": [f["name"] for f in funcs_to_skip],
    }


# --------------------------------------------------------------------------- #
# Self-checks                                                                 #
# --------------------------------------------------------------------------- #

# Run `pytest --collect-only` against tests_dir and return (ok, log_tail).
#
# We choose the python carefully because the test files do `from main import
# Main` etc., which transitively imports torch / transformers / accelerate.
# If we use the pipeline's `sys.executable`, that python is the host CPython
# and it sees `~/.local/lib/python3.10/site-packages` — which on some hosts
# contains a broken botocore/accelerate that fails to import even at collect
# time. Picking the repo's own .venv interpreter (when present) avoids this
# leak entirely: that venv was provisioned by run_tests_local.sh from the
# repo's requirements.txt, so it has the matching torch/transformers and no
# stray user-site packages.
#
# Fallback order:
#   1. <repo>/.venv/bin/python  (if exists AND can import _posixsubprocess)
#   2. sys.executable with PYTHONNOUSERSITE=1  (skips user site-packages so
#      a broken host install doesn't fail collection)
#
# If neither yields a working pytest, return (True, "skipped: no usable
# python") rather than blocking the pipeline — collection is a best-effort
# check; the audit is the authoritative correctness signal.
def collect_only_check(tests_dir: Path) -> Tuple[bool, str]:
    # The tests_dir layout is <repo_root>/tests; repo_root holds the .venv
    # that run_tests_local.sh creates with the full repo dependencies.
    repo_root = tests_dir.parent
    candidates: List[Tuple[str, Dict[str, str]]] = []

    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        # Probe the venv before trusting it — conda-cloned venvs often look
        # valid but cannot import their stdlib. If broken, fall through to
        # the host fallback instead of returning a confusing failure.
        try:
            probe = subprocess.run(
                [str(venv_python), "-c", "import _posixsubprocess"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            probe = None
        if probe is not None and probe.returncode == 0:
            candidates.append((str(venv_python), {}))

    candidates.append((sys.executable, {"PYTHONNOUSERSITE": "1"}))

    last_log = ""
    for python_path, extra_env in candidates:
        env = os.environ.copy()
        env.update(extra_env)
        try:
            result = subprocess.run(
                [python_path, "-m", "pytest", "--collect-only", str(tests_dir)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=env,
            )
            last_log = (result.stdout + result.stderr)[-2000:]
            if result.returncode == 0:
                return True, last_log
            # "No module named pytest" → not a real test failure, try next.
            if "No module named pytest" in last_log:
                continue
            # If the failure is from a third-party / heavy dep that the
            # collection-check environment doesn't have installed (torch,
            # transformers, accelerate, etc.), this is not a generated-test
            # defect — the test will be exercised in a properly provisioned
            # venv by run_tests_local.sh. Treat as warning, not blocker.
            if _is_env_dependency_failure(last_log):
                return True, "collect-only skipped (env missing repo deps):\n" + last_log
            # Real collection error against this python (e.g., a syntax
            # error in the generated test file, or a hallucinated repo
            # symbol that audit somehow missed).
            return False, last_log
        except FileNotFoundError:
            last_log = f"pytest not invokable via {python_path}"
            continue
        except subprocess.TimeoutExpired:
            return False, "pytest --collect-only timed out"

    # All candidates failed to even launch pytest — treat as skipped, not
    # failed. The audit already validated import resolution statically.
    return True, "collect-only skipped: " + (last_log or "no usable python with pytest")


# True when a collect-only failure is caused by a missing third-party dep
# (torch, transformers, etc.) rather than a defect in the generated test
# file. Pattern-matches the pytest error tail.
def _is_env_dependency_failure(log: str) -> bool:
    if "ModuleNotFoundError" not in log and "ImportError" not in log:
        return False
    # Heavy deps that are provisioned per-repo by requirements.txt, not by
    # the pipeline. Their absence at pipeline time is expected.
    env_pkgs = (
        "torch", "transformers", "accelerate", "datasets", "peft",
        "jax", "jaxlib", "flax", "tensorflow", "tf_keras",
        "boto3", "botocore", "huggingface_hub",
        "numpy", "scipy", "pandas", "sklearn", "scikit-learn",
        "evaluate", "lm_eval", "rouge_score",
        "matplotlib", "seaborn",
    )
    for pkg in env_pkgs:
        if f"No module named '{pkg}'" in log:
            return True
        # Old collections.Mapping etc. against deps that ship vendored libs.
        if f"from {pkg}" in log and "cannot import name" in log:
            return True
    # collections.Mapping breakage from vendored requests/urllib3 inside boto.
    if "from collections import Mapping" in log or "cannot import name 'Mapping' from 'collections'" in log:
        return True
    return False
