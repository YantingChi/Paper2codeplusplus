# 9D planetary gear train design

- paper_benchmark_name: 9D planetary gear train design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A nine-dimensional planetary gear train design problem that minimizes gear ratio errors under complex constraints.

## Equations Or Components
- f(x) = max{|i_k - i^0_k|} for k ∈ {1,2,R}
- i1 = N6/N4, i^0_1 = 3.11
- i2 = (N6*(N1*N3 + N2*N4))/(N1*N3*(N6-N4)), i^0_2 = 1.84
- i_R = N2*N6/(N1*N3), i^0_R = -3.11
- Additional constraints as per Rao et al. (2012)

## Variables Or Inputs
- ρ
- N6
- N5
- N4
- N3
- N2
- N1
- m2
- m1

## Constraints Or Bounds
- N1 ∈ [17,96]
- N2 ∈ [14,54]
- N3 ∈ [14,51]
- N4 ∈ [17,46]
- N5 ∈ [14,51]
- N6 ∈ [48,124]
- p ∈ {3,4,5}
- m1, m3 ∈ {1.75,2.0,2.25,2.5,2.75,3.0}

## Notes
- Standard benchmark from literature; no license restrictions
