#!/usr/bin/env bash
# What this file does:
#   Grades a single paper's generated code using the PaperBench rubric.
#   Runs the PaperBench judge against the code in OUTPUT_REPO_DIR and writes
#   a grader_output.json with a 0-1 score.
#
#   By default it grades a CLUTTER-FREE COPY of the repo (vendored .venv,
#   __pycache__, *.bak, outputs/, tests/, ... are stripped). This is because the
#   judge picks which files to read per rubric item from the directory tree, and
#   a tree full of vendored junk makes it miss the real implementation files and
#   wrongly mark them "missing" (score 0). The original repo is never modified.
#
# Usage examples:
#   PAPER_NAME=adaptive-pruning bash scripts/grade_paper.sh
#   PAPER_NAME=adaptive-pruning JUDGE=dummy bash scripts/grade_paper.sh
#   PAPER_NAME=bam JUDGE=simple GRADER_MODEL=gpt-5 bash scripts/grade_paper.sh
#   PAPERNAME="adaptive pruning" GRADER_LLM_PROVIDER=aoai-proxy JUDGE=simple bash scripts/grade_paper.sh
#   PAPER_NAME=bam GRADER_LLM_PROVIDER=azure GRADER_MODEL=<deployment> bash scripts/grade_paper.sh
#
# Required env var:
#   PAPER_NAME        - paper ID matching PaperBench data/papers/<id>/ directory
#                       PAPERNAME is accepted as an alias and normalized by
#                       lowercasing and changing whitespace/underscores to hyphens.
#
# Optional env vars:
#   JUDGE             - judge type: simple|dummy|random (default: simple)
#                       simple = real LLM judge (needs OpenAI, Azure, or AOAI proxy credentials)
#                       dummy  = marks every leaf 1.0, no API call (for testing the plumbing)
#                       random = random 0/1 per leaf (for baseline comparisons)
#   GRADER_MODEL      - OpenAI model or Azure deployment for simple judge
#                       (default: OPENAI_MODEL_DEPLOYMENT, then GPT_VERSION, then gpt-5.2)
#   GRADER_LLM_PROVIDER - auto|openai|azure|aoai-proxy (default: auto)
#   GRADER_OPENAI_API_KEY, GRADER_OPENAI_BASE_URL
#                       OpenAI-compatible credentials for the simple judge.
#   GRADER_AZURE_OPENAI_API_KEY, GRADER_AZURE_OPENAI_ENDPOINT
#                       Azure credentials for the simple judge; falls back to
#                       AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT.
#   AOAI_PROXY_ENDPOINT - local AOAI proxy endpoint (default: http://127.0.0.1:8787)
#   AOAI_PROXY_DUMMY_API_KEY - dummy key sent to local proxy (default: dummy)
#   AOAI_PROXY_HEALTH_CHECK - 1 to check /healthz before use, 0 to skip (default: 1)
#   AOAI_PROXY_HEALTH_TIMEOUT_SECONDS - proxy health timeout seconds (default: 5)
#   AOAI_PROXY_API_STYLE - URL path style the proxy expects: auto|openai|azure (default: auto)
#                       The proxy forwards the request path verbatim to its
#                       upstream, so the prefix must match the upstream provider:
#                         openai upstream (api.openai.com)     -> /v1/...
#                         azure  upstream (*.openai.azure.com) -> /openai/v1/...
#                       Using the wrong one makes the UPSTREAM return 404.
#                       auto = probe the proxy's /v1/models route and pick
#                       openai-style if it answers 200, else azure-style.
#   CODE_ONLY         - only grade Code Development nodes, skip execution/result nodes (default: True)
#   CLEAN_SUBMISSION  - True  = grade a clutter-free COPY of the repo (default).
#                       False = grade the repo exactly as-is (old behavior).
#                       Originals are never modified either way.
#   CLEAN_EXCLUDES    - space-separated basename/glob patterns to drop from the
#                       graded copy. Override to taste, e.g.
#                       CLEAN_EXCLUDES="__pycache__ *.bak outputs"
#                       (Default list strips venvs, caches, backups, outputs, tests.)
#   OUTPUT_REPO_DIR   - path to the generated code repo
#                       (default: $ROOT_DIR/outputs/paperbench_repos/<PAPER_NAME>_repo)
#   EVAL_LABEL        - internal label prefix for output dir name (set by run_codex_with_eval.sh)

