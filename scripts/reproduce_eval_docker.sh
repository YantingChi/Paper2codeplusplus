#!/usr/bin/env bash
set -euo pipefail

# Reproduce evaluation from the Transformer plan:
# 1) build Docker env
# 2) mount code folder
# 3) download datasets (WMT14 de-en + WSJ)
# 4) run experiment commands
#
# Usage:
#   bash scripts/reproduce_eval_docker.sh [CODE_DIR]
#
# Example:
#   MODE=eval \
#   TRANSLATION_CHECKPOINT=checkpoints/checkpoint_step_50000.pt \
#   TASKS=translation,parsing \
#   bash scripts/reproduce_eval_docker.sh outputs/Transformer_repo
#
# Environment variables:
#   IMAGE_NAME               Docker image tag (default: paper2code-transformer-repro:latest)
#   MODE                     auto|train|eval|both (default: auto)
#   TASKS                    Comma-separated tasks: translation,parsing (default: translation,parsing)
#   USE_GPU                  auto|true|false (default: auto)
#   TRANSLATION_CHECKPOINT   Checkpoint path inside mounted repo for translation eval
#   PARSING_CHECKPOINT       Checkpoint path inside mounted repo for parsing eval
#   HF_CACHE_DIR             Host cache directory for huggingface datasets
#   WMT14_MAX_TRAIN          Optional cap for train examples (0 = full)
#   WMT14_MAX_VAL            Optional cap for val examples (0 = full)
#   WSJ_MAX_TRAIN            Optional cap for train examples (0 = full)
#   WSJ_MAX_VAL              Optional cap for val examples (0 = full)

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

