# Rubric-driven repair — held-out results & generalization

## What changed vs the (discarded) grader-driven repair
The earlier `codes/3.5_repair.py` drove repair from `grader_output.json` (the held-out grader's
per-leaf scores, `# Reality` critiques, and file-selection logs). That is training-on-the-test;
its 0.8415 on adaptive-pruning was contaminated. It was **deleted**. Repair now runs through the
existing **`codes/8.1_self_ameliorating.py`**, whose signal is entirely **paper-derived**:
- `codes/8`'s paper2code rubric (Code-Development leaves), and
- `codes/7`'s reproduction rubric (methods + required hyperparameters), folded in by a small
  enrichment I added to 8.1 (`load_reproduction_rubric_leaves` + `--reproduction_rubric_path`,
  auto-detected). It never reads the grader.

`scripts/grade_paper.sh` is used **only** to verify (held-out), never as a repair signal.

## Held-out grades (proxy judge, JUDGE=simple, CODE_ONLY)

| paper | baseline (clean stage-3) | after rubric-driven 8.1 | Δ |
|------|------|------|------|
| adaptive-pruning | 0.4624 | **0.5178** | +0.0554 |
| pinn | 0.7235 | **0.9503** | +0.2268 |
| robust-clip | 0.2726 | **0.3122** | +0.0396 |

Contaminated reference (NOT honest): adaptive-pruning grader-driven repair = 0.8415. The gap
0.8415 − 0.5178 ≈ **0.32 was grader-overfitting**.

## Findings
1. **The repair generalizes — positive on all 3 papers.** It is not adaptive-pruning-specific.
2. **Magnitude scales with paper tractability.** `pinn` (self-contained physics-informed NN, few
   external deps) jumps to **0.95**. `adaptive-pruning` and `robust-clip` gain less because their
   remaining failures need large *infrastructure* the self-ameliorator's SEARCH/REPLACE patches
   can't synthesize: external pruning baselines (CoFi/Mask-Tuning) for adaptive-pruning, and LVLM
   adversarial-eval pipelines / TeCoA objective for robust-clip. 8.1 *did* fix their core
   method + hyperparameter fidelity (e.g. robust-clip: all FARE hyperparameters and the FARE
   min-max objective pass post-repair; adaptive-pruning: APT forward, salience, sparsity schedule).
   Those infra gaps are Stage-8.2 ("wire baselines") territory, not code-dev fidelity.
3. **8.1 self-check vs the held-out grader.** 8.1 converges to (near) all-pass on its paper-derived
   rubric, but the grader's finer rubric still finds gaps — so the honest score is bounded by how
   well the paper-derived rubric covers the grader's. This is the inherent (and correct) limit of a
   grader-independent method.

## How to reproduce (per paper)
```bash
# 1. (once) generate the paper-derived rubric if absent
PAPER_NAME=<p> bash scripts/run_paper_with_aoai_proxy.sh --stages 7,8
# 2. baseline grade (held-out, verification only)
PAPER_NAME=<p> JUDGE=simple GRADER_LLM_PROVIDER=aoai-proxy bash scripts/grade_paper.sh
# 3. rubric-driven repair (grader-independent)
PAPER_NAME=<p> bash scripts/run_paper_with_aoai_proxy.sh --only 8.1
# 4. after grade (held-out)
PAPER_NAME=<p> JUDGE=simple GRADER_LLM_PROVIDER=aoai-proxy bash scripts/grade_paper.sh
```

## Code changes (this redesign)
- `codes/3.5_repair.py` — **deleted** (grader-driven; replaced).
- `scripts/run_codex.sh` — stage-3.5 wiring **reverted** (8.1 was already wired as stage 8.1).
- `codes/8.1_self_ameliorating.py` — added reproduction-rubric folding (methods + required
  hyperparameters) for finer, still paper-derived coverage; `--reproduction_rubric_path` (auto-detected).
- `codes/utils.py` — `read_python_files` now skips `.venv`/site-packages/caches/VCS and reads with
  `errors='replace'` (a generated repo's `.venv` previously crashed 8.1 with a UnicodeDecodeError and
  would have bloated the prompt with thousands of library files).
