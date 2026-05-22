#!/usr/bin/env bash
#
# Run PaperCoder/Codex generation for every PaperBench JSON paper.
#
# This wrapper discovers each `paper.json` under `data/paperbench_jsons/<paper>/`,
# passes that paper-specific environment into `scripts/run_codex.sh`, writes one
# log per paper, continues after failed papers, and exits non-zero at the end if
# any paper failed. Set `MAX_JOBS` above 1 to opt into parallel runs.
#
# Sample usage:
#   scripts/run_paperbench_codex_all.sh
#   scripts/run_paperbench_codex_all.sh --start 5
#   scripts/run_paperbench_codex_all.sh --only 5
#   MAX_JOBS=3 scripts/run_paperbench_codex_all.sh --only 7
#   DRY_RUN=1 scripts/run_paperbench_codex_all.sh --start 3
#   PAPERBENCH_JSON_DIR=/path/to/jsons LOG_DIR=/tmp/paperbench_logs scripts/run_paperbench_codex_all.sh
#
# `--start STAGE` and `--only STAGE` are forwarded to scripts/run_codex.sh for
# every paper. See `scripts/run_codex.sh --help` for the list of stages.
#
# Stage dependency note: stage 7 (rubric) auto-detects the download report
# produced by stage 6 (download_report.md or c1_download_dataset_summary.json
# under each paper's HARBOR_ASSET_DIR) and feeds it to 7_getting_rubric.py via
# --download_report_path so the rubric grounds prerequisites in real assets.
# Running --only 7 in isolation works but produces a paper-only rubric when
# stage 6 has not been run for that paper.

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    echo "Error: $(basename "${BASH_SOURCE[0]}") must be executed, not sourced." >&2
    echo "       Sourcing leaks background jobs into your interactive shell," >&2
    echo "       which breaks the per-paper pid tracking." >&2
    echo "Run as: bash ${BASH_SOURCE[0]}  (or)  ${BASH_SOURCE[0]}" >&2
    return 1 2>/dev/null || exit 1
fi

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RUN_CODEX_SCRIPT="${RUN_CODEX_SCRIPT:-$ROOT_DIR/scripts/run_codex.sh}"
PAPERBENCH_JSON_DIR="${PAPERBENCH_JSON_DIR:-$ROOT_DIR/data/paperbench_jsons}"
OUTPUT_BASE_DIR="${OUTPUT_BASE_DIR:-$ROOT_DIR/outputs/paperbench}"
OUTPUT_REPO_BASE_DIR="${OUTPUT_REPO_BASE_DIR:-$ROOT_DIR/outputs/paperbench_repos}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/outputs/paperbench_batch_logs}"
MAX_JOBS="${MAX_JOBS:-1}"
DRY_RUN="${DRY_RUN:-0}"

# Outer-level pass-throughs to scripts/run_codex.sh. Default empty: forward nothing.
OUTER_START_STAGE=""
OUTER_ONLY_STAGE=""

# Parse outer-script flags. Anything else is rejected so we don't silently
# swallow flags meant for run_codex.sh.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --start)
            [[ $# -ge 2 ]] || { echo "--start needs a value" >&2; exit 2; }
            OUTER_START_STAGE="$2"
            shift 2
            ;;
        --start=*)
            OUTER_START_STAGE="${1#*=}"
            shift
            ;;
        --only)
            [[ $# -ge 2 ]] || { echo "--only needs a value" >&2; exit 2; }
            OUTER_ONLY_STAGE="$2"
            shift 2
            ;;
        --only=*)
            OUTER_ONLY_STAGE="${1#*=}"
            shift
            ;;
        -h|--help)
            cat <<'USAGE'
Usage: run_paperbench_codex_all.sh [--start STAGE | --only STAGE]

Forwards --start/--only to scripts/run_codex.sh for every discovered paper.
Run `scripts/run_codex.sh --help` for the list of stages.

