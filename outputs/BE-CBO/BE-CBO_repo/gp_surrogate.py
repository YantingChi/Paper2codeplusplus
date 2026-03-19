"""
gp_surrogate.py

This module implements the GPSurrogate class using BoTorch/GPyTorch to model the expensive
objective function for Bayesian optimization. The GP surrogate is updated only with feasible
objective data. It provides methods to update the GP model with new data, predict the
posterior mean and variance for candidate design points, and compute the Expected Improvement
(EI) acquisition value.

The class interfaces are as follows:
    - __init__(config: dict): Initializes the GP surrogate using configuration settings.
    - update(data: List[Tuple[torch.Tensor, float]]) -> None: Updates the GP model with new feasible data.
    - predict(x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]: Returns predictive mean and variance.
    - acquisition(x: torch.Tensor) -> torch.Tensor: Computes Expected Improvement at candidate points.
    
Author: BE-CBO Research Team (2024)
"""

from typing import List, Tuple
import torch
from torch import Tensor
from botorch.models.gp_regression import SingleTaskGP
from botorch.fit import fit_gpytorch_model
from botorch.acquisition import ExpectedImprovement
from gpytorch.mlls.exact_marginal_log_likelihood import ExactMarginalLogLikelihood

from config import config  # Global configuration from config.py
from utils import to_device, get_logger


class GPSurrogate:
    """
    GPSurrogate encapsulates the Gaussian Process (GP) surrogate model for the objective function.
    It uses a SingleTaskGP with a Matern5/2 kernel (per configuration) to model the expensive black-box objective.
    
    Attributes:
        config (dict): Configuration parameters.
        device (str): Computation device ("cpu" or "cuda").
        model (SingleTaskGP): The GP model instance (None if not trained yet).
        train_x (Tensor): Tensor of training inputs with shape [n, d].
        train_y (Tensor): Tensor of training objective values with shape [n, 1].
        best_value (float): The best (minimal) objective value observed so far.
    """
    def __init__(self, config_dict: dict = None) -> None:
        """
        Initializes the GPSurrogate with configuration settings.
        
        Args:
            config_dict (dict, optional): Configuration dictionary. If not provided, uses global config.
        """
        # Use provided configuration or fall back to global configuration.
        self.config = config_dict if config_dict is not None else config
        self.device: str = self.config.general.device
        self.model: SingleTaskGP = None  # GP model placeholder
        self.train_x: Tensor = None      # Training inputs, shape: [n, d]
        self.train_y: Tensor = None      # Training objective values, shape: [n, 1]
        self.best_value: float = None    # Best observed objective value (for minimization)
        
        self.logger = get_logger("GPSurrogate")
        self.logger.info("Initialized GPSurrogate on device %s.", self.device)

    def update(self, data: List[Tuple[Tensor, float]]) -> None:
        """
        Updates the GP model with new feasible training data.
        
        Args:
            data (List[Tuple[Tensor, float]]): A list of tuples where each tuple consists of:
                - x: A Tensor representing a candidate point (1D tensor of dimension d).
                - objective_value: A float representing the objective evaluation at x.
                
        The method:
            - Converts the list of data points into tensors (x_train of shape [n, d] and y_train of shape [n, 1]).
            - Instantiates a new SingleTaskGP model with the training data.
            - Constructs and fits the GP hyperparameters using an ExactMarginalLogLikelihood and
              the BoTorch fit_gpytorch_model routine.
            - Updates self.best_value as the minimum objective value observed.
            - Logs the update with the number of data points and best value.
        """
        if not data:
            self.logger.error("No feasible data provided for GP update.")
            return

        # Prepare training data lists.
        x_list: List[Tensor] = []
        y_list: List[float] = []
        for entry in data:
            x_candidate, obj_value = entry
            # Ensure x is moved to configured device.
            x_list.append(to_device(x_candidate, self.device))
            y_list.append(obj_value)

        # Stack training data into tensors.
        try:
            x_train: Tensor = torch.stack(x_list, dim=0)  # Shape: [n, d]
        except Exception as e:
            self.logger.error("Error stacking training inputs: %s", str(e))
            raise

        # Convert objective values to a tensor and ensure it has shape [n, 1].
        y_train: Tensor = torch.tensor(y_list, dtype=torch.float32, device=self.device).unsqueeze(-1)

        # Store training data for reference.
        self.train_x, self.train_y = x_train, y_train

        # Instantiate the GP model using SingleTaskGP.
        # The chosen kernel (e.g., Matern5/2) is determined by the configuration (default used by BoTorch).
        self.model = SingleTaskGP(self.train_x, self.train_y)
        
        # Construct Exact Marginal Log Likelihood.
        mll = ExactMarginalLogLikelihood(self.model.likelihood, self.model)
        
        # Optimize hyperparameters using BoTorch's fit_gpytorch_model.
        self.logger.info("Fitting GP hyperparameters with %d data points.", self.train_x.shape[0])
        fit_gpytorch_model(mll)
        
        # Update the best observed objective value (minimization).
        self.best_value = self.train_y.min().item()
        self.logger.info("Updated GP surrogate with %d data points; Best value: %.6f", self.train_x.shape[0], self.best_value)

    def predict(self, x: Tensor) -> Tuple[Tensor, Tensor]:
        """
        Predicts the GP posterior mean and variance for a batch of candidate points.
        
        Args:
            x (Tensor): A tensor of candidate points with shape [n_candidate, d].
            
        Returns:
            Tuple[Tensor, Tensor]: A tuple containing:
                - mean (Tensor): Predictive mean with shape [n_candidate, 1] (or [n_candidate]).
                - variance (Tensor): Predictive variance with shape [n_candidate, 1] (or [n_candidate]).
                
        Raises:
            ValueError: If the GP model has not been trained/updated.
        """
        if self.model is None:
            raise ValueError("GP model is not defined. Please update the model with training data first.")

        self.model.eval()
        # Ensure candidate x is moved to the desired device.
        x = to_device(x, self.device)
        posterior = self.model.posterior(x)
        # Extract the predictive mean and variance from the posterior.
        mean: Tensor = posterior.mean
        variance: Tensor = posterior.variance
        return mean, variance

    def acquisition(self, x: Tensor) -> Tensor:
        """
        Computes the Expected Improvement (EI) acquisition value for candidate points x.
        
        Args:
            x (Tensor): A tensor of candidate points with shape [n_candidate, d].
            
        Returns:
            Tensor: A tensor of EI values for each candidate point.
            
        The EI is computed using BoTorch's ExpectedImprovement acquisition class based on the current GP model and best observed value.
        
        Raises:
            ValueError: If the GP model is not defined or has not been updated with training data.
        """
        if self.model is None or self.best_value is None:
            raise ValueError("GP model is not defined or no best objective value available. Ensure you have run update().")
        
        # Ensure candidate x is on the correct device.
        x = to_device(x, self.device)
        # Create a tensor for the current best observed objective value.
        best_f: Tensor = torch.tensor(self.best_value, dtype=x.dtype, device=x.device)
        # Instantiate the Expected Improvement acquisition function.
        ei = ExpectedImprovement(model=self.model, best_f=best_f, maximize=False)
        # Evaluate and return the EI values at x.
        ei_values: Tensor = ei(x)
        return ei_values