set -Eeuo pipefail

# =============================================================================
# CHANGEABLE SETTINGS (the things you are most likely to edit / override)
# -----------------------------------------------------------------------------
# Every value below uses ${VAR:-default}, so you can override any of them on the
# command line WITHOUT editing this file, e.g.:
#   GRADER_MODEL=gpt-5 OUTPUT_REPO_DIR=/tmp/my_repo bash scripts/grade_paper.sh
# Edit the defaults here only when you want to change them permanently.
# =============================================================================

# ---- Key paths ----
# ROOT_DIR            : repo root, auto-detected from this script's location.
# PAPERBENCH_DIR      : where the PaperBench eval code lives (run_judge.py).
# PAPERBENCH_DATA_DIR : where the per-paper rubrics live (data/papers/<id>/).
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAPERBENCH_DIR="${PAPERBENCH_DIR:-$ROOT_DIR/assets/Other-repo/frontier-evals/project/paperbench}"
PAPERBENCH_DATA_DIR="${PAPERBENCH_DATA_DIR:-$PAPERBENCH_DIR/data}"

# Where generated repos live by default. The actual repo graded is
# OUTPUT_REPO_DIR (resolved below), which defaults to:
#   $REPOS_ROOT_DIR/<PAPER_NAME>_repo
REPOS_ROOT_DIR="${REPOS_ROOT_DIR:-$ROOT_DIR/outputs/paperbench_repos}"

# ---- Input parameters ----
JUDGE="${JUDGE:-simple}"
GRADER_MODEL="${GRADER_MODEL:-${OPENAI_MODEL_DEPLOYMENT:-${GPT_VERSION:-o3-mini}}}"
GRADER_REASONING_EFFORT="${GRADER_REASONING_EFFORT:-high}"  # o3-mini-high
GRADER_LLM_PROVIDER="${GRADER_LLM_PROVIDER:-auto}"
GRADER_OPENAI_API_KEY="${GRADER_OPENAI_API_KEY:-}"
GRADER_OPENAI_BASE_URL="${GRADER_OPENAI_BASE_URL:-${OPENAI_BASE_URL:-}}"
GRADER_AZURE_OPENAI_API_KEY="${GRADER_AZURE_OPENAI_API_KEY:-${AZURE_OPENAI_API_KEY:-}}"
GRADER_AZURE_OPENAI_ENDPOINT="${GRADER_AZURE_OPENAI_ENDPOINT:-${AZURE_OPENAI_ENDPOINT:-}}"
AOAI_PROXY_ENDPOINT="${AOAI_PROXY_ENDPOINT:-http://127.0.0.1:8787}"
AOAI_PROXY_DUMMY_API_KEY="${AOAI_PROXY_DUMMY_API_KEY:-dummy}"
AOAI_PROXY_HEALTH_CHECK="${AOAI_PROXY_HEALTH_CHECK:-1}"
AOAI_PROXY_HEALTH_TIMEOUT_SECONDS="${AOAI_PROXY_HEALTH_TIMEOUT_SECONDS:-5}"
# URL path style the proxy expects (see header). auto = probe and decide.
AOAI_PROXY_API_STYLE="${AOAI_PROXY_API_STYLE:-auto}"
CODE_ONLY="${CODE_ONLY:-True}"
EVAL_LABEL="${EVAL_LABEL:-}"

# ---- Submission cleaning ----
# Grade a clutter-free COPY so the judge's file-ranking step sees only real code.
CLEAN_SUBMISSION="${CLEAN_SUBMISSION:-True}"
# Patterns to strip from the graded copy. If CLEAN_EXCLUDES is set in the env it
# is split on whitespace into the array; otherwise the curated default is used.
if [[ -n "${CLEAN_EXCLUDES:-}" ]]; then
    read -r -a CLEAN_EXCLUDES <<< "$CLEAN_EXCLUDES"
else
    CLEAN_EXCLUDES=(
        ".venv" "venv" "env" "site-packages" "node_modules"   # vendored deps / envs
        "__pycache__" "*.pyc" "*.pyo" "*.pyd"                  # python caches
        ".pytest_cache" ".mypy_cache" ".ruff_cache"            # tool caches
        ".ipynb_checkpoints" "*.egg-info" ".DS_Store"          # misc clutter
        ".git" ".hg" ".svn"                                    # VCS metadata
        "*.bak"                                                # repair-stage backups
        "outputs" "wandb" ".hydra" "lightning_logs" "mlruns"   # run/experiment outputs
        "tests"                                                # test scaffolding
        "third_party" "vendor"                                 # vendored source trees
    )
