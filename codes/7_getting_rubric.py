import argparse
import json
import os
import re
import sys
from typing import Dict, List, Sequence, Tuple

from openai_client import create_openai_client
from utils import get_now_str, num_tokens_from_messages, print_log_cost


BASE_RUBRIC_PROMPT = """Problem:
\t You are given a paper and its method description.
\t What your goal is to generate a rubric to check if a code base genuinely reproduce the paper result.
Input:
\t Paper format as md
\t tables and figure descriptions in paper
\t Paper Method description
\t Paper Evaluation Description contains benchmark info, hyperparameter info, and training related info

Output: A rubric file json file with the following schema

{
  "paper_title": "",
  "core_contributions": [
    {
      "name": "",
      "description": "",
      "anchor": "Section / Subsection / Equation / Table / Figure / Appendix",
      "confidence": 0.0,
      "verification_hint": "How a reproduction would empirically check this contribution. If the contribution is purely theoretical and is not exercised by any figure/table, write exactly 'theoretical_only: implementation check only'."
    }
  ],
  "assets": [
    {
      "name": "",
      "type": "dataset|model|checkpoint|environment|library|repository|encoder|benchmark",
      "action": "obtain|prepare|modify|configure",
      "details": "",
      "anchor": "",
      "required": true
    }
  ],
  "methods": [
    {
      "name": "",
      "kind": "algorithm|subroutine|loss|training_loop|inference_procedure|metric|parser|baseline",
      "details": "",
      "anchor": "",
      "dependencies": []
    }
  ],
  "hyperparameters": [
    {
      "name": "",
      "value": "",
      "scope": "",
      "anchor": "",
      "required_for_reproduction": true
    }
  ],
  "results_to_verify": [
    {
      "target": "Figure|Table|explicit claim",
      "description": "",
      "anchor": "",
      "prerequisites": [],
      "expected_outcome": "Concrete, comparator-bearing trend the paper claims, e.g. 'BaM forward-KL trajectory lies below ADVI/Score/Fisher/GSM (B=2) at the same gradient-evaluation budget on D in {4,16,64,256}, averaged over 10 seeds'. Do NOT invent numeric thresholds the paper did not publish.",
      "comparison_protocol": "How the new code's output is compared to the paper's reported outcome, e.g. 'overlay mean-over-10-seeds curves; check ordering at matched gradient-eval budget'."
    }
  ],
  "uncertain_or_missing": [
    {
      "item": "",
      "reason": ""
    }
  ]
}

Rules:
1. Extract only items that are necessary to reproduce the paper’s core contributions.
2. Ignore generic motivation, related work, and future work unless they define a required baseline.
3. Prefer appendix details when they are more precise than main text.
4. Do not invent missing hyperparameters or implementation details.
5. If a detail seems necessary but is not explicitly specified, put it in "uncertain_or_missing".
6. Split methods into atomic components when the paper itself decomposes them.
7. Keep anchors precise.
"""

EVAL_DESCRIPTION_REQUIREMENTS = """Evaluation description requirements:
- Evaluation Set-up:
  - hardware: CPU/GPU/TPU details if available.
  - machine: machine/server model, cloud type, or cluster details if available.
  - docker_requirements: what is needed to run in Docker (base image, CUDA/toolkit, runtime constraints) if available.
  - other_runtime_requirements: any additional runtime requirements needed for evaluation.
- Benchmark evaluated on:
  - list datasets/benchmarks/tasks used by this paper.
- Training:
  - is_training_required: whether a new model needs training.
  - hyperparameters: training hyperparameters (LR, batch size, epochs, etc.).
  - random_seed: any seed settings.
- Results:
  - metrics_used: metric names.
  - statistical_results: numeric/statistical outcomes reported for this work.
  - figure_results: findings tied to figures/tables for this work.
- Expected Results:
  - summarize expected reproduction outcomes exactly as stated in the paper text.
- Exclude comparisons against external baselines unless needed to explain this paper's own reported value.
"""

METHOD_SECTION_KEYWORDS = (
    "method",
    "approach",
    "model",
    "algorithm",
    "framework",
    "architecture",
    "implementation",
    "training",
    "optimization",
    "proposed",
)

