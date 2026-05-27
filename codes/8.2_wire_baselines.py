"""Stage 8.2 — Wire rival baselines into the generated source repo.

What this script does, in plain English:
  After stage 8.1 has ameliorated the authors' OWN method, the generated repo
  still has no way to RUN the rival baselines that the comparison-tier rubric
  needs. This stage reads the paper's evaluation plan + the asset download
  report and makes the source self-sufficient for running those comparisons.

  1. Discovery (no LLM): read eval_plan.json (the baseline catalogue) and the
     c1 download summary, then classify every baseline into one of four
     buckets:
       - download       : an external repo was cloned to assets/rivals/<slug>/;
                          wrap it.
       - implement      : an algorithm/training method with no external repo;
                          the LLM must code it up from the paper + notes.
       - reference_only : compare to the paper's published number; no code.
       - skip           : out-of-scope (oversize) or download missing.
  2. Scaffold (no LLM): write a skeleton src/baselines/<slug>.py per
     download/implement baseline, plus a deterministic src/baselines/__init__.py
     registry that dispatches `run_baseline_for(slug, dataset, config)`.
  3. LLM check -> patch loop (mirrors 8.1): iteratively fill in each skeleton's
     run_baseline() via SEARCH/REPLACE edits, with .<save_num>.bak backups.
  4. Audit: write stage_8.2_history.json grouping baselines by final bucket.
     Non-zero exit if any in-scope baseline is still a NotImplementedError stub.

Usage example:
  python3.10 codes/8.2_wire_baselines.py \
    --paper_name          all-in-one \
    --output_repo_dir     outputs/paperbench_repos/all-in-one_repo \
    --output_dir          outputs/paperbench/all-in-one \
    --eval_plan_path      tests/harbor/yantingchi/all-in-one/eval_plan/eval_plan.json \
    --asset_root          tests/harbor/yantingchi/all-in-one/harbor/asset \
    --gpt_version         gpt-5.2

  # Discovery only (classify baselines, scaffold skeletons, skip the LLM loop):
  python3.10 codes/8.2_wire_baselines.py ... --dry_run

If a required command returns nonzero (missing eval_plan, unreadable repo) the
script prints a clear error before exiting.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai_client import create_openai_client
from utils import (
    load_accumulated_cost,
    print_log_cost,
    print_response,
    read_python_files,
    save_accumulated_cost,
)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CHECK_PROMPT_PATH = PROMPTS_DIR / "c8.2_wire_baselines_check_prompt.txt"
PATCH_PROMPT_PATH = PROMPTS_DIR / "c8.2_wire_baselines_patch_prompt.txt"

# Marker so re-running 8.2 can recognise its own scaffolded files.
SCAFFOLD_MARKER = "# AUTO-GENERATED-BY-8.2_wire_baselines"
# Substring that means an adapter is still an unfilled skeleton.
STUB_SENTINEL = "Stage 8.2"

# Buckets a baseline can land in after classification.
BUCKET_DOWNLOAD = "download"
BUCKET_IMPLEMENT = "implement"
BUCKET_REFERENCE = "reference_only"
BUCKET_SKIP = "skip"


# Print an error message and exit nonzero (every hard failure routes through here).
def die(message: str) -> None:
    print(f"[8.2_wire_baselines] ERROR: {message}", file=sys.stderr)
    sys.exit(1)


# Turn an arbitrary baseline name/slug into a safe python module name.
def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return slug or "baseline"


# Substrings that mark a transient API error worth retrying with backoff.
_TRANSIENT_MARKERS = (
    "429", "too many requests", "rate limit", "ratelimit", "timeout", "timed out",
    "502", "503", "504", "overloaded", "temporarily unavailable", "connection",
)


# Call client.chat.completions.create with exponential backoff on transient
# errors (429 / 5xx / timeouts). Re-raises non-transient errors immediately and
# gives up after max_retries. time.sleep here is inside the worker process, not
# the agent shell, so it is safe.
def create_with_retry(client: Any, request: Dict[str, object], max_retries: int = 8, base_delay: float = 10.0):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**request)
        except Exception as exc:  # noqa: BLE001 — classify by message, then re-raise
            msg = str(exc).lower()
            transient = any(m in msg for m in _TRANSIENT_MARKERS)
            if not transient or attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2 ** attempt), 120.0)  # cap per-retry sleep at 2 min
            print(f"[8.2] transient API error (attempt {attempt + 1}/{max_retries}): "
                  f"{str(exc)[:140]} — retrying in {delay:.0f}s", flush=True)
            time.sleep(delay)
    raise RuntimeError("create_with_retry exhausted retries without returning")  # unreachable


# ============================================================
# Discovery (no LLM)
# ============================================================

# Load the eval_plan.json and return its `baselines` list (raises if missing).
def load_eval_plan_baselines(eval_plan_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(eval_plan_path):
        die(f"eval_plan not found: {eval_plan_path}")
    with open(eval_plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    baselines = plan.get("baselines") or []
    if not baselines:
        print(f"[8.2] WARNING: eval_plan has no baselines: {eval_plan_path}")
    return baselines


# Read the c1 download summary (preferred) or fall back to download_report.md, and
# return {normalized_name: {"path": str, "fetch_method": str}} for every downloaded rival.
def load_downloaded_rivals(asset_root: str) -> Dict[str, Dict[str, str]]:
    downloaded: Dict[str, Dict[str, str]] = {}

    summary_path = os.path.join(asset_root, "c1_download_dataset_summary.json")
    if os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            for item in summary.get("items", []) or []:
                if item.get("category") != "rivals":
                    continue
                name = str(item.get("name", ""))
                if not name:
                    continue
                downloaded[slugify(name)] = {
                    "path": item.get("path", ""),
                    "fetch_method": item.get("fetch_method", ""),
                }
            return downloaded
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[8.2] WARNING: could not parse {summary_path}: {exc}")

    # Fallback: list the on-disk assets/rivals/ directory directly.
    rivals_dir = os.path.join(asset_root, "assets", "rivals")
    if os.path.isdir(rivals_dir):
        for entry in os.listdir(rivals_dir):
            full = os.path.join(rivals_dir, entry)
            if os.path.isdir(full) and not entry.startswith("_"):
                downloaded[slugify(entry)] = {"path": full, "fetch_method": "unknown"}
    return downloaded


# Match an eval_plan baseline to a downloaded rival dir by slug, then by name.
def match_download(baseline: Dict[str, Any], downloaded: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    for key in (slugify(baseline.get("slug", "")), slugify(baseline.get("name", ""))):
        if key in downloaded:
            return downloaded[key]
    return None


# Classify one baseline into a bucket and attach the resolved fields the later
# steps need (asset_path, rival_hint_files, skip_reason).
def classify_baseline(
    baseline: Dict[str, Any],
    downloaded: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    slug = slugify(baseline.get("slug") or baseline.get("name") or "baseline")
    impl = (baseline.get("implementation_type") or "").strip().lower()
    in_scope = bool(baseline.get("in_scope", True))

    entry: Dict[str, Any] = {
        "slug": slug,
        "name": baseline.get("name", slug),
        "implementation_type": impl,
        "in_scope": in_scope,
        "priority": baseline.get("priority", ""),
        "notes": baseline.get("notes", ""),
        "paper_citation": baseline.get("paper_citation", ""),
        "official_repo_url": baseline.get("official_repo_url", ""),
        "asset_path": None,
        "rival_hint_files": [],
        "skip_reason": "",
    }

    if impl == "reference_only":
        entry["bucket"] = BUCKET_REFERENCE
        return entry
    if not in_scope:
        entry["bucket"] = BUCKET_SKIP
        entry["skip_reason"] = "out_of_scope"
        return entry

    if impl == "download":
        match = match_download(baseline, downloaded)
        if not match or not match.get("path") or not os.path.isdir(match["path"]):
            entry["bucket"] = BUCKET_SKIP
            entry["skip_reason"] = "download_missing"
            return entry
        entry["bucket"] = BUCKET_DOWNLOAD
        entry["asset_path"] = match["path"]
        entry["rival_hint_files"] = _collect_hint_files(match["path"])
        return entry

    # implementation_type == "implement" (or anything else in-scope): code it up.
    entry["bucket"] = BUCKET_IMPLEMENT
    return entry


# Return hint files that reveal a downloaded rival's real API. Rivals from the
# c1 downloader are laid out as <slug>/manifest.json + <slug>/repo/<clone>, where
# the importable package lives UNDER repo/ and usage is shown in repo/examples/.
# So we surface: the manifest, a usage example, the package __init__, and any
# top-level entry script. Without this the LLM guesses repo/main.py and fails.
def _collect_hint_files(asset_path: str) -> List[str]:
    found: List[str] = []

    def add(path: str) -> None:
        if path and os.path.isfile(path) and path not in found:
            found.append(path)

    add(os.path.join(asset_path, "manifest.json"))

    # Search both the asset root and a nested repo/ clone for entry points/examples.
    search_roots = [asset_path]
    repo_sub = os.path.join(asset_path, "repo")
    if os.path.isdir(repo_sub):
        search_roots.append(repo_sub)

    for root in search_roots:
        for name in ("entrypoint.py", "run.py", "main.py", "cli.py", "README.md"):
            add(os.path.join(root, name))
        # A usage example is the single most useful hint for calling the rival.
        examples_dir = os.path.join(root, "examples")
        if os.path.isdir(examples_dir):
            for name in sorted(os.listdir(examples_dir)):
                if name.endswith(".py"):
                    add(os.path.join(examples_dir, name))
                    break
        # The first python package's __init__ shows the public symbols.
        try:
            for name in sorted(os.listdir(root)):
                pkg_init = os.path.join(root, name, "__init__.py")
                if os.path.isfile(pkg_init):
                    add(pkg_init)
                    break
        except OSError:
            pass

    return found[:5]


# Return a short text tree of a downloaded rival's directory (so the LLM sees
# the real import structure, e.g. that the package lives under repo/<pkg>/).
def rival_tree(asset_path: str, max_entries: int = 40) -> str:
    lines: List[str] = []
    base = Path(asset_path)
    try:
        for p in sorted(base.rglob("*")):
            if ".git" in p.parts or "__pycache__" in p.parts:
                continue
            rel = p.relative_to(base)
            lines.append(("  " * (len(rel.parts) - 1)) + rel.parts[-1] + ("/" if p.is_dir() else ""))
            if len(lines) >= max_entries:
                lines.append("... [truncated]")
                break
    except OSError:
        pass
    return "\n".join(lines)


# Read a hint file's first `max_chars` characters (best-effort).
def read_excerpt(path: str, max_chars: int = 2000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(max_chars + 1)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [truncated]"
        return text
    except OSError:
        return ""


# Build the full bucketed manifest for the paper.
def build_manifest(eval_plan_path: str, asset_root: str) -> Dict[str, Any]:
    baselines = load_eval_plan_baselines(eval_plan_path)
    downloaded = load_downloaded_rivals(asset_root)
    classified = [classify_baseline(b, downloaded) for b in baselines]
    return {
        "eval_plan_path": os.path.abspath(eval_plan_path),
        "asset_root": os.path.abspath(asset_root),
        "downloaded_rivals": sorted(downloaded.keys()),
        "baselines": classified,
    }


# ============================================================
# Scaffold (no LLM)
# ============================================================

# Appended to every adapter skeleton. The 8.2 LLM refinement fills it with the
# smallest valid (dataset, config) so the runtime smoke check can exercise the
# adapter regardless of paper-specific input semantics. Until filled it returns
# (None, {}) and the smoke check falls back to the auto-picked dataset.
_SMOKE_ARGS_STUB = (
    "\n\n"
    "## @brief Smallest valid (dataset, config) pair to smoke-test run_baseline.\n"
    "# @return tuple (dataset_or_experiment, config_overrides). 8.2 fills this in.\n"
    "def smoke_args():\n"
    "    # TODO(c8.2-smoke): return the tiniest valid (dataset, config) for this baseline\n"
    "    # (e.g. a 2-D toy / a single-example split). Return (None, {}) to defer to the\n"
    "    # auto-picked dataset.\n"
    "    return (None, {})\n"
)


# Render the skeleton body for a download-bucket adapter (cloned-repo wrapper).
def _download_template_render(entry: Dict[str, Any]) -> str:
    asset_path = entry["asset_path"]
    return (
        f'"""{SCAFFOLD_MARKER} (bucket=download)\n\n'
        f'Adapter for {entry["name"]} (slug: {entry["slug"]}).\n\n'
        f'Wraps the cloned rival repo at:\n    {asset_path}\n\n'
        f'The 8.2 LLM refinement step replaces the NotImplementedError body below\n'
        f'with a real call into that repo\'s entry point. Imports must resolve against\n'
        f'the sys.path injection already set up here (both _RIVAL_ROOT and its repo/\n'
        f'subdir are added, since cloned packages usually live under repo/).\n"""\n'
        "from __future__ import annotations\n\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, Optional\n\n"
        f"_RIVAL_ROOT = Path({asset_path!r})\n"
        "# Cloned rival packages typically live under <slug>/repo/, so add both.\n"
        "for _cand in (_RIVAL_ROOT, _RIVAL_ROOT / \"repo\"):\n"
        "    if _cand.exists() and str(_cand) not in sys.path:\n"
        "        sys.path.insert(0, str(_cand))\n\n\n"
        f"## @brief Run the {entry['name']} baseline against the given dataset.\n"
        "# @param dataset str Dataset slug (must match eval_plan.json).\n"
        "# @param config Optional[Dict[str, Any]] Optional config overrides.\n"
        "# @return Dict[str, Any] Metric results comparable to the authors' own run.\n"
        "def run_baseline(dataset: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n"
        "    # TODO(c8.2-refine-download): call into _RIVAL_ROOT's real entry point.\n"
        f'    raise NotImplementedError("{STUB_SENTINEL} download-wrapper for {entry["slug"]} not yet refined.")\n'
        + _SMOKE_ARGS_STUB
    )


