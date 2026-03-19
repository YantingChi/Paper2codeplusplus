"""
botrainer.py

This module implements the BOTrainer class which orchestrates the Bayesian optimization (BO)
loop for the BE-CBO method. BOTrainer integrates the following modules:

  - Benchmark: encapsulates the objective function, binary constraint function, and design domain.
  - GPSurrogate: the GP surrogate for the expensive objective (using BoTorch/GPyTorch).
  - DeepEnsembleClassifier: the deep ensemble of MLPs for modeling the unknown binary constraint.
  - AcquisitionOptimizer: optimizes the acquisition function (Expected Improvement) under the dynamic
    constraint (dynamic lower bound from the classifier).
    
It uses settings from the configuration (imported from config.py) including:
    - bo.initial_samples, bo.total_evaluations,
    - training parameters for the deep ensemble,
    - GP kernel settings, etc.

The BOTrainer performs the following steps:
    1. Generates initial samples using a Sobol sequence and evaluates each candidate via Benchmark.evaluate().
       Feasible samples (with valid objective values) are stored in the GP training dataset; every sample
       (with its binary feasibility label) is stored in the classifier dataset.
    2. Iteratively:
         - Updates the GP surrogate with feasible observations and trains the deep ensemble classifier
           with all evaluated data.
         - Defines the dynamic lower bound l(x) = 0.5 - σ_E(x) (where σ_E is computed from the classifier).
         - Uses the AcquisitionOptimizer to find a new candidate that satisfies the acquisition function
           (e.g., Expected Improvement) and dynamic constraint.
         - Evaluates the candidate and updates the datasets (GP and classifier) accordingly.
         - Logs per-iteration results (candidate, feasibility, objective value if feasible, best objective,
           feasibility ratio).
    3. Terminates when the total number of evaluations reaches the configured budget.

All hyperparameters are read from config.yaml via config.py.
"""

from typing import List, Tuple, Dict, Any
import torch
import numpy as np

from config import config
from benchmark import Benchmark
from gp_surrogate import GPSurrogate
from deep_ensemble import DeepEnsembleClassifier
from acquisition import AcquisitionOptimizer
from utils import get_logger, set_seed, to_device

