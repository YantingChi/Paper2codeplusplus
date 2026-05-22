# Paper2Code++

Paper2Code++ generates a paper-specific reproduction repository from a cleaned
paper input, then can optionally build evaluation rubrics, tests, and Harbor
benchmark tasks around that generated repository.

## User-configurable values

Set these values before running commands. The paths and model name below are
examples; replace them for your paper and environment.

```bash
export PAPER_NAME="adaptive-pruning"
export PAPER_FORMAT="JSON"
export PAPER_JSON_PATH="/absolute/path/to/paper_cleaned.json"
export PAPER_LATEX_PATH=""
export MODEL="gpt-5.4"

export RUN_ROOT="/mnt/blk1/Paper2Code/outputs/${PAPER_NAME}"
export PIPELINE_OUTPUT_DIR="${RUN_ROOT}/planning"
export GENERATED_REPO_DIR="${RUN_ROOT}/generated_repo"

export EVAL_INFO_JSON="${RUN_ROOT}/eval_info.json"
export EVAL_PLAN_DIR="${RUN_ROOT}/eval_plan"
export HARBOR_ASSET_DIR="${RUN_ROOT}/harbor_asset"
export RUBRIC_PASS1_JSON="${RUN_ROOT}/rubric_pass1.json"
export RUBRIC_JSON="${RUN_ROOT}/paper2code_rubric.json"
export UNIT_TEST_DIR="${RUN_ROOT}/unit_tests"
export HARBOR_OUTPUT_DIR="/mnt/blk1/Paper2Code/outputs/harbor_tasks"
```

## API key setup

You must provide your own API key. Do not commit real keys into this repository,
and do not paste real keys into README or config files.

For the standard OpenAI API:

```bash
export PAPER2CODE_LLM_PROVIDER="openai"
export OPENAI_API_KEY="replace-with-your-own-api-key"
```

For Azure OpenAI instead:

```bash
export PAPER2CODE_LLM_PROVIDER="azure"
export AZURE_OPENAI_API_KEY="replace-with-your-own-azure-key"
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_OPENAI_API_VERSION="2024-12-01-preview"
```

## Sample usage

```bash
cd /mnt/blk1/Paper2Code
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install openai tqdm pyyaml huggingface_hub datasets requests
mkdir -p "${RUN_ROOT}" "${PIPELINE_OUTPUT_DIR}" "${GENERATED_REPO_DIR}"
```

## How to reproduce result

1. Prepare a cleaned paper input.

   If you already have a cleaned JSON or LaTeX file, set `PAPER_JSON_PATH` or
   `PAPER_LATEX_PATH` above and skip this step. If you have a raw S2ORC-style
   JSON file, clean it first:

   ```bash
   python codes/0_pdf_process.py \
     --input_json_path "/absolute/path/to/raw_paper.json" \
     --output_json_path "${PAPER_JSON_PATH}"
   ```

2. Generate the reproduction plan and extracted config.

   ```bash
   python codes/1_planning.py \
     --paper_name "${PAPER_NAME}" \
     --gpt_version "${MODEL}" \
     --paper_format "${PAPER_FORMAT}" \
     --pdf_json_path "${PAPER_JSON_PATH}" \
     --pdf_latex_path "${PAPER_LATEX_PATH}" \
     --output_dir "${PIPELINE_OUTPUT_DIR}"

   python codes/1.1_extract_config.py \
     --paper_name "${PAPER_NAME}" \
     --output_dir "${PIPELINE_OUTPUT_DIR}"
   ```

3. Optionally refine model and dataset names against Hugging Face.

   ```bash
   python codes/1.2_rag_config.py \
     --output_dir "${PIPELINE_OUTPUT_DIR}" \
     --gpt_version "gpt-4.1-mini"
   ```

4. Generate analysis artifacts and code.

   ```bash
   python codes/2_analyzing.py \
     --paper_name "${PAPER_NAME}" \
     --gpt_version "${MODEL}" \
     --paper_format "${PAPER_FORMAT}" \
     --pdf_json_path "${PAPER_JSON_PATH}" \
     --pdf_latex_path "${PAPER_LATEX_PATH}" \
     --output_dir "${PIPELINE_OUTPUT_DIR}"

   python codes/3_coding.py \
     --paper_name "${PAPER_NAME}" \
     --gpt_version "${MODEL}" \
     --paper_format "${PAPER_FORMAT}" \
     --pdf_json_path "${PAPER_JSON_PATH}" \
     --pdf_latex_path "${PAPER_LATEX_PATH}" \
     --output_dir "${PIPELINE_OUTPUT_DIR}" \
     --output_repo_dir "${GENERATED_REPO_DIR}"
   ```