Environment overrides:
  MAX_JOBS, DRY_RUN, PAPERBENCH_JSON_DIR, OUTPUT_BASE_DIR,
  OUTPUT_REPO_BASE_DIR, LOG_DIR, RUN_CODEX_SCRIPT
USAGE
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Try: $(basename "$0") --help" >&2
            exit 2
            ;;
    esac
done

if [[ -n "$OUTER_START_STAGE" && -n "$OUTER_ONLY_STAGE" ]]; then
    echo "--start and --only are mutually exclusive" >&2
    exit 2
fi

if [[ -n "$OUTER_START_STAGE" ]] && ! [[ "$OUTER_START_STAGE" =~ ^[0-9]+$ ]]; then
    echo "--start must be a non-negative integer (got: $OUTER_START_STAGE)" >&2
    exit 2
fi
if [[ -n "$OUTER_ONLY_STAGE" ]] && ! [[ "$OUTER_ONLY_STAGE" =~ ^[0-9]+$ ]]; then
    echo "--only must be a non-negative integer (got: $OUTER_ONLY_STAGE)" >&2
    exit 2
fi

# Build the args array forwarded to run_codex.sh. Empty when no flag was given.
RUN_CODEX_ARGS=()
if [[ -n "$OUTER_START_STAGE" ]]; then
    RUN_CODEX_ARGS=(--start "$OUTER_START_STAGE")
elif [[ -n "$OUTER_ONLY_STAGE" ]]; then
    RUN_CODEX_ARGS=(--only "$OUTER_ONLY_STAGE")
fi

# The repo_has_python_src skip-check exists to avoid redoing the coding stages
# (0-3). When the user targets a stage past 3, the existing code is exactly
# what those stages need to consume, so the skip would silently no-op the run.
SKIP_EXISTING=1
if [[ -n "$OUTER_ONLY_STAGE" ]] && (( OUTER_ONLY_STAGE > 3 )); then
    SKIP_EXISTING=0
elif [[ -n "$OUTER_START_STAGE" ]] && (( OUTER_START_STAGE > 3 )); then
    SKIP_EXISTING=0
fi

# Validate configuration before launching expensive paper runs.
if ! [[ "$MAX_JOBS" =~ ^[1-9][0-9]*$ ]]; then
    echo "MAX_JOBS must be a positive integer; got '$MAX_JOBS'" >&2
    exit 2
fi

if [[ ! -d "$PAPERBENCH_JSON_DIR" ]]; then
    echo "PAPERBENCH_JSON_DIR does not exist: $PAPERBENCH_JSON_DIR" >&2
    exit 2
fi

# Discover paper inputs in stable order so reruns are predictable.
mapfile -t PAPER_JSONS < <(
    find "$PAPERBENCH_JSON_DIR" -mindepth 2 -maxdepth 2 -type f -name paper.json | sort
)

