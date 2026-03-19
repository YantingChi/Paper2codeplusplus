#!/usr/bin/env bash
set -euo pipefail

# Run the generated BE-CBO task with the official terminal-bench harness.
#
# This script:
# 1) regenerates the BE-CBO bundle
# 2) installs uv if needed
# 3) injects a lightweight docker compose compatibility shim
# 4) builds the task through `tb tasks build`
# 5) runs the oracle agent through `tb run`
#
# Usage:
#   bash scripts/run_be_cbo_tb_oracle.sh
#   bash scripts/run_be_cbo_tb_oracle.sh build
#   bash scripts/run_be_cbo_tb_oracle.sh run
#   bash scripts/run_be_cbo_tb_oracle.sh all

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
TB_ROOT="${TB_ROOT:-${PROJECT_ROOT}/terminal-bench}"
PAPER_NAME="${PAPER_NAME:-BE-CBO}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TARGET_REPO_DIR="${TARGET_REPO_DIR:-${PROJECT_ROOT}/outputs/${PAPER_NAME}_repo}"
TASKS_DIR="${TASKS_DIR:-${TARGET_REPO_DIR}/eval}"
EVAL_DIR="${EVAL_DIR:-${TARGET_REPO_DIR}/eval}"
PAPER_JSON_PATH="${PAPER_JSON_PATH:-${PROJECT_ROOT}/data/lightweight/${PAPER_NAME}/${PAPER_NAME}.json}"
OUTPUT_BUNDLE_DIR="${OUTPUT_BUNDLE_DIR:-${EVAL_DIR}/benchmark}"
IMAGE_NAME="${IMAGE_NAME:-paper2code-be-cbo-bundle:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-paper2code-be-cbo-bundle-run}"
HOST_RESULTS_DIR="${HOST_RESULTS_DIR:-${OUTPUT_BUNDLE_DIR}/artifacts/latest_run}"
KEEP_CONTAINER="${KEEP_CONTAINER:-0}"
LOCAL_MACHINE_CHECK="${LOCAL_MACHINE_CHECK:-1}"
TASK_ID="${TASK_ID:-benchmark}"
RUNS_DIR="${RUNS_DIR:-${TARGET_REPO_DIR}/eval/tb_runs}"
RUN_ID="${RUN_ID:-be-cbo-oracle-$(date +%Y%m%d_%H%M%S)}"
UV_BIN="${UV_BIN:-${HOME}/.local/bin/uv}"
ACTION="${1:-all}"
SHIM_DIR=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_be_cbo_tb_oracle.sh [build|run|all]

Stages:
  build   Regenerate the bundle and build the task through terminal-bench.
  run     Regenerate, build, and run the oracle agent through terminal-bench.
  all     Same as run. Default.

Outputs:
  Runs are written to outputs/BE-CBO_repo/eval/tb_runs/<run_id>/
EOF
}

