# iter 7 — Equation fidelity pushed into stages 2+3 (results)

## Goal
Make the generated code literally realize the paper's equations (exact coefficients, operator
family), by carrying equation precision through Stage 2 → Stage 3, so 8.1 (and the grader) see the
literals in the source. No grader signal used. Cost kept moderate (no new stage; one bounded verify).

## Changes (committed)
- **codes/2_analyzing.py** — each per-file logic analysis now appends a fenced
  `<EQUATIONS>[{eq_id, latex, target_function, paper_section, note}]</EQUATIONS>` block with the
  paper's LaTeX transcribed verbatim. (Validated: 28/28 adaptive-pruning files emitted parseable blocks.)
- **codes/3_coding.py** —
  - parses each file's `<EQUATIONS>` block (`parse_equations_block`) and splices an "Equations this
    file MUST implement literally" checklist into the per-file coding prompt;
  - tightened rule 10 (hard-code coefficient literals; preserve KL vs MSE vs CE operator family);
  - `api_call` now sets `reasoning_effort="high"` for `gpt-5*` (was o3-mini only);
  - one bounded per-file self-verify (`verify_and_fix_equations`): for files with equations, an LLM
    confirms each is literally present; if any reported missing, AT MOST ONE targeted regen. Cost
    logged via print_log_cost.

## Run (adaptive-pruning, --stages 2 then resumed --only 3; gpt-5.2, proxy)
- Stage 2: 28/28 files got `<EQUATIONS>` blocks.
- Stage 3: completed 28 files; **25 VERIFY calls, 14 VERIFY-FIX (bounded regen) calls** fired.
- Overhead vs baseline stage-3: ~+25 verify +~14 regen calls AND reasoning_effort=high (~2x/call).

## Measured equation-fidelity delta (NEW iter7 vs OLD pre-iter7 `stage3_12`)
Per-file literal-match (a marker — decimal coefficient or operator keyword extracted from the
equation — must appear IN THE TARGET FILE):
- checkable equations (heuristic): 11
- OLD stage3_12: **0/11 (0%)**
- NEW iter7:     **11/11 (100%)**
- Newly present in NEW: mask-update `alpha=0.01` literal in `main.py`/`src/config.py`/`trainer.py`;
  `MSE` operator in `distillation.py`/`trainer.py`; section anchors; etc.

## Honest caveats (do NOT overclaim)
1. The checkable subset is small (11) and heuristic; symbolic equations (β, Σ) aren't auto-checked.
2. "Marker present in file" ≠ "grader leaf passes." Notably the outlier-EMA stays **config-driven**
   (`salience_ema_beta: 0.85` in configs, consumed in `salience.py`) because the paper writes the
   equation with the symbol β (value 0.85 lives in a table). The grader previously failed that leaf
   for being "configurable, not enforced" — iter7 does NOT change that, since faithful transcription
   of the paper yields β, not a hard literal.
3. **No held-out grade was run** for iter7, and only ONE paper (adaptive-pruning) was processed —
   the repeated kills of long `reasoning_effort=high` runs + the 2h cap consumed the budget. The
   planned second equation-heavy paper (sequential-neural-score-estimation) and the proxy grade are
   NOT done.

## Not done / next
- Run the new equation-heavy paper(s) end-to-end (`sequential-neural-score-estimation`,
  `stochastic-interpolants`) for generalization.
- One held-out proxy grade on adaptive-pruning iter7 vs iter-4 (0.5178) to see if the literal
  coefficients move equation leaves.
- Optional: scope the per-file verify to `.py` files (config `.yaml` files currently trigger a
  harmless but wasted VERIFY-FIX call; the `extract_code_from_content` guard keeps the original).
