"""main.py

This is the entry point for the BE-CBO Bayesian optimization application.
It loads the configuration from config.py (which in turn reads config.yaml),
initializes all key components (Benchmark, GP surrogate, Deep Ensemble classifier,
AcquisitionOptimizer, BOTrainer), runs the Bayesian optimization loop, and finally
evaluates and logs the performance metrics.

The high‐level flow is:
    1. Set the global random seed and device.
    2. Instantiate the Benchmark using a chosen problem configuration.
    3. Create the GP surrogate and the Deep Ensemble classifier using the problem dimension.
    4. Create the AcquisitionOptimizer.
    5. Create the BOTrainer, which orchestrates the BO loop.
    6. Run BOTrainer.run() to execute the optimization.
    7. After BO is finished, collect logs and aggregate classifier data.
    8. Compute evaluation metrics (best objective, feasibility ratio, balanced accuracy of the classifier).
    9. Print and log the summary evaluation metrics.

All configuration values are taken from config.yaml via config.py.
"""

import numpy as np
import torch
from torch import Tensor

# Import configuration and modules
from config import config
from utils import set_seed, get_logger, to_device
from benchmark import Benchmark
from gp_surrogate import GPSurrogate
from deep_ensemble import DeepEnsembleClassifier
from acquisition import AcquisitionOptimizer
from botrainer import BOTrainer
from evaluation import Evaluation


def main() -> None:
    # Initialize logger
    logger = get_logger("Main")
    logger.info("BE-CBO main entry point started.")

    # Set global random seed (from config.general.seed)
    set_seed(config.general.seed)
    logger.info("Random seed set to %d and device: %s", config.general.seed, config.general.device)

    # Choose benchmark problem
    # Use the first problem from the list in config.benchmark.problems as default.
    chosen_problem: str = config.benchmark.problems[0]
    logger.info("Chosen benchmark problem: %s", chosen_problem)

    # Create problem configuration dictionary for Benchmark.
    # For simplicity, we only specify "problem_name". For known benchmark, defaults are defined in Benchmark.
    problem_config: dict = {"problem_name": chosen_problem}

    # Instantiate Benchmark object.
    benchmark_instance = Benchmark(problem_config)
    logger.info("Benchmark instance for '%s' created with dimension %d.",
                benchmark_instance.problem_name, benchmark_instance.dimension)

    # Instantiate GP Surrogate.
    gp_instance = GPSurrogate()
    logger.info("GP Surrogate instantiated.")

    # Instantiate Deep Ensemble Classifier using problem dimension.
    classifier_instance = DeepEnsembleClassifier(problem_dim=benchmark_instance.dimension)
    logger.info("Deep Ensemble Classifier instantiated with problem dimension %d.", benchmark_instance.dimension)

    # Instantiate Acquisition Optimizer.
    acq_optimizer_instance = AcquisitionOptimizer()
    logger.info("Acquisition Optimizer instantiated.")

    # Instantiate BOTrainer with the above modules.
    bo_trainer = BOTrainer(
        benchmark=benchmark_instance,
        gp=gp_instance,
        classifier=classifier_instance,
        acq_opt=acq_optimizer_instance,
        config_obj=config
    )
    logger.info("BOTrainer instantiated.")

    # Run the Bayesian Optimization loop.
    logger.info("Starting the Bayesian Optimization loop with total evaluations = %d.",
                config.bo.total_evaluations)
    bo_trainer.run()
    logger.info("Bayesian Optimization loop completed.")

    # Retrieve the log from BOTrainer which includes each iteration record.
    bo_log = bo_trainer.get_log()
    logger.info("Collected BO log with %d iterations.", len(bo_log))

    # Aggregate classifier data for evaluation:
    # Extract the true feasibility labels and compute predicted feasibility probabilities for each evaluated candidate.
    classifier_data = bo_trainer.classifier_data  # List[Tuple[Tensor, int]]
    true_labels: list = []
    candidate_list: list = []
    for (x, label) in classifier_data:
        true_labels.append(label)
        candidate_list.append(x)
    if candidate_list:
        # Stack candidate design points into one tensor.
        candidates_tensor: Tensor = torch.stack(candidate_list, dim=0)
        # Use the classifier instance to predict feasibility probabilities.
        pred_probs_tensor, _ = classifier_instance.predict(candidates_tensor)
        # Convert predicted probabilities to a Python list.
        pred_probs_list: list = pred_probs_tensor.detach().cpu().numpy().tolist()
    else:
        true_labels = []
        pred_probs_list = []

    # Prepare evaluation results dictionary.
    results: dict = {
        "evaluations": bo_log,
        "classifier_true_labels": true_labels,
        "classifier_pred_probs": pred_probs_list
    }

    # Instantiate Evaluation and compute metrics.
    evaluator = Evaluation(results)
    metrics = evaluator.compute_metrics()

    # Log and print final evaluation metrics.
    logger.info("Final Evaluation Metrics:")
    logger.info("Best Objective: %s", metrics.get("best_objective"))
    logger.info("Feasibility Ratio: %.4f", metrics.get("feasibility_ratio"))
    if metrics.get("balanced_accuracy") is not None:
        logger.info("Balanced Accuracy: %.4f", metrics.get("balanced_accuracy"))
    logger.info("Total Evaluations: %d", metrics.get("total_evaluations"))
    
    print("\n----- BE-CBO Final Evaluation Metrics -----")
    print(f"Best Objective: {metrics.get('best_objective')}")
    print(f"Feasibility Ratio: {metrics.get('feasibility_ratio'):.4f}")
    if metrics.get("balanced_accuracy") is not None:
        print(f"Balanced Accuracy: {metrics.get('balanced_accuracy'):.4f}")
    else:
        print("Balanced Accuracy: NA")
    print(f"Total Evaluations: {metrics.get('total_evaluations')}")
    print("-------------------------------------------\n")

if __name__ == "__main__":
    main()