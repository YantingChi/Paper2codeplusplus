"""
utils.py

This module provides shared data structures and utility functions for the TyPro pipeline,
including the Fact class, configuration parsing from config.yaml, file I/O for fact files,
conversion utilities for Soufflé input, and logging setup.

All functions and classes herein follow strict type annotations and defaults as specified
in the design, ensuring consistency and reproducibility across the AST extraction,
fact optimization, Datalog solving, code transformation, runtime enforcement, and evaluation modules.
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional

import yaml  # Requires pyyaml==6.0

# -----------------------------------------------------------------------------
# Fact Class Definition
# -----------------------------------------------------------------------------
class Fact:
    """
    Fact represents an atomic piece of the analysis from the AST extraction.
    
    Attributes:
        id (int): A unique integer identifier for the fact.
        fact_type (str): A string label indicating the type of fact (e.g., "Cast", "TypeContextPair").
        data (Dict[str, Any]): A dictionary holding additional fields relevant to the fact.
    """
    def __init__(self, fact_id: int, fact_type: str, data: Dict[str, Any]) -> None:
        self.id: int = fact_id
        self.fact_type: str = fact_type
        self.data: Dict[str, Any] = data

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the Fact object to a dictionary representation.
        """
        return {
            "id": self.id,
            "fact_type": self.fact_type,
            "data": self.data
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'Fact':
        """
        Create a Fact object from its dictionary representation.
        
        Args:
            d (Dict[str, Any]): Dictionary containing the keys "id", "fact_type", and "data".
        
        Returns:
            Fact: The constructed Fact object.
        """
        return Fact(int(d.get("id", 0)), str(d.get("fact_type", "")), d.get("data", {}))

    def __str__(self) -> str:
        """
        String representation of a Fact object.
        """
        return f"Fact(id={self.id}, fact_type='{self.fact_type}', data={self.data})"

    def __repr__(self) -> str:
        return self.__str__()

# -----------------------------------------------------------------------------
# Configuration Parsing
# -----------------------------------------------------------------------------
def merge_dicts(default: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge two dictionaries such that keys in override take precedence. 
    Missing keys are filled with default values.
    
    Args:
        default (Dict[str, Any]): The default dictionary.
        override (Dict[str, Any]): The overriding dictionary.
    
    Returns:
        Dict[str, Any]: The merged dictionary.
    """
    merged = default.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged

def parse_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Parse the configuration file 'config.yaml' using PyYAML, and return a configuration dictionary.
    If the file is missing or an error occurs while reading, default configuration values are used.
    
    The default configuration is built to ensure:
      - Build options: optimization "-O3", link_time_optimization true, clang_version "10",
        llvm_ir_transformation "llvmlite".
      - Runtime options: dynamic_linking true, runtime_enforcer_language "C++".
      - Evaluation options: benchmarks and metrics as specified in the configuration.
    
    Args:
        config_path (str): The file path to the configuration YAML file.
    
    Returns:
        Dict[str, Any]: The configuration dictionary.
    """
    default_config: Dict[str, Any] = {
        "training": {
            "learning_rate": None,
            "batch_size": None,
            "epochs": None
        },
        "build": {
            "optimization": "-O3",
            "link_time_optimization": True,
            "clang_version": "10",
            "llvm_ir_transformation": "llvmlite"
        },
        "runtime": {
            "dynamic_linking": True,
            "runtime_enforcer_language": "C++"
        },
        "evaluation": {
            "benchmarks": [
                "SPEC CPU 2006",
                "Apache",
                "lighttpd",
                "nginx",
                "pureftpd",
                "vsftpd",
                "memcached",
                "redis"
            ],
            "metrics": [
                "correctness",
                "security (average target set size)",
                "performance overhead",
                "binary size overhead"
            ]
        }
    }

    if not os.path.exists(config_path):
        logging.warning(f"Configuration file '{config_path}' not found. Using default configuration.")
        return default_config

    try:
        with open(config_path, "r") as file:
            loaded_config = yaml.safe_load(file)
            if not isinstance(loaded_config, dict):
                logging.warning(f"Configuration file '{config_path}' is empty or invalid. Using default configuration.")
                return default_config
            config = merge_dicts(default_config, loaded_config)
            return config
    except Exception as e:
        logging.error(f"Error reading configuration file '{config_path}': {e}. Using default configuration.")
        return default_config

# -----------------------------------------------------------------------------
# File I/O for Fact Files and Module Summaries
# -----------------------------------------------------------------------------
def write_facts_to_file(facts: List[Fact], filename: str) -> None:
    """
    Write a list of Fact objects to a file in JSON format.
    
    Args:
        facts (List[Fact]): A list of Fact objects to write.
        filename (str): The file path where facts will be saved.
    """
    try:
        facts_data = [fact.to_dict() for fact in facts]
        with open(filename, "w") as outfile:
            json.dump(facts_data, outfile, indent=2)
        logging.info(f"Wrote {len(facts)} facts to file '{filename}'.")
    except Exception as e:
        logging.error(f"Failed to write facts to file '{filename}': {e}.")

def read_facts_from_file(filename: str) -> List[Fact]:
    """
    Read a list of Fact objects from a JSON file.
    
    Args:
        filename (str): The file path to read facts from.
    
    Returns:
        List[Fact]: A list of Fact objects read from the file. Returns an empty list if an error occurs.
    """
    if not os.path.exists(filename):
        logging.error(f"Fact file '{filename}' does not exist.")
        return []
    try:
        with open(filename, "r") as infile:
            facts_list = json.load(infile)
            facts = [Fact.from_dict(fact_dict) for fact_dict in facts_list]
        logging.info(f"Read {len(facts)} facts from file '{filename}'.")
        return facts
    except Exception as e:
        logging.error(f"Failed to read facts from file '{filename}': {e}.")
        return []

# -----------------------------------------------------------------------------
# Conversion Utilities for Soufflé Fact Format
# -----------------------------------------------------------------------------
def convert_fact_to_souffle(fact: Fact) -> str:
    """
    Convert a Fact object to a string formatted as a Soufflé fact.
    
    The output format follows the predicate signature defined in the Datalog rules.
    For example, a Fact with fact_type "Cast" and data {"src": 0, "dst": 1} is converted to:
        "Cast(0, 1)"
    
    Args:
        fact (Fact): The Fact object to convert.
    
    Returns:
        str: A string representation of the fact in Soufflé format.
    """
    # Define the expected order of fields for known fact types
    expected_fields: Dict[str, List[str]] = {
        "TypeContextPair": ["id", "type", "context"],
        "PointsTo": ["ptr", "pointee"],
        "StructMember": ["parent", "member", "offset"],
        "UnionMember": ["parent", "member", "fieldType"],
        "Cast": ["src", "dst"],
        "ICall": ["callType", "callLabel", "arity"],
        "FunctionPointer": ["id", "func", "arity", "vararg"],
        "TypeCall": ["id", "type", "callLabel"],
        "TargetSet": ["callLabel", "func"]
    }

    # Determine the field order; if fact type unknown, sort keys alphabetically.
    field_order: List[str]
    if fact.fact_type in expected_fields:
        field_order = expected_fields[fact.fact_type]
    else:
        field_order = sorted(fact.data.keys())

    # Gather the values in the specified order.
    values: List[str] = []
    for field in field_order:
        # For key "id", use the object's id if applicable
        if field == "id" and fact.fact_type in ["TypeContextPair", "FunctionPointer", "TypeCall"]:
            value = fact.id
        else:
            value = fact.data.get(field)
        # Convert value to string based on its type
        if isinstance(value, bool):
            value_str = "true" if value else "false"
        elif isinstance(value, int):
            value_str = str(value)
        elif value is None:
            value_str = "null"
        else:
            # For symbol types (strings), output as is.
            value_str = str(value)
        values.append(value_str)

    # Construct the fact string in the format: FactType(val1, val2, ..., valN)
    fact_str: str = f"{fact.fact_type}(" + ", ".join(values) + ")"
    return fact_str

def convert_facts_to_souffle(facts: List[Fact]) -> List[str]:
    """
    Convert a list of Fact objects into a list of strings formatted for Soufflé.
    
    Args:
        facts (List[Fact]): List of Fact objects.
    
    Returns:
        List[str]: List of formatted fact strings.
    """
    return [convert_fact_to_souffle(fact) for fact in facts]

# -----------------------------------------------------------------------------
# Logging and Debugging Support
# -----------------------------------------------------------------------------
def setup_logging(log_level: int = logging.DEBUG, log_format: Optional[str] = None) -> None:
    """
    Setup the logging configuration.
    
    Args:
        log_level (int): The logging level (default is logging.DEBUG).
        log_format (Optional[str]): Custom log format string. If None, a default format is used.
    """
    if log_format is None:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(level=log_level, format=log_format)
    logging.debug("Logging is set up.")

def get_logger(name: str) -> logging.Logger:
    """
    Retrieve a logger instance with the specified name.
    
    Args:
        name (str): Name for the logger.
    
    Returns:
        logging.Logger: The logger instance.
    """
    return logging.getLogger(name)
    
# -----------------------------------------------------------------------------
# End of utils.py
# -----------------------------------------------------------------------------
