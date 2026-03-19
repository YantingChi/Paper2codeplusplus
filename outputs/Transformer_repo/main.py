"""main.py

This is the main entry point for training and evaluating the Transformer experiment.

It loads the configuration from a YAML file, parses command-line arguments,
initializes the DatasetLoader, TransformerModel, Trainer, and Evaluation modules,
and then orchestrates the experiment workflow (training and/or evaluation).

Usage Examples:
  Run both training and evaluation (default):
    python main.py --config config.yaml --mode both --task translation

  Run only evaluation with a checkpoint:
    python main.py --config config.yaml --mode eval --checkpoint checkpoints/checkpoint_step_50000.pt --task translation

  Run parsing task training only:
    python main.py --config config.yaml --mode train --task parsing
"""

import argparse
import os
import yaml

from dataset_loader import DatasetLoader
from model import TransformerModel
from trainer import Trainer
from evaluation import Evaluation


def load_config(config_path: str) -> dict:
    """
    Load configuration settings from a YAML file and set default values as necessary.
    
    Args:
        config_path: Path to the YAML configuration file.
    
    Returns:
        A dictionary containing the configuration parameters.
    """
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)

    # Training defaults
    if "training" not in config:
        config["training"] = {}
    config["training"].setdefault("steps", 100000)
    config["training"].setdefault("batch_tokens_source", 25000)
    config["training"].setdefault("batch_tokens_target", 25000)
    config["training"].setdefault("dropout", 0.1)
    config["training"].setdefault("label_smoothing", 0.1)
    if "optimizer" not in config["training"]:
        config["training"]["optimizer"] = {}
    config["training"]["optimizer"].setdefault("beta1", 0.9)
    config["training"]["optimizer"].setdefault("beta2", 0.98)
    config["training"]["optimizer"].setdefault("eps", 1e-9)
    if "learning_rate_schedule" not in config["training"]:
        config["training"]["learning_rate_schedule"] = {}
    config["training"]["learning_rate_schedule"].setdefault("warmup_steps", 4000)
    if "checkpoint" not in config["training"]:
        config["training"]["checkpoint"] = {}
    config["training"]["checkpoint"].setdefault("save_interval_minutes", 10)

    # Model defaults
    if "model" not in config:
        config["model"] = {}
    config["model"].setdefault("type", "TransformerBase")
    config["model"].setdefault("encoder_layers", 6)
    config["model"].setdefault("decoder_layers", 6)
    config["model"].setdefault("d_model", 512)
    config["model"].setdefault("d_ff", 2048)
    config["model"].setdefault("num_heads", 8)
    config["model"].setdefault("d_k", 64)
    config["model"].setdefault("d_v", 64)
    config["model"].setdefault("positional_encoding", "sinusoidal")
    config["model"].setdefault("vocab_size", 30000)

    # Inference defaults (for translation)
    if "inference" not in config:
        config["inference"] = {}
    if "beam_search" not in config["inference"]:
        config["inference"]["beam_search"] = {}
    config["inference"]["beam_search"].setdefault("beam_size", 4)
    config["inference"]["beam_search"].setdefault("length_penalty", 0.6)
    config["inference"]["beam_search"].setdefault("max_length_offset", 50)

    # Hardware defaults
    if "hardware" not in config:
        config["hardware"] = {}
    config["hardware"].setdefault("gpus", 8)
    config["hardware"].setdefault("gpu_type", "P100")

    # Translation dataset defaults
    if "translation" not in config:
        config["translation"] = {}
    config["translation"].setdefault("dataset", "WMT14_EnDe")

    # Parsing dataset defaults
    if "parsing" not in config:
        config["parsing"] = {}
    config["parsing"].setdefault("dataset", "WSJ")
    config["parsing"].setdefault("model_layers", 4)
    config["parsing"].setdefault("d_model", 1024)
    if "inference" not in config["parsing"]:
        config["parsing"]["inference"] = {}
    if "beam_search" not in config["parsing"]["inference"]:
        config["parsing"]["inference"]["beam_search"] = {}
    config["parsing"]["inference"]["beam_search"].setdefault("beam_size", 21)
    config["parsing"]["inference"]["beam_search"].setdefault("length_penalty", 0.3)
    config["parsing"]["inference"]["beam_search"].setdefault("max_length_offset", 300)

    # Overall task default (translation or parsing)
    config.setdefault("task", "translation")
    return config