if (( ${#PAPER_JSONS[@]} == 0 )); then
    echo "No paper.json files found under $PAPERBENCH_JSON_DIR" >&2
    exit 1
fi

# Print the exact per-paper environment used by both dry-run and logs.
print_paper_env() {
    local paper_json="$1"
    local paper_dir paper_name output_dir output_repo_dir eval_dir cleaned_path

    paper_dir="$(dirname "$paper_json")"
    paper_name="$(basename "$paper_dir")"
    output_dir="$OUTPUT_BASE_DIR/$paper_name"
    output_repo_dir="$OUTPUT_REPO_BASE_DIR/${paper_name}_repo"
    eval_dir="$output_repo_dir/eval"
    cleaned_path="$paper_dir/paper_cleaned.json"

    cat <<EOF
PAPER_NAME=$paper_name
PDF_JSON_PATH=$paper_json
PDF_JSON_CLEANED_PATH=$cleaned_path
OUTPUT_DIR=$output_dir
OUTPUT_REPO_DIR=$output_repo_dir
EVAL_DIR=$eval_dir
RUN_CODEX_SCRIPT=$RUN_CODEX_SCRIPT
EOF
}

repo_has_python_src() {
    local output_repo_dir="$1"
    local first_python=""

    if [[ ! -d "$output_repo_dir/src" ]]; then
        return 1
    fi

    first_python="$(find "$output_repo_dir/src" -type f -name '*.py' -print -quit 2>/dev/null)"
    [[ -n "$first_python" ]]
}

# Dry-run mode is intentionally read-only and does not create output directories.
if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY_RUN=1: found ${#PAPER_JSONS[@]} paper.json files under $PAPERBENCH_JSON_DIR"
    if (( ${#RUN_CODEX_ARGS[@]} > 0 )); then
        echo "Forwarding to run_codex.sh: ${RUN_CODEX_ARGS[*]}"
    fi
    echo "skip_existing_python_src=$SKIP_EXISTING"
    for paper_json in "${PAPER_JSONS[@]}"; do
        paper_dir="$(dirname "$paper_json")"
        paper_name="$(basename "$paper_dir")"
        output_repo_dir="$OUTPUT_REPO_BASE_DIR/${paper_name}_repo"

        echo "-----"
        print_paper_env "$paper_json"
        if (( SKIP_EXISTING == 1 )) && repo_has_python_src "$output_repo_dir"; then
            echo "SKIP: $output_repo_dir/src already contains Python files"
        else
            echo "COMMAND: PAPER_NAME=... PDF_JSON_PATH=... PDF_JSON_CLEANED_PATH=... OUTPUT_DIR=... OUTPUT_REPO_DIR=... EVAL_DIR=... bash \"$RUN_CODEX_SCRIPT\"${RUN_CODEX_ARGS[@]+ ${RUN_CODEX_ARGS[*]}}"
        fi
    done
    exit 0
fi

if [[ ! -f "$RUN_CODEX_SCRIPT" ]]; then
    echo "RUN_CODEX_SCRIPT does not exist: $RUN_CODEX_SCRIPT" >&2
    exit 2
fi

# Real runs write shared batch state under LOG_DIR.
mkdir -p "$OUTPUT_BASE_DIR" "$OUTPUT_REPO_BASE_DIR" "$LOG_DIR"
FAILED_PAPERS_PATH="$LOG_DIR/failed_papers.txt"
: > "$FAILED_PAPERS_PATH"

# Track background jobs by pid so parallel failures can be attributed to papers.
declare -A PID_TO_NAME=()
declare -A PID_TO_LOG=()
ACTIVE_PIDS=()
FAILURES=()
SKIPPED_PAPERS=()

# Run one paper with isolated output paths derived from its directory name.
run_paper() {
    local paper_json="$1"
    local paper_dir paper_name output_dir output_repo_dir eval_dir cleaned_path

    paper_dir="$(dirname "$paper_json")"
    paper_name="$(basename "$paper_dir")"
    output_dir="$OUTPUT_BASE_DIR/$paper_name"
    output_repo_dir="$OUTPUT_REPO_BASE_DIR/${paper_name}_repo"
    eval_dir="$output_repo_dir/eval"
    cleaned_path="$paper_dir/paper_cleaned.json"

    echo "Started $paper_name at $(date -Is)"
    print_paper_env "$paper_json"
    echo "----- run_codex output -----"

    PAPER_NAME="$paper_name" \
    PDF_JSON_PATH="$paper_json" \
    PDF_JSON_CLEANED_PATH="$cleaned_path" \
    OUTPUT_DIR="$output_dir" \
    OUTPUT_REPO_DIR="$output_repo_dir" \
    EVAL_DIR="$eval_dir" \
    bash "$RUN_CODEX_SCRIPT" ${RUN_CODEX_ARGS[@]+"${RUN_CODEX_ARGS[@]}"}
}

# Remove a completed pid from the active job list.
remove_active_pid() {
    local done_pid="$1"
    local next_active=()
    local pid

    for pid in "${ACTIVE_PIDS[@]}"; do
        if [[ "$pid" != "$done_pid" ]]; then
            next_active+=("$pid")
        fi
    done

    ACTIVE_PIDS=("${next_active[@]}")
}

# Record success or failure after a background run finishes.
record_result() {
    local done_pid="$1"
    local status="$2"
    local paper_name="${PID_TO_NAME[$done_pid]:-unknown}"
    local log_path="${PID_TO_LOG[$done_pid]:-unknown}"

    remove_active_pid "$done_pid"

    if (( status == 0 )); then
        echo "Finished $paper_name (log: $log_path)"
    else
        echo "FAILED $paper_name with exit $status (log: $log_path)" >&2
        FAILURES+=("$paper_name")
        printf '%s\n' "$paper_name" >> "$FAILED_PAPERS_PATH"
    fi

    unset "PID_TO_NAME[$done_pid]" "PID_TO_LOG[$done_pid]"
}

# Wait for any active background job and process its exit status.
wait_for_one() {
    local done_pid=""
    local status=0

    wait -n -p done_pid
    status=$?

    if [[ -z "$done_pid" ]]; then
        echo "wait -n returned without a completed pid" >&2
        return 1
    fi

    record_result "$done_pid" "$status"
}

# Start a paper run in the background and route its output to a per-paper log.
launch_paper() {
    local paper_json="$1"
    local paper_dir paper_name log_path pid

    paper_dir="$(dirname "$paper_json")"
    paper_name="$(basename "$paper_dir")"
    log_path="$LOG_DIR/${paper_name}.log"

    run_paper "$paper_json" > "$log_path" 2>&1 &
    pid=$!

    PID_TO_NAME[$pid]="$paper_name"
    PID_TO_LOG[$pid]="$log_path"
    ACTIVE_PIDS+=("$pid")

    echo "Started $paper_name (pid: $pid, log: $log_path)"
}

echo "Running ${#PAPER_JSONS[@]} PaperBench papers with MAX_JOBS=$MAX_JOBS"
if (( ${#RUN_CODEX_ARGS[@]} > 0 )); then
    echo "Forwarding to run_codex.sh: ${RUN_CODEX_ARGS[*]}"
fi

# Keep at most MAX_JOBS papers running at once.
for paper_json in "${PAPER_JSONS[@]}"; do
    paper_dir="$(dirname "$paper_json")"
    paper_name="$(basename "$paper_dir")"
    output_repo_dir="$OUTPUT_REPO_BASE_DIR/${paper_name}_repo"

    if (( SKIP_EXISTING == 1 )) && repo_has_python_src "$output_repo_dir"; then
        echo "Skipping $paper_name: $output_repo_dir/src already contains Python files"
        SKIPPED_PAPERS+=("$paper_name")
        continue
    fi

    while (( ${#ACTIVE_PIDS[@]} >= MAX_JOBS )); do
        wait_for_one
    done
    launch_paper "$paper_json"
done

# Drain any remaining background jobs before summarizing the batch.
while (( ${#ACTIVE_PIDS[@]} > 0 )); do
    wait_for_one
done

if (( ${#FAILURES[@]} > 0 )); then
    echo "Failed papers (${#FAILURES[@]}):"
    printf '  %s\n' "${FAILURES[@]}"
    echo "Failure list: $FAILED_PAPERS_PATH"
    exit 1
fi

completed_count=$((${#PAPER_JSONS[@]} - ${#SKIPPED_PAPERS[@]}))
echo "All $completed_count launched PaperBench papers completed successfully."
if (( ${#SKIPPED_PAPERS[@]} > 0 )); then
    echo "Skipped already completed papers (${#SKIPPED_PAPERS[@]}):"
    printf '  %s\n' "${SKIPPED_PAPERS[@]}"
fi
