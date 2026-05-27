import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

from openai_client import create_openai_client
from utils import get_now_str, num_tokens_from_messages, print_log_cost

PASS2_PROMPT = """Pass 2: Rubric synthesis prompt

Feed the JSON from Pass 1 into this.

You are converting a reproducibility evidence graph into a paper2code rubric.

Return ONLY valid JSON with this exact schema for every node:
{
  "id": "",
  "requirements": "",
  "weight": 1,
  "task_category": null,
  "finegrained_task_category": null,
  "anchor": null,
  "expected_outcome": null,
  "comparison_protocol": null,
  "prerequisite_ids": null,
  "sub_tasks": []
}

Allowed task_category values:
- "Code Development"
- "Code Execution"
- "Result Analysis"

Allowed finegrained_task_category values:
- "Method Implementation"
- "Experimental Setup"
- "Dataset and Model Acquisition"
- "Data Processing & Preparation"
- "Evaluation, Metrics & Benchmarking"
- "Environment & Infrastructure Setup"
- "Logging, Analysis & Presentation"

Rules:
1. The root node must describe reproducing the paper, e.g.
   "The paper \\"<title>\\" has been reproduced."
   or
   "The core contributions of the paper \\"<title>\\" have been reproduced."
2. Internal nodes must have:
   - non-empty "sub_tasks"
   - task_category = null
   - finegrained_task_category = null
   - anchor, expected_outcome, comparison_protocol, prerequisite_ids all set to null
3. Leaf nodes must:
   - have empty "sub_tasks"
   - have non-null task_category and finegrained_task_category
   - be atomic, testable, and phrased as a single check
   - set anchor, expected_outcome, comparison_protocol, prerequisite_ids to null
     UNLESS rules 5/11/12 require them to be populated (Result-Analysis leaves and
     leaves derived from core_contributions must populate them)
4. Organize the tree in dependency order:
   setup/assets -> method implementation -> execution -> result verification
5. Use these leaf phrasing templates:
   - "Code has been implemented such that ..."
   - "The <dataset/model/environment> is obtained."
   - "The <procedure/experiment> has been run ..."
   - Result-Analysis leaves MUST use this structured template (do NOT use the bare
     "The results of Figure/Table <X> have been reproduced." form):
       "The reproduction reproduces <metric> on <experiment conditions> using
        <named baselines/comparators> and matches the paper's reported
        <qualitative trend> (anchor: <anchor>)."
     Example: "The reproduction reproduces the reverse-KL-vs-gradient-evaluations
     curve on synthetic Gaussian targets with D in {4,16,64,256} using
     ADVI/Score/Fisher/GSM (B=2) as comparators, averaged over 10 seeds, and matches
     the paper's claim that BaM is competitive or faster (anchor: Appendix E.2;
     Figure E.3)."
6. Include hyperparameters as separate leaves when the paper specifies exact values and they materially affect reproduction.
7. Create result-analysis leaves only for explicit empirical claims or figure/table
   outcomes. Every Result-Analysis leaf MUST reference at least one upstream `run_*`
   (or implementation) leaf id in `prerequisite_ids`; otherwise the leaf has no
   upstream computation to check and the audit will flag it.
8. Do not include uncertain items unless the evidence graph marked them as required; if included, phrase them conservatively.
9. Prefer deeper trees for method decomposition when the paper naturally decomposes an algorithm into parts.
10. Assign larger weights only to major method blocks; leaf weights should usually be 1.
11. Carry-forward rule: For every leaf derived from a Pass 1 `results_to_verify[]`
    entry, COPY that entry's `anchor`, `expected_outcome`, and `comparison_protocol`
    verbatim into the leaf's matching fields. Translate the entry's `prerequisites`
    (which are asset names) into `prerequisite_ids` by referencing the leaf ids you
    created earlier in the tree for those assets and for the corresponding `run_*`
    experiment leaf (e.g., `asset_synth_gaussian`, `impl_metrics`,
    `run_gaussian_reverse_kl`). Do NOT drop information from the Pass 1 entry — the
    bare phrasing "The results of Figure X have been reproduced." is forbidden.
12. Core-contributions rule: For every `core_contributions[]` entry:
    (a) ALWAYS create at least one Code Development leaf capturing the
        implementation requirement implied by the contribution.
    (b) If `verification_hint` is non-empty AND does not start with
        "theoretical_only", ALSO create a Result-Analysis leaf whose
        `expected_outcome` is the `verification_hint` (with comparison_protocol set
        to how the check would be performed). Wire its `prerequisite_ids` to the
        implementation leaf(s) from (a) and any `run_*` leaves needed.
    (c) If `verification_hint` starts with "theoretical_only", do NOT create a
        Result-Analysis leaf for that contribution — only the implementation
        leaf(s) from (a).

Now convert the evidence graph into the rubric JSON.
"""


