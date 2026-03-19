"""
benchmark.py

Compact synthetic benchmark definitions for the Harbor BE-CBO task.

Only Townsend, Simionescu, and LSQ are required by the bundled verifier.
The current implementation is intentionally imperfect.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import math

import torch

from utils import generate_sobol_samples, get_logger


class Benchmark:
    def __init__(self, problem_config: Dict[str, Any]) -> None:
        self.problem_name = problem_config.get("problem_name", "Townsend")
        self.logger = get_logger(f"Benchmark-{self.problem_name}")

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
        else:
            raise ValueError(f"Unsupported problem: {self.problem_name}")

    def get_initial_samples(self, num_samples: int) -> torch.Tensor:
        unit = generate_sobol_samples(num_samples=num_samples, dim=self.dimension)
        return self.lower_bounds + unit * (self.upper_bounds - self.lower_bounds)

    def is_within_bounds(self, x: torch.Tensor) -> bool:
        return bool(torch.all(x >= self.lower_bounds) and torch.all(x <= self.upper_bounds))

    def _objective(self, x: torch.Tensor) -> Optional[float]:
        x1 = float(x[0].item())
        x2 = float(x[1].item())

        if self.problem_name == "Townsend":
            return -math.cos((x1 - 0.1) * x2) ** 2 - x1 * math.sin(3.0 * x1 + x2)
        if self.problem_name == "Simionescu":
            return 0.1 * x1 * x2
        if self.problem_name == "LSQ":
            return x1 + x2
        return None

    def _constraint_feasible(self, x: torch.Tensor) -> bool:
        x1 = float(x[0].item())
        x2 = float(x[1].item())

        if self.problem_name == "Townsend":
            # Intentionally wrong: the appendix uses atan2(x1, x2) and c >= 0.
            theta = math.atan2(x2, x1)
            radius_sq = (
                (2.0 * math.cos(theta) - 0.5 * math.cos(2.0 * theta) - 0.25 * math.cos(3.0 * theta) - 0.125 * math.cos(4.0 * theta)) ** 2
                + (2.0 * math.sin(theta)) ** 2
            )
            c_value = radius_sq - x1 ** 2 - x2 ** 2
            return c_value <= 0.0

        if self.problem_name == "Simionescu":
            # Intentionally wrong: the appendix uses atan2(x1, x2) and c >= 0.
            theta = math.atan2(x2, x1)
            c_value = (1.0 + 0.2 * math.cos(8.0 * theta)) ** 2 - x1 ** 2 - x2 ** 2
            return c_value < 0.0

        if self.problem_name == "LSQ":
            # Intentionally wrong: both appendix constraints should be >= 0 simultaneously.
            c1 = x1 + 2.0 * x2 + 0.5 * math.sin(2.0 * math.pi * (x1 ** 2 - 2.0 * x2)) - 1.5
            c2 = 1.5 - x1 ** 2 - x2 ** 2
            return (c1 <= 0.0) or (c2 <= 0.0)

        return False

    def evaluate(self, x: torch.Tensor) -> Tuple[Optional[float], bool]:
        if x.dim() != 1 or x.numel() != self.dimension:
            raise ValueError(
                f"Input x must be a 1D tensor of length {self.dimension}. Got shape {tuple(x.shape)}."
            )

        if not self.is_within_bounds(x):
            self.logger.info("Candidate %s is outside bounds.", x.tolist())
            return None, False

        feasible = self._constraint_feasible(x)
        if not feasible:
            self.logger.info("Candidate %s is infeasible as per constraint evaluation.", x.tolist())
            return None, False

        objective_value = self._objective(x)
        return objective_value, True


if __name__ == "__main__":
    benchmark = Benchmark({"problem_name": "Townsend"})
    samples = benchmark.get_initial_samples(3)
    for sample in samples:
        print(sample.tolist(), benchmark.evaluate(sample))
