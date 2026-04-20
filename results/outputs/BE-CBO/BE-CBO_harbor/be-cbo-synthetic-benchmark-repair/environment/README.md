# BE-CBO Synthetic Benchmark Repair

This Harbor task packages a Paper2Code-generated repository for the paper
"Boundary Exploration for Bayesian Optimization With Unknown Physical Constraints".

The bundled verifier focuses on a deterministic subset of the paper:
- the three synthetic benchmark problems from Appendix B.1.1
- the repository import path
- the benchmark/evaluation output contract

## Layout

- `codebase/`: imperfect generated repository to repair
- `paper.json`: structured paper facts, benchmark formulas, and intended file roles
- `alg.md`: concise BE-CBO algorithm summary
- `benchmark/synthetic_cases.json`: fixed evaluation points for the three synthetic problems
- `benchmark/run_benchmark.py`: runner that imports the repo and evaluates the cases
- `expected_results/reference_results.json`: reference outputs for the benchmark

## Expected Workflow

Run the benchmark with:

```bash
python environment/benchmark/run_benchmark.py \
  --repo-dir environment/codebase \
  --cases environment/benchmark/synthetic_cases.json \
  --expected environment/expected_results/reference_results.json \
  --output environment/codebase/reproduction_result.json
```

The current snapshot is expected to fail before repair because:
- `config.py` is not importable on Python 3.11 due to mutable dataclass defaults
- the synthetic benchmark definitions in `benchmark.py` do not match the paper appendix

## Expected Final Artifact

After repair, keep the modified repo in `environment/codebase` and ensure:

```text
environment/codebase/reproduction_result.json
```

contains at least:
- `runnable`
- `result_same`
