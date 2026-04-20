#!/bin/bash
set -euo pipefail

cd /workspace

cat > environment/codebase/config.py <<'PYCODE'
"""
config.py

Central configuration used across the BE-CBO repository.
"""

from dataclasses import dataclass, field, asdict
from typing import List


@dataclass
class TrainingConfig:
    optimizer: str = "Adam"
    learning_rate: float = 3e-4
    iterations: int = 1000


@dataclass
class EnsembleConfig:
    size: int = 5
    layers: int = 4
    neurons_formula: str = "64 * floor(log2(d))"
    activation: str = "ReLU"
    loss: str = "Variational Inference (ELBO)"
    use_dropout: bool = False


@dataclass
class GPConfig:
    kernel: str = "Matern5/2"
    hyperparameters: str = "default (using BoTorch defaults)"


@dataclass
class BOConfig:
    initial_samples: int = 10
    total_evaluations: int = 200
    random_seeds: int = 10
    acquisition_function: str = "Expected Improvement (EI)"
    dynamic_bound: str = "l(x) = 0.5 - sigma_E(x)"
    optimizer: str = "SLSQP (with pre-optimization using Adam)"


@dataclass
class BenchmarkConfig:
    problems: List[str] = field(default_factory=lambda: [
        "Townsend",
        "Simionescu",
        "LSQ",
        "Three-bar Truss",
        "Tension-Compression String",
        "Welded Beam",
        "Gas Transmission Compressor",
        "Pressure Vessel",
        "Speed Reducer",
        "Planetary Gear Train",
        "Rolling Element Bearing",
        "Cantilever Beam"
    ])


@dataclass
class LoggingConfig:
    verbosity: str = "INFO"
    save_results: bool = True


@dataclass
class GeneralConfig:
    device: str = "cpu"
    seed: int = 42


@dataclass
class Config:
    training: TrainingConfig = field(default_factory=TrainingConfig)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    gp: GPConfig = field(default_factory=GPConfig)
    bo: BOConfig = field(default_factory=BOConfig)
    benchmark: BenchmarkConfig = field(default_factory=BenchmarkConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)


config = Config()


if __name__ == "__main__":
    import json
    print(json.dumps(asdict(config), indent=4))
PYCODE

cat > environment/codebase/benchmark.py <<'PYCODE'
"""
benchmark.py

Analytic benchmark definitions for the BE-CBO repository.
"""

from typing import Tuple, Optional, Dict, Any
import math

import torch

from utils import generate_sobol_samples, get_logger


