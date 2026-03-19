# 2D LSQ function

- paper_benchmark_name: 2D LSQ function
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A linear objective function defined on [0,1]^2 with sinusoidal and quadratic constraints.

## Equations Or Components
- f(x) = x1 + x2
- c1(x) = x1 + 2*x2 + 0.5*sin(2π*(x1 - 2*x2)) - 1.5
- c2(x) = 1.5 - x1 - x2^2

## Variables Or Inputs
- x1
- x2

## Constraints Or Bounds
- x1 ∈ [0, 1]
- x2 ∈ [0, 1]

## Notes
- Based on published formulations; no license restrictions
