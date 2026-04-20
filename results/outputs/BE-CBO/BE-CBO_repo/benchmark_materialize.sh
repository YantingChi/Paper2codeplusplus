#!/usr/bin/env bash
set -euo pipefail

# Generated benchmark materialization script for "BE-CBO"
DEFAULT_BENCHMARK_ROOT=/home/yantingchi/Desktop/DK/Paper2Code/outputs/BE-CBO_repo/benchmark
DEFAULT_MAPPING_CSV=/home/yantingchi/Desktop/DK/Paper2Code/outputs/BE-CBO_repo/benchmark/benchmark_materialization_mapping.csv
BENCHMARK_ROOT="${BENCHMARK_ROOT:-$DEFAULT_BENCHMARK_ROOT}"
MAPPING_CSV="${MAPPING_CSV:-$DEFAULT_MAPPING_CSV}"
FAILURE_LOG="$BENCHMARK_ROOT/benchmark_materialization_failures.txt"

mkdir -p "$BENCHMARK_ROOT"
: > "$FAILURE_LOG"

GENERATED_COUNT=0
DOWNLOADABLE_FAILURE_COUNT=0
UNRESOLVED_COUNT=0

log_message() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

append_failure() {
  printf '%s\n' "$*" >> "$FAILURE_LOG"
}

log_message "Benchmark root: $BENCHMARK_ROOT"
log_message "Benchmark mapping CSV: $MAPPING_CSV"

