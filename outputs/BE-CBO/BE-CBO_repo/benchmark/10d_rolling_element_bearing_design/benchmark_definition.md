# 10D rolling element bearing design

- paper_benchmark_name: 10D rolling element bearing design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A ten-dimensional design problem for rolling element bearings, optimizing dynamic capacity under a set of nonlinear constraints.

## Equations Or Components
- Design vector x = (Dm, Db, Z, f_i, f_o, KDmin, KDmax, ε, ...)
- c1(x) = φ0^2*sin^(-1)(Db/Dm - Z + 1)
- c2(x) = 2*Db - KDmin*(D-d)
- c3(x) = KDmax*(D-d) - 2*Db
- c4(x) = ξBw - Db
- c5(x) = Dm - 0.5*(D+d)
- c6(x) = (0.5+e)*(D+d) - Dm
- c7(x) = 0.5*(D-Dm-Db) - e*Db

## Variables Or Inputs
- Dm
- Db
- Z
- f_i
- f_o
- KDmin
- KDmax
- ε
- ...

## Constraints Or Bounds
- Bounds as specified in Gupta et al. (2007)

## Notes
- Analytic benchmark from published literature; no license restrictions
