# 2D Townsend function

- paper_benchmark_name: 2D Townsend function
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A 2-dimensional trigonometric synthetic function with a nonlinear constraint, defined by analytic expressions.

## Equations Or Components
- f(x) = -[cos((x1 - 0.1)*x2)]^2 - x1*sin(3*x1 + x2)
- c(x) = (2*cos(t) - 1/2*cos(2*t) - 1/4*cos(3*t) - 1/8*cos(4*t))^2 + (2*sin(t))^2 - x2
- t = arctan2(x1, x2)

## Variables Or Inputs
- x1
- x2

## Constraints Or Bounds
- x1 ∈ [-2.25, 2.25]
- x2 ∈ [-2.5, 1.75]

## Notes
- Analytic benchmark based on published literature; no license restrictions
