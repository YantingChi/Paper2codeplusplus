# 2D three-bar truss design

- paper_benchmark_name: 2D three-bar truss design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A two-variable truss design problem that minimizes the structure's volume subject to stress constraints.

## Equations Or Components
- f(x) = l*(2*√2*x1 + x2)
- c1(x) = 2 - (√(2*x1 + x2))/(√(2*x2 + 1 + 2*x1*x2))
- c2(x) = 2 - (1/x1 + √(2*x2))
- c3(x) = 2 - x2/(√(2*x2 + 1 + 2*x1*x2))

## Variables Or Inputs
- x1
- x2

## Constraints Or Bounds
- x1 ∈ [0, 1]
- x2 ∈ [0, 1]

## Notes
- Formulation from literature; no license restrictions
