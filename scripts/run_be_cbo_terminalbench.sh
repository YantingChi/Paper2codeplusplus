#!/usr/bin/env bash
set -euo pipefail

# One-click runner for the BE-CBO Terminal-Bench bundle.
#
# Default flow:
# 1) generate the bundle
# 2) build the Docker image
# 3) run the reference solution
# 4) copy outputs back to the host
# 5) run the checker
#
# Usage:
#   bash scripts/run_be_cbo_terminalbench.sh
#   bash scripts/run_be_cbo_terminalbench.sh generate
#   bash scripts/run_be_cbo_terminalbench.sh build
#   bash scripts/run_be_cbo_terminalbench.sh run
#   bash scripts/run_be_cbo_terminalbench.sh all
#
# Common overrides:
#   PYTHON_BIN=python3.10
#   IMAGE_NAME=paper2code-be-cbo-bundle:test
#   KEEP_CONTAINER=1

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

-
usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_be_cbo_terminalbench.sh [generate|build|run|all]

Stages:
  generate   Generate the Terminal-Bench bundle only.
  build      Generate the bundle and build the Docker image.
  run        Generate, build, run the solution, and copy outputs to host.
  all        Generate, build, run, copy outputs, and run checker. Default.

Environment overrides:
  PYTHON_BIN          Python executable for bundle generation.
  TARGET_REPO_DIR     Generated Paper2Code repo directory.
  EVAL_METRICS_PATH   Explicit paper_only_eval_info JSON path.
  PAPER_JSON_PATH     Paper source JSON path.
  OUTPUT_BUNDLE_DIR   Bundle output directory.
  IMAGE_NAME          Docker image tag.
  CONTAINER_NAME      Docker container name.
  HOST_RESULTS_DIR    Host directory for copied reproduction outputs.
  KEEP_CONTAINER      Keep the container after completion when set to 1.
  LOCAL_MACHINE_CHECK Enable local machine probe when set to 1. Default: 1.
EOF
}

log() {
  echo "[INFO] $*"
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

find_eval_metrics_path() {
  if [[ -n "${EVAL_METRICS_PATH:-}" ]]; then
    printf '%s\n' "${EVAL_METRICS_PATH}"
    return
  fi

  local latest_path
  latest_path="$(
    find "${EVAL_DIR}" -maxdepth 1 -type f -name "${PAPER_NAME}_paper_only_eval_info_*.json" \
      | sort \
      | tail -n 1
  )"

  if [[ -z "${latest_path}" ]]; then
    die "Could not find ${PAPER_NAME}_paper_only_eval_info_*.json under ${EVAL_DIR}"
  fi

  printf '%s\n' "${latest_path}"
}

require_file() {
  local path="$1"
  [[ -e "${path}" ]] || die "Required path does not exist: ${path}"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker command not found"
}

cleanup_container() {
  if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null
  fi
}

run_generate() {
  local eval_metrics_path
  eval_metrics_path="$(find_eval_metrics_path)"

  require_file "${TARGET_REPO_DIR}"
  require_file "${eval_metrics_path}"
  require_file "${PAPER_JSON_PATH}"

  mkdir -p "${OUTPUT_BUNDLE_DIR}"

  log "Generating bundle into ${OUTPUT_BUNDLE_DIR}"
  local cmd=(
    "${PYTHON_BIN}"
    "${PROJECT_ROOT}/codes/eval_terminalbench_bundle.py"
    --paper_name "${PAPER_NAME}"
    --target_repo_dir "${TARGET_REPO_DIR}"
    --eval_metrics_path "${eval_metrics_path}"
    --paper_json_path "${PAPER_JSON_PATH}"
    --output_bundle_dir "${OUTPUT_BUNDLE_DIR}"
  )

  if [[ "${LOCAL_MACHINE_CHECK}" == "1" ]]; then
    cmd+=(--local_machine_check)
  fi

  "${cmd[@]}"
}

run_build() {
  require_docker
  require_file "${OUTPUT_BUNDLE_DIR}/Dockerfile"

  log "Building Docker image ${IMAGE_NAME}"
  docker build -t "${IMAGE_NAME}" "${OUTPUT_BUNDLE_DIR}"
}

run_solution() {
  require_docker

  mkdir -p "${HOST_RESULTS_DIR}"
  cleanup_container

  log "Starting container ${CONTAINER_NAME}"
  docker run -d --name "${CONTAINER_NAME}" "${IMAGE_NAME}" bash -lc 'sleep infinity' >/dev/null

  log "Running BE-CBO reference solution"
  docker exec "${CONTAINER_NAME}" bash /app/solution.sh

  log "Copying outputs to ${HOST_RESULTS_DIR}"
  rm -rf "${HOST_RESULTS_DIR}"
  mkdir -p "${HOST_RESULTS_DIR}"
  docker cp "${CONTAINER_NAME}:/app/repro_outputs/." "${HOST_RESULTS_DIR}/"
  docker logs "${CONTAINER_NAME}" > "${HOST_RESULTS_DIR}/container.log" 2>&1 || true
}

run_checker() {
  require_docker

  log "Running checker"
  docker exec "${CONTAINER_NAME}" bash /app/run-tests.sh
}

case "${ACTION}" in
  generate)
    run_generate
    ;;
  build)
    run_generate
    run_build
    ;;
  run)
    run_generate
    run_build
    run_solution
    ;;
  all)
    run_generate
    run_build
    run_solution
    run_checker
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    die "Unknown action: ${ACTION}"
    ;;
esac

if [[ "${KEEP_CONTAINER}" != "1" && "${ACTION}" != "generate" && "${ACTION}" != "build" ]]; then
  cleanup_container
fi

log "Done."