# Define BOTrainer class based on the design diagram.
class BOTrainer:
    """
    BOTrainer orchestrates the Bayesian optimization loop for BE-CBO.
    
    Attributes:
        benchmark (Benchmark): The benchmark problem instance.
        gp (GPSurrogate): The GP surrogate for the objective function.
        classifier (DeepEnsembleClassifier): The deep ensemble classifier for constraints.
        acq_opt (AcquisitionOptimizer): The optimizer for the acquisition function.
        config: Configuration object from config.py.
        gp_data (List[Tuple[torch.Tensor, float]]): History of feasible evaluations for GP surrogate.
        classifier_data (List[Tuple[torch.Tensor, int]]): History of evaluations with feasibility label.
        log (List[Dict[str, Any]]): Log records for each BO iteration.
        eval_count (int): Counter for the total number of evaluations performed.
        total_evals (int): Total evaluation budget for the BO run.
        init_samples (int): Number of initial samples to be generated.
    """
    
    def __init__(self,
                 benchmark: Benchmark,
                 gp: GPSurrogate,
                 classifier: DeepEnsembleClassifier,
                 acq_opt: AcquisitionOptimizer,
                 config_obj: Any = config) -> None:
        """
        Initializes the BOTrainer with the provided Benchmark, GPSurrogate, DeepEnsembleClassifier,
        AcquisitionOptimizer, and configuration.
        
        Args:
            benchmark (Benchmark): Instance providing objective/constraint functions and domain bounds.
            gp (GPSurrogate): GP surrogate for the objective function.
            classifier (DeepEnsembleClassifier): Deep ensemble classifier for constraints.
            acq_opt (AcquisitionOptimizer): Optimizer for the acquisition function.
            config_obj: Configuration object (default from config.py).
        """
        self.benchmark: Benchmark = benchmark
        self.gp: GPSurrogate = gp
        self.classifier: DeepEnsembleClassifier = classifier
        self.acq_opt: AcquisitionOptimizer = acq_opt
        self.config = config_obj
        
        # Initialize datasets: gp_data stores (x, objective_value) for feasible evaluations;
        # classifier_data stores (x, feasibility_label) for all evaluations.
        self.gp_data: List[Tuple[torch.Tensor, float]] = []
        self.classifier_data: List[Tuple[torch.Tensor, int]] = []
        # Log per-iteration results.
        self.log: List[Dict[str, Any]] = []
        
        # Read evaluation budget settings from configuration.
        self.init_samples: int = self.config.bo.initial_samples
        self.total_evals: int = self.config.bo.total_evaluations
        self.eval_count: int = 0
        
        self.logger = get_logger("BOTrainer")
        self.logger.info("Initialized BOTrainer with initial_samples=%d and total_evals=%d.",
                         self.init_samples, self.total_evals)
    
    def run(self) -> None:
        """
        Runs the full Bayesian optimization loop.
        1. Generates and evaluates initial samples.
        2. Iteratively:
            - Updates the GP surrogate and deep ensemble classifier.
            - Optimizes the acquisition function with dynamic constraint to get a new candidate.
            - Evaluates the candidate and updates datasets.
            - Logs iteration metrics.
        Terminates when the total evaluation budget is reached.
        """
        # Set random seed for reproducibility.
        set_seed()
        
        # Generate initial samples using the benchmark's Sobol sequence generator.
        self.logger.info("Generating %d initial samples...", self.init_samples)
        initial_samples: torch.Tensor = self.benchmark.get_initial_samples(self.init_samples)
        num_dim: int = initial_samples.shape[1]
        
        # Evaluate each initial sample.
        for i in range(initial_samples.shape[0]):
            x: torch.Tensor = initial_samples[i]
            obj_value, feasible_flag = self.benchmark.evaluate(x)
            # If feasible, add (x, obj_value) to gp_data.
            if feasible_flag:
                self.gp_data.append((x.clone().detach(), obj_value))
            # Add to classifier_data regardless of feasibility.
            self.classifier_data.append((x.clone().detach(), int(feasible_flag)))
            # Log the evaluation.
            self.log.append({
                "iteration": self.eval_count + 1,
                "candidate": x.clone().detach().cpu().numpy(),
                "feasible": feasible_flag,
                "objective": obj_value if feasible_flag else None
            })
            self.eval_count += 1
        
        self.logger.info("Initial evaluation complete. Total initial evaluations: %d", self.eval_count)
        
        # Main BO iteration loop until the evaluation budget is reached.
        while self.eval_count < self.total_evals:
            self.logger.info("Starting BO iteration: %d", self.eval_count + 1)
            
            # Update the GP surrogate with only feasible data.
            if len(self.gp_data) > 0:
                self.gp.update(self.gp_data)
            else:
                self.logger.warning("No feasible data available for GP update at iteration %d.", self.eval_count + 1)
            
            # Update the classifier with all evaluation data.
            if len(self.classifier_data) > 0:
                self.classifier.train_model(self.classifier_data)
            else:
                self.logger.warning("No data available for classifier training at iteration %d.", self.eval_count + 1)
            
            # Define the acquisition function as a function that takes a candidate x and returns the EI value.
            def acq_func(x: torch.Tensor) -> torch.Tensor:
                return self.gp.acquisition(x)
            
            # Prepare bounds for the design domain.
            # Stack lower_bounds and upper_bounds to produce a tensor of shape [2, d].
            bounds: torch.Tensor = torch.stack([self.benchmark.lower_bounds, self.benchmark.upper_bounds], dim=0)
            
            # Use AcquisitionOptimizer to propose a new candidate x_new.
            x_new: torch.Tensor = self.acq_opt.optimize(acq_func, self.classifier, bounds)
            x_new = x_new.detach()
            self.logger.info("Proposed candidate: %s", x_new.cpu().numpy())
            
            # Evaluate the candidate using Benchmark.evaluate().
            obj_value_new, feasible_new = self.benchmark.evaluate(x_new)
            self.logger.info("Evaluation result: Feasible=%s, Objective=%s", feasible_new, obj_value_new)
            
            # If candidate is feasible, update GP dataset.
            if feasible_new:
                self.gp_data.append((x_new.clone(), obj_value_new))
            
            # Always update the classifier dataset with the new point and its feasibility label.
            self.classifier_data.append((x_new.clone(), int(feasible_new)))
            
            # For logging, compute current best objective (if available) and feasibility ratio.
            best_objective: float = np.inf
            if len(self.gp_data) > 0:
                # Get best objective among feasible points.
                best_objective = min([val for (_, val) in self.gp_data])
            feasible_count: int = sum([label for (_, label) in self.classifier_data])
            feasibility_ratio: float = feasible_count / (len(self.classifier_data))
            
            # Log iteration details.
            iter_log: Dict[str, Any] = {
                "iteration": self.eval_count + 1,
                "candidate": x_new.cpu().numpy(),
                "feasible": feasible_new,
                "objective": obj_value_new if feasible_new else None,
                "best_objective": best_objective if best_objective != np.inf else None,
                "feasibility_ratio": feasibility_ratio
            }
            self.log.append(iter_log)
            
            self.logger.info("Iteration %d complete. Best objective so far: %s, Feasibility ratio: %.3f",
                             self.eval_count + 1,
                             best_objective if best_objective != np.inf else "None",
                             feasibility_ratio)
            
            # Increment evaluation counter.
            self.eval_count += 1
            
        self.logger.info("BO run complete. Total evaluations: %d", self.eval_count)
        # Final summary logging.
        final_best: float = min([val for (_, val) in self.gp_data]) if len(self.gp_data) > 0 else np.nan
        overall_feasibility_ratio: float = sum([label for (_, label) in self.classifier_data]) / len(self.classifier_data)
        self.logger.info("Final Best Objective: %s", final_best)
        self.logger.info("Final Feasibility Ratio: %.3f", overall_feasibility_ratio)
    
    def get_log(self) -> List[Dict[str, Any]]:
        """
        Returns the log of all BO iterations.
        
        Returns:
            List[Dict[str, Any]]: List of logged iteration results.
        """
        return self.log

