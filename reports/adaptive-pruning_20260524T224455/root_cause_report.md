# Root-Cause Report — adaptive-pruning (baseline score 0.474)

Grader: JUDGE=simple, CODE_ONLY=True. Grading is **binary per leaf** (0 or 1).
86 leaves: **56 pass, 30 fail**. Failing weight 32 of 88.

## Failure clusters (weight-sorted, mapped to pipeline stage)

### Cluster 1 — Placeholder / "proxy" / un-vendored implementations  (weight ≈ 7, stage 3 coding; some stage 2)
The code explicitly substitutes real algorithms with stubs.
- `791e26f6` (w=2) Mask Tuning baseline — only a *fetch registry* in `configs/baselines.yaml`, no implementation.
- `1ad3cbb6` (w=2) CoFi / "Prune+Distill" — `src/training/baselines.py` labels it a **"minimal runnable proxy"** and states *"we do not vendor CoFiPruning"*.
- `95d71d15`, `a7b5b5ae` LoRA+Prune / LoRA+Prune+Distill — configured by toggling flags, not implementing the method.
**Evidence quote:** *"In this repo we do not vendor CoFiPruning; instead we run a CoFi-style schedule..."*
**Fix locus:** stage-3 coding prompt must forbid proxy/stub/simplified implementations.

### Cluster 2 — Equations implemented approximately / incorrectly  (weight ≈ 16, stage 3 coding + stage 2 analysis)
The largest cluster. Code exists but diverges from the paper's exact math.
- `56fadbbe` salience computed as `abs(activation*grad)` instead of `abs(W · ∂L/∂W)`.
- `bf86efe4` APT adapter weight not computed as `W + 2·W_B W_A`.
- `9500d7e2` MHA mask `m_o` applied per-output-feature, not grouped by attention head.
- `169a5eb2` FFN mask `m_i` not tied to hidden-dimension pruning.
- `6c5119f5` outlier EMA not using `0.85 / 0.15` coefficients.
- `92744e38`/`28658a50`/`8f4b756f` distillation: teacher→student layer mapping not MSE-argmin; TrTransform/loss terms diverge.
- `50d7ad1a`/`256c6f16`/`d3dcd793` block-category `f` (eq 13) and eq-14 parameter counting use string heuristics, not the paper's formula.
**Fix locus:** stage-3 coding prompt (exact-equation rule) + stage-2 analysis (specify exact formulas).

### Cluster 3 — Correct code in the wrong pipeline location  (weight ≈ 2, stage 3 coding)
- `ca6ea57b`/`d5ec9b1a` adapter `merge_adapter()` is implemented but only called at **export**, AFTER evaluation. The paper requires merging **before inference**.
**Evidence quote:** *"the evaluator benchmarks inference without merging; merging occurs only later during export."*
**Fix locus:** stage-3 coding prompt (placement rule).

### Cluster 4 — Hyperparameter/config not verifiably enforced or file absent  (weight ≈ 4, stage 1 config + stage 3)
- `c99c524a` SQuAD 40 epochs — no SQuAD task config present; judge cannot verify.
- `e193b120` GLUE lr 2e-4 — defaulted but overridable; no GLUE resolved-config artifact to prove it.
- `452a6371` Finetune 10 epochs — judge's file view was empty for this requirement.
- `a5c6d56b` SQuAD 20/20 distill split — present but not clearly enforced.
**Fix locus:** stage-1 config / stage-3 (emit explicit per-dataset config files with exact table values).

### Cluster 5 — Dataset / eval access  (weight ≈ 2, stage 3 coding)
- `4e1da193` CNN/DM test split set to empty string.
- `698b1e1c` ROUGE computed but not reported on CNN/DM test set.
- `0c47a836` "relative accuracy" (Sec 5.5) metric not implemented.

## Iteration strategy
1. **Iter 1 (cheapest, broadest):** stage-3 coding prompt — add fidelity rules attacking Clusters 1, 2, 3, 4-emit, 5. Re-run `--only 3`, re-grade.
2. **Iter 2 (if needed):** push exact-equation specificity into stage-2 analysis prompt (`--stages 2,3`).

---

## Grader retrieval failure (debugging log) — why iter-1 grades returned 0.0

**Symptom:** after the iter-1 stage-3 regen, the re-grade scored **0.0 on all 86 leaves**; every
leaf's judge response said *"the submission's `<files>` section is empty"*.

**Investigation (systematic-debugging skill):**
1. Confirmed via per-leaf logs that the file-ranking model returned non-resolving paths
   (`.`, then `README.md`) → empty `<files>` for every leaf.
2. Traced the judge's file enumeration: `paperbench/judge/simple.py::_get_whitelisted_files`
   (`os.walk`-based, `max_file_depth=4`, dir blacklist) → `_prepare_relevant_files` shows the model a
   file tree and reads back the files it names from `submission_dir / rel_path`.
3. Replicated the judge's *actual* walk function on the repo → it found **89** code/config files.
   So the corpus was NOT empty on disk; the judge process was seeing an empty tree.
4. Compared against the **baseline** log for the same leaf: baseline selected real files
   (`src/data/dataset_loader.py`, …). So retrieval worked at baseline and broke for my runs.
5. Read `grade.py::run_judge` (computer=None, `submission_dir=submission_path`) and `grade_paper.sh`:
   the script does **`cd "$PAPERBENCH_DIR"`** before invoking the judge. The judge error line printed a
   **relative** path: `File outputs/paperbench_repos/adaptive-pruning_repo not found!`.

**Root cause:** I invoked the grader with a **relative** `OUTPUT_REPO_DIR`. The `-d` validation ran
from the repo root (passed), but after `cd "$PAPERBENCH_DIR"` the relative path pointed nowhere →
empty walk → empty tree → 0.0. The baseline used the script's **absolute default** path, so it worked.

**Red herring:** an earlier hypothesis blamed the repo's bloated `outputs/` dir (2870 files,
819 whitelisted) overflowing the judge's tree-token budget. Stashing `outputs/` and re-grading twice
still gave 0.0 (those runs reused the relative path), refuting it. `outputs/` bloat is a *latent*
secondary risk (a 900+ file tree can truncate), but it did not cause this failure.

**Fix:** grade with an absolute submission path — simplest: **omit `OUTPUT_REPO_DIR`** so
`grade_paper.sh` uses its absolute default (`$ROOT_DIR/outputs/paperbench_repos/<paper>_repo`). A probe
grade with the absolute path immediately restored real file selection.

**Lesson for the pipeline/runner:** any wrapper that sets `OUTPUT_REPO_DIR` for `grade_paper.sh` should
pass an **absolute** path (or the script should `realpath` it before the `cd`). This is a grading-harness
usage gotcha, not a code-quality issue.
