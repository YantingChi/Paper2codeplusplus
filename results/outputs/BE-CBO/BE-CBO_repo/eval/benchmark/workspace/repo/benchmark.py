"""
benchmark.py

This module defines the Benchmark class which encapsulates the definitions of
synthetic and real‐world benchmark problems for the BE-CBO project.
It provides methods for:
  - Evaluating a candidate design point (compute objective value and feasibility flag).
  - Generating initial samples using a Sobol sequence (scaled to the problem domain).

The Benchmark class supports several benchmark problems including:
  "Townsend", "Simionescu", "LSQ", "Three-bar Truss",
  "Tension-Compression String", "Welded Beam",
  "Gas Transmission Compressor", "Pressure Vessel",
  "Speed Reducer", "Planetary Gear Train", "Rolling Element Bearing",
  and "Cantilever Beam".

Each problem is characterized by its dimension, design space bounds, an objective function,
and a (hidden) binary constraint function. Infeasible designs (e.g., out-of-bound or failing constraints)
return a None objective value and a feasibility flag of False.

Authors: BE-CBO Research Team
Date: 2024
"""

from typing import Tuple, Optional, Dict, Any
import torch
import math
from utils import generate_sobol_samples, to_device, get_logger
from config import config  # Access global configuration if needed


class Benchmark:
    """
    The Benchmark class encapsulates the objective function,
    binary constraint function, and domain information for a specific benchmark problem.
    It provides functionality to evaluate candidate designs and generate initial samples.
    """

    def __init__(self, problem_config: Dict[str, Any]) -> None:
        """
        Initialize the Benchmark instance with a given problem configuration.
        
        Args:
            problem_config (Dict[str, Any]): Dictionary containing keys such as:
                - "problem_name": Name of the benchmark problem.
                - "dimension": (Optional) The problem dimensionality.
                - "bounds": (Optional) A tuple [lower_bounds, upper_bounds] as lists.
        
        If "dimension" and "bounds" are not provided, default values for each supported problem are used.
        """
        self.logger = get_logger(f"Benchmark-{problem_config.get('problem_name', 'Unknown')}")
        self.problem_name: str = problem_config.get("problem_name", "Townsend")

        if "dimension" in problem_config and "bounds" in problem_config:
            self.dimension: int = int(problem_config["dimension"])
            bounds = problem_config["bounds"]
            self.lower_bounds: torch.Tensor = torch.tensor(bounds[0], dtype=torch.float32)
            self.upper_bounds: torch.Tensor = torch.tensor(bounds[1], dtype=torch.float32)
        else:
            # Set default dimensions and bounds for known problems
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
                self.lower_bounds = torch.tensor([2.6, 0.7, 17, 7.3, 7.3, 2.9, 5], dtype=torch.float32)
                self.upper_bounds = torch.tensor([3.6, 0.8, 28, 8.3, 8.3, 3.9, 5.5], dtype=torch.float32)
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
        """
        Internal method to initialize objective and constraint functions based on the problem name.
        The objective function f(x) computes a scalar objective value,
        while the constraint function c(x) returns 1 if feasible and 0 otherwise.
        """
        if self.problem_name == "Townsend":
            # Objective: f(x) = -[cos((x0 - 0.1) * x1)]^2 - x0*sin(3*x0 + x1)
            self.objective_fn = lambda x: - (torch.cos((x[0] - 0.1) * x[1]) ** 2).item() - (x[0] * torch.sin(3 * x[0] + x[1])).item()
            # Constraint: Feasible if x0 + x1 >= 0
            self.constraint_fn = lambda x: 1 if (x[0] + x[1]).item() >= 0 else 0

        elif self.problem_name == "Simionescu":
            # Objective: f(x) = 0.1*x0*x1 + (x0 - x1)**2
            self.objective_fn = lambda x: (0.1 * x[0] * x[1] + (x[0] - x[1]) ** 2).item()
            # Constraint: Feasible if x0 - x1 <= 0.5
            self.constraint_fn = lambda x: 1 if (x[0] - x[1]).item() <= 0.5 else 0

        elif self.problem_name == "LSQ":
            # Objective: f(x) = x0 + x1
            self.objective_fn = lambda x: (x[0] + x[1]).item()
            # Constraint: Feasible if x0^2 + x1^2 <= 1.0
            self.constraint_fn = lambda x: 1 if (x[0] ** 2 + x[1] ** 2).item() <= 1.0 else 0

        elif self.problem_name == "Three-bar Truss":
            # Objective: f(x) = 2*sqrt(2)*x0 + x1
            self.objective_fn = lambda x: (2 * math.sqrt(2) * x[0] + x[1]).item()
            # Constraint: Feasible if x0 + x1 >= 0.5
            self.constraint_fn = lambda x: 1 if (x[0] + x[1]).item() >= 0.5 else 0

        elif self.problem_name == "Tension-Compression String":
            # Objective: f(x) = (x0 + 2) * x1 * (x2**3)
            self.objective_fn = lambda x: ((x[0] + 2) * x[1] * (x[2] ** 3)).item()
            # Constraint: Feasible if x2 <= 1.5
            self.constraint_fn = lambda x: 1 if x[2].item() <= 1.5 else 0

        elif self.problem_name == "Welded Beam":
            # Objective: For simulation purposes, use the sum of the components.
            self.objective_fn = lambda x: x.sum().item()
            # Constraint: Feasible if the sum of x components is less than 20.
            self.constraint_fn = lambda x: 1 if x.sum().item() < 20 else 0

        elif self.problem_name == "Gas Transmission Compressor":
            # Objective: f(x) = (sum(x))^2
            self.objective_fn = lambda x: (x.sum() ** 2).item()
            # Constraint: Feasible if the sum of x is at least 30.
            self.constraint_fn = lambda x: 1 if x.sum().item() >= 30 else 0

        elif self.problem_name == "Pressure Vessel":
            # Objective: f(x) = sum(x)
            self.objective_fn = lambda x: x.sum().item()
            # Constraint: Feasible if all elements are greater than 5.
            self.constraint_fn = lambda x: 1 if torch.min(x).item() > 5 else 0

        elif self.problem_name == "Speed Reducer":
            # Objective: f(x) = sum(x)
            self.objective_fn = lambda x: x.sum().item()
            # Constraint: Feasible if the sum of x is less than 50.
            self.constraint_fn = lambda x: 1 if x.sum().item() < 50 else 0

        elif self.problem_name == "Planetary Gear Train":
            # Objective: f(x) = (sum(x))^2
            self.objective_fn = lambda x: (x.sum() ** 2).item()
            # Constraint: Feasible if the sum of x is less than 400.
            self.constraint_fn = lambda x: 1 if x.sum().item() < 400 else 0

        elif self.problem_name == "Rolling Element Bearing":
            # Objective: f(x) = sum(x)
            self.objective_fn = lambda x: x.sum().item()
            # Constraint: Feasible if the minimum element of x is greater than 1.
            self.constraint_fn = lambda x: 1 if torch.min(x).item() > 1 else 0

        elif self.problem_name == "Cantilever Beam":
            # Objective: f(x) = sum(x)
            self.objective_fn = lambda x: x.sum().item()
            # Constraint: Feasible if the maximum element of x is less than 9.5.
            self.constraint_fn = lambda x: 1 if torch.max(x).item() < 9.5 else 0

        else:
            raise ValueError(f"Unsupported problem: {self.problem_name}")

    def is_within_bounds(self, x: torch.Tensor) -> bool:
        """
        Checks whether the candidate design x lies within the specified domain bounds.

        Args:
            x (torch.Tensor): Candidate design point as a 1D tensor.

        Returns:
            bool: True if x is within the lower and upper bounds; otherwise, False.
        """
        return torch.all(x >= self.lower_bounds) and torch.all(x <= self.upper_bounds)

    def evaluate(self, x: torch.Tensor) -> Tuple[Optional[float], bool]:
        """
        Evaluates a candidate design x by checking domain bounds and binary constraint.

        Args:
            x (torch.Tensor): Candidate design point as a tensor of shape (dimension,).

        Returns:
            Tuple[Optional[float], bool]:
                - (objective_value, True) if candidate is feasible.
                - (None, False) if candidate is infeasible (either due to bounds or constraint).
        """
        if x.dim() != 1 or x.numel() != self.dimension:
            raise ValueError(f"Input x must be a 1D tensor of length {self.dimension}. Got shape {x.shape}.")

        # Check if x lies within the defined bounds.
        if not self.is_within_bounds(x):
            self.logger.info(f"Candidate {x.tolist()} is out of bounds.")
            return None, False

        # Evaluate constraint: if satisfied (returns 1), candidate is feasible.
        feasibility = self.constraint_fn(x)
        if feasibility == 1:
            objective_value = self.objective_fn(x)
            return objective_value, True
        else:
            self.logger.info(f"Candidate {x.tolist()} is infeasible as per constraint evaluation.")
            return None, False

    def get_initial_samples(self, num_samples: int) -> torch.Tensor:
        """
        Generates initial candidate designs using a Sobol sequence and scales them to the design domain.

        Args:
            num_samples (int): Number of design points to generate.

        Returns:
            torch.Tensor: Tensor of shape (num_samples, dimension) containing the initial candidate designs.
        """
        # Generate samples in the unit hypercube.
        unit_samples = generate_sobol_samples(num_samples, self.dimension)
        # Scale samples to the actual design domain:
        # sample = lower_bounds + (upper_bounds - lower_bounds) * unit_sample
        scaled_samples = self.lower_bounds + (self.upper_bounds - self.lower_bounds) * unit_samples
        return scaled_samples


# If the module is executed directly, perform a test run with the "Townsend" problem.
if __name__ == "__main__":
    test_config = {
        "problem_name": "Townsend"
        # Optionally, "dimension" and "bounds" can be provided.
    }
    benchmark = Benchmark(test_config)
    print("Generating initial samples for the Townsend problem:")
    initial_samples = benchmark.get_initial_samples(5)
    print(initial_samples)
    
    print("\nEvaluating each initial sample:")
    for i in range(initial_samples.shape[0]):
        candidate = initial_samples[i]
        obj_val, feasible = benchmark.evaluate(candidate)
        print(f"Sample {i+1}: x = {candidate.tolist()}, Feasible = {feasible}, Objective = {obj_val}")