PASS3_PROMPT = """Pass 3: Self-audit prompt

Audit the rubric JSON for paper2code quality.

Return ONLY valid JSON:
{
  "passes": true,
  "issues": [
    {
      "node_id": "",
      "issue_type": "non_atomic|missing_dependency|hallucinated_detail|wrong_category|redundant_node|too_broad|result_without_prereq",
      "fix": ""
    }
  ]
}

Audit rules:
1. Every leaf must represent one verifiable action.
2. No leaf should combine multiple independent actions.
3. Internal nodes should be meaningful groups, not wrappers with one child unless necessary.
4. Every result-analysis node must have the setup/implementation/execution prerequisites somewhere earlier in the tree.
5. No item may mention datasets/models/hyperparameters absent from the evidence graph.
6. Related-work-only items should be removed unless used in experiments.
7. Categories must be valid and consistent with the requirement text.
"""


TASK_CATEGORY_VALUES = [
    "Code Development",
    "Code Execution",
    "Result Analysis",
]

FINEGRAINED_TASK_CATEGORY_VALUES = [
    "Method Implementation",
    "Experimental Setup",
    "Dataset and Model Acquisition",
    "Data Processing & Preparation",
    "Evaluation, Metrics & Benchmarking",
    "Environment & Infrastructure Setup",
    "Logging, Analysis & Presentation",
]


# build_nullable_enum_schema: wrap an enum of allowed strings in a schema that also
# permits null. Used for task_category / finegrained_task_category so internal nodes
# can serialize them as null.
def build_nullable_enum_schema(values: List[str]) -> Dict[str, object]:
    return {
        "anyOf": [
            {"type": "string", "enum": values},
            {"type": "null"},
        ]
    }


# nullable: wrap an arbitrary JSON-schema fragment so it also permits null. Used for
# the carry-forward leaf fields (anchor, expected_outcome, comparison_protocol,
# prerequisite_ids) which are required on every node but must be null on internal
# (non-leaf) nodes.
def nullable(inner: Dict[str, object]) -> Dict[str, object]:
    return {"anyOf": [inner, {"type": "null"}]}


# CARRY_FORWARD_LEAF_FIELDS: schema fragments for the four optional fields that let
# Result-Analysis (and other) leaves preserve information from the Pass 1 evidence
# graph. Each field is required on every node (strict mode) but is nullable so
# internal nodes can serialize them as null.
CARRY_FORWARD_LEAF_FIELDS: Dict[str, Dict[str, object]] = {
    "anchor": nullable({"type": "string"}),
    "expected_outcome": nullable({"type": "string"}),
    "comparison_protocol": nullable({"type": "string"}),
    "prerequisite_ids": nullable(
        {"type": "array", "items": {"type": "string"}}
    ),
}

CARRY_FORWARD_LEAF_FIELD_NAMES: List[str] = list(CARRY_FORWARD_LEAF_FIELDS.keys())


