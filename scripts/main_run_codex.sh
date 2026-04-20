#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# export OPENAI_API_KEY=""

PYTHON_BIN="${PYTHON_BIN:-python3.10}"
GPT_VERSION="${GPT_VERSION:-o3-mini}"

PAPER_NAME="${PAPER_NAME:-BE-CBO}"
PDF_JSON_PATH="${PDF_JSON_PATH:-$ROOT_DIR/data/lightweight/BE-CBO/BE-CBO.json}"
PDF_JSON_CLEANED_PATH="${PDF_JSON_CLEANED_PATH:-$ROOT_DIR/data/lightweight/BE-CBO/BE-CBO_cleaned.json}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/outputs/BE-CBO}"
OUTPUT_REPO_DIR="${OUTPUT_REPO_DIR:-$ROOT_DIR/outputs/BE-CBO_repo}"
EVAL_DIR="${EVAL_DIR:-$OUTPUT_REPO_DIR/eval}"
EVAL_INFO_PATH="${EVAL_INFO_PATH:-$EVAL_DIR/${PAPER_NAME}_paper_only_eval_info_${GPT_VERSION}.json}"
OUTPUT_BUNDLE_DIR="${OUTPUT_BUNDLE_DIR:-$EVAL_DIR/benchmark}"
LOCAL_MACHINE_CHECK="${LOCAL_MACHINE_CHECK:-1}"

mkdir -p "$OUTPUT_DIR" "$OUTPUT_REPO_DIR" "$EVAL_DIR"

echo "$PAPER_NAME"

echo "------- Preprocess -------"

"$PYTHON_BIN" "$ROOT_DIR/codes/0_pdf_process.py" \
    --input_json_path "$PDF_JSON_PATH" \
    --output_json_path "$PDF_JSON_CLEANED_PATH"

echo "------- PaperCoder -------"

"$PYTHON_BIN" "$ROOT_DIR/codes/1_planning.py" \
    --paper_name "$PAPER_NAME" \
    --gpt_version "$GPT_VERSION" \
    --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
    --output_dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/codes/1.1_extract_config.py" \
    --paper_name "$PAPER_NAME" \
    --output_dir "$OUTPUT_DIR"

cp -rp "$OUTPUT_DIR/planning_config.yaml" "$OUTPUT_REPO_DIR/config.yaml"

"$PYTHON_BIN" "$ROOT_DIR/codes/2_analyzing.py" \
    --paper_name "$PAPER_NAME" \
    --gpt_version "$GPT_VERSION" \
    --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
    --output_dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/codes/3_coding.py" \
    --paper_name "$PAPER_NAME" \
    --gpt_version "$GPT_VERSION" \
    --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
    --output_dir "$OUTPUT_DIR" \
    --output_repo_dir "$OUTPUT_REPO_DIR"

echo "------- Eval Info -------"

"$PYTHON_BIN" "$ROOT_DIR/codes/5_eval_get_running_info.py" \
    --paper_name "$PAPER_NAME" \
    --paper_format JSON \
    --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
    --gpt_version "$GPT_VERSION" \
    --output_path "$EVAL_INFO_PATH"

echo "------- Harbor Bundle -------"

bundle_cmd=(
    "$PYTHON_BIN"
    "$ROOT_DIR/codes/6_eval_terminalbench_bundle.py"
    --paper_name "$PAPER_NAME"
    --target_repo_dir "$OUTPUT_REPO_DIR"
    --eval_metrics_path "$EVAL_INFO_PATH"
    --paper_json_path "$PDF_JSON_PATH"
    --output_bundle_dir "$OUTPUT_BUNDLE_DIR"
    --gpt_version "$GPT_VERSION"
)

if [[ "$LOCAL_MACHINE_CHECK" == "1" ]]; then
    bundle_cmd+=(--local_machine_check)
fi

"${bundle_cmd[@]}"