# Benchmark 1: 2D Townsend function
TARGET_DIR="$BENCHMARK_ROOT/2d_townsend_function"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "2D Townsend function",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "2D Townsend function",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Benchmark is directly referenced in the paper and based on Townsend (2014).",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "2d_townsend_function",
  "materialization_commands": [],
  "definition_summary": "A 2-dimensional trigonometric synthetic function with a nonlinear constraint, defined by analytic expressions.",
  "equations_or_components": [
    "f(x) = -[cos((x1 - 0.1)*x2)]^2 - x1*sin(3*x1 + x2)",
    "c(x) = (2*cos(t) - 1/2*cos(2*t) - 1/4*cos(3*t) - 1/8*cos(4*t))^2 + (2*sin(t))^2 - x2",
    "t = arctan2(x1, x2)"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [-2.25, 2.25]",
    "x2 ∈ [-2.5, 1.75]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Analytic benchmark based on published literature; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 2D Townsend function
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 2D Townsend function
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Benchmark is directly referenced in the paper and based on Townsend (2014).
definition_summary: A 2-dimensional trigonometric synthetic function with a nonlinear constraint, defined by analytic expressions.
equations_or_components:
- f(x) = -[cos((x1 - 0.1)*x2)]^2 - x1*sin(3*x1 + x2)
- c(x) = (2*cos(t) - 1/2*cos(2*t) - 1/4*cos(3*t) - 1/8*cos(4*t))^2 + (2*sin(t))^2 - x2
- t = arctan2(x1, x2)
variables_or_inputs:
- x1
- x2
constraints_or_bounds:
- x1 ∈ [-2.25, 2.25]
- x2 ∈ [-2.5, 1.75]
license_or_access_notes:
- Analytic benchmark based on published literature; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 2D Townsend function"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "2D Townsend function",
  "matched_benchmark_name": "2D Townsend function",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A 2-dimensional trigonometric synthetic function with a nonlinear constraint, defined by analytic expressions.",
  "equations_or_components": [
    "f(x) = -[cos((x1 - 0.1)*x2)]^2 - x1*sin(3*x1 + x2)",
    "c(x) = (2*cos(t) - 1/2*cos(2*t) - 1/4*cos(3*t) - 1/8*cos(4*t))^2 + (2*sin(t))^2 - x2",
    "t = arctan2(x1, x2)"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [-2.25, 2.25]",
    "x2 ∈ [-2.5, 1.75]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Analytic benchmark based on published literature; no license restrictions"
  ],
  "match_reason": "Benchmark is directly referenced in the paper and based on Townsend (2014).",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 2D Townsend function"

unset TARGET_DIR

# Benchmark 2: 2D Simionescu function
TARGET_DIR="$BENCHMARK_ROOT/2d_simionescu_function"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "2D Simionescu function",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "2D Simionescu function",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Directly referenced in the paper with its analytic definition (Simionescu, 2014).",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "2d_simionescu_function",
  "materialization_commands": [],
  "definition_summary": "A 2D hyperbolic paraboloid function with a sinusoidal constraint, defined over the square domain [-1.25, 1.25]^2.",
  "equations_or_components": [
    "f(x) = 0.1 * x1 * x2",
    "c(x) = (1 + 0.2*cos(8*arctan(x1/x2)))^2 - (x1 + x2)",
    "Parameters: rT = 1, rS = 0.2, n = 8"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [-1.25, 1.25]",
    "x2 ∈ [-1.25, 1.25]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Analytic benchmark from literature; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 2D Simionescu function
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 2D Simionescu function
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Directly referenced in the paper with its analytic definition (Simionescu, 2014).
definition_summary: A 2D hyperbolic paraboloid function with a sinusoidal constraint, defined over the square domain [-1.25, 1.25]^2.
equations_or_components:
- f(x) = 0.1 * x1 * x2
- c(x) = (1 + 0.2*cos(8*arctan(x1/x2)))^2 - (x1 + x2)
- Parameters: rT = 1, rS = 0.2, n = 8
variables_or_inputs:
- x1
- x2
constraints_or_bounds:
- x1 ∈ [-1.25, 1.25]
- x2 ∈ [-1.25, 1.25]
license_or_access_notes:
- Analytic benchmark from literature; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 2D Simionescu function"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "2D Simionescu function",
  "matched_benchmark_name": "2D Simionescu function",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A 2D hyperbolic paraboloid function with a sinusoidal constraint, defined over the square domain [-1.25, 1.25]^2.",
  "equations_or_components": [
    "f(x) = 0.1 * x1 * x2",
    "c(x) = (1 + 0.2*cos(8*arctan(x1/x2)))^2 - (x1 + x2)",
    "Parameters: rT = 1, rS = 0.2, n = 8"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [-1.25, 1.25]",
    "x2 ∈ [-1.25, 1.25]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Analytic benchmark from literature; no license restrictions"
  ],
  "match_reason": "Directly referenced in the paper with its analytic definition (Simionescu, 2014).",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 2D Simionescu function"

unset TARGET_DIR

# Benchmark 3: 2D LSQ function
TARGET_DIR="$BENCHMARK_ROOT/2d_lsq_function"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "2D LSQ function",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "2D LSQ function",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Referenced with explicit analytic formulas (Gramacy et al., 2016).",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "2d_lsq_function",
  "materialization_commands": [],
  "definition_summary": "A linear objective function defined on [0,1]^2 with sinusoidal and quadratic constraints.",
  "equations_or_components": [
    "f(x) = x1 + x2",
    "c1(x) = x1 + 2*x2 + 0.5*sin(2π*(x1 - 2*x2)) - 1.5",
    "c2(x) = 1.5 - x1 - x2^2"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [0, 1]",
    "x2 ∈ [0, 1]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Based on published formulations; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 2D LSQ function
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 2D LSQ function
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Referenced with explicit analytic formulas (Gramacy et al., 2016).
definition_summary: A linear objective function defined on [0,1]^2 with sinusoidal and quadratic constraints.
equations_or_components:
- f(x) = x1 + x2
- c1(x) = x1 + 2*x2 + 0.5*sin(2π*(x1 - 2*x2)) - 1.5
- c2(x) = 1.5 - x1 - x2^2
variables_or_inputs:
- x1
- x2
constraints_or_bounds:
- x1 ∈ [0, 1]
- x2 ∈ [0, 1]
license_or_access_notes:
- Based on published formulations; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 2D LSQ function"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "2D LSQ function",
  "matched_benchmark_name": "2D LSQ function",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A linear objective function defined on [0,1]^2 with sinusoidal and quadratic constraints.",
  "equations_or_components": [
    "f(x) = x1 + x2",
    "c1(x) = x1 + 2*x2 + 0.5*sin(2π*(x1 - 2*x2)) - 1.5",
    "c2(x) = 1.5 - x1 - x2^2"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [0, 1]",
    "x2 ∈ [0, 1]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Based on published formulations; no license restrictions"
  ],
  "match_reason": "Referenced with explicit analytic formulas (Gramacy et al., 2016).",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
# 2D LSQ function

- paper_benchmark_name: 2D LSQ function
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A linear objective function defined on [0,1]^2 with sinusoidal and quadratic constraints.

## Equations Or Components
- f(x) = x1 + x2
- c1(x) = x1 + 2*x2 + 0.5*sin(2π*(x1 - 2*x2)) - 1.5
- c2(x) = 1.5 - x1 - x2^2

## Variables Or Inputs
- x1
- x2

## Constraints Or Bounds
- x1 ∈ [0, 1]
- x2 ∈ [0, 1]

## Notes
- Based on published formulations; no license restrictions
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 2D LSQ function"

unset TARGET_DIR

# Benchmark 4: 2D three-bar truss design
TARGET_DIR="$BENCHMARK_ROOT/2d_three_bar_truss_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "2D three-bar truss design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "2D three-bar truss design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "The benchmark is taken directly from Ray & Saini (2001) with an analytic formulation.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "2d_three_bar_truss_design",
  "materialization_commands": [],
  "definition_summary": "A two-variable truss design problem that minimizes the structure's volume subject to stress constraints.",
  "equations_or_components": [
    "f(x) = l*(2*√2*x1 + x2)",
    "c1(x) = 2 - (√(2*x1 + x2))/(√(2*x2 + 1 + 2*x1*x2))",
    "c2(x) = 2 - (1/x1 + √(2*x2))",
    "c3(x) = 2 - x2/(√(2*x2 + 1 + 2*x1*x2))"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [0, 1]",
    "x2 ∈ [0, 1]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Formulation from literature; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 2D three-bar truss design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 2D three-bar truss design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: The benchmark is taken directly from Ray & Saini (2001) with an analytic formulation.
definition_summary: A two-variable truss design problem that minimizes the structure's volume subject to stress constraints.
equations_or_components:
- f(x) = l*(2*√2*x1 + x2)
- c1(x) = 2 - (√(2*x1 + x2))/(√(2*x2 + 1 + 2*x1*x2))
- c2(x) = 2 - (1/x1 + √(2*x2))
- c3(x) = 2 - x2/(√(2*x2 + 1 + 2*x1*x2))
variables_or_inputs:
- x1
- x2
constraints_or_bounds:
- x1 ∈ [0, 1]
- x2 ∈ [0, 1]
license_or_access_notes:
- Formulation from literature; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 2D three-bar truss design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "2D three-bar truss design",
  "matched_benchmark_name": "2D three-bar truss design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A two-variable truss design problem that minimizes the structure's volume subject to stress constraints.",
  "equations_or_components": [
    "f(x) = l*(2*√2*x1 + x2)",
    "c1(x) = 2 - (√(2*x1 + x2))/(√(2*x2 + 1 + 2*x1*x2))",
    "c2(x) = 2 - (1/x1 + √(2*x2))",
    "c3(x) = 2 - x2/(√(2*x2 + 1 + 2*x1*x2))"
  ],
  "variables_or_inputs": [
    "x1",
    "x2"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [0, 1]",
    "x2 ∈ [0, 1]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Formulation from literature; no license restrictions"
  ],
  "match_reason": "The benchmark is taken directly from Ray & Saini (2001) with an analytic formulation.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 2D three-bar truss design"

unset TARGET_DIR

# Benchmark 5: 3D tension-compression string design
TARGET_DIR="$BENCHMARK_ROOT/3d_tension_compression_string_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "3D tension-compression string design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "3D tension-compression string design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "The benchmark is directly cited from Hedar et al. (2006) with analytic definitions.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "3d_tension_compression_string_design",
  "materialization_commands": [],
  "definition_summary": "A three-dimensional design problem minimizing the weight of a tension-compression string subject to multiple engineering constraints.",
  "equations_or_components": [
    "f(x) = (x1 + 2) * x2 * x3^3",
    "c1(x): Constraint related to deflection and shear stress",
    "c2(x): Constraint related to material limits",
    "c3(x): Constraint related to surge frequency",
    "c4(x) = 1 - x2 + x3^1.5"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [2,15] (integer)",
    "x2 ∈ [0.25, 1.3]",
    "x3 ∈ [0.05, 2]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Based on published work; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 3D tension-compression string design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 3D tension-compression string design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: The benchmark is directly cited from Hedar et al. (2006) with analytic definitions.
definition_summary: A three-dimensional design problem minimizing the weight of a tension-compression string subject to multiple engineering constraints.
equations_or_components:
- f(x) = (x1 + 2) * x2 * x3^3
- c1(x): Constraint related to deflection and shear stress
- c2(x): Constraint related to material limits
- c3(x): Constraint related to surge frequency
- c4(x) = 1 - x2 + x3^1.5
variables_or_inputs:
- x1
- x2
- x3
constraints_or_bounds:
- x1 ∈ [2,15] (integer)
- x2 ∈ [0.25, 1.3]
- x3 ∈ [0.05, 2]
license_or_access_notes:
- Based on published work; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 3D tension-compression string design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "3D tension-compression string design",
  "matched_benchmark_name": "3D tension-compression string design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A three-dimensional design problem minimizing the weight of a tension-compression string subject to multiple engineering constraints.",
  "equations_or_components": [
    "f(x) = (x1 + 2) * x2 * x3^3",
    "c1(x): Constraint related to deflection and shear stress",
    "c2(x): Constraint related to material limits",
    "c3(x): Constraint related to surge frequency",
    "c4(x) = 1 - x2 + x3^1.5"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [2,15] (integer)",
    "x2 ∈ [0.25, 1.3]",
    "x3 ∈ [0.05, 2]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Based on published work; no license restrictions"
  ],
  "match_reason": "The benchmark is directly cited from Hedar et al. (2006) with analytic definitions.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 3D tension-compression string design"

unset TARGET_DIR

# Benchmark 6: 4D welded beam design
TARGET_DIR="$BENCHMARK_ROOT/4d_welded_beam_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "4D welded beam design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "4D welded beam design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Direct analytic formulation from Belegundu & Arora (1985) is used.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "4d_welded_beam_design",
  "materialization_commands": [],
  "definition_summary": "A four-dimensional welded beam design problem minimizing cost with multiple mechanical constraints.",
  "equations_or_components": [
    "f(x) = 1.10471*x1^2*x2 + 0.04811*x3*x4*(14 + x2)",
    "c1(x) = 13000 - τ(x)",
    "c2(x) = 30000 - σ(x)",
    "c3(x) = P_c(x) - 6000",
    "c4(x) = 0.25 - δ(x)",
    "c5(x) = x4 - x1"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [0.125, 10]",
    "x2, x3, x4 ∈ [0.1, 10]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Formulation from literature; no license issues"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 4D welded beam design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 4D welded beam design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Direct analytic formulation from Belegundu & Arora (1985) is used.
definition_summary: A four-dimensional welded beam design problem minimizing cost with multiple mechanical constraints.
equations_or_components:
- f(x) = 1.10471*x1^2*x2 + 0.04811*x3*x4*(14 + x2)
- c1(x) = 13000 - τ(x)
- c2(x) = 30000 - σ(x)
- c3(x) = P_c(x) - 6000
- c4(x) = 0.25 - δ(x)
- c5(x) = x4 - x1
variables_or_inputs:
- x1
- x2
- x3
- x4
constraints_or_bounds:
- x1 ∈ [0.125, 10]
- x2, x3, x4 ∈ [0.1, 10]
license_or_access_notes:
- Formulation from literature; no license issues
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 4D welded beam design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "4D welded beam design",
  "matched_benchmark_name": "4D welded beam design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A four-dimensional welded beam design problem minimizing cost with multiple mechanical constraints.",
  "equations_or_components": [
    "f(x) = 1.10471*x1^2*x2 + 0.04811*x3*x4*(14 + x2)",
    "c1(x) = 13000 - τ(x)",
    "c2(x) = 30000 - σ(x)",
    "c3(x) = P_c(x) - 6000",
    "c4(x) = 0.25 - δ(x)",
    "c5(x) = x4 - x1"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [0.125, 10]",
    "x2, x3, x4 ∈ [0.1, 10]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Formulation from literature; no license issues"
  ],
  "match_reason": "Direct analytic formulation from Belegundu & Arora (1985) is used.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 4D welded beam design"

unset TARGET_DIR

# Benchmark 7: 4D gas transmission compressor design
TARGET_DIR="$BENCHMARK_ROOT/4d_gas_transmission_compressor_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "4D gas transmission compressor design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "4D gas transmission compressor design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "The benchmark is taken directly from Pant et al. (2009) with published analytic expressions.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "4d_gas_transmission_compressor_design",
  "materialization_commands": [],
  "definition_summary": "A four-dimensional design problem for minimizing the annual cost of a gas transmission system, with complex expressions and one engineering constraint.",
  "equations_or_components": [
    "f(x) = (8.61e5)*sqrt(x1)*x2^(-2/3)*x3^(-1/2)*x4 + (3.69e4)*x3 + (7.72e8)*... - (765.43e6)*...",
    "c(x) = 1 - (expression involving x4)",
    "Refer to Pant et al. (2009) for full details"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [20, 50]",
    "x2 ∈ [1, 10]",
    "x3 ∈ [20, 50]",
    "x4 ∈ [0.1, 60]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Based on literature; refer to original publication for any licensing details"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 4D gas transmission compressor design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 4D gas transmission compressor design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: The benchmark is taken directly from Pant et al. (2009) with published analytic expressions.
definition_summary: A four-dimensional design problem for minimizing the annual cost of a gas transmission system, with complex expressions and one engineering constraint.
equations_or_components:
- f(x) = (8.61e5)*sqrt(x1)*x2^(-2/3)*x3^(-1/2)*x4 + (3.69e4)*x3 + (7.72e8)*... - (765.43e6)*...
- c(x) = 1 - (expression involving x4)
- Refer to Pant et al. (2009) for full details
variables_or_inputs:
- x1
- x2
- x3
- x4
constraints_or_bounds:
- x1 ∈ [20, 50]
- x2 ∈ [1, 10]
- x3 ∈ [20, 50]
- x4 ∈ [0.1, 60]
license_or_access_notes:
- Based on literature; refer to original publication for any licensing details
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 4D gas transmission compressor design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "4D gas transmission compressor design",
  "matched_benchmark_name": "4D gas transmission compressor design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A four-dimensional design problem for minimizing the annual cost of a gas transmission system, with complex expressions and one engineering constraint.",
  "equations_or_components": [
    "f(x) = (8.61e5)*sqrt(x1)*x2^(-2/3)*x3^(-1/2)*x4 + (3.69e4)*x3 + (7.72e8)*... - (765.43e6)*...",
    "c(x) = 1 - (expression involving x4)",
    "Refer to Pant et al. (2009) for full details"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [20, 50]",
    "x2 ∈ [1, 10]",
    "x3 ∈ [20, 50]",
    "x4 ∈ [0.1, 60]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Based on literature; refer to original publication for any licensing details"
  ],
  "match_reason": "The benchmark is taken directly from Pant et al. (2009) with published analytic expressions.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 4D gas transmission compressor design"

unset TARGET_DIR

# Benchmark 8: 4D pressure vessel design
TARGET_DIR="$BENCHMARK_ROOT/4d_pressure_vessel_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "4D pressure vessel design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "4D pressure vessel design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Analytic formulation directly referenced from Coello & Montes (2002).",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "4d_pressure_vessel_design",
  "materialization_commands": [],
  "definition_summary": "A four-dimensional pressure vessel design problem that minimizes total cost with four design constraints.",
  "equations_or_components": [
    "f(x) = 0.6224*x1*x3*x4 + 1.7781*x2^3 + 3.1661*x2*x1*x4 + 19.84*x2*x1*x3",
    "c1(x) = x1 - 0.0193*x3",
    "c2(x) = x2 - 0.00954*x3",
    "c3(x) = π*x2^3*x4 + (4/3)*π*x3^3 - 1296000",
    "c4(x) = 240 - x4"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4"
  ],
  "constraints_or_bounds": [
    "Design-specific bounds (see Coello & Montes, 2002)"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Literature-based benchmark; no known license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 4D pressure vessel design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 4D pressure vessel design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Analytic formulation directly referenced from Coello & Montes (2002).
definition_summary: A four-dimensional pressure vessel design problem that minimizes total cost with four design constraints.
equations_or_components:
- f(x) = 0.6224*x1*x3*x4 + 1.7781*x2^3 + 3.1661*x2*x1*x4 + 19.84*x2*x1*x3
- c1(x) = x1 - 0.0193*x3
- c2(x) = x2 - 0.00954*x3
- c3(x) = π*x2^3*x4 + (4/3)*π*x3^3 - 1296000
- c4(x) = 240 - x4
variables_or_inputs:
- x1
- x2
- x3
- x4
constraints_or_bounds:
- Design-specific bounds (see Coello & Montes, 2002)
license_or_access_notes:
- Literature-based benchmark; no known license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 4D pressure vessel design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "4D pressure vessel design",
  "matched_benchmark_name": "4D pressure vessel design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A four-dimensional pressure vessel design problem that minimizes total cost with four design constraints.",
  "equations_or_components": [
    "f(x) = 0.6224*x1*x3*x4 + 1.7781*x2^3 + 3.1661*x2*x1*x4 + 19.84*x2*x1*x3",
    "c1(x) = x1 - 0.0193*x3",
    "c2(x) = x2 - 0.00954*x3",
    "c3(x) = π*x2^3*x4 + (4/3)*π*x3^3 - 1296000",
    "c4(x) = 240 - x4"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4"
  ],
  "constraints_or_bounds": [
    "Design-specific bounds (see Coello & Montes, 2002)"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Literature-based benchmark; no known license restrictions"
  ],
  "match_reason": "Analytic formulation directly referenced from Coello & Montes (2002).",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 4D pressure vessel design"

unset TARGET_DIR

# Benchmark 9: 7D speed reducer design
TARGET_DIR="$BENCHMARK_ROOT/7d_speed_reducer_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "7D speed reducer design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "7D speed reducer design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Benchmark is taken from Lemonge et al. (2010) with explicit analytic formulations.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "7d_speed_reducer_design",
  "materialization_commands": [],
  "definition_summary": "A seven-dimensional design problem for a speed reducer, minimizing weight subject to multiple mechanical constraints.",
  "equations_or_components": [
    "f(x) = 0.7854*x1*x2^2*(3.3333*x2^3 + 14.9334*x3 - 43.0934) - 1.508*x1*(x2^6 + x7^2) + 7.4777*(x3^6 + x3^7) + 0.7854*(x4*x2^6 + x5*x2^7)",
    "c7(x) = 40 - x2*x3",
    "c8(x) = x1/x2 - 5",
    "c9(x) = 12 - x1/x2",
    "c10(x) = 1 - (1.5*x6 + 1.9)/x4",
    "c11(x) = 1 - (1.1*x7 + 1.9)/x5"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4",
    "x5",
    "x6",
    "x7"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [2.6,3.6]",
    "x2 ∈ [0.7,0.8]",
    "x3 ∈ [17,28]",
    "x4, x5 ∈ [7.3,8.3]",
    "x6 ∈ [2.9,3.9]",
    "x7 ∈ [5,5.5]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Published benchmark; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 7D speed reducer design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 7D speed reducer design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Benchmark is taken from Lemonge et al. (2010) with explicit analytic formulations.
definition_summary: A seven-dimensional design problem for a speed reducer, minimizing weight subject to multiple mechanical constraints.
equations_or_components:
- f(x) = 0.7854*x1*x2^2*(3.3333*x2^3 + 14.9334*x3 - 43.0934) - 1.508*x1*(x2^6 + x7^2) + 7.4777*(x3^6 + x3^7) + 0.7854*(x4*x2^6 + x5*x2^7)
- c7(x) = 40 - x2*x3
- c8(x) = x1/x2 - 5
- c9(x) = 12 - x1/x2
- c10(x) = 1 - (1.5*x6 + 1.9)/x4
- c11(x) = 1 - (1.1*x7 + 1.9)/x5
variables_or_inputs:
- x1
- x2
- x3
- x4
- x5
- x6
- x7
constraints_or_bounds:
- x1 ∈ [2.6,3.6]
- x2 ∈ [0.7,0.8]
- x3 ∈ [17,28]
- x4, x5 ∈ [7.3,8.3]
- x6 ∈ [2.9,3.9]
- x7 ∈ [5,5.5]
license_or_access_notes:
- Published benchmark; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 7D speed reducer design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "7D speed reducer design",
  "matched_benchmark_name": "7D speed reducer design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A seven-dimensional design problem for a speed reducer, minimizing weight subject to multiple mechanical constraints.",
  "equations_or_components": [
    "f(x) = 0.7854*x1*x2^2*(3.3333*x2^3 + 14.9334*x3 - 43.0934) - 1.508*x1*(x2^6 + x7^2) + 7.4777*(x3^6 + x3^7) + 0.7854*(x4*x2^6 + x5*x2^7)",
    "c7(x) = 40 - x2*x3",
    "c8(x) = x1/x2 - 5",
    "c9(x) = 12 - x1/x2",
    "c10(x) = 1 - (1.5*x6 + 1.9)/x4",
    "c11(x) = 1 - (1.1*x7 + 1.9)/x5"
  ],
  "variables_or_inputs": [
    "x1",
    "x2",
    "x3",
    "x4",
    "x5",
    "x6",
    "x7"
  ],
  "constraints_or_bounds": [
    "x1 ∈ [2.6,3.6]",
    "x2 ∈ [0.7,0.8]",
    "x3 ∈ [17,28]",
    "x4, x5 ∈ [7.3,8.3]",
    "x6 ∈ [2.9,3.9]",
    "x7 ∈ [5,5.5]"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Published benchmark; no license restrictions"
  ],
  "match_reason": "Benchmark is taken from Lemonge et al. (2010) with explicit analytic formulations.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 7D speed reducer design"

unset TARGET_DIR

# Benchmark 10: 9D planetary gear train design
TARGET_DIR="$BENCHMARK_ROOT/9d_planetary_gear_train_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "9D planetary gear train design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "9D planetary gear train design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "The formulation is directly taken from Rao et al. (2012) with detailed analytic expressions.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "9d_planetary_gear_train_design",
  "materialization_commands": [],
  "definition_summary": "A nine-dimensional planetary gear train design problem that minimizes gear ratio errors under complex constraints.",
  "equations_or_components": [
    "f(x) = max{|i_k - i^0_k|} for k ∈ {1,2,R}",
    "i1 = N6/N4, i^0_1 = 3.11",
    "i2 = (N6*(N1*N3 + N2*N4))/(N1*N3*(N6-N4)), i^0_2 = 1.84",
    "i_R = N2*N6/(N1*N3), i^0_R = -3.11",
    "Additional constraints as per Rao et al. (2012)"
  ],
  "variables_or_inputs": [
    "ρ",
    "N6",
    "N5",
    "N4",
    "N3",
    "N2",
    "N1",
    "m2",
    "m1"
  ],
  "constraints_or_bounds": [
    "N1 ∈ [17,96]",
    "N2 ∈ [14,54]",
    "N3 ∈ [14,51]",
    "N4 ∈ [17,46]",
    "N5 ∈ [14,51]",
    "N6 ∈ [48,124]",
    "p ∈ {3,4,5}",
    "m1, m3 ∈ {1.75,2.0,2.25,2.5,2.75,3.0}"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Standard benchmark from literature; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 9D planetary gear train design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 9D planetary gear train design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: The formulation is directly taken from Rao et al. (2012) with detailed analytic expressions.
definition_summary: A nine-dimensional planetary gear train design problem that minimizes gear ratio errors under complex constraints.
equations_or_components:
- f(x) = max{|i_k - i^0_k|} for k ∈ {1,2,R}
- i1 = N6/N4, i^0_1 = 3.11
- i2 = (N6*(N1*N3 + N2*N4))/(N1*N3*(N6-N4)), i^0_2 = 1.84
- i_R = N2*N6/(N1*N3), i^0_R = -3.11
- Additional constraints as per Rao et al. (2012)
variables_or_inputs:
- ρ
- N6
- N5
- N4
- N3
- N2
- N1
- m2
- m1
constraints_or_bounds:
- N1 ∈ [17,96]
- N2 ∈ [14,54]
- N3 ∈ [14,51]
- N4 ∈ [17,46]
- N5 ∈ [14,51]
- N6 ∈ [48,124]
- p ∈ {3,4,5}
- m1, m3 ∈ {1.75,2.0,2.25,2.5,2.75,3.0}
license_or_access_notes:
- Standard benchmark from literature; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 9D planetary gear train design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "9D planetary gear train design",
  "matched_benchmark_name": "9D planetary gear train design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A nine-dimensional planetary gear train design problem that minimizes gear ratio errors under complex constraints.",
  "equations_or_components": [
    "f(x) = max{|i_k - i^0_k|} for k ∈ {1,2,R}",
    "i1 = N6/N4, i^0_1 = 3.11",
    "i2 = (N6*(N1*N3 + N2*N4))/(N1*N3*(N6-N4)), i^0_2 = 1.84",
    "i_R = N2*N6/(N1*N3), i^0_R = -3.11",
    "Additional constraints as per Rao et al. (2012)"
  ],
  "variables_or_inputs": [
    "ρ",
    "N6",
    "N5",
    "N4",
    "N3",
    "N2",
    "N1",
    "m2",
    "m1"
  ],
  "constraints_or_bounds": [
    "N1 ∈ [17,96]",
    "N2 ∈ [14,54]",
    "N3 ∈ [14,51]",
    "N4 ∈ [17,46]",
    "N5 ∈ [14,51]",
    "N6 ∈ [48,124]",
    "p ∈ {3,4,5}",
    "m1, m3 ∈ {1.75,2.0,2.25,2.5,2.75,3.0}"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Standard benchmark from literature; no license restrictions"
  ],
  "match_reason": "The formulation is directly taken from Rao et al. (2012) with detailed analytic expressions.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 9D planetary gear train design"

unset TARGET_DIR

# Benchmark 11: 10D rolling element bearing design
TARGET_DIR="$BENCHMARK_ROOT/10d_rolling_element_bearing_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "10D rolling element bearing design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "10D rolling element bearing design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "The benchmark follows the analytic formulation presented by Gupta et al. (2007).",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "10d_rolling_element_bearing_design",
  "materialization_commands": [],
  "definition_summary": "A ten-dimensional design problem for rolling element bearings, optimizing dynamic capacity under a set of nonlinear constraints.",
  "equations_or_components": [
    "Design vector x = (Dm, Db, Z, f_i, f_o, KDmin, KDmax, ε, ...)",
    "c1(x) = φ0^2*sin^(-1)(Db/Dm - Z + 1)",
    "c2(x) = 2*Db - KDmin*(D-d)",
    "c3(x) = KDmax*(D-d) - 2*Db",
    "c4(x) = ξBw - Db",
    "c5(x) = Dm - 0.5*(D+d)",
    "c6(x) = (0.5+e)*(D+d) - Dm",
    "c7(x) = 0.5*(D-Dm-Db) - e*Db"
  ],
  "variables_or_inputs": [
    "Dm",
    "Db",
    "Z",
    "f_i",
    "f_o",
    "KDmin",
    "KDmax",
    "ε",
    "..."
  ],
  "constraints_or_bounds": [
    "Bounds as specified in Gupta et al. (2007)"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Analytic benchmark from published literature; no license restrictions"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 10D rolling element bearing design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 10D rolling element bearing design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: The benchmark follows the analytic formulation presented by Gupta et al. (2007).
definition_summary: A ten-dimensional design problem for rolling element bearings, optimizing dynamic capacity under a set of nonlinear constraints.
equations_or_components:
- Design vector x = (Dm, Db, Z, f_i, f_o, KDmin, KDmax, ε, ...)
- c1(x) = φ0^2*sin^(-1)(Db/Dm - Z + 1)
- c2(x) = 2*Db - KDmin*(D-d)
- c3(x) = KDmax*(D-d) - 2*Db
- c4(x) = ξBw - Db
- c5(x) = Dm - 0.5*(D+d)
- c6(x) = (0.5+e)*(D+d) - Dm
- c7(x) = 0.5*(D-Dm-Db) - e*Db
variables_or_inputs:
- Dm
- Db
- Z
- f_i
- f_o
- KDmin
- KDmax
- ε
- ...
constraints_or_bounds:
- Bounds as specified in Gupta et al. (2007)
license_or_access_notes:
- Analytic benchmark from published literature; no license restrictions
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 10D rolling element bearing design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "10D rolling element bearing design",
  "matched_benchmark_name": "10D rolling element bearing design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A ten-dimensional design problem for rolling element bearings, optimizing dynamic capacity under a set of nonlinear constraints.",
  "equations_or_components": [
    "Design vector x = (Dm, Db, Z, f_i, f_o, KDmin, KDmax, ε, ...)",
    "c1(x) = φ0^2*sin^(-1)(Db/Dm - Z + 1)",
    "c2(x) = 2*Db - KDmin*(D-d)",
    "c3(x) = KDmax*(D-d) - 2*Db",
    "c4(x) = ξBw - Db",
    "c5(x) = Dm - 0.5*(D+d)",
    "c6(x) = (0.5+e)*(D+d) - Dm",
    "c7(x) = 0.5*(D-Dm-Db) - e*Db"
  ],
  "variables_or_inputs": [
    "Dm",
    "Db",
    "Z",
    "f_i",
    "f_o",
    "KDmin",
    "KDmax",
    "ε",
    "..."
  ],
  "constraints_or_bounds": [
    "Bounds as specified in Gupta et al. (2007)"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Analytic benchmark from published literature; no license restrictions"
  ],
  "match_reason": "The benchmark follows the analytic formulation presented by Gupta et al. (2007).",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
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
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 10D rolling element bearing design"

unset TARGET_DIR

# Benchmark 12: 30D cantilever beam design
TARGET_DIR="$BENCHMARK_ROOT/30d_cantilever_beam_design"
mkdir -p "$TARGET_DIR"
cat > "$TARGET_DIR/benchmark_metadata.json" <<'EOF'
{
  "paper_benchmark_name": "30D cantilever beam design",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_benchmark_name": "30D cantilever beam design",
  "matched_research_field": "Constrained Bayesian Optimization",
  "name_match_type": "exact",
  "field_match_status": "same_field",
  "match_reason": "Benchmark is referenced from Cheng et al. (2018) and defined by complex analytic expressions.",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "source_type": "literature_formula",
  "source_url": "",
  "download_subdir": "30d_cantilever_beam_design",
  "materialization_commands": [],
  "definition_summary": "A high-dimensional cantilever beam design problem aimed at minimizing tip deflection, with a complex set of constraints as detailed in the original paper.",
  "equations_or_components": [
    "Full analytic formulation available in Cheng et al. (2018)"
  ],
  "variables_or_inputs": [
    "30-dimensional design vector"
  ],
  "constraints_or_bounds": [
    "Complex bounds and constraints as detailed in Cheng et al. (2018)"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Refer to the original publication for licensing details; generally no restrictions for analytic benchmarks"
  ],
  "failure_reason": ""
}
EOF
cat > "$TARGET_DIR/DOWNLOAD_STATUS.txt" <<'EOF'
paper_benchmark_name: 30D cantilever beam design
paper_research_field: Constrained Bayesian Optimization
matched_benchmark_name: 30D cantilever beam design
matched_research_field: Constrained Bayesian Optimization
name_match_type: exact
field_match_status: same_field
status: well_established_formula
materialization_strategy: generate_definition
source_type: literature_formula
source_url: Not provided
match_reason: Benchmark is referenced from Cheng et al. (2018) and defined by complex analytic expressions.
definition_summary: A high-dimensional cantilever beam design problem aimed at minimizing tip deflection, with a complex set of constraints as detailed in the original paper.
equations_or_components:
- Full analytic formulation available in Cheng et al. (2018)
variables_or_inputs:
- 30-dimensional design vector
constraints_or_bounds:
- Complex bounds and constraints as detailed in Cheng et al. (2018)
license_or_access_notes:
- Refer to the original publication for licensing details; generally no restrictions for analytic benchmarks
expected_artifacts:
- spec.txt
EOF
log_message "Generating local benchmark definition for: 30D cantilever beam design"
cat > "$TARGET_DIR/benchmark_definition.json" <<'EOF'
{
  "paper_benchmark_name": "30D cantilever beam design",
  "matched_benchmark_name": "30D cantilever beam design",
  "status": "well_established_formula",
  "materialization_strategy": "generate_definition",
  "paper_research_field": "Constrained Bayesian Optimization",
  "matched_research_field": "Constrained Bayesian Optimization",
  "definition_summary": "A high-dimensional cantilever beam design problem aimed at minimizing tip deflection, with a complex set of constraints as detailed in the original paper.",
  "equations_or_components": [
    "Full analytic formulation available in Cheng et al. (2018)"
  ],
  "variables_or_inputs": [
    "30-dimensional design vector"
  ],
  "constraints_or_bounds": [
    "Complex bounds and constraints as detailed in Cheng et al. (2018)"
  ],
  "expected_artifacts": [
    "spec.txt"
  ],
  "license_or_access_notes": [
    "Refer to the original publication for licensing details; generally no restrictions for analytic benchmarks"
  ],
  "match_reason": "Benchmark is referenced from Cheng et al. (2018) and defined by complex analytic expressions.",
  "source_type": "literature_formula",
  "source_url": ""
}
EOF
cat > "$TARGET_DIR/benchmark_definition.md" <<'EOF'
# 30D cantilever beam design

- paper_benchmark_name: 30D cantilever beam design
- status: well_established_formula
- paper_research_field: Constrained Bayesian Optimization
- matched_research_field: Constrained Bayesian Optimization
- source_type: literature_formula
- source_url: Not provided

## Definition Summary
A high-dimensional cantilever beam design problem aimed at minimizing tip deflection, with a complex set of constraints as detailed in the original paper.

## Equations Or Components
- Full analytic formulation available in Cheng et al. (2018)

## Variables Or Inputs
- 30-dimensional design vector

## Constraints Or Bounds
- Complex bounds and constraints as detailed in Cheng et al. (2018)

## Notes
- Refer to the original publication for licensing details; generally no restrictions for analytic benchmarks
EOF
GENERATED_COUNT=$((GENERATED_COUNT + 1))
log_message "Generated local benchmark definition for: 30D cantilever beam design"

unset TARGET_DIR

log_message "Generated local benchmark definitions: $GENERATED_COUNT"
if [[ "$DOWNLOADABLE_FAILURE_COUNT" -gt 0 ]]; then
  log_message "Some downloadable benchmarks failed. See $FAILURE_LOG"
  exit 1
fi

if [[ "$UNRESOLVED_COUNT" -gt 0 ]]; then
  log_message "Some benchmarks still need manual follow-up. See $FAILURE_LOG"
else
  log_message "All benchmarks were downloaded or generated without shell-level failures."
fi

log_message "Benchmark materialization script completed."
