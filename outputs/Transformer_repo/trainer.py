"""trainer.py

This module implements the Trainer class which manages the training loop for the TransformerModel.
It sets up the Adam optimizer with a custom learning rate scheduler (as defined by the Transformer paper),
applies label smoothing to the loss, and periodically saves checkpoints of the training state.

Required external packages:
    torch==1.9.0
    numpy==1.21.0
    sacrebleu==2.0.0
    tqdm==4.62.0
"""

import os
import time
import math
from typing import Any, Dict

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.optim import Adam
from torch.optim.lr_scheduler import LambdaLR
from tqdm import tqdm


class Trainer:
    """
    Trainer class for training a TransformerModel instance.
    It initializes the training process with the provided model, training data, and configuration parameters.
    It includes methods for training the model, saving checkpoints, and loading from a checkpoint.
    """

    def __init__(self, model: nn.Module, train_data: torch.utils.data.DataLoader, config: Dict[str, Any]) -> None:
        """
        Initialize Trainer.

        Args:
            model: The TransformerModel instance to be trained.
            train_data: DataLoader object providing the training batches.
            config: Configuration dictionary derived from config.yaml.
        """
        self.model: nn.Module = model
        self.train_loader: torch.utils.data.DataLoader = train_data
        self.config: Dict[str, Any] = config

        # Training hyperparameters
        self.total_steps: int = int(config.get("training", {}).get("steps", 100000))
        optimizer_config: Dict[str, Any] = config.get("training", {}).get("optimizer", {})
        self.beta1: float = float(optimizer_config.get("beta1", 0.9))
        self.beta2: float = float(optimizer_config.get("beta2", 0.98))
        self.eps: float = float(optimizer_config.get("eps", 1e-9))

        # Learning rate scheduler parameters from config.model and config.training
        model_config: Dict[str, Any] = config.get("model", {})
        self.d_model: float = float(model_config.get("d_model", 512))
        lr_schedule_config: Dict[str, Any] = config.get("training", {}).get("learning_rate_schedule", {})
        self.warmup_steps: int = int(lr_schedule_config.get("warmup_steps", 4000))

        # Label smoothing value
        self.label_smoothing: float = float(config.get("training", {}).get("label_smoothing", 0.1))

        # Checkpoint saving configuration (save every X minutes)
        checkpoint_config: Dict[str, Any] = config.get("training", {}).get("checkpoint", {})
        self.checkpoint_interval_minutes: int = int(checkpoint_config.get("save_interval_minutes", 10))

        # Initialize the Adam optimizer with model parameters
        self.optimizer: Adam = Adam(
            self.model.parameters(),
            lr=0,  # LR will be set by the scheduler
            betas=(self.beta1, self.beta2),
            eps=self.eps
        )

        # Define custom learning rate scheduler using LambdaLR.
        # Learning rate formula: lr = d_model^(-0.5) * min(step^(-0.5), step * warmup_steps^(-1.5))
        def lr_lambda(step: int) -> float:
            if step == 0:
                step = 1
            return (self.d_model ** -0.5) * min(step ** -0.5, step * (self.warmup_steps ** -1.5))

        self.scheduler: LambdaLR = LambdaLR(self.optimizer, lr_lambda=lr_lambda)

        # Bookkeeping: global training step and checkpoint timing
        self.global_step: int = 0
        self.last_checkpoint_time: float = time.time()

        # Set device and move model to device
        self.device: torch.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)

    def label_smoothed_loss(self, logits: torch.Tensor, target: torch.Tensor, pad_index: int = 0) -> torch.Tensor:
        """
        Compute the label smoothed cross-entropy loss.

        Args:
            logits: Tensor of shape (batch_size, seq_len, vocab_size) from the model.
            target: Tensor of shape (batch_size, seq_len) containing target token indices.
            pad_index: Index of the pad token to ignore in loss calculation (default is 0).

        Returns:
            A scalar tensor representing the averaged loss over non-pad tokens.
        """
        vocab_size: int = logits.size(-1)
        # Compute log probabilities
        log_probs: torch.Tensor = F.log_softmax(logits, dim=-1)
        # Flatten logits and targets for loss computation
        log_probs = log_probs.view(-1, vocab_size)
        target = target.view(-1)

        smoothing: float = self.label_smoothing
        # Create a tensor filled with smoothing value for all entries
        with torch.no_grad():
            true_dist: torch.Tensor = torch.full_like(log_probs, smoothing / (vocab_size - 1))
            # For each target token, fill its correct index with (1 - smoothing)
            true_dist.scatter_(1, target.unsqueeze(1), 1.0 - smoothing)
        
        # Create mask to ignore pad tokens
        non_pad_mask: torch.Tensor = target.ne(pad_index)
        # Compute loss only for non-pad tokens
        loss: torch.Tensor = -true_dist[non_pad_mask] * log_probs[non_pad_mask]
        loss_sum: torch.Tensor = loss.sum()
        count: int = non_pad_mask.sum().item()
        if count > 0:
            loss_avg: torch.Tensor = loss_sum / count
        else:
            loss_avg = loss_sum
        return loss_avg

    def train(self) -> None:
        """
        Train the TransformerModel using the provided training data and configuration.
        The training loop iterates for the configured number of steps, computes the loss with label smoothing,
        performs backpropagation, updates optimizer and scheduler, logs progress, and saves checkpoints periodically.
        """
        self.model.train()
        progress_bar = tqdm(total=self.total_steps, desc="Training", unit="step")
        train_iterator = iter(self.train_loader)

        while self.global_step < self.total_steps:
            try:
                batch = next(train_iterator)
            except StopIteration:
                train_iterator = iter(self.train_loader)
                batch = next(train_iterator)

            # Move batch data to device. Expecting keys "src" and "tgt" for translation,
            # or "input" for parsing (if applicable). For training, target must be provided.
            if "src" in batch and "tgt" in batch:
                src: torch.Tensor = batch["src"].to(self.device)
                tgt: torch.Tensor = batch["tgt"].to(self.device)
            else:
                raise ValueError("Batch does not contain required keys 'src' and 'tgt' for training.")

            # Forward pass with teacher forcing.
            logits: torch.Tensor = self.model(src, tgt)

            # Compute label-smoothed loss.
            loss: torch.Tensor = self.label_smoothed_loss(logits, tgt, pad_index=0)

            # Backpropagation and parameter update.
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
            self.scheduler.step()
            self.global_step += 1

            # Log training metrics.
            current_lr: float = self.optimizer.param_groups[0]["lr"]
            progress_bar.set_postfix(loss=f"{loss.item():.4f}", lr=f"{current_lr:.6f}")
            progress_bar.update(1)

            # Check if it's time to save a checkpoint.
            current_time: float = time.time()
            elapsed_minutes: float = (current_time - self.last_checkpoint_time) / 60.0
            if elapsed_minutes >= self.checkpoint_interval_minutes:
                checkpoint_path: str = os.path.join("checkpoints", f"checkpoint_step_{self.global_step}.pt")
                self.save_checkpoint(checkpoint_path)
                self.last_checkpoint_time = current_time

        progress_bar.close()
        # Save a final checkpoint at the end of training.
        final_checkpoint_path: str = os.path.join("checkpoints", f"checkpoint_final_step_{self.global_step}.pt")
        self.save_checkpoint(final_checkpoint_path)

    def save_checkpoint(self, path: str) -> None:
        """
        Save a checkpoint of the training process.

        Args:
            path: The file path where the checkpoint will be saved.
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        checkpoint: Dict[str, Any] = {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "global_step": self.global_step
        }
        torch.save(checkpoint, path)
        print(f"Checkpoint saved at {path}")

    def load_checkpoint(self, path: str) -> None:
        """
        Load a checkpoint to resume training.

        Args:
            path: The file path from which the checkpoint will be loaded.
        """
        checkpoint: Dict[str, Any] = torch.load(path, map_location=self.device)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.global_step = checkpoint.get("global_step", self.global_step)
        print(f"Checkpoint loaded from {path}")