5. Run the generated reproduction repository.

   The generated entry point depends on the paper and on the files selected by
   the planning stage.

   ```bash
   cd "${GENERATED_REPO_DIR}"
   if [ -f requirements.txt ]; then python -m pip install -r requirements.txt; fi
   if [ -f reproduce.sh ]; then
     bash reproduce.sh
   elif [ -f main.py ]; then
     python main.py
   elif [ -f app.py ]; then
     python app.py
   else
     find . -maxdepth 2 -type f | sort
   fi
   ```

## Optional evaluation artifacts

These steps create paper-grounded evaluation metadata, asset plans, rubrics,
pytest suites, and a Harbor task. Stage 6 requires the `codex` CLI if assets
need to be materialized.

```bash
cd /mnt/blk1/Paper2Code

python codes/5_eval_get_running_info.py \
  --paper_name "${PAPER_NAME}" \
  --paper_format "${PAPER_FORMAT}" \
  --pdf_json_path "${PAPER_JSON_PATH}" \
  --pdf_latex_path "${PAPER_LATEX_PATH}" \
  --gpt_version "${MODEL}" \
  --output_path "${EVAL_INFO_JSON}"

python codes/5.1_get_evaluation_plan.py \
  --paper_json_path "${PAPER_JSON_PATH}" \
  --eval_info_json "${EVAL_INFO_JSON}" \
  --generated_repo_path "${GENERATED_REPO_DIR}" \
  --output_dir "${EVAL_PLAN_DIR}" \
  --gpt_version "${MODEL}"

python codes/6_download_dataset.py \
  --paper_json_path "${PAPER_JSON_PATH}" \
  --generated_repo_path "${GENERATED_REPO_DIR}" \
  --eval_info_json "${EVAL_INFO_JSON}" \
  --eval_plan_json "${EVAL_PLAN_DIR}/eval_plan.json" \
  --output_dir "${HARBOR_ASSET_DIR}" \
  --gpt_version "${MODEL}"

python codes/7_getting_rubric.py \
  --paper_name "${PAPER_NAME}" \
  --paper_format "${PAPER_FORMAT}" \
  --pdf_json_path "${PAPER_JSON_PATH}" \
  --pdf_latex_path "${PAPER_LATEX_PATH}" \
  --eval_plan_path "${EVAL_PLAN_DIR}/eval_plan.json" \
  --download_report_path "${HARBOR_ASSET_DIR}/c1_download_dataset_summary.json" \
  --gpt_version "${MODEL}" \
  --output_path "${RUBRIC_PASS1_JSON}"

python codes/8_getting_paper2code_rubric.py \
  --pass1_json_path "${RUBRIC_PASS1_JSON}" \
  --paper_name "${PAPER_NAME}" \
  --gpt_version "${MODEL}" \
  --output_path "${RUBRIC_JSON}"

python codes/9_getting_unit_test_codex.py \
  --rubric_json_path "${RUBRIC_JSON}" \
  --paper_name "${PAPER_NAME}" \
  --repo_plan_path "${PIPELINE_OUTPUT_DIR}/planning_response.json" \
  --generated_repo_path "${GENERATED_REPO_DIR}" \
  --output_dir "${UNIT_TEST_DIR}" \
  --gpt_version "${MODEL}"

python codes/10_get_harbor_set.py \
  --paper_name "${PAPER_NAME}" \
  --paper_json_path "${PAPER_JSON_PATH}" \
  --planning_dir "${PIPELINE_OUTPUT_DIR}" \
  --repo_dir "${GENERATED_REPO_DIR}" \
  --unit_test_dir "${UNIT_TEST_DIR}" \
  --harbor_asset_dir "${HARBOR_ASSET_DIR}" \
  --harbor_output_dir "${HARBOR_OUTPUT_DIR}" \
  --task_slug "${PAPER_NAME}" \
  --force
```