# Render the skeleton body for an implement-bucket adapter.
def _implement_template_render(entry: Dict[str, Any]) -> str:
    notes = (entry.get("notes") or "").replace('"""', "'''")
    citation = entry.get("paper_citation", "")
    return (
        f'"""{SCAFFOLD_MARKER} (bucket=implement)\n\n'
        f'Adapter for {entry["name"]} (slug: {entry["slug"]}).\n\n'
        "This baseline has NO external repo. The 8.2 LLM refinement step writes a\n"
        "faithful Python implementation here (or adds a code path inside\n"
        "src/pipeline/ and calls it from run_baseline). Reuse existing primitives\n"
        "(dataset loader, model factory, metrics) rather than duplicating them.\n\n"
        f"Paper citation: {citation}\n"
        f"Eval-plan notes:\n    {notes}\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "from typing import Any, Dict, Optional\n\n\n"
        f"## @brief Run the {entry['name']} baseline against the given dataset.\n"
        "# @param dataset str Dataset slug (must match eval_plan.json).\n"
        "# @param config Optional[Dict[str, Any]] Optional config overrides.\n"
        "# @return Dict[str, Any] Metric results comparable to the authors' own run.\n"
        "def run_baseline(dataset: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n"
        "    # TODO(c8.2-refine-implement): implement per the eval-plan notes above.\n"
        f'    raise NotImplementedError("{STUB_SENTINEL} implement-skeleton for {entry["slug"]} not yet refined.")\n'
        + _SMOKE_ARGS_STUB
    )