class Benchmark:
    def __init__(self, problem_config: Dict[str, Any]) -> None:
        self.logger = get_logger(f"Benchmark-{problem_config.get('problem_name', 'Unknown')}")
        self.problem_name: str = problem_config.get("problem_name", "Townsend")

        if "dimension" in problem_config and "bounds" in problem_config:
            self.dimension = int(problem_config["dimension"])
            bounds = problem_config["bounds"]
            self.lower_bounds = torch.tensor(bounds[0], dtype=torch.float32)
            self.upper_bounds = torch.tensor(bounds[1], dtype=torch.float32)
        else:
            if self.problem_name == "Townsend":
                self.dimension = 2
                self.lower_bounds = torch.tensor([-2.25, -2.5], dtype=torch.float32)
                self.upper_bounds = torch.tensor([2.25, 1.75], dtype=torch.float32)
            elif self.problem_name == "Simionescu":
                self.dimension = 2
                self.lower_bounds = torch.tensor([-1.25, -1.25], dtype=torch.float32)
                self.upper_bounds = torch.tensor([1.25, 1.25], dtype=torch.float32)
            elif self.problem_name == "LSQ":
                self.dimension = 2
                self.lower_bounds = torch.tensor([0.0, 0.0], dtype=torch.float32)
                self.upper_bounds = torch.tensor([1.0, 1.0], dtype=torch.float32)
            elif self.problem_name == "Three-bar Truss":
                self.dimension = 2
                self.lower_bounds = torch.tensor([0.0, 0.0], dtype=torch.float32)
                self.upper_bounds = torch.tensor([1.0, 1.0], dtype=torch.float32)
            elif self.problem_name == "Tension-Compression String":
                self.dimension = 3
                self.lower_bounds = torch.tensor([2.0, 0.25, 0.05], dtype=torch.float32)
                self.upper_bounds = torch.tensor([15.0, 1.3, 2.0], dtype=torch.float32)
            elif self.problem_name == "Welded Beam":
                self.dimension = 4
                self.lower_bounds = torch.tensor([0.125, 0.1, 0.1, 0.1], dtype=torch.float32)
                self.upper_bounds = torch.tensor([10.0, 10.0, 10.0, 10.0], dtype=torch.float32)
            elif self.problem_name == "Gas Transmission Compressor":
                self.dimension = 4
                self.lower_bounds = torch.tensor([20.0, 1.0, 20.0, 0.1], dtype=torch.float32)
                self.upper_bounds = torch.tensor([50.0, 10.0, 50.0, 60.0], dtype=torch.float32)
            elif self.problem_name == "Pressure Vessel":
                self.dimension = 4
                self.lower_bounds = torch.tensor([1.0, 1.0, 10.0, 10.0], dtype=torch.float32)
                self.upper_bounds = torch.tensor([100.0, 100.0, 100.0, 100.0], dtype=torch.float32)
            elif self.problem_name == "Speed Reducer":
                self.dimension = 7
                self.lower_bounds = torch.tensor([2.6, 0.7, 17.0, 7.3, 7.3, 2.9, 5.0], dtype=torch.float32)
                self.upper_bounds = torch.tensor([3.6, 0.8, 28.0, 8.3, 8.3, 3.9, 5.5], dtype=torch.float32)
            elif self.problem_name == "Planetary Gear Train":
                self.dimension = 9
                self.lower_bounds = torch.zeros(9, dtype=torch.float32)
                self.upper_bounds = torch.ones(9, dtype=torch.float32) * 100.0
            elif self.problem_name == "Rolling Element Bearing":
                self.dimension = 10
                self.lower_bounds = torch.zeros(10, dtype=torch.float32)
                self.upper_bounds = torch.ones(10, dtype=torch.float32) * 50.0
            elif self.problem_name == "Cantilever Beam":
                self.dimension = 30
                self.lower_bounds = torch.zeros(30, dtype=torch.float32)
                self.upper_bounds = torch.ones(30, dtype=torch.float32) * 10.0
            else:
                raise ValueError(f"Unsupported problem: {self.problem_name}")

        self._init_problem_functions()

    def _init_problem_functions(self) -> None:
        if self.problem_name == "Townsend":
            def objective(x: torch.Tensor) -> float:
                x1 = float(x[0].item())
                x2 = float(x[1].item())
                return -(math.cos((x1 - 0.1) * x2) ** 2) - x1 * math.sin(3.0 * x1 + x2)

            def constraint(x: torch.Tensor) -> int:
                x1 = float(x[0].item())
                x2 = float(x[1].item())
                t = math.atan2(x1, x2)
                value = (
                    (2.0 * math.cos(t) - 0.5 * math.cos(2.0 * t) - 0.25 * math.cos(3.0 * t) - 0.125 * math.cos(4.0 * t)) ** 2
                    + (2.0 * math.sin(t)) ** 2
                    - x1 ** 2
                    - x2 ** 2
                )
                return 1 if value >= 0.0 else 0

            self.objective_fn = objective
            self.constraint_fn = constraint

        elif self.problem_name == "Simionescu":
            def objective(x: torch.Tensor) -> float:
                x1 = float(x[0].item())
                x2 = float(x[1].item())
                return 0.1 * x1 * x2

            def constraint(x: torch.Tensor) -> int:
                x1 = float(x[0].item())
                x2 = float(x[1].item())
                theta = math.atan2(x1, x2)
                value = (1.0 + 0.2 * math.cos(8.0 * theta)) ** 2 - x1 ** 2 - x2 ** 2
                return 1 if value >= 0.0 else 0

            self.objective_fn = objective
            self.constraint_fn = constraint

        elif self.problem_name == "LSQ":
            def objective(x: torch.Tensor) -> float:
                return float((x[0] + x[1]).item())

            def constraint(x: torch.Tensor) -> int:
                x1 = float(x[0].item())
                x2 = float(x[1].item())
                c1 = x1 + 2.0 * x2 + 0.5 * math.sin(2.0 * math.pi * (x1 ** 2 - 2.0 * x2)) - 1.5
                c2 = 1.5 - x1 ** 2 - x2 ** 2
                return 1 if c1 >= 0.0 and c2 >= 0.0 else 0

            self.objective_fn = objective
            self.constraint_fn = constraint

        elif self.problem_name == "Three-bar Truss":
            self.objective_fn = lambda x: float((2 * math.sqrt(2) * x[0] + x[1]).item())
            self.constraint_fn = lambda x: 1 if (x[0] + x[1]).item() >= 0.5 else 0

        elif self.problem_name == "Tension-Compression String":
            self.objective_fn = lambda x: float(((x[0] + 2) * x[1] * (x[2] ** 3)).item())
            self.constraint_fn = lambda x: 1 if x[2].item() <= 1.5 else 0

        elif self.problem_name == "Welded Beam":
            self.objective_fn = lambda x: float(x.sum().item())
            self.constraint_fn = lambda x: 1 if x.sum().item() < 20 else 0

        elif self.problem_name == "Gas Transmission Compressor":
            self.objective_fn = lambda x: float((x.sum() ** 2).item())
            self.constraint_fn = lambda x: 1 if x.sum().item() >= 30 else 0

        elif self.problem_name == "Pressure Vessel":
            self.objective_fn = lambda x: float(x.sum().item())
            self.constraint_fn = lambda x: 1 if torch.min(x).item() > 5 else 0

        elif self.problem_name == "Speed Reducer":
            self.objective_fn = lambda x: float(x.sum().item())
            self.constraint_fn = lambda x: 1 if x.sum().item() < 50 else 0

        elif self.problem_name == "Planetary Gear Train":
            self.objective_fn = lambda x: float((x.sum() ** 2).item())
            self.constraint_fn = lambda x: 1 if x.sum().item() < 400 else 0

        elif self.problem_name == "Rolling Element Bearing":
            self.objective_fn = lambda x: float(x.sum().item())
            self.constraint_fn = lambda x: 1 if torch.min(x).item() > 1 else 0

        elif self.problem_name == "Cantilever Beam":
            self.objective_fn = lambda x: float(x.sum().item())
            self.constraint_fn = lambda x: 1 if torch.max(x).item() < 9.5 else 0

        else:
            raise ValueError(f"Unsupported problem: {self.problem_name}")

    def is_within_bounds(self, x: torch.Tensor) -> bool:
        return bool(torch.all(x >= self.lower_bounds) and torch.all(x <= self.upper_bounds))

    def evaluate(self, x: torch.Tensor) -> Tuple[Optional[float], bool]:
        if x.dim() != 1 or x.numel() != self.dimension:
            raise ValueError(f"Input x must be a 1D tensor of length {self.dimension}. Got shape {x.shape}.")
        if not self.is_within_bounds(x):
            return None, False
        feasible = self.constraint_fn(x)
        if feasible == 1:
            return self.objective_fn(x), True
        return None, False

    def get_initial_samples(self, num_samples: int) -> torch.Tensor:
        unit_samples = generate_sobol_samples(num_samples, self.dimension)
        return self.lower_bounds + (self.upper_bounds - self.lower_bounds) * unit_samples
PYCODE

python environment/benchmark/run_benchmark.py \
  --repo-dir environment/codebase \
  --cases environment/benchmark/synthetic_cases.json \
  --expected environment/expected_results/reference_results.json \
  --output environment/codebase/reproduction_result.json
