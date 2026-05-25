#!/usr/bin/env bash
# Runs selected Paper2Code pipeline stages for a single PAPER_NAME.
# Sample usage:
# PAPER_NAME=adaptive-pruning bash /mnt/blk1/Paper2Code/scripts/run_codex.sh --start 1
# PAPER_NAME=bbox bash /mnt/blk1/Paper2Code/scripts/run_codex.sh --start 5 2>&1 | tee ${PAPER_NAME}_run.log
# PAPER_NAME=adaptive-pruning bash /mnt/blk1/Paper2Code/scripts/run_codex.sh --stages 5,5.1,7,8.1
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_STAGE="initialization"

on_error() {
    local status=$?
    local failed_command=${BASH_COMMAND:-unknown}
    echo "[run_codex] ERROR during ${CURRENT_STAGE}: command failed with exit status ${status}" >&2
    echo "[run_codex] Last command: ${failed_command}" >&2
    echo "[run_codex] PAPER_NAME=${PAPER_NAME:-unset}; START_STAGE=${START_STAGE:-unset}; ONLY_STAGE=${ONLY_STAGE:-unset}; STAGES=${STAGES:-unset}" >&2
    exit "$status"
}
trap on_error ERR

# export OPENAI_API_KEY=""

# use openai api key
# export PAPER2CODE_LLM_PROVIDER=openai
# export OPENAI_API_KEY="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"

# Use azure openai key
export PAPER2CODE_LLM_PROVIDER=azure
export AZURE_OPENAI_API_KEY="DROto1OI8KzrSyjn2GRMMrDNu15IzvbLzINMiQrUwLIvjTlBEfEhJQQJ99CEACHYHv6XJ3w3AAAAACOGM255"
export AZURE_OPENAI_ENDPOINT="https://csci8980-group11-resource.openai.azure.com/"
python codes/api_key_selector.py


python $ROOT_DIR/codes/api_key_selector.py
Error_log_file=$ROOT_DIR/results/error_log_${PAPER_NAME:-unknown}.log




