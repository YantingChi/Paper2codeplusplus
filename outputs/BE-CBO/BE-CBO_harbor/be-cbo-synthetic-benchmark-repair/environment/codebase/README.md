# BE-CBO Codebase Notes

This bundled repo is intentionally incomplete and slightly wrong.

For the Harbor task, only these modules matter:
- `config.py`
- `benchmark.py`
- `evaluation.py`

The verifier uses:

```bash
python environment/benchmark/run_benchmark.py \
  --repo-dir environment/codebase \
  --cases environment/benchmark/synthetic_cases.json \
  --expected environment/expected_results/reference_results.json \
  --output environment/codebase/reproduction_result.json
```

Important contract:
- `Benchmark.evaluate(x)` must return `(objective, feasible)`
- infeasible points must return `(None, False)`
- feasible points must return `(float_value, True)`