fi

CURRENT_STEP="initialization"
SELECTED_GRADER_PROVIDER=""
SELECTED_GRADER_BASE_URL=""
TMP_CLEAN_DIR=""              # temp dir holding the cleaned copy (removed on exit)
ORIGINAL_SUBMISSION_PATH=""   # the real repo, before cleaning

on_error() {
    local status=$?
    local failed_command=${BASH_COMMAND:-unknown}
    echo "[grade_paper] ERROR during ${CURRENT_STEP}: exit status ${status}" >&2
    echo "[grade_paper] Last command: ${failed_command}" >&2
    echo "[grade_paper] PAPER_NAME=${PAPER_NAME:-unset}; PAPERNAME=${PAPERNAME:-unset}; GRADER_LLM_PROVIDER=${GRADER_LLM_PROVIDER}" >&2
    exit "$status"
}
trap on_error ERR

die() {
    echo "Error: $*" >&2
    exit 1
}

# cleanup_clean_submission: remove the temporary cleaned-copy directory (if one was
# created) when the script exits for any reason. The original repo is never touched.
cleanup_clean_submission() {
    if [[ -n "${TMP_CLEAN_DIR:-}" && -d "$TMP_CLEAN_DIR" ]]; then
        rm -rf "$TMP_CLEAN_DIR"
    fi
}
trap cleanup_clean_submission EXIT

# build_clean_submission: copy $1 into a fresh temp dir with the CLEAN_EXCLUDES
# patterns stripped (vendored envs, caches, backups, outputs, tests, ...), then
# point SUBMISSION_PATH at the copy. Uses rsync when available (never copies the
# excluded files at all - important for huge vendored .venv trees); otherwise
# falls back to cp + find-prune. Prints how many files were dropped.
build_clean_submission() {
    local src="$1"
    local before after pat

    TMP_CLEAN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/grade_clean_${PAPER_NAME}_XXXXXX")" \
        || die "Failed to create temp dir for the cleaned submission copy."

    before="$(find "$src" -type f 2>/dev/null | wc -l | tr -d ' ')"

    if command -v rsync >/dev/null 2>&1; then
        local rsync_args=(-a)
        for pat in "${CLEAN_EXCLUDES[@]}"; do
            rsync_args+=("--exclude=$pat")
        done
        if ! rsync "${rsync_args[@]}" "$src/" "$TMP_CLEAN_DIR/"; then
            die "rsync failed while building the cleaned submission copy."
        fi
    else
        if ! cp -a "$src/." "$TMP_CLEAN_DIR/"; then
            die "cp failed while building the cleaned submission copy."
        fi
        for pat in "${CLEAN_EXCLUDES[@]}"; do
            # -depth deletes children before parents; basename match handles dirs and globs
            find "$TMP_CLEAN_DIR" -depth -name "$pat" -exec rm -rf {} + 2>/dev/null || true
        done
    fi

    after="$(find "$TMP_CLEAN_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')"
    echo "  Cleaning   : stripped clutter for grading (files ${before} -> ${after})"
    echo "  Excluded   : ${CLEAN_EXCLUDES[*]}"
    SUBMISSION_PATH="$TMP_CLEAN_DIR"
}

trim_trailing_slashes() {
    local value="$1"
    while [[ "$value" == */ ]]; do
        value="${value%/}"
    done
    printf '%s' "$value"
}

ensure_trailing_slash() {
    local value
    value="$(trim_trailing_slashes "$1")"
    printf '%s/\n' "$value"
}

normalize_paper_name() {
    local value="$1"
    value="$(printf '%s' "$value" | tr '[:upper:] _' '[:lower:]--')"
    value="$(printf '%s' "$value" | sed -E 's/-+/-/g; s/^-//; s/-$//')"
    printf '%s\n' "$value"
}

normalize_azure_openai_base_url() {
    local endpoint
    endpoint="$(trim_trailing_slashes "$1")"
    if [[ "$endpoint" == */openai/v1 ]]; then
        printf '%s/\n' "$endpoint"
    elif [[ "$endpoint" == */openai ]]; then
        printf '%s/v1/\n' "$endpoint"
    else
        printf '%s/openai/v1/\n' "$endpoint"
    fi
}

