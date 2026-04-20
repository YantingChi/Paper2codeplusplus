# 4D gas transmission compressor design

- paper_benchmark_name: 4D gas transmission compressor design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A four-dimensional design problem for minimizing the annual cost of a gas transmission system, with complex expressions and one engineering constraint.

## Equations Or Components
- f(x) = (8.61e5)*sqrt(x1)*x2^(-2/3)*x3^(-1/2)*x4 + (3.69e4)*x3 + (7.72e8)*... - (765.43e6)*...
- c(x) = 1 - (expression involving x4)
- Refer to Pant et al. (2009) for full details

## Variables Or Inputs
- x1
- x2
- x3
- x4

## Constraints Or Bounds
- x1 ∈ [20, 50]
- x2 ∈ [1, 10]
- x3 ∈ [20, 50]
- x4 ∈ [0.1, 60]

## Notes
- Based on literature; refer to original publication for any licensing details