PAPER2CODE_NODE_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "requirements": {"type": "string"},
        "weight": {"type": "integer", "minimum": 1},
        "task_category": build_nullable_enum_schema(TASK_CATEGORY_VALUES),
        "finegrained_task_category": build_nullable_enum_schema(
            FINEGRAINED_TASK_CATEGORY_VALUES
        ),
        **CARRY_FORWARD_LEAF_FIELDS,
        "sub_tasks": {
            "type": "array",
            "items": {"$ref": "#/$defs/node"},
        },
    },
    "required": [
        "id",
        "requirements",
        "weight",
        "task_category",
        "finegrained_task_category",
        *CARRY_FORWARD_LEAF_FIELD_NAMES,
        "sub_tasks",
    ],
    "$defs": {
        "node": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "requirements": {"type": "string"},
                "weight": {"type": "integer", "minimum": 1},
                "task_category": build_nullable_enum_schema(TASK_CATEGORY_VALUES),
                "finegrained_task_category": build_nullable_enum_schema(
                    FINEGRAINED_TASK_CATEGORY_VALUES
                ),
                **CARRY_FORWARD_LEAF_FIELDS,
                "sub_tasks": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/node"},
                },
            },
            "required": [
                "id",
                "requirements",
                "weight",
                "task_category",
                "finegrained_task_category",
                *CARRY_FORWARD_LEAF_FIELD_NAMES,
                "sub_tasks",
            ],
        }
    },
}