# The deterministic registry that later code / tests use to call any baseline.
def _registry_template(slugs: List[str]) -> str:
    listed = ", ".join(repr(s) for s in sorted(slugs))
    return (
        f'"""{SCAFFOLD_MARKER} (registry)\n\n'
        "Dispatch table for rival baselines wired by stage 8.2. Each baseline lives\n"
        "in src/baselines/<slug>.py and exposes run_baseline(dataset, config).\n\n"
        "Usage:\n"
        "    from src.baselines import run_baseline_for, available_baselines\n"
        "    result = run_baseline_for('nle', dataset='sbibm_tasks')\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "import importlib\n"
        "from typing import Any, Dict, List, Optional\n\n"
        f"_BASELINE_SLUGS: List[str] = [{listed}]\n\n\n"
        "## @brief List the rival baseline slugs wired into this repo.\n"
        "# @return List[str] Sorted baseline slugs.\n"
        "def available_baselines() -> List[str]:\n"
        "    return list(_BASELINE_SLUGS)\n\n\n"
        "## @brief Run a rival baseline by slug.\n"
        "# @param slug str One of available_baselines().\n"
        "# @param dataset str Dataset slug.\n"
        "# @param config Optional[Dict[str, Any]] Optional config overrides.\n"
        "# @return Dict[str, Any] The baseline's metric results.\n"
        "def run_baseline_for(slug: str, dataset: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n"
        "    if slug not in _BASELINE_SLUGS:\n"
        "        raise KeyError(\n"
        "            f\"unknown baseline {slug!r}; available: {_BASELINE_SLUGS}\"\n"
        "        )\n"
        "    module = importlib.import_module(f\"src.baselines.{slug}\")\n"
        "    return module.run_baseline(dataset, config)\n"
    )