log() {
  echo "[INFO] $*"
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

ensure_uv() {
  if [[ -x "${UV_BIN}" ]]; then
    return
  fi

  log "Installing uv into user site-packages"
  python3 -m pip install --user uv
  [[ -x "${UV_BIN}" ]] || die "uv installation finished but ${UV_BIN} was not found"
}

ensure_docker() {
  command -v docker >/dev/null 2>&1 || die "docker command not found"
}

generate_bundle() {
  log "Regenerating BE-CBO bundle"
  PYTHON_BIN="${PYTHON_BIN}" \
  PAPER_NAME="${PAPER_NAME}" \
  TARGET_REPO_DIR="${TARGET_REPO_DIR}" \
  EVAL_DIR="${EVAL_DIR}" \
  PAPER_JSON_PATH="${PAPER_JSON_PATH}" \
  OUTPUT_BUNDLE_DIR="${OUTPUT_BUNDLE_DIR}" \
  IMAGE_NAME="${IMAGE_NAME}" \
  CONTAINER_NAME="${CONTAINER_NAME}" \
  HOST_RESULTS_DIR="${HOST_RESULTS_DIR}" \
  KEEP_CONTAINER="${KEEP_CONTAINER}" \
  LOCAL_MACHINE_CHECK="${LOCAL_MACHINE_CHECK}" \
  bash "${PROJECT_ROOT}/scripts/run_be_cbo_terminalbench.sh" generate
}

make_docker_compose_shim() {
  local shim_dir="$1"
  local real_docker
  real_docker="$(command -v docker)"

  mkdir -p "${shim_dir}"
  cat > "${shim_dir}/docker" <<EOF
#!/usr/bin/env bash
set -euo pipefail

REAL_DOCKER="${real_docker}"

if [[ "\${1:-}" != "compose" ]]; then
  exec "\${REAL_DOCKER}" "\$@"
fi

shift
project_name=""
compose_file=""

while [[ \$# -gt 0 ]]; do
  case "\$1" in
    -p)
      project_name="\$2"
      shift 2
      ;;
    -f)
      compose_file="\$2"
      shift 2
      ;;
    *)
      break
      ;;
  esac
done

subcommand="\${1:-}"
shift || true

if [[ -z "\${compose_file}" ]]; then
  echo "[docker-compose-shim] missing compose file" >&2
  exit 1
fi

task_dir="\$(cd -- "\$(dirname -- "\${compose_file}")" && pwd)"
image_name="\${T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME:?missing image env}"
container_name="\${T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME:?missing container env}"
host_logs_path="\${T_BENCH_TASK_LOGS_PATH:-}"
host_agent_logs_path="\${T_BENCH_TASK_AGENT_LOGS_PATH:-}"
container_logs_path="\${T_BENCH_CONTAINER_LOGS_PATH:-/logs}"
container_agent_logs_path="\${T_BENCH_CONTAINER_AGENT_LOGS_PATH:-/agent-logs}"
test_dir="\${T_BENCH_TEST_DIR:-/tests}"

case "\${subcommand}" in
  build)
    exec "\${REAL_DOCKER}" build -t "\${image_name}" -f "\${task_dir}/Dockerfile" "\${task_dir}"
    ;;
  up)
    mkdir -p "\${host_logs_path}" "\${host_agent_logs_path}"
    "\${REAL_DOCKER}" rm -f "\${container_name}" >/dev/null 2>&1 || true
    exec "\${REAL_DOCKER}" run -d \
      --name "\${container_name}" \
      -e TEST_DIR="\${test_dir}" \
      -v "\${host_logs_path}:\${container_logs_path}" \
      -v "\${host_agent_logs_path}:\${container_agent_logs_path}" \
      "\${image_name}" \
      sh -c "sleep infinity"
    ;;
  down)
    "\${REAL_DOCKER}" rm -f "\${container_name}" >/dev/null 2>&1 || true
    exit 0
    ;;
  *)
    echo "[docker-compose-shim] unsupported compose subcommand: \${subcommand}" >&2
    exit 1
    ;;
esac
EOF
  chmod +x "${shim_dir}/docker"
}

tb_build() {
  local shim_dir="$1"
  log "Building task ${TASK_ID} through terminal-bench"
  (
    cd "${TB_ROOT}"
    PATH="${shim_dir}:${PATH}" "${UV_BIN}" run tb tasks build -t "${TASK_ID}" --tasks-dir "${TASKS_DIR}"
  )
}

tb_run_oracle() {
  local shim_dir="$1"
  mkdir -p "${RUNS_DIR}"
  log "Running terminal-bench oracle for ${TASK_ID}"
  (
    cd "${TB_ROOT}"
    PATH="${shim_dir}:${PATH}" "${UV_BIN}" run tb run \
      --agent oracle \
      --dataset-path "${TASKS_DIR}" \
      --task-id "${TASK_ID}" \
      --output-path "${RUNS_DIR}" \
      --run-id "${RUN_ID}" \
      --n-concurrent 1 \
      --no-cleanup \
      --no-rebuild
  )
}

main() {
  ensure_docker
  ensure_uv
  generate_bundle

  SHIM_DIR="$(mktemp -d)"
  trap 'if [[ -n "${SHIM_DIR:-}" ]]; then rm -rf "${SHIM_DIR}"; fi' EXIT
  make_docker_compose_shim "${SHIM_DIR}"

  case "${ACTION}" in
    build)
      tb_build "${SHIM_DIR}"
      ;;
    run|all)
      tb_build "${SHIM_DIR}"
      tb_run_oracle "${SHIM_DIR}"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage
      die "Unknown action: ${ACTION}"
      ;;
  esac

  if [[ "${ACTION}" == "run" || "${ACTION}" == "all" ]]; then
    log "Run output: ${RUNS_DIR}/${RUN_ID}"
  fi

  log "Done."
}

main "$@"