check_aoai_proxy_health() {
    local endpoint="$1"
    local health_url
    health_url="$(trim_trailing_slashes "$endpoint")/healthz"

    if [[ "$AOAI_PROXY_HEALTH_CHECK" == "0" ]]; then
        return 0
    fi

    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time "$AOAI_PROXY_HEALTH_TIMEOUT_SECONDS" "$health_url" >/dev/null
        return $?
    fi

    python3 - "$health_url" "$AOAI_PROXY_HEALTH_TIMEOUT_SECONDS" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
timeout = int(sys.argv[2])
with urllib.request.urlopen(url, timeout=timeout) as response:
    if response.status != 200:
        raise SystemExit(f"unexpected proxy health status: {response.status}")
PY
}

configure_openai_grader() {
    local api_key="${GRADER_OPENAI_API_KEY:-${OPENAI_API_KEY:-}}"
    if [[ -z "$api_key" ]]; then
        die "GRADER_LLM_PROVIDER=openai requires GRADER_OPENAI_API_KEY or OPENAI_API_KEY."
    fi

    export OPENAI_API_KEY="$api_key"
    if [[ -n "$GRADER_OPENAI_BASE_URL" ]]; then
        export OPENAI_BASE_URL
        OPENAI_BASE_URL="$(ensure_trailing_slash "$GRADER_OPENAI_BASE_URL")"
        SELECTED_GRADER_BASE_URL="$OPENAI_BASE_URL"
    else
        unset OPENAI_BASE_URL
        SELECTED_GRADER_BASE_URL="https://api.openai.com/v1/"
    fi
    SELECTED_GRADER_PROVIDER="openai"
}

configure_azure_grader() {
    if [[ -z "$GRADER_AZURE_OPENAI_API_KEY" ]]; then
        die "GRADER_LLM_PROVIDER=azure requires GRADER_AZURE_OPENAI_API_KEY or AZURE_OPENAI_API_KEY."
    fi
    if [[ -z "$GRADER_AZURE_OPENAI_ENDPOINT" ]]; then
        die "GRADER_LLM_PROVIDER=azure requires GRADER_AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_ENDPOINT."
    fi

    export OPENAI_API_KEY="$GRADER_AZURE_OPENAI_API_KEY"
    export OPENAI_BASE_URL
    OPENAI_BASE_URL="$(normalize_azure_openai_base_url "$GRADER_AZURE_OPENAI_ENDPOINT")"
    SELECTED_GRADER_PROVIDER="azure"
    SELECTED_GRADER_BASE_URL="$OPENAI_BASE_URL"
}

# probe_proxy_supports_openai_style: returns 0 if the proxy answers HTTP 200 at
# <endpoint>/v1/models (the OpenAI path style), non-zero otherwise. Used by
# AOAI_PROXY_API_STYLE=auto to tell an OpenAI-backed proxy (/v1/...) apart from
# an Azure-backed proxy (/openai/v1/...). The dummy key is fine here because the
# proxy strips it and injects the real upstream key before forwarding.
probe_proxy_supports_openai_style() {
    local endpoint
    endpoint="$(trim_trailing_slashes "$1")"
    local probe_url="$endpoint/v1/models"

    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time "$AOAI_PROXY_HEALTH_TIMEOUT_SECONDS" \
            -H "Authorization: Bearer $AOAI_PROXY_DUMMY_API_KEY" \
            "$probe_url" >/dev/null 2>&1
        return $?
    fi

    python3 - "$probe_url" "$AOAI_PROXY_HEALTH_TIMEOUT_SECONDS" "$AOAI_PROXY_DUMMY_API_KEY" <<'PY'
import sys
import urllib.request

url, timeout, key = sys.argv[1], int(sys.argv[2]), sys.argv[3]
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
try:
    with urllib.request.urlopen(req, timeout=timeout) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

configure_aoai_proxy_grader() {
    local endpoint
    endpoint="$(trim_trailing_slashes "$AOAI_PROXY_ENDPOINT")"
    if [[ -z "$endpoint" ]]; then
        die "GRADER_LLM_PROVIDER=aoai-proxy requires AOAI_PROXY_ENDPOINT."
    fi
    if [[ -z "$AOAI_PROXY_DUMMY_API_KEY" ]]; then
        die "GRADER_LLM_PROVIDER=aoai-proxy requires AOAI_PROXY_DUMMY_API_KEY."
    fi

    CURRENT_STEP="AOAI proxy health check"
    if ! check_aoai_proxy_health "$endpoint"; then
        die "AOAI proxy health check failed at ${endpoint}/healthz."
    fi

    # Pick the URL path style the proxy's upstream expects. The proxy forwards
    # our request path verbatim, so the wrong prefix makes the UPSTREAM 404
    # (e.g. Azure path /openai/v1/... sent to an OpenAI upstream).
    CURRENT_STEP="resolve AOAI proxy API style"
    local style="${AOAI_PROXY_API_STYLE,,}"
    if [[ "$style" == "auto" ]]; then
        if probe_proxy_supports_openai_style "$endpoint"; then
            style="openai"
        else
            style="azure"
        fi
    fi

    export OPENAI_API_KEY="$AOAI_PROXY_DUMMY_API_KEY"
    export OPENAI_BASE_URL
    case "$style" in
        openai)
            OPENAI_BASE_URL="$(ensure_trailing_slash "$endpoint/v1")"
            ;;
        azure)
            OPENAI_BASE_URL="$(normalize_azure_openai_base_url "$endpoint")"
            ;;
        *)
            die "AOAI_PROXY_API_STYLE must be auto, openai, or azure (got: $AOAI_PROXY_API_STYLE)."
            ;;
    esac
    SELECTED_GRADER_PROVIDER="aoai-proxy (${style}-style)"
    SELECTED_GRADER_BASE_URL="$OPENAI_BASE_URL"
}

