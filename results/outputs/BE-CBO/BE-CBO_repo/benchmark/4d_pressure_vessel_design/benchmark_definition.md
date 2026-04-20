# 4D pressure vessel design

- paper_benchmark_name: 4D pressure vessel design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A four-dimensional pressure vessel design problem that minimizes total cost with four design constraints.

## Equations Or Components
- f(x) = 0.6224*x1*x3*x4 + 1.7781*x2^3 + 3.1661*x2*x1*x4 + 19.84*x2*x1*x3
- c1(x) = x1 - 0.0193*x3
- c2(x) = x2 - 0.00954*x3
- c3(x) = π*x2^3*x4 + (4/3)*π*x3^3 - 1296000
- c4(x) = 240 - x4

## Variables Or Inputs
- x1
- x2
- x3
- x4

## Constraints Or Bounds
- Design-specific bounds (see Coello & Montes, 2002)

## Notes
- Literature-based benchmark; no known license restrictions