START_STAGE="${START_STAGE:-0}"
ONLY_STAGE="${ONLY_STAGE:-}"
STAGES="${STAGES:-}"
START_STAGE_ARG=0
ONLY_STAGE_ARG=0
STAGES_ARG=0
STAGE_SELECTIONS=()
usage() {
    cat <<'EOF'
Usage: run_codex.sh [--start STAGE | --only STAGE | --stages LIST]

Stages:
  0    Preprocess          (codes/0_pdf_process.py)
  1    Planning            (codes/1_planning.py + 1.1_extract_config.py)
  2    Analyzing           (codes/2_analyzing.py)
  3    Coding              (codes/3_coding.py)
  5    Eval Info           (codes/5_eval_get_running_info.py)
  5.1  Eval Plan           (codes/5.1_get_evaluation_plan.py)
  6    Download Dataset    (codes/6_download_dataset.py)
  7    Repro Rubric        (codes/7_getting_rubric.py)
  8    Paper2Code Rubric   (codes/8_getting_paper2code_rubric.py)
  8.1  Self-Ameliorating   (codes/8.1_self_ameliorating.py)
  9    Unit Tests          (codes/9a_categorize_and_plan.py + codes/9b_synthesize_tests.py)
 10    SkyDiscover Bundle  (codes/10_get_skyDiscover.py)
 11    SkyDiscover Run     (codes/11_run_sky_discover.py)

Default: --start 0 (run all stages).
--only STAGE runs exactly one stage.
--stages LIST runs comma-separated stages exactly (e.g. --stages 5,5.1,7,8.1).
--start, --only, and --stages are mutually exclusive.
Stage numbers may be decimals (e.g. --only 5.1, --start 8.1).
Earlier stages' artifacts must already exist when starting mid-pipeline.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)
            [[ $# -ge 2 ]] || { echo "Error: --start requires a value" >&2; usage >&2; exit 1; }
            START_STAGE="$2"
            START_STAGE_ARG=1
            shift 2
            ;;
        --start=*)
            START_STAGE="${1#*=}"
            START_STAGE_ARG=1
            shift
            ;;
        --only)
            [[ $# -ge 2 ]] || { echo "Error: --only requires a value" >&2; usage >&2; exit 1; }
            ONLY_STAGE="$2"
            ONLY_STAGE_ARG=1
            shift 2
            ;;
        --only=*)
            ONLY_STAGE="${1#*=}"
            ONLY_STAGE_ARG=1
            shift
            ;;
        --stages)
            [[ $# -ge 2 ]] || { echo "Error: --stages requires a value" >&2; usage >&2; exit 1; }
            STAGES="$2"
            STAGES_ARG=1
            shift 2
            ;;
        --stages=*)
            STAGES="${1#*=}"
            STAGES_ARG=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

selection_count=0
if (( START_STAGE_ARG == 1 )) || [[ "$START_STAGE" != "0" ]]; then
    ((selection_count += 1))
fi
if (( ONLY_STAGE_ARG == 1 )) || [[ -n "$ONLY_STAGE" ]]; then
    ((selection_count += 1))
fi
if (( STAGES_ARG == 1 )) || [[ -n "$STAGES" ]]; then
    ((selection_count += 1))
fi

if (( selection_count > 1 )); then
    echo "Error: --start, --only, and --stages are mutually exclusive" >&2
    usage >&2
    exit 1
fi

if ! [[ "$START_STAGE" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    echo "Error: --start must be a non-negative number (got: $START_STAGE)" >&2
    exit 1
fi

if [[ -n "$ONLY_STAGE" ]] && ! [[ "$ONLY_STAGE" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    echo "Error: --only must be a non-negative number (got: $ONLY_STAGE)" >&2
    exit 1
fi

if (( STAGES_ARG == 1 )) && [[ -z "$STAGES" ]]; then
    echo "Error: --stages requires a non-empty comma-separated list" >&2
    exit 1
fi

if [[ -n "$STAGES" ]]; then
    if [[ "$STAGES" == *,,* || "$STAGES" == ,* || "$STAGES" == *, ]]; then
        echo "Error: --stages entries must not be empty (got: $STAGES)" >&2
        exit 1
    fi

    IFS=',' read -r -a STAGE_SELECTIONS <<< "$STAGES"
    for selected_stage in "${STAGE_SELECTIONS[@]}"; do
        if ! [[ "$selected_stage" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
            echo "Error: --stages values must be non-negative numbers (got: $selected_stage)" >&2
            exit 1
        fi
    done
fi

run_stage() {
    local stage_num="$1"
    local selected_stage

    if [[ -n "$ONLY_STAGE" ]]; then
        [[ "$ONLY_STAGE" = "$stage_num" ]]
    elif [[ -n "$STAGES" ]]; then
        for selected_stage in "${STAGE_SELECTIONS[@]}"; do
            if [[ "$selected_stage" = "$stage_num" ]]; then
                return 0
            fi
        done
        return 1
    else
        awk -v start="$START_STAGE" -v cur="$stage_num" 'BEGIN { exit !(start <= cur) }'
    fi
}


# PAPER_NAME="adaptive-pruning"
# ---- Required ----
if [[ -z "${PAPER_NAME:-}" ]]; then
    echo "Error: PAPER_NAME is required (e.g., PAPER_NAME=adaptive-pruning ./scripts/run_codex.sh)" >&2
    exit 1
fi

# ---- General ----
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
GPT_VERSION="${GPT_VERSION:-gpt-5.2}"

# ---- Data roots (paperbench layout) ----
PAPERBENCH_PAPERS_DIR="${PAPERBENCH_PAPERS_DIR:-$ROOT_DIR/data/paperbench_papers}"
PAPERBENCH_JSONS_DIR="${PAPERBENCH_JSONS_DIR:-$ROOT_DIR/data/paperbench_jsons}"

# ---- Per-paper inputs (derived from PAPER_NAME) ----
PDF_JSON_PATH="${PDF_JSON_PATH:-$PAPERBENCH_JSONS_DIR/$PAPER_NAME/paper.json}"
PDF_JSON_CLEANED_PATH="${PDF_JSON_CLEANED_PATH:-$PAPERBENCH_JSONS_DIR/$PAPER_NAME/paper_cleaned.json}"

# ---- Output directories (derived from PAPER_NAME) ----
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/outputs/paperbench_log/$PAPER_NAME}"
OUTPUT_REPO_DIR="${OUTPUT_REPO_DIR:-$ROOT_DIR/outputs/paperbench_repos/${PAPER_NAME}_repo}"
EVAL_DIR="${EVAL_DIR:-$OUTPUT_REPO_DIR/eval}"
TESTS_OUTPUT_DIR="${TESTS_OUTPUT_DIR:-$ROOT_DIR/outputs/paperbench_tests/${PAPER_NAME}_tests}"
HARBOR_ASSET_DIR="${HARBOR_ASSET_DIR:-$ROOT_DIR/tests/harbor/yantingchi/${PAPER_NAME}/harbor/asset}"
HARBOR_TASKS_DIR="${HARBOR_TASKS_DIR:-$ROOT_DIR/outputs/harbor_tasks}"
# ---- Legacy Harbor settings ----
HARBOR_AGENT="${HARBOR_AGENT:-codex}"
HARBOR_MODEL="${HARBOR_MODEL:-$GPT_VERSION}"   # matches the pipeline's AI model
HARBOR_JOBS_DIR="${HARBOR_JOBS_DIR:-$ROOT_DIR/outputs/harbor_jobs}"
HARBOR_JOB_NAME="${HARBOR_JOB_NAME:-$PAPER_NAME}"

# ---- Stage 10-11: SkyDiscover settings (used when 10_get_skyDiscover.py is active) ----
SKYDISCOVER_TASKS_DIR="${SKYDISCOVER_TASKS_DIR:-$ROOT_DIR/outputs/skydiscover_tasks}"
SKYDISCOVER_JOBS_DIR="${SKYDISCOVER_JOBS_DIR:-$ROOT_DIR/outputs/skydiscover_jobs}"
SKYDISCOVER_SEARCH="${SKYDISCOVER_SEARCH:-adaevolve}"
SKYDISCOVER_ITERATIONS="${SKYDISCOVER_ITERATIONS:-10}"

# ---- Stage artifact paths (derived) ----
EVAL_INFO_PATH="${EVAL_INFO_PATH:-$EVAL_DIR/${PAPER_NAME}_paper_only_eval_info_${GPT_VERSION}.json}"
RUBRIC_PATH="${RUBRIC_PATH:-$EVAL_DIR/${PAPER_NAME}_reproduction_rubric_${GPT_VERSION}.json}"
PAPER2CODE_RUBRIC_PATH="${PAPER2CODE_RUBRIC_PATH:-$EVAL_DIR/${PAPER_NAME}_paper2code_rubric_${GPT_VERSION}.json}"
REPO_PLAN_PATH="${REPO_PLAN_PATH:-$OUTPUT_DIR/planning_response.json}"
OUTPUT_BUNDLE_DIR="${OUTPUT_BUNDLE_DIR:-$EVAL_DIR/benchmark}"

# Stage 5.1 outputs an eval plan (hardware-constrained baseline/dataset list).
# Stage 6 always consumes it via --eval_plan_json (supersedes eval_info_json).
EVAL_PLAN_DIR="${EVAL_PLAN_DIR:-$ROOT_DIR/tests/harbor/yantingchi/${PAPER_NAME}/eval_plan}"
EVAL_PLAN_JSON_PATH="${EVAL_PLAN_JSON_PATH:-$EVAL_PLAN_DIR/eval_plan.json}"

# Stage 6 produces a download report under HARBOR_ASSET_DIR. Stage 7 (rubric)
# consumes it to ground results_to_verify[].prerequisites in real assets.
# Prefer the human-readable markdown if present, fall back to the structured
# JSON summary that 6_download_dataset.py writes unconditionally.
DOWNLOAD_REPORT_MD_PATH="${DOWNLOAD_REPORT_MD_PATH:-$HARBOR_ASSET_DIR/download_report.md}"
DOWNLOAD_REPORT_JSON_PATH="${DOWNLOAD_REPORT_JSON_PATH:-$HARBOR_ASSET_DIR/c1_download_dataset_summary.json}"

# ---- Misc ----
LOCAL_MACHINE_CHECK="${LOCAL_MACHINE_CHECK:-1}"

mkdir -p "$OUTPUT_DIR" "$OUTPUT_REPO_DIR" "$EVAL_DIR"

# ---- Centralized run log + intermediate repo snapshots ----
# AALOG_DIR holds one consolidated log per script invocation.
# SUBREPOS_DIR holds a copy of OUTPUT_REPO_DIR after each producing stage
# (stage 3 and stage 8.1) so we can diff codex output vs self-ameliorated output.
AALOG_DIR="$ROOT_DIR/outputs/aalog"
SUBREPOS_DIR="$ROOT_DIR/outputs/paperbench_subrepos"
mkdir -p "$AALOG_DIR" "$SUBREPOS_DIR"

# allocate_run_log: atomically claim the next free outputs/aalog/<prefix>_<N>.log
# slot using `set -o noclobber`. Sets RUN_INDEX and RUN_LOG_FILE globals.
allocate_run_log() {
    local prefix="$1"
    local n=1
    while true; do
        local candidate="$AALOG_DIR/${prefix}_${n}.log"
        if (set -o noclobber; : > "$candidate") 2>/dev/null; then
            RUN_INDEX="$n"
            RUN_LOG_FILE="$candidate"
            return
        fi
        n=$((n + 1))
    done
}

# snapshot_repo: copy $OUTPUT_REPO_DIR to subrepos/$PAPER_NAME/<tag>_<N>/.
# Tries the current run's RUN_INDEX first; falls back to the next free N for
# this (paper, tag) tuple if the run's slot is already taken (mixed-script use
# or parallel race). mkdir is the atomic claim primitive.
snapshot_repo() {
    local tag="$1"
    local paper_dir="$SUBREPOS_DIR/$PAPER_NAME"
    mkdir -p "$paper_dir"
    local n="$RUN_INDEX"
    local dest=""
    while true; do
        dest="$paper_dir/${tag}_${n}"
        if mkdir "$dest" 2>/dev/null; then
            break
        fi
        n=$((n + 1))
    done
    echo "[run_codex] Snapshotting $OUTPUT_REPO_DIR -> $dest"
    if ! cp -a "$OUTPUT_REPO_DIR/." "$dest/"; then
        echo "[run_codex] ERROR: failed to snapshot $tag repo to $dest" >&2
        return 1
    fi
}

allocate_run_log "run_codex_${PAPER_NAME}"
echo "[run_codex] Logging to $RUN_LOG_FILE (index $RUN_INDEX)"
# Mirror stdout+stderr into RUN_LOG_FILE while still printing to the terminal.
exec > >(tee -a "$RUN_LOG_FILE") 2>&1

echo "$PAPER_NAME"
if [[ -n "$STAGES" ]]; then
    echo "Running stages: $STAGES"
elif [[ -n "$ONLY_STAGE" ]]; then
    echo "Running only stage: $ONLY_STAGE"
else
    echo "Starting from stage: $START_STAGE"
fi

if run_stage 0; then
    CURRENT_STAGE="Stage 0: Preprocess"
    echo "------- Stage 0: Preprocess -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/0_pdf_process.py" \
        --input_json_path "$PDF_JSON_PATH" \
        --output_json_path "$PDF_JSON_CLEANED_PATH"
fi

if run_stage 1; then
    CURRENT_STAGE="Stage 1: Planning"
    echo "------- Stage 1: Planning (PaperCoder) -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/1_planning.py" \
        --paper_name "$PAPER_NAME" \
        --gpt_version "$GPT_VERSION" \
        --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
        --output_dir "$OUTPUT_DIR"

    "$PYTHON_BIN" "$ROOT_DIR/codes/1.1_extract_config.py" \
        --paper_name "$PAPER_NAME" \
        --output_dir "$OUTPUT_DIR"

    cp -rp "$OUTPUT_DIR/planning_config.yaml" "$OUTPUT_REPO_DIR/config.yaml"
fi

if run_stage 2; then
    CURRENT_STAGE="Stage 2: Analyzing"
    echo "------- Stage 2: Analyzing -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/2_analyzing.py" \
        --paper_name "$PAPER_NAME" \
        --gpt_version "$GPT_VERSION" \
        --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
        --output_dir "$OUTPUT_DIR"
fi

STAGE3_RAN=0
if run_stage 3; then
    CURRENT_STAGE="Stage 3: Coding"
    echo "------- Stage 3: Coding -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/3_coding.py" \
        --paper_name "$PAPER_NAME" \
        --gpt_version "$GPT_VERSION" \
        --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
        --output_dir "$OUTPUT_DIR" \
        --output_repo_dir "$OUTPUT_REPO_DIR"
    STAGE3_RAN=1
fi
if (( STAGE3_RAN == 1 )); then
    snapshot_repo "stage3"
fi

# Stage 3.5: targeted, feedback-driven repair. Regenerates ONLY the files behind
# failing rubric leaves (per a grader_output.json), leaving passing files frozen,
# to avoid the ~+/-6-leaf stochastic noise of a full stage-3 regeneration.
# Needs a grader_output.json: set REPAIR_GRADER_OUTPUT, else the most recent one
# for this paper is used. Exits nonzero with a clear message if none is found.
if run_stage 3.5; then
    CURRENT_STAGE="Stage 3.5: Repair"
    echo "------- Stage 3.5: Repair (feedback-driven, targeted) -------"
    REPAIR_GRADER_OUTPUT="${REPAIR_GRADER_OUTPUT:-}"
    if [[ -z "$REPAIR_GRADER_OUTPUT" ]]; then
        REPAIR_GRADER_OUTPUT="$(ls -t "$ROOT_DIR"/outputs/paperbench_eval/"$PAPER_NAME"/*/grader_output.json 2>/dev/null | head -1)"
    fi
    if [[ -z "$REPAIR_GRADER_OUTPUT" || ! -f "$REPAIR_GRADER_OUTPUT" ]]; then
        echo "[run_codex][ERROR] Stage 3.5 needs a grader_output.json to repair against." >&2
        echo "[run_codex][ERROR] Set REPAIR_GRADER_OUTPUT=/path/to/grader_output.json (none found for $PAPER_NAME)." >&2
        exit 1
    fi
    echo "[run_codex] Stage 3.5 repairing against: $REPAIR_GRADER_OUTPUT"
    "$PYTHON_BIN" "$ROOT_DIR/codes/3.5_repair.py" \
        --paper_name "$PAPER_NAME" \
        --gpt_version "$GPT_VERSION" \
        --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
        --output_dir "$OUTPUT_DIR" \
        --output_repo_dir "$OUTPUT_REPO_DIR" \
        --grader_output "$REPAIR_GRADER_OUTPUT"
    snapshot_repo "stage3_5"
fi

if run_stage 5; then
    CURRENT_STAGE="Stage 5: Eval Info"
    echo "------- Stage 5: Eval Info -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/5_eval_get_running_info.py" \
        --paper_name "$PAPER_NAME" \
        --paper_format JSON \
        --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
        --gpt_version "$GPT_VERSION" \
        --output_path "$EVAL_INFO_PATH"
fi

if run_stage 5.1; then
    CURRENT_STAGE="Stage 5.1: Eval Plan"
    echo "------- Stage 5.1: Eval Plan (hardware-constrained) -------"
    mkdir -p "$EVAL_PLAN_DIR"
    "$PYTHON_BIN" "$ROOT_DIR/codes/5.1_get_evaluation_plan.py" \
        --paper_json_path "$PDF_JSON_CLEANED_PATH" \
        --eval_info_json "$EVAL_INFO_PATH" \
        --generated_repo_path "$OUTPUT_REPO_DIR" \
        --output_dir "$EVAL_PLAN_DIR" \
        --gpt_version "$GPT_VERSION"
fi

if run_stage 6; then
    CURRENT_STAGE="Stage 6: Download Dataset"
    echo "------- Stage 6: Download Dataset -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/6_download_dataset.py" \
        --paper_json_path "$PDF_JSON_CLEANED_PATH" \
        --generated_repo_path "$OUTPUT_REPO_DIR" \
        --eval_info_json "$EVAL_INFO_PATH" \
        --eval_plan_json "$EVAL_PLAN_JSON_PATH" \
        --output_dir "$HARBOR_ASSET_DIR" \
        --gpt_version "$GPT_VERSION"
fi

if run_stage 7; then
    CURRENT_STAGE="Stage 7: Reproduction Rubric"
    echo "------- Stage 7: Reproduction Rubric (Pass 1) -------"

    # Resolve the download report path: explicit override wins, then the
    # markdown report, then the JSON summary. If none of those exist (e.g. the
    # user is running stage 7 without having run stage 6) we omit the flag and
    # fall back to paper-only rubric extraction.
    rubric_extra_args=()
    resolved_download_report=""
    if [[ -n "${DOWNLOAD_REPORT_PATH:-}" && -f "${DOWNLOAD_REPORT_PATH:-}" ]]; then
        resolved_download_report="$DOWNLOAD_REPORT_PATH"
    elif [[ -f "$DOWNLOAD_REPORT_MD_PATH" ]]; then
        resolved_download_report="$DOWNLOAD_REPORT_MD_PATH"
    elif [[ -f "$DOWNLOAD_REPORT_JSON_PATH" ]]; then
        resolved_download_report="$DOWNLOAD_REPORT_JSON_PATH"
    fi

    if [[ -n "$resolved_download_report" ]]; then
        echo "  using download_report: $resolved_download_report"
        rubric_extra_args+=(--download_report_path "$resolved_download_report")
    else
        echo "  no download_report found under $HARBOR_ASSET_DIR; running paper-only rubric"
    fi

    "$PYTHON_BIN" "$ROOT_DIR/codes/7_getting_rubric.py" \
        --paper_name "$PAPER_NAME" \
        --paper_format JSON \
        --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
        --gpt_version "$GPT_VERSION" \
        --output_path "$RUBRIC_PATH" \
        ${rubric_extra_args[@]+"${rubric_extra_args[@]}"}
fi

if run_stage 8; then
    CURRENT_STAGE="Stage 8: Paper2Code Rubric"
    echo "------- Stage 8: Paper2Code Rubric (Pass 2/3) -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/8_getting_paper2code_rubric.py" \
        --pass1_json_path "$RUBRIC_PATH" \
        --paper_name "$PAPER_NAME" \
        --gpt_version "$GPT_VERSION" \
        --output_path "$PAPER2CODE_RUBRIC_PATH"
fi

STAGE81_RAN=0
if run_stage 8.1; then
    CURRENT_STAGE="Stage 8.1: Self-Ameliorating"
    echo "------- Stage 8.1: Self-Ameliorating (Code Dev patches) -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/8.1_self_ameliorating.py" \
        --paper_name "$PAPER_NAME" \
        --gpt_version "$GPT_VERSION" \
        --output_dir "$OUTPUT_DIR" \
        --output_repo_dir "$OUTPUT_REPO_DIR" \
        --rubric_path "$PAPER2CODE_RUBRIC_PATH"
    STAGE81_RAN=1
fi
if (( STAGE81_RAN == 1 )); then
    snapshot_repo "stage8.1"
fi

# if run_stage 9; then
#     echo "------- Stage 9: Unit Tests -------"
#     "$PYTHON_BIN" "$ROOT_DIR/codes/9_getting_unit_test.py" \
#         --rubric_json_path "$PAPER2CODE_RUBRIC_PATH" \
#         --paper_name "$PAPER_NAME" \
#         --repo_plan_path "$REPO_PLAN_PATH" \
#         --generated_repo_path "$OUTPUT_REPO_DIR" \
#         --gpt_version "$GPT_VERSION" \
#         --output_dir "$TESTS_OUTPUT_DIR"
# fi

if run_stage 9; then
    CURRENT_STAGE="Stage 9a: Categorize Rubric + Pass 1"
    echo "------- Stage 9a: Categorize Rubric + Pass 1 -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/9a_categorize_and_plan.py" \
        --rubric_json_path "$PAPER2CODE_RUBRIC_PATH" \
        --paper_name "$PAPER_NAME" \
        --repo_plan_path "$REPO_PLAN_PATH" \
        --generated_repo_path "$OUTPUT_REPO_DIR" \
        --gpt_version "$GPT_VERSION" \
        --output_dir "$TESTS_OUTPUT_DIR"

    CURRENT_STAGE="Stage 9b: Synthesize Unit Tests"
    echo "------- Stage 9b: Synthesize Unit Tests (both tiers) -------"
    "$PYTHON_BIN" "$ROOT_DIR/codes/9b_synthesize_tests.py" \
        --specs_path "$TESTS_OUTPUT_DIR/test_specs.json" \
        --generated_repo_path "$OUTPUT_REPO_DIR" \
        --output_dir "$TESTS_OUTPUT_DIR" \
        --gpt_version "$GPT_VERSION" \
        --tier both
fi

#/mnt/blk1/Paper2Code/data/paperbench_papers/adaptive-pruning/rubric_ap.json 


if run_stage 10; then
    CURRENT_STAGE="Stage 10: SkyDiscover Bundle"
    echo "------- Stage 10: SkyDiscover Bundle -------"

    # Serializes the generated codebase into SkyDiscover's expected format:
    # initial_program.py (CODEBASE dict) + evaluator.py (weighted pytest reward).
    bundle_args=(
        --paper_name "$PAPER_NAME"
        --paper_json_path "$PDF_JSON_CLEANED_PATH"
        --planning_dir "$OUTPUT_DIR"
        --repo_dir "$OUTPUT_REPO_DIR"
        --unit_test_dir "$TESTS_OUTPUT_DIR"
        --skydiscover_output_dir "$SKYDISCOVER_TASKS_DIR"
        --task_slug "$PAPER_NAME"
        --force
    )

    if [[ -d "$HARBOR_ASSET_DIR" ]]; then
        echo "  noting stage-6 assets from: $HARBOR_ASSET_DIR"
        bundle_args+=(--harbor_asset_dir "$HARBOR_ASSET_DIR")
    else
        echo "  no stage-6 assets at $HARBOR_ASSET_DIR"
    fi

    "$PYTHON_BIN" "$ROOT_DIR/codes/10_get_skyDiscover.py" "${bundle_args[@]}"
fi

if run_stage 11; then
    CURRENT_STAGE="Stage 11: SkyDiscover Run"
    echo "------- Stage 11: SkyDiscover Run -------"
    # Iteratively evolves the codebase over SKYDISCOVER_ITERATIONS rounds.
    # Each round: SkyDiscover asks the LLM to improve initial_program.py,
    # evaluator.py scores the result via the pytest suite, best version is kept.
    # evaluator.py appends one JSON line per iteration to cost_log.jsonl.
    cost_log="$SKYDISCOVER_JOBS_DIR/$PAPER_NAME/cost_log.jsonl"
    mkdir -p "$SKYDISCOVER_JOBS_DIR/$PAPER_NAME"
    skydiscover_args=(
        --skydiscover_output_dir "$SKYDISCOVER_TASKS_DIR"
        --task_slug "$PAPER_NAME"
        --search "$SKYDISCOVER_SEARCH"
        --model "$GPT_VERSION"
        --iterations "$SKYDISCOVER_ITERATIONS"
        --output_dir "$SKYDISCOVER_JOBS_DIR/$PAPER_NAME"
        --cost_log "$cost_log"
    )
    "$PYTHON_BIN" "$ROOT_DIR/codes/11_run_sky_discover.py" "${skydiscover_args[@]}" 2>&1 | tee "$Error_log_file"

    # ---- cost summary ---------------------------------------------------- #
    if [[ -f "$cost_log" ]]; then
        echo ""
        echo "------- SkyDiscover Cost Summary -------"
        COST_LOG_PATH="$cost_log" "$PYTHON_BIN" - <<'PYEOF'
import json, os, sys
from pathlib import Path

log_path = os.environ.get("COST_LOG_PATH", "")
try:
    entries = [json.loads(l) for l in Path(log_path).read_text().splitlines() if l.strip()]
except Exception as e:
    print(f"  Could not read cost log: {e}")
    sys.exit(0)
if not entries:
    print("  No evaluation entries found.")
    sys.exit(0)

iters = len(entries)
best = max(entries, key=lambda e: e["score"])
total_dur = sum(e["duration_s"] for e in entries)
total_tok = sum(e["codebase_tokens_est"] for e in entries)
# Each iteration: SkyDiscover sends ~tokens_est input + generates ~tokens_est/2 output.
# GPT-4o Azure pricing (rough): $5/1M input tokens, $15/1M output tokens.
cost_usd = total_tok * (5 + 15 * 0.5) / 1_000_000

print(f"  Iterations completed  : {iters}")
print(f"  Best score            : {best['score']:.4f}  (iter {best['iteration']})")
print(f"  Total eval time       : {total_dur:.1f}s  (~{total_dur/iters:.1f}s / iter)")
print(f"  Est. tokens used      : {total_tok:,}  (~{total_tok // 1000}K)")
print(f"  Est. API cost (GPT-4o): ${cost_usd:.4f}")
print(f"  Full cost log         : {log_path}")
PYEOF
    fi
fi

# echo "------- Harbor Bundle -------"

# bundle_cmd=(
#     "$PYTHON_BIN"
#     "$ROOT_DIR/codes/eval_terminalbench_bundle.py"
#     --paper_name "$PAPER_NAME"
#     --target_repo_dir "$OUTPUT_REPO_DIR"
#     --eval_metrics_path "$EVAL_INFO_PATH"
#     --paper_json_path "$PDF_JSON_PATH"
#     --output_bundle_dir "$OUTPUT_BUNDLE_DIR"
#     --gpt_version "$GPT_VERSION"
# )

# if [[ "$LOCAL_MACHINE_CHECK" == "1" ]]; then
#     bundle_cmd+=(--local_machine_check)
# fi

# "${bundle_cmd[@]}"


# harbor run -p "$HARBOR_TASKS_DIR" -m "<model>" -a "<agent>"
