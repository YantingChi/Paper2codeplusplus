"""
utils.py

This module provides general-purpose utility functions for the BE-CBO project.
Functions include:
  - Random seed initialization for reproducibility.
  - Logger setup based on configuration.
  - Sobol sequence generation for initial sample creation.
  - Helper methods for tensor manipulation (e.g., moving tensors to device, computing statistics).

All functions read from the centralized configuration (config.py) to ensure consistency 
across the entire project.
"""

import random
import numpy as np
import torch
import logging
from config import config  # Import global configuration from config.py


def set_seed(seed: int = None) -> None:
    """
    Sets the random seed for Python's random module, NumPy, and Torch.
    Ensures reproducibility across experiments.
    
    Args:
        seed (int, optional): The seed to use. If None, the seed from config.general.seed is used.
    """
    if seed is None:
        seed = config.general.seed
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if config.general.device.lower() == "cuda":
        torch.cuda.manual_seed_all(seed)


def get_logger(name: str = "BECBO") -> logging.Logger:
    """
    Initializes and returns a logger with the specified name.
    The logging level and file output are set based on configuration.
    
    Args:
        name (str): Name of the logger instance.
    
    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)
    
    # Prevent adding handlers multiple times if the logger is already configured.
    if not logger.hasHandlers():
        # Determine the logging level from configuration.
        level_name = config.logging.verbosity.upper()
        level = getattr(logging, level_name, logging.INFO)
        logger.setLevel(level)
        
        # Define a consistent logging format.
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        
        # Setup stream handler for console output.
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
        # Optionally add a file handler if saving results is enabled.
        if config.logging.save_results:
            file_handler = logging.FileHandler("becbo.log")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    
    return logger


def generate_sobol_samples(num_samples: int, dim: int, scramble: bool = True) -> torch.Tensor:
    """
    Generates a Sobol sequence using PyTorch's SobolEngine.
    Produces a tensor with shape [num_samples, dim] containing samples in [0, 1]^dim.
    The generated tensor is moved to the device specified in the configuration.
    
    Args:
        num_samples (int): Number of samples to generate.
        dim (int): Dimensionality of each sample.
        scramble (bool): Whether to scramble the Sobol sequence for randomness.

    Returns:
        torch.Tensor: Low-discrepancy sequence samples with shape [num_samples, dim].
    """
    engine = torch.quasirandom.SobolEngine(dimension=dim, scramble=scramble)
    samples = engine.draw(n=num_samples).float()
    return to_device(samples)


def to_device(tensor: torch.Tensor, device: str = None) -> torch.Tensor:
    """
    Moves a given tensor to the specified device.
    If device is not provided, uses config.general.device.
    
    Args:
        tensor (torch.Tensor): The tensor to move.
        device (str, optional): Target device (e.g., "cpu" or "cuda"). Defaults to config.general.device.
    
    Returns:
        torch.Tensor: The tensor on the specified device.
    """
    if device is None:
        device = config.general.device
    return tensor.to(device)


def compute_tensor_stats(tensor: torch.Tensor) -> dict:
    """
    Computes basic statistics (mean, standard deviation, minimum, maximum)
    for the given tensor.
    
    Args:
        tensor (torch.Tensor): The tensor to compute statistics on.
    
    Returns:
        dict: A dictionary containing the mean, std, min, and max values.
    """
    return {
        "mean": tensor.mean().item(),
        "std": tensor.std().item(),
        "min": tensor.min().item(),
        "max": tensor.max().item()
    }


def tensor_to_numpy(tensor: torch.Tensor) -> np.ndarray:
    """
    Converts a PyTorch tensor to a NumPy array.
    Moves the tensor to CPU and detaches it from the computation graph if necessary.
    
    Args:
        tensor (torch.Tensor): The tensor to convert.
    
    Returns:
        np.ndarray: The corresponding NumPy array.
    """
    return tensor.detach().cpu().numpy()


# For debugging and testing the module independently
if __name__ == "__main__":
    # Set random seeds based on configuration.
    set_seed()
    
    # Initialize the logger and output a test message.
    logger = get_logger()
    logger.info("Random seed has been set successfully.")
    
    # Generate and log Sobol sequence samples.
    num_samples_test = 5
    dim_test = 3
    samples = generate_sobol_samples(num_samples=num_samples_test, dim=dim_test)
    logger.info("Generated Sobol samples:\n%s", samples)
    
    # Compute and log tensor statistics.
    stats = compute_tensor_stats(samples)
    logger.info("Sobol sample statistics: %s", stats)
    
    # Convert tensor to numpy array and log.
    np_samples = tensor_to_numpy(samples)
    logger.info("Samples as NumPy array:\n%s", np_samples)
