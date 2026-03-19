#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/.env.azure" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.azure"
fi

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY or configure $ROOT_DIR/.env.azure first.}"

GPT_VERSION="${GPT_VERSION:-${OPENAI_MODEL_DEPLOYMENT:-gpt-5.4}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

PAPER_NAME="${PAPER_NAME:-Transformer}"
PDF_LATEX_CLEANED_PATH="${PDF_LATEX_CLEANED_PATH:-$ROOT_DIR/examples/Transformer_cleaned.tex}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/outputs/Transformer}"
OUTPUT_REPO_DIR="${OUTPUT_REPO_DIR:-$ROOT_DIR/outputs/Transformer_repo}"

mkdir -p "$OUTPUT_DIR" "$OUTPUT_REPO_DIR"

echo "$PAPER_NAME"
echo "------- PaperCoder -------"

"$PYTHON_BIN" "$ROOT_DIR/codes/1_planning.py" \
    --paper_name "$PAPER_NAME" \
    --gpt_version "$GPT_VERSION" \
    --pdf_latex_path "$PDF_LATEX_CLEANED_PATH" \
    --paper_format LaTeX \
    --output_dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/codes/1.1_extract_config.py" \
    --paper_name "$PAPER_NAME" \
    --output_dir "$OUTPUT_DIR"

cp -rp "$OUTPUT_DIR/planning_config.yaml" "$OUTPUT_REPO_DIR/config.yaml"

"$PYTHON_BIN" "$ROOT_DIR/codes/2_analyzing.py" \
    --paper_name "$PAPER_NAME" \
    --gpt_version "$GPT_VERSION" \
    --pdf_latex_path "$PDF_LATEX_CLEANED_PATH" \
    --paper_format LaTeX \
    --output_dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/codes/3_coding.py" \
    --paper_name "$PAPER_NAME" \
    --gpt_version "$GPT_VERSION" \
    --pdf_latex_path "$PDF_LATEX_CLEANED_PATH" \
    --paper_format LaTeX \
    --output_dir "$OUTPUT_DIR" \
    --output_repo_dir "$OUTPUT_REPO_DIR"