# Write src/baselines/__init__.py (registry) + one <slug>.py per download/implement
# baseline. Existing non-stub adapters are left untouched (idempotent re-runs).
def scaffold_adapters(repo_dir: str, manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    baselines_dir = os.path.join(repo_dir, "src", "baselines")
    os.makedirs(baselines_dir, exist_ok=True)

    written: List[str] = []
    skipped_existing: List[str] = []
    wired_slugs: List[str] = []

    for entry in manifest["baselines"]:
        if entry["bucket"] not in (BUCKET_DOWNLOAD, BUCKET_IMPLEMENT):
            continue
        slug = entry["slug"]
        wired_slugs.append(slug)
        adapter_path = os.path.join(baselines_dir, f"{slug}.py")

        # Don't clobber an adapter a human (or a prior refinement) already filled in.
        if os.path.exists(adapter_path):
            try:
                existing = Path(adapter_path).read_text(encoding="utf-8")
            except OSError:
                existing = ""
            if SCAFFOLD_MARKER not in existing or STUB_SENTINEL not in existing:
                skipped_existing.append(slug)
                continue

        if entry["bucket"] == BUCKET_DOWNLOAD:
            body = _download_template_render(entry)
        else:
            body = _implement_template_render(entry)
        Path(adapter_path).write_text(body, encoding="utf-8")
        written.append(slug)

    # Always (re)write the registry so it lists exactly the wired slugs.
    Path(os.path.join(baselines_dir, "__init__.py")).write_text(
        _registry_template(wired_slugs), encoding="utf-8"
    )

    return {"written": written, "skipped_existing": skipped_existing, "wired_slugs": wired_slugs}


# ============================================================
# Codebase serialization (copied from 8.1 — digit-prefixed module can't be imported)
# ============================================================

# Read every .py (plus config.yaml) under repo_dir into one prompt-ready string.
def serialize_codebase(repo_dir: str) -> str:
    py_files = read_python_files(repo_dir)
    blocks = []
    for relpath, content in sorted(py_files.items()):
        blocks.append(f"```python\n## File: {relpath}\n{content}\n```")
    config_path = os.path.join(repo_dir, "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            blocks.append(f"```yaml\n## File: config.yaml\n{f.read()}\n```")
    return "\n\n".join(blocks)


# Path prefixes whose full contents the patch step needs (the reusable primitives
# a baseline adapter will call into). Vendored rival trees (e.g. simformer/) are
# deliberately excluded — the LLM gets those as hint excerpts in the task text.
_FOCUS_PREFIXES = (
    "src/baselines/", "src/data/", "src/metrics", "src/models/", "src/model/",
    "src/pipeline/", "src/experiments/", "src/utils/", "src/llm/", "src/config",
    "src/", "main.py",
)


# Build a size-capped, focused view of the repo for the patch step: a full
# file tree (so the LLM knows what exists) plus the contents of the reusable
# `src/` primitives and the target adapters, prioritised and truncated to fit
# `max_chars`. Avoids the context_length_exceeded blow-up that dumping a
# vendored rival tree (e.g. simformer/, ~1.3 MB) causes.
def build_focused_codebase(repo_dir: str, max_chars: int = 160000) -> str:
    py_files = read_python_files(repo_dir)

    tree = "\n".join(f"  {relpath}" for relpath in sorted(py_files))
    header = f"## Repository file tree (paths only)\n{tree}\n\n## Selected file contents\n"

    def priority(relpath: str) -> int:
        for i, pref in enumerate(_FOCUS_PREFIXES):
            if relpath.startswith(pref):
                return i
        return len(_FOCUS_PREFIXES)

    ordered = sorted(py_files, key=lambda rp: (priority(rp), rp))
    blocks: List[str] = []
    used = len(header)
    for relpath in ordered:
        # Only spend budget on the focused primitives + adapters; skip vendored code.
        if priority(relpath) == len(_FOCUS_PREFIXES):
            continue
        content = py_files[relpath]
        block = f"```python\n## File: {relpath}\n{content}\n```"
        if used + len(block) > max_chars:
            block = block[: max(0, max_chars - used)] + "\n... [truncated]\n```"
            blocks.append(block)
            break
        blocks.append(block)
        used += len(block)

    config_path = os.path.join(repo_dir, "config.yaml")
    if os.path.exists(config_path) and used < max_chars:
        with open(config_path, "r", encoding="utf-8") as f:
            blocks.append(f"```yaml\n## File: config.yaml\n{f.read()}\n```")

    # Embed the CONTENTS of small config files (yaml under configs/ + repo-root
    # config*.yaml), not just their paths — seeing how datasets/tasks are
    # configured is what lets the adapter load the right config (this unlocked
    # bbox). Skip the hydra `outputs/` run-dirs and big files.
    cfg_blocks: List[str] = []
    cfg_used = 0
    cfg_paths: List[Path] = []
    cfg_dir = os.path.join(repo_dir, "configs")
    if os.path.isdir(cfg_dir):
        cfg_paths += sorted(Path(cfg_dir).rglob("*.yaml")) + sorted(Path(cfg_dir).rglob("*.yml"))
    cfg_paths += sorted(Path(repo_dir).glob("config*.yaml")) + sorted(Path(repo_dir).glob("config*.yml"))
    for p in cfg_paths:
        if "/outputs/" in str(p) or "/.hydra/" in str(p) or ".venv" in str(p):
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(content) > 4000:
            content = content[:4000] + "\n... [truncated]"
        block = f"```yaml\n## Config: {p.relative_to(repo_dir)}\n{content}\n```"
        if cfg_used + len(block) > 45000:
            break
        cfg_blocks.append(block)
        cfg_used += len(block)
    if cfg_blocks:
        blocks.append("## Config files (load one of these where the repo needs a config "
                      "— e.g. to populate dataset/task registries)\n" + "\n\n".join(cfg_blocks))

    return header + "\n\n".join(blocks)


# Collect the FULL source of every `src.*` module that the failing adapters
# import, so the LLM sees the exact signatures it must call (e.g. EvalRunner's
# real __init__). This is precise and never truncates the critical files — the
# general focused dump can drop them when src/ is huge (e.g. all-in-one ~970KB).
def collect_referenced_src_sources(repo_dir: str, failing: List[Dict[str, Any]],
                                    per_file_cap: int = 30000, total_cap: int = 110000) -> str:
    import ast as _ast
    modules: List[str] = []
    seen: set = set()
    for e in failing:
        adapter = os.path.join(repo_dir, "src", "baselines", f"{e['slug']}.py")
        if not os.path.isfile(adapter):
            continue
        try:
            tree = _ast.parse(Path(adapter).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in _ast.walk(tree):
            mod = None
            if isinstance(node, _ast.ImportFrom) and (node.module or "").startswith("src"):
                mod = node.module
            elif isinstance(node, _ast.Import):
                for a in node.names:
                    if a.name.startswith("src."):
                        if a.name not in seen:
                            seen.add(a.name); modules.append(a.name)
            if mod and mod not in seen:
                seen.add(mod); modules.append(mod)

    blocks: List[str] = []
    used = 0
    for mod in modules:
        parts = mod.split(".")
        for cand in (os.path.join(repo_dir, *parts) + ".py", os.path.join(repo_dir, *parts, "__init__.py")):
            if os.path.isfile(cand):
                try:
                    content = Path(cand).read_text(encoding="utf-8")
                except OSError:
                    break
                if len(content) > per_file_cap:
                    content = content[:per_file_cap] + "\n... [truncated]"
                rel = os.path.relpath(cand, repo_dir)
                block = f"```python\n## File: {rel}  (module {mod} — use these EXACT signatures)\n{content}\n```"
                if used + len(block) > total_cap:
                    break
                blocks.append(block); used += len(block)
                break
    return "\n\n".join(blocks)


# ============================================================
# LLM check
# ============================================================

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
                    "slug": {"type": "string"},
                    "status": {"type": "string", "enum": ["pass", "fail"]},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["slug", "status", "evidence", "reason"],
            },
        },
    },
    "required": ["results"],
}


