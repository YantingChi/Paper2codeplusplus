#!/bin/bash
set -euo pipefail

mkdir -p /logs/verifier
cd /workspace

verification_output="/tmp/verification_result.json"

set +e
python environment/benchmark/run_benchmark.py \
  --repo-dir environment/codebase \
  --cases environment/benchmark/synthetic_cases.json \
  --expected environment/expected_results/reference_results.json \
  --output "${verification_output}"
runner_exit=$?
set -e

python /tests/check_results.py \
  --expected /workspace/environment/expected_results/reference_results.json \
  --verification-output "${verification_output}" \
  --agent-output /workspace/environment/codebase/reproduction_result.json \
  --runner-exit "${runner_exit}"

exit 0