class Main:
    """
    Main class orchestrates the overall experiment workflow including data loading,
    model initialization, training, and evaluation.
    """
    def __init__(self, config: dict, args: argparse.Namespace) -> None:
        self.config = config
        self.args = args
        # Override task from CLI if provided.
        if self.args.task:
            self.config["task"] = self.args.task
        self.task: str = self.config.get("task", "translation")

    def run_experiment(self) -> None:
        """
        Run the complete experiment workflow:
          1. Load data using DatasetLoader.
          2. Initialize the TransformerModel with task-specific parameters.
          3. Train the model if required.
          4. Evaluate the model and print evaluation metrics.
        """
        print("=== Experiment Start ===")
        print("Task:", self.task)

        # Instantiate DatasetLoader and load data.
        print("Loading data...")
        dataset_loader = DatasetLoader(self.config)
        train_loader, val_loader = dataset_loader.load_data()
        print(f"Data loaded: {len(train_loader)} training batches, {len(val_loader)} validation batches.")

        # Prepare model parameters based on task.
        model_params = {}
        if self.task == "translation":
            model_config = self.config.get("model", {})
            model_params["d_model"] = int(model_config.get("d_model", 512))
            model_params["d_ff"] = int(model_config.get("d_ff", 2048))
            model_params["num_heads"] = int(model_config.get("num_heads", 8))
            model_params["encoder_layers"] = int(model_config.get("encoder_layers", 6))
            model_params["decoder_layers"] = int(model_config.get("decoder_layers", 6))
            model_params["dropout"] = float(self.config.get("training", {}).get("dropout", 0.1))
            model_params["d_k"] = int(model_config.get("d_k", 64))
            model_params["d_v"] = int(model_config.get("d_v", 64))
            model_params["positional_encoding"] = model_config.get("positional_encoding", "sinusoidal")
            model_params["vocab_size"] = int(model_config.get("vocab_size", 30000))
            model_params["max_seq_len"] = 5000
            model_params["bos_idx"] = 2
            model_params["eos_idx"] = 3
        elif self.task == "parsing":
            parsing_config = self.config.get("parsing", {})
            # Use parsing-specific parameters for layers and d_model.
            model_params["d_model"] = int(parsing_config.get("d_model", 1024))
            model_params["encoder_layers"] = int(parsing_config.get("model_layers", 4))
            model_params["decoder_layers"] = int(parsing_config.get("model_layers", 4))
            # Other parameters drawn from general model config.
            general_model_config = self.config.get("model", {})
            model_params["d_ff"] = int(general_model_config.get("d_ff", 2048))
            model_params["num_heads"] = int(general_model_config.get("num_heads", 8))
            model_params["dropout"] = float(self.config.get("training", {}).get("dropout", 0.1))
            model_params["d_k"] = int(general_model_config.get("d_k", 64))
            model_params["d_v"] = int(general_model_config.get("d_v", 64))
            model_params["positional_encoding"] = general_model_config.get("positional_encoding", "sinusoidal")
            model_params["vocab_size"] = int(general_model_config.get("vocab_size", 30000))
            model_params["max_seq_len"] = 5000
            model_params["bos_idx"] = 2
            model_params["eos_idx"] = 3
        else:
            raise ValueError(f"Unsupported task: {self.task}")

        print("Initializing Transformer Model with parameters:")
        for key, value in model_params.items():
            print(f"  {key}: {value}")
        model_instance = TransformerModel(model_params)
        print("Model initialization completed.")

        # Training phase if required.
        if self.args.mode in ["train", "both"]:
            print("Starting training phase...")
            trainer = Trainer(model_instance, train_loader, self.config)
            if self.args.checkpoint is not None and os.path.exists(self.args.checkpoint):
                print(f"Loading checkpoint from: {self.args.checkpoint}")
                trainer.load_checkpoint(self.args.checkpoint)
            trainer.train()
            print("Training completed.")
        else:
            print("Training phase skipped as per command-line argument.")

        # Evaluation phase.
        if self.args.mode in ["eval", "both"]:
            print("Starting evaluation phase...")
            evaluation_instance = Evaluation(model_instance, val_loader, self.config)
            metrics = evaluation_instance.evaluate()
            print("Evaluation Metrics:")
            for metric, score in metrics.items():
                print(f"  {metric}: {score:.4f}")
        else:
            print("Evaluation phase skipped as per command-line argument.")

        print("=== Experiment End ===")


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.
    
    Returns:
        An argparse Namespace with the parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Transformer Experiment Runner")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to the configuration YAML file (default: config.yaml)")
    parser.add_argument("--task", type=str, choices=["translation", "parsing"], default=None,
                        help="Task to run: 'translation' or 'parsing'. Overrides config.yaml if provided.")
    parser.add_argument("--mode", type=str, choices=["train", "eval", "both"], default="both",
                        help="Mode to run: 'train', 'eval', or 'both' (default: both)")
    parser.add_argument("--checkpoint", type=str, default=None,
                        help="Path to a checkpoint file to load the model (optional)")
    return parser.parse_args()


def main() -> None:
    """
    Main function that loads configuration, parses arguments, and runs the experiment.
    """
    args = parse_arguments()
    config = load_config(args.config)
    experiment = Main(config, args)
    experiment.run_experiment()


if __name__ == "__main__":
    main()
