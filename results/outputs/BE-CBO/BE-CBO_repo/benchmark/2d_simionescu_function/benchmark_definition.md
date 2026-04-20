# 2D Simionescu function

- paper_benchmark_name: 2D Simionescu function
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A 2D hyperbolic paraboloid function with a sinusoidal constraint, defined over the square domain [-1.25, 1.25]^2.

## Equations Or Components
- f(x) = 0.1 * x1 * x2
- c(x) = (1 + 0.2*cos(8*arctan(x1/x2)))^2 - (x1 + x2)
- Parameters: rT = 1, rS = 0.2, n = 8

## Variables Or Inputs
- x1
- x2

## Constraints Or Bounds
- x1 ∈ [-1.25, 1.25]
- x2 ∈ [-1.25, 1.25]

## Notes
- Analytic benchmark from literature; no license restrictions