CODE_DIR_INPUT="${1:-${PROJECT_ROOT}/outputs/Transformer_repo}"
if [[ "${CODE_DIR_INPUT}" = /* ]]; then
  CODE_DIR="${CODE_DIR_INPUT}"
else
  CODE_DIR="${PROJECT_ROOT}/${CODE_DIR_INPUT}"
fi

if [[ ! -d "${CODE_DIR}" ]]; then
  echo "[ERROR] CODE_DIR does not exist: ${CODE_DIR}"
  exit 1
fi

if [[ ! -f "${CODE_DIR}/main.py" || ! -f "${CODE_DIR}/config.yaml" ]]; then
  echo "[ERROR] CODE_DIR must contain main.py and config.yaml: ${CODE_DIR}"
  exit 1
fi

IMAGE_NAME="${IMAGE_NAME:-paper2code-transformer-repro:latest}"
MODE="${MODE:-auto}"
TASKS="${TASKS:-translation,parsing}"
USE_GPU="${USE_GPU:-auto}"

TRANSLATION_CHECKPOINT="${TRANSLATION_CHECKPOINT:-checkpoints/checkpoint_step_50000.pt}"
PARSING_CHECKPOINT="${PARSING_CHECKPOINT:-}"

HF_CACHE_DIR="${HF_CACHE_DIR:-${PROJECT_ROOT}/.hf_cache}"
mkdir -p "${HF_CACHE_DIR}"

WMT14_MAX_TRAIN="${WMT14_MAX_TRAIN:-0}"
WMT14_MAX_VAL="${WMT14_MAX_VAL:-0}"
WSJ_MAX_TRAIN="${WSJ_MAX_TRAIN:-0}"
WSJ_MAX_VAL="${WSJ_MAX_VAL:-0}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] docker command not found. Please install Docker first."
  exit 1
fi

if [[ "${MODE}" != "auto" && "${MODE}" != "train" && "${MODE}" != "eval" && "${MODE}" != "both" ]]; then
  echo "[ERROR] MODE must be one of: auto, train, eval, both"
  exit 1
fi

echo "[INFO] Project root: ${PROJECT_ROOT}"
echo "[INFO] Mounted code dir: ${CODE_DIR}"
echo "[INFO] Docker image: ${IMAGE_NAME}"
echo "[INFO] Mode: ${MODE}"
echo "[INFO] Tasks: ${TASKS}"

GPU_ARGS=()
if [[ "${USE_GPU}" == "true" ]]; then
  GPU_ARGS=(--gpus all)
elif [[ "${USE_GPU}" == "auto" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_ARGS=(--gpus all)
    echo "[INFO] NVIDIA GPU detected. Enabling --gpus all."
  else
    echo "[INFO] No NVIDIA GPU detected. Running on CPU."
  fi
else
  echo "[INFO] GPU disabled by USE_GPU=${USE_GPU}."
fi

DOCKERFILE_TMP="$(mktemp)"
trap 'rm -f "${DOCKERFILE_TMP}"' EXIT

cat > "${DOCKERFILE_TMP}" <<'DOCKERFILE'
FROM pytorch/pytorch:1.9.0-cuda11.1-cudnn8-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    numpy==1.21.0 \
    sacrebleu==2.0.0 \
    tqdm==4.62.0 \
    pyyaml==6.0.1 \
    datasets==2.16.1 \
    nltk==3.8.1

WORKDIR /workspace/repo
DOCKERFILE

echo "[INFO] Building Docker image..."
docker build -f "${DOCKERFILE_TMP}" -t "${IMAGE_NAME}" "${PROJECT_ROOT}"

echo "[INFO] Running reproduction in container..."
docker run --rm -t \
  "${GPU_ARGS[@]}" \
  -e MODE="${MODE}" \
  -e TASKS="${TASKS}" \
  -e TRANSLATION_CHECKPOINT="${TRANSLATION_CHECKPOINT}" \
  -e PARSING_CHECKPOINT="${PARSING_CHECKPOINT}" \
  -e WMT14_MAX_TRAIN="${WMT14_MAX_TRAIN}" \
  -e WMT14_MAX_VAL="${WMT14_MAX_VAL}" \
  -e WSJ_MAX_TRAIN="${WSJ_MAX_TRAIN}" \
  -e WSJ_MAX_VAL="${WSJ_MAX_VAL}" \
  -e HF_HOME="/workspace/.cache/huggingface" \
  -v "${CODE_DIR}:/workspace/repo" \
  -v "${HF_CACHE_DIR}:/workspace/.cache/huggingface" \
  "${IMAGE_NAME}" \
  bash -lc '
set -euo pipefail

cd /workspace/repo
mkdir -p data/WMT14_EnDe data/WSJ checkpoints logs

echo "[INFO] Downloading and preparing datasets..."
python - <<'"'"'PY'"'"'
import os
from datasets import load_dataset
import nltk

def parse_max(name: str) -> int:
    value = os.environ.get(name, "0").strip()
    try:
        return int(value)
    except Exception:
        return 0

def limit_items(items, max_n: int):
    if max_n > 0:
        return items[:max_n]
    return items

# WMT14 de-en
train_max = parse_max("WMT14_MAX_TRAIN")
val_max = parse_max("WMT14_MAX_VAL")
wmt = load_dataset("wmt14", "de-en")

def write_wmt(split_name: str, src_path: str, tgt_path: str, max_n: int):
    src_lines = []
    tgt_lines = []
    for row in wmt[split_name]:
        translation = row.get("translation", {})
        src = translation.get("en", "").strip()
        tgt = translation.get("de", "").strip()
        if src and tgt:
            src_lines.append(src)
            tgt_lines.append(tgt)
    src_lines = limit_items(src_lines, max_n)
    tgt_lines = limit_items(tgt_lines, max_n)
    with open(src_path, "w", encoding="utf-8") as sf:
        sf.write("\n".join(src_lines) + ("\n" if src_lines else ""))
    with open(tgt_path, "w", encoding="utf-8") as tf:
        tf.write("\n".join(tgt_lines) + ("\n" if tgt_lines else ""))

write_wmt("train", "data/WMT14_EnDe/train.src", "data/WMT14_EnDe/train.tgt", train_max)
write_wmt("validation", "data/WMT14_EnDe/val.src", "data/WMT14_EnDe/val.tgt", val_max)

# WSJ from NLTK treebank
wsj_train_max = parse_max("WSJ_MAX_TRAIN")
wsj_val_max = parse_max("WSJ_MAX_VAL")
nltk.download("treebank", quiet=True)
from nltk.corpus import treebank
trees = [t.pformat(margin=10**9) for t in treebank.parsed_sents()]
split_idx = int(len(trees) * 0.9)
train_trees = trees[:split_idx]
val_trees = trees[split_idx:]
train_trees = limit_items(train_trees, wsj_train_max)
val_trees = limit_items(val_trees, wsj_val_max)
with open("data/WSJ/train.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(train_trees) + ("\n" if train_trees else ""))
with open("data/WSJ/val.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(val_trees) + ("\n" if val_trees else ""))

print("[INFO] Dataset preparation complete.")
PY

timestamp="$(date +%Y%m%d_%H%M%S)"
log_dir="logs/repro_${timestamp}"
mkdir -p "${log_dir}"

run_task() {
  local task="$1"
  local checkpoint="$2"
  local mode="${MODE}"
  local cmd=()

  if [[ "${mode}" == "auto" ]]; then
    if [[ -n "${checkpoint}" && -f "${checkpoint}" ]]; then
      mode="eval"
    else
      mode="both"
    fi
  fi

  if [[ "${mode}" == "eval" ]]; then
    if [[ -n "${checkpoint}" && -f "${checkpoint}" ]]; then
      cmd=(python main.py --config config.yaml --mode eval --task "${task}" --checkpoint "${checkpoint}")
    else
      echo "[WARN] ${task}: eval mode requested but checkpoint not found. Running eval without checkpoint."
      cmd=(python main.py --config config.yaml --mode eval --task "${task}")
    fi
  else
    cmd=(python main.py --config config.yaml --mode "${mode}" --task "${task}")
  fi

  echo "[INFO] Running ${task} with mode=${mode}"
  echo "[INFO] Command: ${cmd[*]}"
  "${cmd[@]}" | tee "${log_dir}/${task}.log"
}

IFS="," read -r -a task_array <<< "${TASKS}"
for task in "${task_array[@]}"; do
  task_trimmed="$(echo "${task}" | xargs)"
  if [[ "${task_trimmed}" == "translation" ]]; then
    run_task "translation" "${TRANSLATION_CHECKPOINT}"
  elif [[ "${task_trimmed}" == "parsing" ]]; then
    run_task "parsing" "${PARSING_CHECKPOINT}"
  else
    echo "[WARN] Unknown task: ${task_trimmed}. Skipping."
  fi
done

echo "[INFO] Finished. Logs saved to: ${log_dir}"
'

echo "[INFO] Done."
