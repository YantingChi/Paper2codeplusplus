# BE-CBO Algorithm Summary

BE-CBO solves constrained black-box optimization problems where feasibility is unknown in advance and infeasible evaluations do not produce objective values.

## Core Loop

1. Start from an initial Sobol sample set.
2. Fit a GP surrogate on feasible objective observations only.
3. Fit a deep-ensemble binary classifier on all evaluated points to predict feasibility.
4. Estimate classifier uncertainty `sigma_E(x)` and define the dynamic lower bound:
   `l(x) = 0.5 - sigma_E(x)`.
5. Choose the next point by maximizing Expected Improvement under:
   `l(x) <= C(x) <= 1`.
6. Evaluate the point, add feasible objective values to the GP dataset, and add every point to the classifier dataset.

## Benchmark Facts Used In This Task

The verifier only checks the three synthetic Appendix B.1.1 problems:
- Townsend
- Simionescu
- LSQ

The important paper-grounded requirements for this task are:
- `benchmark.py` must encode the Appendix B.1.1 synthetic objectives and constraints correctly.
- `config.py` must be importable on Python 3.11 so the repo can run at all.
- `evaluation.py` must produce the expected benchmark summary from the deterministic case list.

## Expected File Roles

- `config.py`: central defaults for training, BO budget, and benchmark list
- `utils.py`: logger, seeds, Sobol sampling, device helpers
- `benchmark.py`: problem definitions and feasibility checks
- `evaluation.py`: final metrics from recorded evaluations