# If this module is executed directly, run a simple BOTrainer on a default benchmark.
if __name__ == "__main__":
    # Set seed for reproducibility
    set_seed()
    
    # For demonstration, choose the first benchmark problem from the configuration.
    # Here, we use the "Townsend" problem.
    problem_config: Dict[str, Any] = {"problem_name": "Townsend"}
    benchmark_instance = Benchmark(problem_config)
    
    # Create GP surrogate instance.
    gp_instance = GPSurrogate()
    
    # Create Deep Ensemble Classifier instance with problem dimension from benchmark.
    classifier_instance = DeepEnsembleClassifier(problem_dim=benchmark_instance.dimension)
    
    # Create Acquisition Optimizer instance.
    acq_optimizer_instance = AcquisitionOptimizer()
    
    # Initialize BOTrainer with the above instances and global configuration.
    bo_trainer = BOTrainer(
        benchmark=benchmark_instance,
        gp=gp_instance,
        classifier=classifier_instance,
        acq_opt=acq_optimizer_instance,
        config_obj=config
    )
    
    # Run the BO training loop.
    bo_trainer.run()
    
    # Optionally, retrieve and print the log of BO iterations.
    final_log = bo_trainer.get_log()
    print("Final BO Log:")
    for log_entry in final_log:
        print(log_entry)