# Load a prompt template file (raises with a clear message if missing).
def load_prompt(path: Path) -> str:
    if not path.exists():
        die(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


# Build the per-baseline check messages. `pending` is the list of download/implement
# entries; `adapters_text` is the current contents of each adapter file.
def build_check_messages(
    check_prompt: str,
    pending: List[Dict[str, Any]],
    adapters_text: str,
) -> List[Dict[str, str]]:
    system = (
        "You are a meticulous code reviewer for rival-baseline adapters. For EACH "
        "baseline, decide whether src/baselines/<slug>.py now contains a REAL "
        "implementation (PASS) or is still an unfilled NotImplementedError stub / "
        "obviously broken (FAIL). Judge only from the code shown. Output strict JSON "
        "matching the schema; no markdown."
    )
    manifest_json = json.dumps(
        [
            {
                "slug": e["slug"],
                "bucket": e["bucket"],
                "name": e["name"],
                "notes": e.get("notes", ""),
                "asset_path": e.get("asset_path"),
            }
            for e in pending
        ],
        indent=2,
        ensure_ascii=False,
    )
    user = f"""{check_prompt}

<baselines_json>
{manifest_json}
</baselines_json>

<current_adapters>
{adapters_text}
</current_adapters>

Return JSON: {{"results": [{{"slug": "...", "status": "pass|fail", "evidence": "...", "reason": "..."}}, ...]}}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# Call the model with structured JSON output. Returns (parsed, completion_json).
def call_check(client: Any, gpt_version: str, messages: List[Dict[str, str]]) -> Tuple[Dict, Dict]:
    request: Dict[str, object] = {
        "model": gpt_version,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "baseline_check", "schema": CHECK_SCHEMA, "strict": True},
        },
    }
    if "o3" in gpt_version or "o4" in gpt_version:
        request["reasoning_effort"] = "high"
    else:
        request["temperature"] = 0
    completion = create_with_retry(client, request)
    completion_json = json.loads(completion.model_dump_json())
    parsed = json.loads(completion_json["choices"][0]["message"]["content"])
    return parsed, completion_json


# ============================================================
# LLM patch (SEARCH/REPLACE)
# ============================================================

# Build the patch messages for the failing baselines, including download hint
# excerpts and the relevant source so the LLM can write correct edits.
def build_patch_messages(
    patch_prompt: str,
    failing: List[Dict[str, Any]],
    codebase: str,
    referenced_src: str = "",
) -> List[Dict[str, str]]:
    tasks_text = ""
    for e in failing:
        tasks_text += f"\n--- baseline: {e['slug']} (bucket={e['bucket']}) ---\n"
        tasks_text += f"name: {e['name']}\n"
        if e.get("notes"):
            tasks_text += f"eval_plan notes: {e['notes']}\n"
        if e.get("paper_citation"):
            tasks_text += f"paper citation: {e['paper_citation']}\n"
        if e["bucket"] == BUCKET_DOWNLOAD and e.get("asset_path"):
            tasks_text += f"rival code root (already on sys.path, incl. its repo/ subdir): {e['asset_path']}\n"
            tasks_text += "  rival directory tree:\n"
            tasks_text += "\n".join("    " + ln for ln in rival_tree(e["asset_path"]).splitlines()) + "\n"
            for hint in e.get("rival_hint_files", []):
                rel = os.path.relpath(hint, e["asset_path"])
                tasks_text += f"\n  hint file {rel}:\n"
                excerpt = read_excerpt(hint, max_chars=1500)
                tasks_text += "\n".join("    " + ln for ln in excerpt.splitlines()) + "\n"
        if e.get("import_problems"):
            tasks_text += "\n  MUST-FIX unresolved imports in the current adapter:\n"
            for prob in e["import_problems"]:
                tasks_text += f"    - {prob}\n"
        if e.get("runtime_error"):
            tasks_text += ("\n  MUST-FIX runtime traceback from actually calling run_baseline "
                           "(fix the code so this no longer occurs; do NOT invent methods/attrs "
                           "that the repo classes lack — check the provided source):\n")
            tasks_text += "\n".join("    " + ln for ln in e["runtime_error"].splitlines()) + "\n"

    system = (
        "You are a code editor. Produce minimal SEARCH/REPLACE edits that make each "
        "rival-baseline adapter run for real. Use the EXACT format and nothing else.\n"
        "Rules:\n"
        "- Each edit: `Filename: <relative path>` then a SEARCH block, ======= separator, "
        "REPLACE block, then the closing line.\n"
        "- SEARCH text must be an EXACT substring of the current file (whitespace matters).\n"
        "- For download adapters: import only modules reachable under the rival code root "
        "(already added to sys.path via _RIVAL_ROOT). Never invent module names.\n"
        "- For implement adapters: write a faithful implementation; reuse the repo's own "
        "dataset loader / model factory / metrics modules instead of duplicating them. If the "
        "paper detail is ambiguous, leave a TODO comment rather than fabricating numbers.\n"
        "- Keep edits surgical; do not rename existing functions/classes.\n"
        "- Whenever you add/modify a function, include or update its Doxygen block "
        "(## @brief / # @param / # @return) immediately before the def."
    )
    referenced_section = (
        f"\n## Exact source of repo modules your adapters import (call these REAL signatures)\n{referenced_src}\n----\n"
        if referenced_src else ""
    )
    user = f"""{patch_prompt}
{referenced_section}
## Current code repository
{codebase}

----

## Baselines to wire (still failing)
{tasks_text}

----

## Format example
Filename: src/baselines/nle.py
<<<<<<< SEARCH
    # TODO(c8.2-refine-download): call into _RIVAL_ROOT's real entry point.
    raise NotImplementedError("Stage 8.2 download-wrapper for nle not yet refined.")
=======
    from nle_runner import run as _run  # module under _RIVAL_ROOT
    return _run(dataset=dataset, **(config or {{}}))
>>>>>>> REPLACE

----

## Answer"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# Call the model for the patch text (no structured output). Returns (text, completion).
def call_patch(client: Any, gpt_version: str, messages: List[Dict[str, str]]) -> Tuple[str, Dict]:
    request: Dict[str, object] = {"model": gpt_version, "messages": messages}
    if "o3" in gpt_version or "o4" in gpt_version:
        request["reasoning_effort"] = "high"
    completion = create_with_retry(client, request)
    completion_json = json.loads(completion.model_dump_json())
    content = completion_json["choices"][0]["message"]["content"]
    return content, completion_json


# Apply SEARCH/REPLACE edits to files under debug_dir (copied from 8.1/4_debugging).
def parse_and_apply_changes(responses: List[str], debug_dir: str, save_num: int = 1) -> int:
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
            matches = re.findall(
                r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
                file_content_block,
                re.DOTALL,
            )
            if not matches:
                print(f"❌ No SEARCH/REPLACE patterns found for file: {filename}\n")
                continue
            if not os.path.exists(filepath):
                print(f"❌ File does not exist: {filepath}\n")
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    file_content = f.read()
            except OSError as e:
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
                    print(f"❌ {filename}: Search text for modification {idx} not found:\n{search_text[:200]}...\n")
            if modified:
                backup_path = f"{filepath}.{save_num:03d}.bak"
                try:
                    os.rename(filepath, backup_path)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(file_content)
                    print(f"💾 {filename}: saved. Backup: {backup_path}\n")
                    files_modified += 1
                except OSError as e:
                    print(f"❌ Error saving file {filepath}: {e}\n")
            else:
                print(f"ℹ️ {filename}: no modifications applied\n")
    return files_modified


# Read the current contents of every wired adapter, concatenated for the check prompt.
def serialize_adapters(repo_dir: str, wired_slugs: List[str]) -> str:
    baselines_dir = os.path.join(repo_dir, "src", "baselines")
    blocks = []
    for slug in sorted(wired_slugs):
        p = os.path.join(baselines_dir, f"{slug}.py")
        if os.path.isfile(p):
            blocks.append(f"```python\n## File: src/baselines/{slug}.py\n{Path(p).read_text(encoding='utf-8')}\n```")
    return "\n\n".join(blocks)


# Does the adapter file for `slug` still look like an unfilled stub?
def adapter_is_stub(repo_dir: str, slug: str) -> bool:
    p = os.path.join(repo_dir, "src", "baselines", f"{slug}.py")
    if not os.path.isfile(p):
        return True
    text = Path(p).read_text(encoding="utf-8")
    return STUB_SENTINEL in text and "NotImplementedError" in text


# Collect the top-level symbol names defined in a repo `src.*` module (or None
# if the module file can't be found). Used to validate adapter imports.
def _src_module_symbols(repo_dir: str, module: str) -> Optional[set]:
    import ast as _ast
    parts = module.split(".")
    for cand in (os.path.join(repo_dir, *parts) + ".py", os.path.join(repo_dir, *parts, "__init__.py")):
        if os.path.isfile(cand):
            try:
                tree = _ast.parse(Path(cand).read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                return set()
            names: set = set()
            for node in tree.body:
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, _ast.Assign):
                    for t in node.targets:
                        if isinstance(t, _ast.Name):
                            names.add(t.id)
                elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                    names.add(node.target.id)
                elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                    for alias in node.names:
                        names.add(alias.asname or alias.name.split(".")[0])
            return names
    return None


# AST-validate an adapter's `from src.X import Y` statements (anywhere in the
# file, including inside run_baseline) against the real repo. Returns a list of
# human-readable "unresolved import" messages — empty when all src imports resolve.
def audit_adapter_src_imports(repo_dir: str, slug: str) -> List[str]:
    import ast as _ast
    p = os.path.join(repo_dir, "src", "baselines", f"{slug}.py")
    if not os.path.isfile(p):
        return [f"{slug}: adapter file missing"]
    try:
        tree = _ast.parse(Path(p).read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{slug}: SYNTAX ERROR at line {exc.lineno}: {exc.msg}"]
    problems: List[str] = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ImportFrom):
            continue
        module = node.module or ""
        if not module.startswith("src.") and module != "src":
            continue
        symbols = _src_module_symbols(repo_dir, module)
        if symbols is None:
            problems.append(f"`from {module} import ...` — module not found in repo")
            continue
        for alias in node.names:
            if alias.name != "*" and alias.name not in symbols:
                problems.append(
                    f"`from {module} import {alias.name}` — '{alias.name}' is not defined in {module} "
                    f"(available: {', '.join(sorted(symbols)) or 'none'})"
                )
    return problems


# Exception classes that are ALWAYS environment/domain (never the adapter's bug).
_ENV_ERROR_NAMES = (
    "FileNotFoundError", "OSError", "IOError", "PermissionError", "IsADirectoryError",
    "NotADirectoryError", "ConnectionError", "ConnectionResetError", "TimeoutError",
    "BrokenPipeError", "MemoryError",
)
# Exception classes that are ALWAYS a fixable adapter bug, no matter the input —
# structural problems the adapter author introduced.
_STRUCTURAL_ERROR_NAMES = (
    "AttributeError", "NameError", "ImportError", "ModuleNotFoundError",
    "SyntaxError", "IndentationError", "NotImplementedError", "RecursionError",
    "UnboundLocalError", "ValidationError", "ConcretizationTypeError",
)
# Substrings that mark an acceptable environment/domain failure regardless of class.
_ENV_ERROR_MARKERS = (
    "cuda", "out of memory", "api key", "apikey", "unauthorized", "auth", "rate limit",
    "connection", "timed out", "no such file or directory", "permission denied",
    "no gpu", "could not find rival", "not downloaded", "hf_token", "huggingface",
    "is not a local folder", "valid model identifier", "offline", "max retries",
)


# Pick a representative dataset slug for the smoke run: prefer an eval_plan
# in_scope dataset that actually has downloaded data (largest byte count in the
# c1 summary), so the smoke reaches real adapter code instead of env-erroring on
# an absent/budget-skipped dataset. Falls back to the largest downloaded
# benchmark, then "toy". (Fix B)
def pick_smoke_dataset(asset_root: str, eval_plan_path: Optional[str] = None) -> str:
    in_scope: List[str] = []
    if eval_plan_path and os.path.exists(eval_plan_path):
        try:
            plan = json.loads(Path(eval_plan_path).read_text(encoding="utf-8"))
            for d in plan.get("datasets", []) or []:
                if d.get("in_scope", True):
                    name = (d.get("slug") or d.get("name") or "").strip()
                    if name:
                        in_scope.append(name)
        except (OSError, json.JSONDecodeError):
            pass

    bytes_by: Dict[str, int] = {}
    summary_path = os.path.join(asset_root, "c1_download_dataset_summary.json")
    if os.path.exists(summary_path):
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            for item in summary.get("items", []) or []:
                if item.get("category") == "benchmarks" and item.get("name"):
                    bytes_by[str(item["name"])] = int(item.get("bytes", 0) or 0)
        except (OSError, json.JSONDecodeError):
            pass

    # Rank in-scope eval_plan datasets by how much data was actually downloaded.
    ranked: List[Tuple[int, str]] = []
    for name in in_scope:
        best = 0
        for dl_name, nb in bytes_by.items():
            if slugify(dl_name) == slugify(name) or slugify(name) in slugify(dl_name) or slugify(dl_name) in slugify(name):
                best = max(best, nb)
        ranked.append((best, name))
    if ranked:
        ranked.sort(reverse=True)
        return ranked[0][1]
    if bytes_by:
        return max(bytes_by, key=lambda k: bytes_by[k])
    return "toy"


# Run one adapter's run_baseline() in a subprocess (repo venv if present) with a
# timeout, and classify the outcome: "ok" | "code_error" | "env_error" | "timeout".
# Uses the adapter's own smoke_args() (Fix A) when present so paper-specific input
# semantics are respected; otherwise falls back to `dataset`.
def smoke_run_adapter(repo_dir: str, slug: str, dataset: str, timeout: int = 90) -> Tuple[str, str]:
    import subprocess
    venv_py = os.path.join(repo_dir, ".venv", "bin", "python")
    py = venv_py if os.path.isfile(venv_py) else sys.executable
    code = (
        "import sys, traceback, importlib\n"
        f"ds, cfg = ({dataset!r}, {{'seed': 42}})\n"
        f"m = importlib.import_module('src.baselines.{slug}')\n"
        "if hasattr(m, 'smoke_args'):\n"
        "    try:\n"
        "        a = m.smoke_args()\n"
        "        if isinstance(a, (list, tuple)) and a:\n"
        "            if a[0]:\n"
        "                ds = a[0]; print('USED_SMOKE_ARGS')\n"
        "            if len(a) > 1 and isinstance(a[1], dict):\n"
        "                cfg = a[1]\n"
        "    except Exception:\n"
        "        pass\n"
        "from src.baselines import run_baseline_for\n"
        "try:\n"
        f"    run_baseline_for('{slug}', ds, cfg)\n"
        "    print('SMOKE_OK')\n"
        "except BaseException:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(7)\n"
    )
    env = dict(os.environ, PYTHONPATH=repo_dir)
    try:
        proc = subprocess.run([py, "-c", code], cwd=repo_dir, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", "smoke run exceeded timeout (reached real code)"
    if proc.returncode == 0 and "SMOKE_OK" in proc.stdout:
        return "ok", "ran to completion"
    # Did the adapter's own smoke_args() supply the input? If so, the adapter
    # vouched the input is valid, so ANY non-environment error is its bug
    # (including ValueError). If we fell back to the auto-picked dataset, only
    # structural errors count — a ValueError is likely a correct input rejection.
    used_smoke = "USED_SMOKE_ARGS" in proc.stdout
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
    detail = "\n".join(tail)
    low = detail.lower()
    exc_line = next((ln for ln in reversed(tail) if ln.strip() and ":" in ln), "")
    exc_name = exc_line.split(":", 1)[0].split(".")[-1].strip()
    if exc_name in _ENV_ERROR_NAMES or any(m in low for m in _ENV_ERROR_MARKERS):
        return "env_error", detail
    if exc_name in _STRUCTURAL_ERROR_NAMES:
        return "code_error", detail
    # Signature-mismatch TypeErrors are structural bugs regardless of input.
    if exc_name == "TypeError" and any(
        s in low for s in ("unexpected keyword argument", "positional argument",
                           "takes no", "missing", "got multiple values")
    ):
        return "code_error", detail
    # Input/domain errors (ValueError, KeyError, RuntimeError, AssertionError, …):
    # only a bug if the adapter's own smoke_args() produced the input.
    if exc_name.endswith("Error") and used_smoke:
        return "code_error", detail
    return "env_error", detail


# ============================================================
# CLI + main
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 8.2 — wire rival baselines into the generated repo so it can run comparisons."
    )
    parser.add_argument("--paper_name", type=str, required=True)
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument("--output_repo_dir", type=str, required=True,
                        help="The generated codebase to wire baselines into.")
    parser.add_argument("--output_dir", type=str, required=True,
                        help="Where stage_8.2_history.json + artifacts are written.")
    parser.add_argument("--eval_plan_path", type=str, default=None,
                        help="Defaults to tests/harbor/yantingchi/<paper>/eval_plan/eval_plan.json")
    parser.add_argument("--asset_root", type=str, default=None,
                        help="Defaults to tests/harbor/yantingchi/<paper>/harbor/asset")
    parser.add_argument("--max_iterations", type=int, default=5)
    parser.add_argument("--save_num_start", type=int, default=200,
                        help="Backup index for iter 0 (default 200, leaving 100-199 for 8.1).")
    parser.add_argument("--dry_run", action="store_true",
                        help="Discovery + scaffold only; skip the LLM check/patch loop.")
    parser.add_argument("--no_smoke", action="store_true",
                        help="Skip the runtime smoke check (don't actually call run_baseline).")
    return parser.parse_args()


# Resolve the default harbor paths from the repo root + paper name.
def resolve_default_paths(args: argparse.Namespace) -> Tuple[str, str]:
    project_root = Path(__file__).resolve().parent.parent
    harbor = project_root / "tests" / "harbor" / "yantingchi" / args.paper_name
    eval_plan = args.eval_plan_path or str(harbor / "eval_plan" / "eval_plan.json")
    asset_root = args.asset_root or str(harbor / "harbor" / "asset")
    return eval_plan, asset_root


def main() -> None:
    args = parse_args()
    eval_plan_path, asset_root = resolve_default_paths(args)

    if not os.path.isdir(args.output_repo_dir):
        die(f"output_repo_dir not found: {args.output_repo_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    # ---- Step 1: discovery ----
    manifest = build_manifest(eval_plan_path, asset_root)
    by_bucket: Dict[str, List[str]] = {}
    for e in manifest["baselines"]:
        by_bucket.setdefault(e["bucket"], []).append(e["slug"])
    print("=" * 60)
    print(f"[8.2] {args.paper_name}: classified {len(manifest['baselines'])} baselines")
    for bucket in (BUCKET_DOWNLOAD, BUCKET_IMPLEMENT, BUCKET_REFERENCE, BUCKET_SKIP):
        slugs = by_bucket.get(bucket, [])
        print(f"  {bucket:14s}: {len(slugs)}  {slugs}")

    # ---- Step 2: scaffold ----
    scaffold = scaffold_adapters(args.output_repo_dir, manifest)
    wired_slugs = scaffold["wired_slugs"]
    print(f"[8.2] scaffolded {len(scaffold['written'])} adapter(s); "
          f"kept {len(scaffold['skipped_existing'])} already-filled; "
          f"registry lists {len(wired_slugs)} slug(s).")

    artifacts_dir = os.path.join(args.output_dir, "stage_8.2_artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)

    pending = [e for e in manifest["baselines"] if e["bucket"] in (BUCKET_DOWNLOAD, BUCKET_IMPLEMENT)]

    iterations: List[Dict[str, Any]] = []
    total_cost = load_accumulated_cost(f"{args.output_dir}/accumulated_cost.json")

    if args.dry_run or not pending:
        print("[8.2] dry_run or nothing to wire — skipping LLM loop.")
    else:
        client = create_openai_client()
        check_prompt = load_prompt(CHECK_PROMPT_PATH)
        patch_prompt = load_prompt(PATCH_PROMPT_PATH)
        smoke_dataset = pick_smoke_dataset(asset_root, eval_plan_path)
        print(f"[8.2] smoke dataset for runtime checks: {smoke_dataset!r}"
              + (" (smoke disabled)" if args.no_smoke else ""))

        for iter_idx in range(args.max_iterations):
            print("=" * 60)
            print(f"[8.2] iteration {iter_idx} / {args.max_iterations - 1}")

            adapters_text = serialize_adapters(args.output_repo_dir, wired_slugs)
            check_messages = build_check_messages(check_prompt, pending, adapters_text)
            stage = f"[8.2][CHECK iter={iter_idx}] {args.paper_name}"
            check_result, check_completion = call_check(client, args.gpt_version, check_messages)
            print_response(check_completion)
            total_cost = print_log_cost(check_completion, args.gpt_version, stage, args.output_dir, total_cost)

            results = check_result.get("results", [])
            failed_slugs = [r["slug"] for r in results if r.get("status") == "fail"]
            # Defensive: also treat any slug still matching the stub sentinel as failing.
            for slug in wired_slugs:
                if slug not in failed_slugs and adapter_is_stub(args.output_repo_dir, slug):
                    failed_slugs.append(slug)
            # Deterministic gate: any adapter with an unresolved `src.*` import is
            # failing regardless of what the LLM reviewer thought — and we keep the
            # exact errors to feed back into the patch prompt.
            import_problems: Dict[str, List[str]] = {}
            for slug in wired_slugs:
                probs = audit_adapter_src_imports(args.output_repo_dir, slug)
                if probs:
                    import_problems[slug] = probs
                    if slug not in failed_slugs:
                        failed_slugs.append(slug)

            # Runtime smoke: actually call run_baseline() and feed CODE-level
            # tracebacks (AttributeError/TypeError/etc.) back to the patch step.
            # Env/domain failures (missing dataset, CUDA, API auth) are accepted.
            runtime_errors: Dict[str, str] = {}
            if not args.no_smoke:
                for slug in wired_slugs:
                    if adapter_is_stub(args.output_repo_dir, slug) or slug in import_problems:
                        continue  # no point smoking a known-broken adapter
                    status, detail = smoke_run_adapter(args.output_repo_dir, slug, smoke_dataset)
                    if status == "code_error":
                        runtime_errors[slug] = detail
                        if slug not in failed_slugs:
                            failed_slugs.append(slug)
                    print(f"[8.2]   smoke {slug}: {status}")

            Path(os.path.join(artifacts_dir, f"iter_{iter_idx:03d}_check.json")).write_text(
                json.dumps({"llm": check_result, "import_problems": import_problems,
                            "runtime_errors": runtime_errors}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            iterations.append({"iter": iter_idx, "failed_slugs": failed_slugs,
                               "import_problems": import_problems,
                               "runtime_errors": list(runtime_errors.keys())})
            print(f"[8.2] iter {iter_idx}: {len(failed_slugs)} baseline(s) still failing "
                  f"({len(import_problems)} unresolved imports, {len(runtime_errors)} runtime code errors)")

            if not failed_slugs:
                print("[8.2] all wired baselines implemented. Stopping.")
                break

            failing = []
            for e in pending:
                if e["slug"] in failed_slugs:
                    e = dict(e)
                    e["import_problems"] = import_problems.get(e["slug"], [])
                    e["runtime_error"] = runtime_errors.get(e["slug"], "")
                    failing.append(e)
            # Use a focused, size-capped view (full repo serialization blows the
            # model's context window when the repo vendors a rival tree).
            codebase = build_focused_codebase(args.output_repo_dir)
            referenced_src = collect_referenced_src_sources(args.output_repo_dir, failing)
            patch_messages = build_patch_messages(patch_prompt, failing, codebase, referenced_src)
            stage = f"[8.2][PATCH iter={iter_idx}] {args.paper_name}"
            patch_text, patch_completion = call_patch(client, args.gpt_version, patch_messages)
            total_cost = print_log_cost(patch_completion, args.gpt_version, stage, args.output_dir, total_cost)

            Path(os.path.join(artifacts_dir, f"iter_{iter_idx:03d}_patches.txt")).write_text(
                patch_text, encoding="utf-8"
            )
            save_num = args.save_num_start + iter_idx
            files_modified = parse_and_apply_changes([patch_text], args.output_repo_dir, save_num=save_num)
            if files_modified == 0:
                print(f"⚠️ [8.2] no files modified at iter {iter_idx}; stopping.")
                break

    # ---- Step 3: audit + history ----
    final_status: Dict[str, List[str]] = {
        "wired_download": [], "wired_implement": [], "reference_only": [],
        "skipped_oversize": [], "skipped_no_asset": [], "still_failing": [],
    }
    reference_values: Dict[str, str] = {}
    for e in manifest["baselines"]:
        slug, bucket = e["slug"], e["bucket"]
        if bucket == BUCKET_REFERENCE:
            final_status["reference_only"].append(slug)
            reference_values[slug] = e.get("notes", "")
        elif bucket == BUCKET_SKIP:
            key = "skipped_no_asset" if e.get("skip_reason") == "download_missing" else "skipped_oversize"
            final_status[key].append(slug)
        elif bucket in (BUCKET_DOWNLOAD, BUCKET_IMPLEMENT):
            # "Wired" requires: not a stub, all src.* imports resolve, AND a final
            # smoke run reaches real code (no AttributeError/Validation/etc. bug).
            failing_now = bool(adapter_is_stub(args.output_repo_dir, slug)
                               or audit_adapter_src_imports(args.output_repo_dir, slug))
            if not failing_now and not args.no_smoke:
                status, _detail = smoke_run_adapter(
                    args.output_repo_dir, slug, pick_smoke_dataset(asset_root, eval_plan_path))
                failing_now = status == "code_error"
            if failing_now:
                final_status["still_failing"].append(slug)
            else:
                final_status["wired_download" if bucket == BUCKET_DOWNLOAD else "wired_implement"].append(slug)

    history = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "eval_plan_path": manifest["eval_plan_path"],
        "asset_root": manifest["asset_root"],
        "max_iterations": args.max_iterations,
        "dry_run": bool(args.dry_run),
        "final_status": final_status,
        "reference_values": reference_values,
        "iterations": iterations,
        "accumulated_cost": total_cost,
    }
    history_path = os.path.join(args.output_dir, "stage_8.2_history.json")
    Path(history_path).write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    save_accumulated_cost(f"{args.output_dir}/accumulated_cost.json", total_cost)

    print("=" * 60)
    print(f"[8.2] {args.paper_name} summary")
    for key, slugs in final_status.items():
        print(f"  {key:18s}: {len(slugs)}  {slugs}")
    print(f"  history: {history_path}")
    print("=" * 60)

    # In dry_run we deliberately leave stubs in place, so don't treat them as failures.
    if final_status["still_failing"] and not args.dry_run:
        die("some in-scope baselines remain unimplemented after max_iterations: "
            + ", ".join(final_status["still_failing"]))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 — top-level guard prints a clear message
        print(f"[8.2_wire_baselines] ERROR: {error}", file=sys.stderr)
        sys.exit(1)
