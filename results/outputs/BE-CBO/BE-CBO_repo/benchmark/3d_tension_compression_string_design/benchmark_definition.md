# 3D tension-compression string design

- paper_benchmark_name: 3D tension-compression string design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A three-dimensional design problem minimizing the weight of a tension-compression string subject to multiple engineering constraints.

## Equations Or Components
- f(x) = (x1 + 2) * x2 * x3^3
- c1(x): Constraint related to deflection and shear stress
- c2(x): Constraint related to material limits
- c3(x): Constraint related to surge frequency
- c4(x) = 1 - x2 + x3^1.5

## Variables Or Inputs
- x1
- x2
- x3

## Constraints Or Bounds
- x1 ∈ [2,15] (integer)
- x2 ∈ [0.25, 1.3]
- x3 ∈ [0.05, 2]

## Notes
- Based on published work; no license restrictions
