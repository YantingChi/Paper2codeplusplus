The workdir is `/workspace`.

You do not have internet access at runtime.

What is provided:
- `environment/codebase/`: the Paper2Code-generated BE-CBO repository. It is intentionally imperfect.
- `environment/paper.json`: structured paper facts and expected module roles.
- `environment/alg.md`: a concise summary of the target algorithm.
- `environment/benchmark/`: a deterministic synthetic benchmark and runner.
- `environment/expected_results/`: reference outputs for the benchmark.

Task:
1. Inspect `environment/alg.md`, `environment/paper.json`, and the code in `environment/codebase`.
2. Fix the code so the synthetic benchmark matches the paper appendix for the three synthetic problems:
   - Townsend
   - Simionescu
   - LSQ
3. The repo must import and run successfully on the bundled benchmark.
4. Re-run the benchmark after fixing the code.
5. Leave the modified repository in `environment/codebase`.
6. Produce `environment/codebase/reproduction_result.json`.

Run command:
```bash
python environment/benchmark/run_benchmark.py \
  --repo-dir environment/codebase \
  --cases environment/benchmark/synthetic_cases.json \
  --expected environment/expected_results/reference_results.json \
  --output environment/codebase/reproduction_result.json
```

Expected output contract:
- The modified repo remains in `environment/codebase`.
- `environment/codebase/reproduction_result.json` must contain:
  - `runnable`: true or false
  - `result_same`: true or false

Constraints:
- Do not use network access.
- Do not call external APIs.
- Keep the task self-contained.
- The verifier checks the synthetic benchmark only; you do not need to finish the full 12-problem BO pipeline for this task.