METHOD_SECTION_EXCLUDE_KEYWORDS = (
    "related work",
    "background",
    "preliminar",
    "experiment",
    "evaluation",
    "result",
    "discussion",
    "conclusion",
    "limitation",
    "broader impact",
)

RUBRIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "paper_title": {"type": "string"},
        "core_contributions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "anchor": {"type": "string"},
                    "confidence": {"type": "number"},
                    # verification_hint: how a reproduction would empirically check this
                    # contribution. Empty or "theoretical_only: implementation check only"
                    # signals that Pass 2 should NOT create a Result-Analysis leaf for it.
                    "verification_hint": {"type": "string"},
                },
                "required": [
                    "name",
                    "description",
                    "anchor",
                    "confidence",
                    "verification_hint",
                ],
            },
        },
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "dataset",
                            "model",
                            "checkpoint",
                            "environment",
                            "library",
                            "repository",
                            "encoder",
                            "benchmark",
                        ],
                    },
                    "action": {
                        "type": "string",
                        "enum": ["obtain", "prepare", "modify", "configure"],
                    },
                    "details": {"type": "string"},
                    "anchor": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["name", "type", "action", "details", "anchor", "required"],
            },
        },
        "methods": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "algorithm",
                            "subroutine",
                            "loss",
                            "training_loop",
                            "inference_procedure",
                            "metric",
                            "parser",
                            "baseline",
                        ],
                    },
                    "details": {"type": "string"},
                    "anchor": {"type": "string"},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "kind", "details", "anchor", "dependencies"],
            },
        },
        "hyperparameters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "scope": {"type": "string"},
                    "anchor": {"type": "string"},
                    "required_for_reproduction": {"type": "boolean"},
                },
                "required": [
                    "name",
                    "value",
                    "scope",
                    "anchor",
                    "required_for_reproduction",
                ],
            },
        },
        "results_to_verify": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target": {"type": "string"},
                    "description": {"type": "string"},
                    "anchor": {"type": "string"},
                    "prerequisites": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    # expected_outcome: comparator-bearing qualitative trend the paper
                    # claims (metric, named baselines, conditions, direction). No
                    # invented numeric thresholds.
                    "expected_outcome": {"type": "string"},
                    # comparison_protocol: how the new code's output is compared against
                    # the paper's reported outcome (averaging, ordering, matched budget).
                    "comparison_protocol": {"type": "string"},
                },
                "required": [
                    "target",
                    "description",
                    "anchor",
                    "prerequisites",
                    "expected_outcome",
                    "comparison_protocol",
                ],
            },
        },
        "uncertain_or_missing": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "item": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["item", "reason"],
            },
        },
    },
    "required": [
        "paper_title",
        "core_contributions",
        "assets",
        "methods",
        "hyperparameters",
        "results_to_verify",
        "uncertain_or_missing",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a reproduction rubric from the paper, figure/table descriptions, "
            "and method-related paper sections."
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
    parser.add_argument(
        "--max_paper_chars",
        type=int,
        default=140000,
        help="Maximum characters from markdown-style paper context.",
    )
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
    parser.add_argument(
        "--max_method_chars",
        type=int,
        default=50000,
        help="Maximum characters from method-specific paper context.",
    )

    parser.add_argument(
        "--max_ref_chars",
        type=int,
        default=40000,
        help="Maximum characters from figure/table descriptions.",
    )
    parser.add_argument(
        "--download_report_path",
        type=str,
        default="",
        help=(
            "Optional path to the download report produced by 6_download_dataset.py. "
            "Accepts either the human-readable download_report.md or the structured "
            "c1_download_dataset_summary.json. When provided, the rubric model uses it "
            "to ground 'results_to_verify' prerequisites in assets that actually exist."
        ),
    )
    parser.add_argument(
        "--max_download_report_chars",
        type=int,
        default=20000,
        help="Maximum characters from the download report context.",
    )
    parser.add_argument(
        "--eval_plan_path",
        type=str,
        default="",
        help=(
            "Optional path to eval_plan.json produced by '5.1 get_evaluation_plan.py'. "
            "When provided, results_to_verify is derived only from this hardware-feasible "
            "plan instead of all results in the paper."
        ),
    )
    parser.add_argument(
        "--max_eval_plan_chars",
        type=int,
        default=20000,
        help="Maximum characters from the eval plan context.",
    )
    parser.add_argument(
        "--eval_result_dir",
        type=str,
        default="",
        help="Optional output directory for rubric artifacts and logs.",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="",
        help="Optional explicit output json path or output directory.",
    )
    parser.add_argument(
        "--save_raw_completion",
        action="store_true",
        help="Save raw model response json in the output file.",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate_text(text: str, max_chars: int, marker: str) -> Tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    truncated = text[:max_chars].rstrip()
    truncated += f"\n...[TRUNCATED {marker}]..."
    return truncated, True


def build_section_heading(section: str, sec_num: str) -> str:
    cleaned_section = normalize_text(section) or "Untitled Section"
    cleaned_sec_num = normalize_text(sec_num)
    if cleaned_sec_num:
        return f"{cleaned_sec_num} {cleaned_section}"
    return cleaned_section


def group_section_entries(entries: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped_sections: List[Dict[str, object]] = []
    current_key: Tuple[str, str] | None = None
    current_section: Dict[str, object] | None = None

    for entry in entries:
        text = normalize_text(str(entry.get("text", "")))
        if not text:
            continue

        section = str(entry.get("section", "") or "")
        sec_num = str(entry.get("sec_num", "") or "")
        key = (section, sec_num)

        if key != current_key:
            current_key = key
            current_section = {
                "section": section,
                "sec_num": sec_num,
                "heading": build_section_heading(section, sec_num),
                "paragraphs": [],
            }
            grouped_sections.append(current_section)

        assert current_section is not None
        paragraphs = current_section["paragraphs"]
        assert isinstance(paragraphs, list)
        paragraphs.append(text)

    return grouped_sections


def render_grouped_sections(grouped_sections: Sequence[Dict[str, object]]) -> str:
    blocks: List[str] = []
    for section in grouped_sections:
        heading = str(section["heading"])
        paragraphs = section["paragraphs"]
        assert isinstance(paragraphs, list)
        block = [f"## {heading}"]
        block.extend(str(paragraph) for paragraph in paragraphs)
        blocks.append("\n\n".join(block))
    return "\n\n".join(blocks).strip()


def is_method_section_heading(heading: str) -> bool:
    lowered = heading.lower()
    if any(keyword in lowered for keyword in METHOD_SECTION_EXCLUDE_KEYWORDS):
        return False
    return any(keyword in lowered for keyword in METHOD_SECTION_KEYWORDS)


def extract_method_sections(grouped_sections: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    method_sections = [
        section
        for section in grouped_sections
        if is_method_section_heading(str(section["heading"]))
    ]
    if method_sections:
        return method_sections

    return list(grouped_sections[: min(6, len(grouped_sections))])


def render_ref_entries(ref_entries: Dict[str, object]) -> Tuple[str, int]:
    rendered_entries: List[str] = []

    for ref_key in sorted(ref_entries.keys()):
        entry = ref_entries.get(ref_key, {})
        if not isinstance(entry, dict):
            continue

        ref_type = normalize_text(str(entry.get("type_str", ""))).lower()
        if ref_type not in {"figure", "table"}:
            continue

        raw_text = normalize_text(str(entry.get("text", "")))
        if not raw_text:
            continue

        label = ref_type.title()
        fig_num = normalize_text(str(entry.get("fig_num", "") or entry.get("num", "") or ""))
        prefix = f"{label} {fig_num}:"
        if raw_text.lower().startswith(label.lower()):
            rendered_entries.append(raw_text)
        else:
            rendered_entries.append(f"{prefix} {raw_text}".strip())

    return "\n".join(rendered_entries).strip(), len(rendered_entries)


def extract_abstract_text(paper_obj: Dict[str, object]) -> str:
    abstract = paper_obj.get("abstract", "")
    if isinstance(abstract, str):
        return normalize_text(abstract)
    if isinstance(abstract, list):
        parts = []
        for item in abstract:
            if isinstance(item, dict):
                parts.append(normalize_text(str(item.get("text", ""))))
            else:
                parts.append(normalize_text(str(item)))
        return "\n".join(part for part in parts if part).strip()
    return normalize_text(str(abstract))


def load_json_paper_context(
    args: argparse.Namespace,
) -> Tuple[Dict[str, str], Dict[str, object]]:
    if len(args.pdf_json_path.strip()) == 0:
        raise ValueError("`--pdf_json_path` is required when paper_format is JSON.")

    with open(args.pdf_json_path, "r", encoding="utf-8") as f:
        paper_obj = json.load(f)

    pdf_parse = paper_obj.get("pdf_parse", {})
    if not isinstance(pdf_parse, dict):
        pdf_parse = {}

    body_text = pdf_parse.get("body_text", [])
    back_matter = pdf_parse.get("back_matter", [])
    ref_entries = pdf_parse.get("ref_entries", {})
    if not isinstance(body_text, list):
        body_text = []
    if not isinstance(back_matter, list):
        back_matter = []
    if not isinstance(ref_entries, dict):
        ref_entries = {}

    grouped_sections = group_section_entries(list(body_text) + list(back_matter))
    method_sections = extract_method_sections(grouped_sections)
    figure_table_text, ref_count = render_ref_entries(ref_entries)

    title = normalize_text(str(paper_obj.get("title", "") or paper_obj.get("paper_id", "") or args.paper_name))
    abstract_text = extract_abstract_text(paper_obj)

    markdown_parts: List[str] = [f"# {title}"]
    if abstract_text:
        markdown_parts.append(f"## Abstract\n\n{abstract_text}")

    rendered_sections = render_grouped_sections(grouped_sections)
    if rendered_sections:
        markdown_parts.append(rendered_sections)

    paper_markdown, paper_truncated = truncate_text(
        "\n\n".join(markdown_parts).strip(),
        args.max_paper_chars,
        "PAPER MARKDOWN",
    )
    method_description, method_truncated = truncate_text(
        render_grouped_sections(method_sections),
        args.max_method_chars,
        "METHOD CONTEXT",
    )
    figure_table_descriptions, refs_truncated = truncate_text(
        figure_table_text,
        args.max_ref_chars,
        "FIGURE/TABLE CONTEXT",
    )

    context_blocks = {
        "paper_markdown": paper_markdown,
        "method_description": method_description or "Not identified from section headings.",
        "figure_table_descriptions": figure_table_descriptions or "No figure/table descriptions found.",
    }
    meta = {
        "source_path": args.pdf_json_path,
        "paper_format": args.paper_format,
        "paper_title": title,
        "section_count": len(grouped_sections),
        "method_section_count": len(method_sections),
        "figure_table_count": ref_count,
        "paper_markdown_chars": len(paper_markdown),
        "method_chars": len(context_blocks["method_description"]),
        "figure_table_chars": len(context_blocks["figure_table_descriptions"]),
        "paper_markdown_truncated": paper_truncated,
        "method_truncated": method_truncated,
        "figure_table_truncated": refs_truncated,
    }
    return context_blocks, meta


def load_latex_paper_context(
    args: argparse.Namespace,
) -> Tuple[Dict[str, str], Dict[str, object]]:
    if len(args.pdf_latex_path.strip()) == 0:
        raise ValueError("`--pdf_latex_path` is required when paper_format is LaTeX.")

    with open(args.pdf_latex_path, "r", encoding="utf-8") as f:
        latex_text = f.read()

    paper_markdown, paper_truncated = truncate_text(
        latex_text,
        args.max_paper_chars,
        "PAPER LATEX",
    )

    method_matches = re.findall(
        r"\\(?:sub)*section\{([^}]*(?:method|approach|implementation|training|algorithm|framework)[^}]*)\}(.*?)(?=\\(?:sub)*section\{|\\appendix|\Z)",
        latex_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    method_blocks = [
        f"## {normalize_text(title)}\n\n{normalize_text(content)}"
        for title, content in method_matches
    ]
    method_description, method_truncated = truncate_text(
        "\n\n".join(method_blocks).strip() or normalize_text(latex_text),
        args.max_method_chars,
        "METHOD CONTEXT",
    )

    caption_matches = re.findall(
        r"\\caption\{([^}]*)\}",
        latex_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    figure_table_descriptions, refs_truncated = truncate_text(
        "\n".join(normalize_text(match) for match in caption_matches if normalize_text(match)),
        args.max_ref_chars,
        "FIGURE/TABLE CONTEXT",
    )

    context_blocks = {
        "paper_markdown": paper_markdown,
        "method_description": method_description or "Not identified from LaTeX section headings.",
        "figure_table_descriptions": figure_table_descriptions or "No figure/table descriptions found.",
    }
    meta = {
        "source_path": args.pdf_latex_path,
        "paper_format": args.paper_format,
        "section_count": len(re.findall(r"\\(?:sub)*section\{", latex_text)),
        "method_section_count": len(method_matches),
        "figure_table_count": len(caption_matches),
        "paper_markdown_chars": len(paper_markdown),
        "method_chars": len(context_blocks["method_description"]),
        "figure_table_chars": len(context_blocks["figure_table_descriptions"]),
        "paper_markdown_truncated": paper_truncated,
        "method_truncated": method_truncated,
        "figure_table_truncated": refs_truncated,
    }
    return context_blocks, meta


def load_paper_context(
    args: argparse.Namespace,
) -> Tuple[Dict[str, str], Dict[str, object]]:
    if args.paper_format == "JSON":
        return load_json_paper_context(args)
    return load_latex_paper_context(args)


def summarize_download_summary_json(payload: Dict[str, object]) -> str:
    # Render c1_download_dataset_summary.json as a compact markdown table the LLM
    # can read alongside the paper. We keep only fields useful for grounding
    # results_to_verify prerequisites: category, name, fetch_method, size, source_url.
    lines: List[str] = []
    output_dir = str(payload.get("output_dir", "")).strip()
    if output_dir:
        lines.append(f"- Asset root: `{output_dir}`")

    budget = payload.get("budget", {})
    if isinstance(budget, dict) and budget.get("max_gb") is not None:
        lines.append(
            f"- Budget: {budget.get('max_gb')} GB "
            f"(used: {budget.get('used_human', '?')}, "
            f"remaining: {budget.get('remaining_human', '?')})"
        )

    items = payload.get("items", [])
    if isinstance(items, list) and items:
        lines.append("")
        lines.append("## Items")
        lines.append("")
        lines.append("| category | name | fetch_method | bytes | source |")
        lines.append("|---|---|---|---:|---|")
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(
                "| {category} | {name} | {fetch_method} | {bytes_human} | {source_url} |".format(
                    category=str(item.get("category", "")),
                    name=str(item.get("name", "")),
                    fetch_method=str(item.get("fetch_method", "")) or "?",
                    bytes_human=str(item.get("bytes_human", "")) or "?",
                    source_url=str(item.get("source_url", "")) or "",
                )
            )

    expected = payload.get("expected_baselines", {})
    if isinstance(expected, dict) and expected.get("total"):
        lines.append("")
        lines.append(
            f"## Expected baselines: {expected.get('present', 0)}/{expected.get('total', 0)} present"
        )
        missing = expected.get("missing_slugs", [])
        if isinstance(missing, list) and missing:
            lines.append(f"- Missing slugs: {', '.join(str(s) for s in missing)}")

    return "\n".join(lines).strip()


def load_download_report(
    path: str, max_chars: int
) -> Tuple[str, Dict[str, object]]:
    # Returns (rendered_text, meta). When the path is empty or the file is missing,
    # rendered_text is "" and meta records that nothing was loaded. We accept either
    # a markdown report or the c1_download_dataset_summary.json produced by
    # 6_download_dataset.py — JSON gets reduced to a compact markdown table so the
    # rubric model only sees grounding info (category, name, fetch_method, size).
    raw_path = path.strip()
    if not raw_path:
        return "", {"loaded": False}

    candidate = os.path.expanduser(raw_path)
    if not os.path.isfile(candidate):
        return "", {
            "loaded": False,
            "source_path": candidate,
            "error": "download_report file not found",
        }

    with open(candidate, "r", encoding="utf-8") as f:
        raw_text = f.read()

    rendered = raw_text
    is_json = candidate.lower().endswith(".json")
    if is_json:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                summary = summarize_download_summary_json(parsed)
                rendered = summary or json.dumps(parsed, indent=2, ensure_ascii=False)
            else:
                rendered = json.dumps(parsed, indent=2, ensure_ascii=False)
        except Exception:
            # Fall through with raw_text — still useful as plain text grounding.
            rendered = raw_text

    rendered_truncated, was_truncated = truncate_text(
        rendered.strip(), max_chars, "DOWNLOAD REPORT"
    )

    return rendered_truncated, {
        "loaded": True,
        "source_path": candidate,
        "format": "json" if is_json else "text",
        "chars": len(rendered_truncated),
        "truncated": was_truncated,
    }


def load_eval_plan(path: str, max_chars: int) -> Tuple[str, Dict[str, object]]:
    raw_path = path.strip()
    if not raw_path:
        return "", {"loaded": False}

    candidate = os.path.expanduser(raw_path)
    if not os.path.isfile(candidate):
        return "", {
            "loaded": False,
            "source_path": candidate,
            "error": "eval_plan file not found",
        }

    with open(candidate, "r", encoding="utf-8") as f:
        raw_text = f.read()

    try:
        parsed = json.loads(raw_text)
        rendered = json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        rendered = raw_text

    rendered_truncated, was_truncated = truncate_text(
        rendered.strip(), max_chars, "EVAL PLAN"
    )

    return rendered_truncated, {
        "loaded": True,
        "source_path": candidate,
        "chars": len(rendered_truncated),
        "truncated": was_truncated,
    }


def build_messages(
    context_blocks: Dict[str, str],
    paper_meta: Dict[str, object],
) -> List[Dict[str, str]]:
    system_prompt = """You are an expert research-paper analyst and reproducibility evaluator.

Generate a rubric that can be used to check whether a code base genuinely reproduces
the paper's core contributions and claimed results.

Rules:
- Use only the provided context.
- Prefer precise anchors from section names, equations, figures, tables, and appendix labels.
- Use the paper markdown, figure/table descriptions, and method description together.
- Recover the paper evaluation description from the paper content itself, covering benchmarks, training, hyperparameters, metrics, and expected results.
- If a detail appears necessary but is not explicitly specified, put it in uncertain_or_missing.
- When a download_report is provided, treat it as the authoritative inventory of assets
  that physically exist for this paper. Use it to populate `results_to_verify[].prerequisites`
  with concrete asset names that appear in the report (benchmarks, rivals/baselines, metrics).
  Do not invent prerequisites that have no matching entry in the report when one is provided.
- When an eval_plan is provided, treat it as the authoritative, hardware-feasible list of
  experiments. Restrict `results_to_verify` entries to only those experiments, benchmarks,
  and baselines that appear in the eval_plan. Do not include results that require more
  GPU VRAM or dataset size than the constraints allow.
- For each `results_to_verify[]` entry, `expected_outcome` must state (a) the metric,
  (b) the named baselines/comparators, (c) the experiment conditions (dataset, dimensions,
  batch size, seeds, budget), and (d) the qualitative trend the paper claims
  (e.g., "BaM forward-KL trajectory lies below ADVI/Score/Fisher/GSM at the same
  gradient-evaluation budget on D in {4,16,64,256}"). `comparison_protocol` must state
  how the new code's output is compared to the paper's reported outcome (e.g., "overlay
  mean-over-10-seeds curves; check ordering at matched gradient-eval budget").
  Do NOT invent numeric thresholds the paper did not publish.
- For each `core_contributions[]` entry, fill `verification_hint` with how a reproduction
  would empirically check the contribution. If the contribution is purely theoretical and
  not exercised by any figure or table, write exactly
  "theoretical_only: implementation check only" so Pass 2 knows not to create a
  Result-Analysis leaf for it.
- Return only valid JSON matching the required schema.
- Do not output chain-of-thought.
"""

    download_report_block = context_blocks.get("download_report", "").strip()
    download_report_section = (
        f"\n<download_report>\n{download_report_block}\n</download_report>\n"
        if download_report_block
        else ""
    )

    eval_plan_block = context_blocks.get("eval_plan", "").strip()
    eval_plan_section = (
        f"\n<eval_plan>\n{eval_plan_block}\n</eval_plan>\n"
        if eval_plan_block
        else ""
    )

    prerequisites_guidance = (
        "- Each `results_to_verify[].prerequisites` entry should be an asset name "
        "(benchmark, rival/baseline, or metric) drawn from the download_report when available. "
        "If no download_report is provided, fall back to the paper text and mark anything "
        "uncertain in `uncertain_or_missing`.\n"
    )

    results_to_verify_guidance = (
        "- An `eval_plan` is provided. Populate `results_to_verify` ONLY from the "
        "experiments, benchmarks, and baselines listed in the eval_plan. Do not add "
        "results that are absent from the eval_plan — those have been excluded because "
        "they exceed hardware constraints (GPU VRAM or dataset size).\n"
        if eval_plan_block
        else ""
    )

    user_prompt = f"""<rubric_instructions>
{BASE_RUBRIC_PROMPT}
</rubric_instructions>

<evaluation_extraction_requirements>
{EVAL_DESCRIPTION_REQUIREMENTS}
</evaluation_extraction_requirements>

<task>
Generate a single rubric JSON that combines the paper, method description, figure/table descriptions, and (when present) the download_report inventory.
</task>

<success_criteria>
- Focus on reproduction-critical contributions, assets, methods, hyperparameters, and results.
- Keep anchors precise and audit-friendly.
- Use figures/tables when they define what must be reproduced or verified.
- Recover Evaluation Set-up, Benchmark evaluated on, Training, Results, and Expected Results details from the paper content.
- Include atomic method components in the methods field when the paper decomposes the approach.
- Move any necessary but unspecified detail to uncertain_or_missing.
{prerequisites_guidance}{results_to_verify_guidance}</success_criteria>

<paper_meta>
{json.dumps(paper_meta, ensure_ascii=False)}
</paper_meta>

<paper_markdown>
{context_blocks["paper_markdown"]}
</paper_markdown>

<figure_table_descriptions>
{context_blocks["figure_table_descriptions"]}
</figure_table_descriptions>

<method_description>
{context_blocks["method_description"]}
</method_description>
{download_report_section}{eval_plan_section}"""

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
                "name": "paper_reproduction_rubric",
                "schema": RUBRIC_SCHEMA,
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
            save_path = os.path.join(
                output_dir,
                f"{args.paper_name}_reproduction_rubric_{args.gpt_version}_{get_now_str()}.json",
            )
        return save_path, output_dir

    if eval_result_dir:
        output_dir = eval_result_dir
    else:
        output_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "results")
        )

    save_path = os.path.join(
        output_dir,
        f"{args.paper_name}_reproduction_rubric_{args.gpt_version}_{get_now_str()}.json",
    )
    return save_path, output_dir


def main(args: argparse.Namespace) -> None:
    client = create_openai_client()

    context_blocks, paper_meta = load_paper_context(args)

    download_report_text, download_report_meta = load_download_report(
        args.download_report_path,
        args.max_download_report_chars,
    )
    context_blocks["download_report"] = download_report_text
    paper_meta["download_report"] = download_report_meta

    eval_plan_text, eval_plan_meta = load_eval_plan(
        args.eval_plan_path,
        args.max_eval_plan_chars,
    )
    context_blocks["eval_plan"] = eval_plan_text
    paper_meta["eval_plan"] = eval_plan_meta

    messages = build_messages(
        context_blocks=context_blocks,
        paper_meta=paper_meta,
    )

    token_count = -1
    try:
        token_count = num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")

    request_json = build_request_json(args.gpt_version, messages)
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())

    output_content = completion_json["choices"][0]["message"]["content"]
    rubric_json = extract_json_from_content(output_content)

    output_payload: Dict[str, object] = {
        "paper_name": args.paper_name,
        "model": args.gpt_version,
        "paper_meta": paper_meta,
        "prompt_token_estimate": token_count,
        "reproduction_rubric": rubric_json,
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
    print("Reproduction Rubric Summary")
    print(f"Paper name: {args.paper_name}")
    print(f"Model: {args.gpt_version}")
    print(f"Prompt token estimate: {token_count}")
    print(f"Saved to: {save_path}")
    print("=" * 50)

    try:
        print_log_cost(
            completion_json,
            args.gpt_version,
            f"[ReproductionRubric] {args.paper_name}",
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
