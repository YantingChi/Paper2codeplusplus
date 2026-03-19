"""
evaluation.py

This module provides the Evaluation class that computes key metrics from the results of
the Bayesian optimization (BO) process carried out by the BOTrainer.

It computes the following metrics:
  1. Best (minimum) feasible objective value (only consider evaluations where feasibility is True).
  2. Feasibility ratio: (# feasible evaluations) / (total evaluations).
  3. Balanced Accuracy for the constraint classifier using the ground truth labels and predicted 
     feasibility probabilities. Predictions are thresholded at 0.5. Balanced Accuracy is the average
     of sensitivity (TPR) and specificity (TNR).

The Evaluation class accepts a results dictionary containing at least:
    - "evaluations": A list of records; each record is a dictionary with keys:
          "candidate": design vector,
          "feasible": boolean flag,
          "objective": objective value if feasible (None otherwise).
    - Optionally, it may contain:
          "classifier_true_labels": list of ground truth labels (1 for feasible, 0 for infeasible).
          "classifier_pred_probs": list of predicted feasibility probabilities (floats).

If any required key is missing, a warning is logged and the metric may be skipped.

Usage Example:
    from evaluation import Evaluation
    results = {
        "evaluations": [
            {"iteration": 1, "candidate": [0.1, 0.2], "feasible": True, "objective": 1.25},
            {"iteration": 2, "candidate": [0.3, 0.4], "feasible": False, "objective": None},
            ...
        ],
        "classifier_true_labels": [1, 0, ...],
        "classifier_pred_probs": [0.65, 0.35, ...]
    }
    evaluator = Evaluation(results)
    metrics = evaluator.compute_metrics()
    print(metrics)
"""

from typing import Any, Dict, List, Optional
import numpy as np
import logging

from config import config  # Import global configuration (from config.py)
from utils import get_logger  # Utility function for logging

class Evaluation:
    """
    Evaluation class computes metrics from the BOTrainer run results.
    
    Attributes:
        results (Dict[str, Any]): Dictionary containing the evaluation log and classifier data.
        evaluations (List[Dict[str, Any]]): List of evaluation records.
        classifier_true_labels (Optional[List[int]]): Ground truth binary labels for constraint feasibility.
        classifier_pred_probs (Optional[List[float]]): Predicted feasibility probabilities from the classifier.
        logger (logging.Logger): Logger instance for logging messages.
    """
    def __init__(self, results: Dict[str, Any]) -> None:
        """
        Initializes the Evaluation instance with the provided results dictionary.
        
        Args:
            results (Dict[str, Any]): Results dictionary populated during the BOTrainer run.
                                       Expected keys:
                                         - "evaluations": List of evaluation records.
                                         - "classifier_true_labels" (optional): List of true labels.
                                         - "classifier_pred_probs" (optional): List of predicted probabilities.
        """
        self.logger = get_logger("Evaluation")
        self.results: Dict[str, Any] = results
        
        if "evaluations" not in self.results:
            self.logger.warning("The results dictionary does not contain key 'evaluations'. "
                                "An empty evaluation list will be used.")
            self.evaluations: List[Dict[str, Any]] = []
        else:
            self.evaluations = self.results.get("evaluations", [])
        
        # Optional classifier performance data
        self.classifier_true_labels: Optional[List[int]] = self.results.get("classifier_true_labels", None)
        self.classifier_pred_probs: Optional[List[float]] = self.results.get("classifier_pred_probs", None)
        
    def compute_metrics(self) -> Dict[str, Any]:
        """
        Computes evaluation metrics:
          - best_objective: Minimum objective value among feasible evaluations.
          - feasibility_ratio: Fraction of feasibility evaluations.
          - balanced_accuracy: Average of sensitivity and specificity computed from classifier data.
          - total_evaluations: Total number of evaluations performed.
        
        Returns:
            Dict[str, Any]: Dictionary of computed metrics.
        """
        # Compute Best (Minimum) Objective from feasible evaluations.
        feasible_objectives: List[float] = []
        for record in self.evaluations:
            feasible_flag = record.get("feasible", False)
            objective_value = record.get("objective", None)
            if feasible_flag and (objective_value is not None):
                feasible_objectives.append(objective_value)
        
        if feasible_objectives:
            best_objective: float = min(feasible_objectives)
        else:
            best_objective = float("inf")
            self.logger.warning("No feasible evaluations found. Best objective set to infinity.")
        
        # Compute Feasibility Ratio.
        total_evaluations: int = len(self.evaluations)
        count_feasible: int = sum(1 for record in self.evaluations if record.get("feasible", False))
        feasibility_ratio: float = (count_feasible / total_evaluations) if total_evaluations > 0 else 0.0
        
        # Compute Classifier Accuracy: Balanced Accuracy.
        balanced_accuracy: Optional[float] = None
        if (self.classifier_true_labels is not None) and (self.classifier_pred_probs is not None):
            true_labels_array = np.array(self.classifier_true_labels, dtype=int)
            pred_probs_array = np.array(self.classifier_pred_probs, dtype=float)
            # Convert predicted probabilities to binary predictions using threshold 0.5.
            pred_labels_array = (pred_probs_array >= 0.5).astype(int)
            
            # Compute counts.
            TP = np.sum((true_labels_array == 1) & (pred_labels_array == 1))
            FN = np.sum((true_labels_array == 1) & (pred_labels_array == 0))
            TN = np.sum((true_labels_array == 0) & (pred_labels_array == 0))
            FP = np.sum((true_labels_array == 0) & (pred_labels_array == 1))
            
            sensitivity: float = TP / (TP + FN) if (TP + FN) > 0 else 0.0
            specificity: float = TN / (TN + FP) if (TN + FP) > 0 else 0.0
            balanced_accuracy = (sensitivity + specificity) / 2.0
        else:
            self.logger.warning("Classifier performance data ('classifier_true_labels' and/or "
                                "'classifier_pred_probs') missing. Skipping balanced accuracy computation.")
            balanced_accuracy = None
        
        metrics: Dict[str, Any] = {
            "best_objective": best_objective,
            "feasibility_ratio": feasibility_ratio,
            "balanced_accuracy": balanced_accuracy,
            "total_evaluations": total_evaluations
        }
        
        self.logger.info("Evaluation Metrics Computed: "
                         "Best Objective = %s, Feasibility Ratio = %.4f, Balanced Accuracy = %s, Total Evaluations = %d",
                         best_objective, feasibility_ratio, str(balanced_accuracy), total_evaluations)
        
        return metrics

# For independent module testing.
if __name__ == "__main__":
    # Set up a dummy results dictionary for testing purposes.
    dummy_results = {
        "evaluations": [
            {"iteration": 1, "candidate": [0.1, 0.2], "feasible": True, "objective": 1.25},
            {"iteration": 2, "candidate": [0.3, 0.4], "feasible": False, "objective": None},
            {"iteration": 3, "candidate": [0.2, 0.3], "feasible": True, "objective": 0.95},
            {"iteration": 4, "candidate": [0.5, 0.6], "feasible": True, "objective": 1.10},
            {"iteration": 5, "candidate": [0.6, 0.7], "feasible": False, "objective": None}
        ],
        "classifier_true_labels": [1, 0, 1, 1, 0],
        "classifier_pred_probs": [0.65, 0.35, 0.55, 0.45, 0.25]
    }
    
    evaluator = Evaluation(dummy_results)
    metrics = evaluator.compute_metrics()
    
    print("Computed Evaluation Metrics:")
    for key, value in metrics.items():
        print(f"{key}: {value}")
