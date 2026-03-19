#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set before running this script.}"

python3.10 "$ROOT_DIR/codes/eval_generate_benchmark_usage.py" \
  --paper_name "BE-CBO" \
  --paper_format "JSON" \
  --pdf_json_path "$ROOT_DIR/data/lightweight/BE-CBO/BE-CBO_cleaned.json" \
  --benchmark_details_path "$ROOT_DIR/outputs/BE-CBO_repo/eval/BE-CBO_paper_only_eval_info_o3-mini_20260316_113056.json" \
  --output_repo_dir "$ROOT_DIR/outputs/BE-CBO_repo" \
  --eval_result_dir "$ROOT_DIR/outputs/BE-CBO_repo/eval" \
  --gpt_version "o3-mini" \
  --run_downloads
