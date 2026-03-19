# 4D welded beam design

- paper_benchmark_name: 4D welded beam design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A four-dimensional welded beam design problem minimizing cost with multiple mechanical constraints.

## Equations Or Components
- f(x) = 1.10471*x1^2*x2 + 0.04811*x3*x4*(14 + x2)
- c1(x) = 13000 - τ(x)
- c2(x) = 30000 - σ(x)
- c3(x) = P_c(x) - 6000
- c4(x) = 0.25 - δ(x)
- c5(x) = x4 - x1

## Variables Or Inputs
- x1
- x2
- x3
- x4

## Constraints Or Bounds
- x1 ∈ [0.125, 10]
- x2, x3, x4 ∈ [0.1, 10]

## Notes
- Formulation from literature; no license issues