PAPER2CODE_AUDIT_SCHEMA: Dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "passes": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "node_id": {"type": "string"},
                    "issue_type": {
                        "type": "string",
                        "enum": [
                            "non_atomic",
                            "missing_dependency",
                            "hallucinated_detail",
                            "wrong_category",
                            "redundant_node",
                            "too_broad",
                            "result_without_prereq",
                        ],
                    },
                    "fix": {"type": "string"},
                },
                "required": ["node_id", "issue_type", "fix"],
            },
        },
    },
    "required": ["passes", "issues"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert the Pass 1 reproducibility evidence graph into a paper2code "
            "rubric, then optionally self-audit the result."
        )
    )
    parser.add_argument(
        "--pass1_json_path",
        type=str,
        required=True,
        help="Path to the JSON output from 7_getting_rubric.py or a raw evidence graph JSON.",
    )
    parser.add_argument(
        "--paper_name",
        type=str,
        default="",
        help="Optional paper name override.",
    )
    parser.add_argument("--gpt_version", type=str, default="gpt-5.4")
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
        help="Save raw completion payloads instead of raw message text.",
    )
    parser.add_argument(
        "--skip_audit",
        action="store_true",
        help="Skip the Pass 3 rubric self-audit step.",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_pass1_payload(pass1_json_path: str) -> Tuple[Dict[str, object], Dict[str, object]]:
    with open(pass1_json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise ValueError("The Pass 1 input JSON must be an object.")

    evidence_graph = payload.get("reproduction_rubric")
    input_type = "pass1_output"

    if not isinstance(evidence_graph, dict):
        evidence_graph = payload.get("evidence_graph")
        input_type = "named_evidence_graph"

    if not isinstance(evidence_graph, dict):
        evidence_graph = payload
        input_type = "raw_evidence_graph"

    input_meta = {
        "source_path": os.path.abspath(pass1_json_path),
        "input_type": input_type,
        "paper_name": normalize_text(str(payload.get("paper_name", "") or "")),
        "paper_title": normalize_text(str(evidence_graph.get("paper_title", "") or "")),
    }
    return evidence_graph, input_meta


def resolve_paper_name(
    paper_name_arg: str,
    input_meta: Dict[str, object],
    evidence_graph: Dict[str, object],
    pass1_json_path: str,
) -> str:
    if normalize_text(paper_name_arg):
        return normalize_text(paper_name_arg)

    for key in ("paper_name", "paper_title"):
        value = normalize_text(str(input_meta.get(key, "") or ""))
        if value:
            return value

    paper_title = normalize_text(str(evidence_graph.get("paper_title", "") or ""))
    if paper_title:
        return paper_title

    return os.path.splitext(os.path.basename(pass1_json_path))[0]


def build_pass2_messages(
    evidence_graph: Dict[str, object],
    paper_name: str,
) -> List[Dict[str, str]]:
    system_prompt = """You are an expert reproducibility analyst converting evidence graphs
into paper2code rubrics.

Rules:
- Use only the provided evidence graph.
- Return only valid JSON matching the required schema.
- Do not output markdown or commentary.
- Internal nodes must use null categories and have non-empty sub_tasks.
- Leaf nodes must use valid non-null categories and have empty sub_tasks.
- Every node must include anchor, expected_outcome, comparison_protocol, and
  prerequisite_ids (all four are required by the schema). Internal nodes and
  leaves that do not need them must serialize them as null. Result-Analysis
  leaves and leaves derived from core_contributions with a non-theoretical
  verification_hint must populate them per the carry-forward rule.
- The forbidden phrasing "The results of Figure/Table <X> have been reproduced."
  must NOT appear anywhere in the output — every Result-Analysis leaf must use
  the structured template that includes the metric, named comparators,
  conditions, and the qualitative trend the paper claims.
"""

    user_prompt = f"""{PASS2_PROMPT}

<paper_name>
{paper_name}
</paper_name>

<pass1_evidence_graph_json>
{json.dumps(evidence_graph, indent=2, ensure_ascii=False)}
</pass1_evidence_graph_json>
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_pass3_messages(
    evidence_graph: Dict[str, object],
    paper2code_rubric: Dict[str, object],
    paper_name: str,
) -> List[Dict[str, str]]:
    system_prompt = """You are an expert reproducibility rubric auditor.

Rules:
- Use only the provided evidence graph and rubric JSON.
- Return only valid JSON matching the required schema.
- Do not repair the rubric. Audit it and report issues precisely.
"""

    user_prompt = f"""{PASS3_PROMPT}

<paper_name>
{paper_name}
</paper_name>

<pass1_evidence_graph_json>
{json.dumps(evidence_graph, indent=2, ensure_ascii=False)}
</pass1_evidence_graph_json>

<paper2code_rubric_json>
{json.dumps(paper2code_rubric, indent=2, ensure_ascii=False)}
</paper2code_rubric_json>
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def extract_json_from_content(content: str) -> Any:
    text = content.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    fenced_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced_match:
        return json.loads(fenced_match.group(1))

    left_curly = text.find("{")
    right_curly = text.rfind("}")
    if left_curly >= 0 and right_curly > left_curly:
        return json.loads(text[left_curly : right_curly + 1])

    raise ValueError("Could not parse JSON from model output.")


def build_request_json(
    model_name: str,
    messages: List[Dict[str, str]],
    schema_name: str,
    schema: Dict[str, object],
) -> Dict[str, object]:
    request_json: Dict[str, object] = {
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
        request_json["reasoning_effort"] = "high"
    else:
        request_json["temperature"] = 0

    return request_json


def build_pass2_request_json(
    model_name: str,
    messages: List[Dict[str, str]],
) -> Dict[str, object]:
    return build_request_json(
        model_name=model_name,
        messages=messages,
        schema_name="paper2code_rubric_tree",
        schema=PAPER2CODE_NODE_SCHEMA,
    )


def build_pass3_request_json(
    model_name: str,
    messages: List[Dict[str, str]],
) -> Dict[str, object]:
    return build_request_json(
        model_name=model_name,
        messages=messages,
        schema_name="paper2code_rubric_audit",
        schema=PAPER2CODE_AUDIT_SCHEMA,
    )


def call_model(
    client: Any,
    request_json: Dict[str, object],
) -> Tuple[Dict[str, object], str, Any]:
    completion = client.chat.completions.create(**request_json)
    completion_json = json.loads(completion.model_dump_json())
    output_content = completion_json["choices"][0]["message"]["content"]
    parsed_json = extract_json_from_content(output_content)
    return completion_json, output_content, parsed_json


def resolve_output_paths(
    args: argparse.Namespace,
    paper_name: str,
    pass1_json_path: str,
) -> Tuple[str, str]:
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
                f"{paper_name}_paper2code_rubric_{args.gpt_version}_{get_now_str()}.json",
            )
        return save_path, output_dir

    if eval_result_dir:
        output_dir = eval_result_dir
    else:
        output_dir = os.path.dirname(os.path.abspath(pass1_json_path))

    save_path = os.path.join(
        output_dir,
        f"{paper_name}_paper2code_rubric_{args.gpt_version}_{get_now_str()}.json",
    )
    return save_path, output_dir


def estimate_tokens(messages: List[Dict[str, str]]) -> int:
    try:
        return num_tokens_from_messages(messages)
    except Exception as exc:
        print(f"[WARNING] Token counting failed: {exc}")
        return -1


def main(args: argparse.Namespace) -> None:
    client = create_openai_client()

    evidence_graph, input_meta = load_pass1_payload(args.pass1_json_path)
    paper_name = resolve_paper_name(
        paper_name_arg=args.paper_name,
        input_meta=input_meta,
        evidence_graph=evidence_graph,
        pass1_json_path=args.pass1_json_path,
    )

    pass2_messages = build_pass2_messages(
        evidence_graph=evidence_graph,
        paper_name=paper_name,
    )
    pass2_token_count = estimate_tokens(pass2_messages)
    pass2_request_json = build_pass2_request_json(args.gpt_version, pass2_messages)
    pass2_completion_json, pass2_raw_output, paper2code_rubric = call_model(
        client=client,
        request_json=pass2_request_json,
    )

    rubric_audit = None
    pass3_token_count = -1
    pass3_completion_json = None
    pass3_raw_output = None

    if not args.skip_audit:
        pass3_messages = build_pass3_messages(
            evidence_graph=evidence_graph,
            paper2code_rubric=paper2code_rubric,
            paper_name=paper_name,
        )
        pass3_token_count = estimate_tokens(pass3_messages)
        pass3_request_json = build_pass3_request_json(args.gpt_version, pass3_messages)
        pass3_completion_json, pass3_raw_output, rubric_audit = call_model(
            client=client,
            request_json=pass3_request_json,
        )

    output_payload: Dict[str, object] = {
        "paper_name": paper_name,
        "paper_title": input_meta.get("paper_title", ""),
        "model": args.gpt_version,
        "pass1_json_path": os.path.abspath(args.pass1_json_path),
        "input_meta": input_meta,
        "audit_enabled": not args.skip_audit,
        "prompt_token_estimates": {
            "pass2": pass2_token_count,
            "pass3": pass3_token_count if not args.skip_audit else None,
        },
        "paper2code_rubric": paper2code_rubric,
        "rubric_audit": rubric_audit,
    }

    if args.save_raw_completion:
        output_payload["raw_completion"] = {
            "pass2": pass2_completion_json,
            "pass3": pass3_completion_json,
        }
    else:
        output_payload["raw_model_output"] = {
            "pass2": pass2_raw_output,
            "pass3": pass3_raw_output,
        }

    save_path, output_dir = resolve_output_paths(
        args=args,
        paper_name=paper_name,
        pass1_json_path=args.pass1_json_path,
    )
    os.makedirs(output_dir, exist_ok=True)

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print("Paper2Code Rubric Summary")
    print(f"Paper name: {paper_name}")
    print(f"Model: {args.gpt_version}")
    print(f"Pass 2 prompt token estimate: {pass2_token_count}")
    if args.skip_audit:
        print("Pass 3 audit: skipped")
    else:
        print(f"Pass 3 prompt token estimate: {pass3_token_count}")
    print(f"Saved to: {save_path}")
    print("=" * 50)

    total_accumulated_cost = 0
    try:
        total_accumulated_cost = print_log_cost(
            pass2_completion_json,
            args.gpt_version,
            f"[Paper2CodeRubric][Pass2] {paper_name}",
            output_dir,
            total_accumulated_cost,
        )
    except Exception as exc:
        print(f"[WARNING] Pass 2 cost logging skipped: {exc}")

    if pass3_completion_json is not None:
        try:
            print_log_cost(
                pass3_completion_json,
                args.gpt_version,
                f"[Paper2CodeRubric][Pass3] {paper_name}",
                output_dir,
                total_accumulated_cost,
            )
        except Exception as exc:
            print(f"[WARNING] Pass 3 cost logging skipped: {exc}")


if __name__ == "__main__":
    try:
        cli_args = parse_args()
        main(cli_args)
    except Exception as error:
        print(f"[ERROR] {error}")
        sys.exit(1)