select_auto_grader_provider() {
    local proxy_endpoint
    proxy_endpoint="$(trim_trailing_slashes "$AOAI_PROXY_ENDPOINT")"

    if [[ -n "${GRADER_OPENAI_API_KEY:-${OPENAI_API_KEY:-}}" ]]; then
        configure_openai_grader
    elif [[ -n "$GRADER_AZURE_OPENAI_API_KEY" && -n "$GRADER_AZURE_OPENAI_ENDPOINT" ]]; then
        configure_azure_grader
    elif [[ -n "$proxy_endpoint" ]] && check_aoai_proxy_health "$proxy_endpoint"; then
        configure_aoai_proxy_grader
    else
        die "JUDGE=simple requires OpenAI credentials, Azure credentials, or a healthy AOAI proxy."
    fi
}

# ---- Validate PAPER_NAME ----
CURRENT_STEP="validate paper name"
if [[ -z "${PAPER_NAME:-}" && -n "${PAPERNAME:-}" ]]; then
    PAPER_NAME="$(normalize_paper_name "$PAPERNAME")"
    export PAPER_NAME
fi
if [[ -z "${PAPER_NAME:-}" ]]; then
    die "PAPER_NAME is required (e.g., PAPER_NAME=adaptive-pruning bash scripts/grade_paper.sh)."
fi

# ---- Resolve paths ----
CURRENT_STEP="resolve paths"
OUTPUT_REPO_DIR="${OUTPUT_REPO_DIR:-$REPOS_ROOT_DIR/${PAPER_NAME}_repo}"
SUBMISSION_PATH="$OUTPUT_REPO_DIR"
ORIGINAL_SUBMISSION_PATH="$SUBMISSION_PATH"

# Build timestamped output dir, with optional label prefix
TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
if [[ -n "$EVAL_LABEL" ]]; then
    GRADE_OUT_DIR="$ROOT_DIR/outputs/paperbench_eval/$PAPER_NAME/${EVAL_LABEL}_${TIMESTAMP}"
else
    GRADE_OUT_DIR="$ROOT_DIR/outputs/paperbench_eval/$PAPER_NAME/${TIMESTAMP}"
fi

# ---- Validate API key for simple judge ----
if [[ "$JUDGE" == "simple" ]]; then
    CURRENT_STEP="configure grader credentials"
    case "${GRADER_LLM_PROVIDER,,}" in
        auto)
            select_auto_grader_provider
            ;;
        openai)
            configure_openai_grader
            ;;
        azure)
            configure_azure_grader
            ;;
        aoai-proxy|aoai_proxy)
            configure_aoai_proxy_grader
            ;;
        *)
            die "GRADER_LLM_PROVIDER must be auto, openai, azure, or aoai-proxy (got: $GRADER_LLM_PROVIDER)."
            ;;
    esac
fi

