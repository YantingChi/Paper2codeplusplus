#!/usr/bin/env bash
# Runs Paper2Code through the local Azure OpenAI proxy.
# Sample usage:
# PAPER_NAME=adaptive-pruning bash scripts/run_paper_with_aoai_proxy.sh --only 5.1
# AOAI_PROXY_ENDPOINT=http://127.0.0.1:8788 PAPER_NAME=bam bash scripts/run_paper_with_aoai_proxy.sh --stages 7,8
# PAPER_NAME=bbox AOAI_PROXY_HEALTH_CHECK=0 bash scripts/run_paper_with_aoai_proxy.sh --start 5
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AOAI_PROXY_ENDPOINT="${AOAI_PROXY_ENDPOINT:-http://127.0.0.1:8787}"
AOAI_PROXY_ENDPOINT="${AOAI_PROXY_ENDPOINT%/}"
AOAI_PROXY_DUMMY_API_KEY="${AOAI_PROXY_DUMMY_API_KEY:-dummy}"
AOAI_PROXY_HEALTH_CHECK="${AOAI_PROXY_HEALTH_CHECK:-1}"
AOAI_PROXY_HEALTH_TIMEOUT_SECONDS="${AOAI_PROXY_HEALTH_TIMEOUT_SECONDS:-5}"
DELEGATE_SCRIPT="${DELEGATE_SCRIPT:-$ROOT_DIR/scripts/run_codex.sh}"
CURRENT_STEP="initialization"

on_error() {
    local status=$?
    local failed_command=${BASH_COMMAND:-unknown}
    echo "[run_paper_with_aoai_proxy] ERROR during ${CURRENT_STEP}: exit status ${status}" >&2
    echo "[run_paper_with_aoai_proxy] Last command: ${failed_command}" >&2
    echo "[run_paper_with_aoai_proxy] AOAI_PROXY_ENDPOINT=${AOAI_PROXY_ENDPOINT}; PAPER_NAME=${PAPER_NAME:-unset}" >&2
    exit "$status"
}
trap on_error ERR

# Validate the launcher inputs before exporting proxy credentials.
CURRENT_STEP="preflight"
if [[ ! -f "$DELEGATE_SCRIPT" ]]; then
    echo "Error: delegate script not found: $DELEGATE_SCRIPT" >&2
    exit 1
fi
if [[ -z "${PAPER_NAME:-}" ]]; then
    echo "Error: PAPER_NAME is required (e.g., PAPER_NAME=adaptive-pruning bash scripts/run_paper_with_aoai_proxy.sh --only 5.1)" >&2
    exit 1
fi

# Check the local proxy before starting a long Paper2Code run.
if [[ "$AOAI_PROXY_HEALTH_CHECK" == "1" ]]; then
    CURRENT_STEP="proxy health check"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time "$AOAI_PROXY_HEALTH_TIMEOUT_SECONDS" "$AOAI_PROXY_ENDPOINT/healthz" >/dev/null
    else
        python3 - "$AOAI_PROXY_ENDPOINT/healthz" "$AOAI_PROXY_HEALTH_TIMEOUT_SECONDS" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
timeout = int(sys.argv[2])
with urllib.request.urlopen(url, timeout=timeout) as response:
    if response.status != 200:
        raise SystemExit(f"unexpected proxy health status: {response.status}")
PY
    fi
fi

# Expose only the local proxy endpoint and a dummy key to Paper2Code.
CURRENT_STEP="export proxy environment"
export PAPER2CODE_LLM_PROVIDER=azure
export AZURE_OPENAI_ENDPOINT="$AOAI_PROXY_ENDPOINT"
export AZURE_OPENAI_API_KEY="$AOAI_PROXY_DUMMY_API_KEY"

# Delegate to the existing Paper2Code stage runner.
CURRENT_STEP="run Paper2Code"
exec bash "$DELEGATE_SCRIPT" "$@"
