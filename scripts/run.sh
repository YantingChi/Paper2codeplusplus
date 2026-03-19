#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/mnt/blk1/Paper2Code"

# export OPENAI_API_KEY=""

PYTHON_BIN="${PYTHON_BIN:-python3.10}"
GPT_VERSION="${GPT_VERSION:-o3-mini}"




PAPER_NAME="TyPro"
PDF_JSON_PATH=$ROOT_DIR/data/lightweight/${PAPER_NAME}/${PAPER_NAME}.json
PDF_JSON_CLEANED_PATH=$ROOT_DIR/data/lightweight/${PAPER_NAME}/${PAPER_NAME}_cleaned.json


OUTPUT_ROOT=$ROOT_DIR/outputs/$PAPER_NAME

OUTPUT_DIR="${OUTPUT_DIR:-$OUTPUT_ROOT}"
OUTPUT_REPO_DIR="${OUTPUT_REPO_DIR:-${OUTPUT_ROOT}_repo}"
EVAL_DIR="$OUTPUT_REPO_DIR/eval"
EVAL_METRICS_PATH="${EVAL_METRICS_PATH:-$EVAL_DIR/${PAPER_NAME}_paper_only_eval_info_${GPT_VERSION}.json}"
OUTPUT_BUNDLE_DIR="${OUTPUT_BUNDLE_DIR:-$EVAL_DIR/benchmark}"
IMAGE_NAME="${IMAGE_NAME:-paper2code-be-cbo-bundle:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-paper2code-be-cbo-bundle-run}"
HOST_RESULTS_DIR="${HOST_RESULTS_DIR:-$OUTPUT_BUNDLE_DIR/artifacts/latest_run}"
KEEP_CONTAINER="${KEEP_CONTAINER:-0}"
LOCAL_MACHINE_CHECK="${LOCAL_MACHINE_CHECK:-1}"

mkdir -p "$OUTPUT_DIR" "$OUTPUT_REPO_DIR" "$EVAL_DIR"

echo "$PAPER_NAME"

echo "------- Preprocess -------"

pushd ./
cd $ROOT_DIR


# "$PYTHON_BIN" "$ROOT_DIR/codes/0_pdf_process.py" \
#     --input_json_path "$PDF_JSON_PATH" \
#     --output_json_path "$PDF_JSON_CLEANED_PATH"

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
    --output_path "$EVAL_METRICS_PATH" \
    --paper_format JSON \
    --pdf_json_path "$PDF_JSON_CLEANED_PATH" \
    --gpt_version "$GPT_VERSION"

echo "------- Harbor Bundle -------"

PYTHON_BIN="$PYTHON_BIN" \
PAPER_NAME="$PAPER_NAME" \
TARGET_REPO_DIR="$OUTPUT_REPO_DIR" \
EVAL_DIR="$EVAL_DIR" \
EVAL_METRICS_PATH="$EVAL_METRICS_PATH" \
PAPER_JSON_PATH="$PDF_JSON_PATH" \
OUTPUT_BUNDLE_DIR="$OUTPUT_BUNDLE_DIR" \
IMAGE_NAME="$IMAGE_NAME" \
CONTAINER_NAME="$CONTAINER_NAME" \
HOST_RESULTS_DIR="$HOST_RESULTS_DIR" \
KEEP_CONTAINER="$KEEP_CONTAINER" \
LOCAL_MACHINE_CHECK="$LOCAL_MACHINE_CHECK" \
bash "$ROOT_DIR/scripts/run_be_cbo_terminalbench.sh" generate

popd
