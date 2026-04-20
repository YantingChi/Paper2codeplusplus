# 7D speed reducer design

- paper_benchmark_name: 7D speed reducer design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A seven-dimensional design problem for a speed reducer, minimizing weight subject to multiple mechanical constraints.

## Equations Or Components
- f(x) = 0.7854*x1*x2^2*(3.3333*x2^3 + 14.9334*x3 - 43.0934) - 1.508*x1*(x2^6 + x7^2) + 7.4777*(x3^6 + x3^7) + 0.7854*(x4*x2^6 + x5*x2^7)
- c7(x) = 40 - x2*x3
- c8(x) = x1/x2 - 5
- c9(x) = 12 - x1/x2
- c10(x) = 1 - (1.5*x6 + 1.9)/x4
- c11(x) = 1 - (1.1*x7 + 1.9)/x5

## Variables Or Inputs
- x1
- x2
- x3
- x4
- x5
- x6
- x7

## Constraints Or Bounds
- x1 ∈ [2.6,3.6]
- x2 ∈ [0.7,0.8]
- x3 ∈ [17,28]
- x4, x5 ∈ [7.3,8.3]
- x6 ∈ [2.9,3.9]
- x7 ∈ [5,5.5]

## Notes
- Published benchmark; no license restrictions