# ---- Validate submission directory ----
CURRENT_STEP="validate submission directory"
if [[ ! -d "$SUBMISSION_PATH" ]]; then
    echo "Error: Submission directory not found: $SUBMISSION_PATH" >&2
    echo "  Make sure stage 3 (or later) has been run for PAPER_NAME=$PAPER_NAME"
    exit 1
fi

# ---- Grade a clutter-free copy (optional) ----
# Repoints SUBMISSION_PATH at a stripped temp copy so the judge's file-ranking
# step is not drowned by vendored deps / caches / backups. Original is untouched.
if [[ "${CLEAN_SUBMISSION,,}" == "true" ]]; then
    CURRENT_STEP="build cleaned submission copy"
    build_clean_submission "$SUBMISSION_PATH"
fi

# ---- Validate PaperBench has a rubric for this paper ----
CURRENT_STEP="validate PaperBench rubric"
if [[ ! -d "$PAPERBENCH_DATA_DIR/papers/$PAPER_NAME" ]]; then
    echo "Error: No PaperBench rubric found for paper '$PAPER_NAME'." >&2
    echo "  Expected: $PAPERBENCH_DATA_DIR/papers/$PAPER_NAME/"
    echo "  Available papers:"
    ls "$PAPERBENCH_DATA_DIR/papers/" 2>/dev/null | sed 's/^/    /' || echo "    (could not list)"
    exit 1
fi

CURRENT_STEP="create output directory"
mkdir -p "$GRADE_OUT_DIR"

echo "  Paper      : $PAPER_NAME"
echo "  Judge      : $JUDGE"
echo "  Model      : $GRADER_MODEL (only for simple judge)"
if [[ "$JUDGE" == "simple" ]]; then
    echo "  Provider   : $SELECTED_GRADER_PROVIDER"
    echo "  Base URL   : $SELECTED_GRADER_BASE_URL"
fi
echo "  Submission : $ORIGINAL_SUBMISSION_PATH"
if [[ -n "${TMP_CLEAN_DIR:-}" ]]; then
    echo "  Graded copy: $SUBMISSION_PATH (cleaned, temporary)"
fi
echo "  Output dir : $GRADE_OUT_DIR"

# ---- Build judge arguments ----
CURRENT_STEP="build judge arguments"
judge_args=(
    "--submission_path=$SUBMISSION_PATH"
    "--paper_id=$PAPER_NAME"
    "--judge=$JUDGE"
    "--code_only=$CODE_ONLY"
    "--out_dir=$GRADE_OUT_DIR"
)

# completer_config is required only for simple judge
if [[ "$JUDGE" == "simple" ]]; then
    judge_args+=(
        "--completer_config=preparedness_turn_completer.oai_completions_turn_completer:OpenAICompletionsTurnCompleter.Config"
        "--completer_config.model=$GRADER_MODEL"
    )
    # Optional: pass reasoning effort for reasoning models (e.g. o3-mini-high).
    # Only added when GRADER_REASONING_EFFORT is set, so default behavior is
    # unchanged for non-reasoning models.
    if [[ -n "${GRADER_REASONING_EFFORT:-}" ]]; then
        judge_args+=( "--completer_config.reasoning_effort=$GRADER_REASONING_EFFORT" )
    fi
fi

# ---- Run the judge from inside the paperbench directory ----
CURRENT_STEP="run PaperBench judge"
cd "$PAPERBENCH_DIR"
if ! PAPERBENCH_DATA_DIR="$PAPERBENCH_DATA_DIR" \
        uv run python paperbench/scripts/run_judge.py "${judge_args[@]}"; then
    echo "Error: PaperBench judge failed for paper '$PAPER_NAME'. See output above for details."
    exit 1
fi

# ---- Print score summary ----
CURRENT_STEP="print score summary"
RESULT_FILE="$GRADE_OUT_DIR/grader_output.json"
if [[ -f "$RESULT_FILE" ]]; then
    echo ""
    echo "------- PaperBench Score: $PAPER_NAME -------"
    python3 - "$RESULT_FILE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
score   = d["score"]
total   = d["num_leaf_nodes"]
invalid = d["num_invalid_leaf_nodes"]
valid   = total - invalid
print(f"  Score : {score:.4f}")
print(f"  Nodes : {valid}/{total} valid  ({invalid} invalid/failed)")
print(f"  Output: {sys.argv[1]}")
PYEOF
else
    echo "Warning: grader_output.json not found at $RESULT_FILE"
fi
