"""evaluation.py

This module implements the Evaluator class, which sets up and runs a suite of benchmarks
to evaluate the TyPro-protected binaries against baseline (unprotected) binaries.
It measures correctness, security (average target set size), performance overhead,
binary size overhead, and optionally dynamic linking update times.

The Evaluator uses configuration settings from config.yaml via utils.parse_config,
and it relies on standard Python modules and utilities defined in utils.py.
All methods use strong type annotations and default values where appropriate.

Author: TyPro Evaluation Module
"""

import os
import sys
import json
import time
import statistics
import subprocess
import logging
from typing import Any, Dict, List, Optional

from utils import parse_config, get_logger

# -----------------------------------------------------------------------------
# Evaluator class definition
# -----------------------------------------------------------------------------
class Evaluator:
    """
    Evaluator class sets up and runs benchmarks for evaluating the TyPro pipeline.
    
    It measures:
        - Correctness: comparing output between baseline (unprotected)
          and protected binaries.
        - Security: computes the average number of allowed targets per indirect call
          from the TargetSet mapping.
        - Performance Overhead: measures average runtime (plus standard deviation)
          over multiple runs.
        - Binary Size Overhead: compares file sizes between baseline and protected binaries.
        - If dynamic linking is enabled, measures additional time for dynamic module update.
    
    Methods:
        __init__(benchmark_config: dict) -> None
        run_benchmarks() -> Dict[str, Any]
            Runs all benchmarks and returns a dictionary containing evaluation metrics.
    """

    def __init__(self, benchmark_config: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize the Evaluator with configuration data.
        
        Args:
            benchmark_config (Optional[Dict[str, Any]]): Benchmark configuration.
                If not provided, the configuration loaded from config.yaml is used.
                Expected keys include "benchmarks" (a list of benchmark names) and "metrics".
        """
        self.config: Dict[str, Any] = parse_config("config.yaml")
        # Load the evaluation section from configuration; if benchmark_config provided, override.
        self.benchmark_config: Dict[str, Any] = benchmark_config if benchmark_config is not None else self.config.get("evaluation", {})
        # List of benchmark names; using default list if not specified.
        self.benchmarks: List[str] = self.benchmark_config.get("benchmarks", [
            "SPEC CPU 2006", "Apache", "lighttpd", "nginx", "pureftpd", "vsftpd", "memcached", "redis"
        ])
        # Default number of runs for performance measurement.
        self.num_runs: int = 10
        self.logger: logging.Logger = get_logger("Evaluator")
        self.logger.debug("Evaluator initialized with benchmarks: %s", self.benchmarks)

    def run_benchmarks(self) -> Dict[str, Any]:
        """
        Run the evaluation benchmarks and collect metrics on correctness,
        target set sizes, performance overhead, binary size overhead, and dynamic linking (if enabled).
        
        Returns:
            Dict[str, Any]: Dictionary aggregating the benchmark results.
                Format:
                  {
                    "correctness": {benchmark: True/False or error message},
                    "target_set": {benchmark: average_target_set_size (float)},
                    "performance": { benchmark: { "baseline_runtime": value,
                                                  "protected_runtime": value,
                                                  "overhead(%)": value,
                                                  "std_dev_baseline": value,
                                                  "std_dev_protected": value } },
                    "binary_size": { benchmark: { "baseline_size": value,
                                                  "protected_size": value,
                                                  "overhead(%)": value } },
                    "dynamic_linking": { benchmark: { "module_update_time": value } }  // Only if dynamic linking enabled
                  }
        """
        results: Dict[str, Any] = {
            "correctness": {},
            "target_set": {},
            "performance": {},
            "binary_size": {}
        }
        # If runtime dynamic linking is enabled in configuration, add that category.
        if self.config.get("runtime", {}).get("dynamic_linking", False):
            results["dynamic_linking"] = {}

        # Iterate over all benchmarks defined in the configuration.
        for benchmark in self.benchmarks:
            self.logger.info("Running benchmark: %s", benchmark)

            # Define binary paths.
            # For this implementation, we assume a directory structure:
            #   benchmarks/<benchmark>/baseline/<benchmark>
            #   benchmarks/<benchmark>/typro/<benchmark>_typro
            baseline_binary: str = os.path.join("benchmarks", benchmark, "baseline", benchmark)
            protected_binary: str = os.path.join("benchmarks", benchmark, "typro", benchmark + "_typro")

            # Check that binaries exist.
            if not os.path.exists(baseline_binary):
                error_msg = f"Baseline binary for benchmark '{benchmark}' not found at '{baseline_binary}'."
                self.logger.error(error_msg)
                results["correctness"][benchmark] = error_msg
                continue
            if not os.path.exists(protected_binary):
                error_msg = f"Protected binary for benchmark '{benchmark}' not found at '{protected_binary}'."
                self.logger.error(error_msg)
                results["correctness"][benchmark] = error_msg
                continue

            # -------------------------
            # Correctness Evaluation
            # -------------------------
            baseline_output: str = self._run_binary(baseline_binary, benchmark, mode="baseline")
            protected_output: str = self._run_binary(protected_binary, benchmark, mode="protected")
            is_correct: bool = (baseline_output == protected_output)
            results["correctness"][benchmark] = is_correct
            if not is_correct:
                self.logger.error("Output mismatch for benchmark '%s'.", benchmark)
            else:
                self.logger.info("Benchmark '%s' passed correctness check.", benchmark)

            # -------------------------
            # Performance Evaluation
            # -------------------------
            baseline_times: List[float] = self._time_runs(baseline_binary)
            protected_times: List[float] = self._time_runs(protected_binary)
            try:
                avg_baseline: float = statistics.mean(baseline_times)
            except statistics.StatisticsError:
                avg_baseline = 0.0
            try:
                std_baseline: float = statistics.stdev(baseline_times) if len(baseline_times) > 1 else 0.0
            except statistics.StatisticsError:
                std_baseline = 0.0
            try:
                avg_protected: float = statistics.mean(protected_times)
            except statistics.StatisticsError:
                avg_protected = 0.0
            try:
                std_protected: float = statistics.stdev(protected_times) if len(protected_times) > 1 else 0.0
            except statistics.StatisticsError:
                std_protected = 0.0
            overhead_percent: float = ((avg_protected - avg_baseline) / avg_baseline * 100.0) if avg_baseline > 0 else 0.0

            results["performance"][benchmark] = {
                "baseline_runtime": avg_baseline,
                "protected_runtime": avg_protected,
                "overhead(%)": overhead_percent,
                "std_dev_baseline": std_baseline,
                "std_dev_protected": std_protected
            }
            self.logger.info("Benchmark '%s' performance: baseline=%.4fs, protected=%.4fs (overhead=%.2f%%).",
                             benchmark, avg_baseline, avg_protected, overhead_percent)

            # -------------------------
            # Binary Size Evaluation
            # -------------------------
            try:
                baseline_size: int = os.path.getsize(baseline_binary)
                protected_size: int = os.path.getsize(protected_binary)
                size_overhead_percent: float = ((protected_size - baseline_size) / baseline_size * 100.0) if baseline_size > 0 else 0.0
            except Exception as size_error:
                self.logger.error("Error measuring binary size for benchmark '%s': %s", benchmark, size_error)
                baseline_size = 0
                protected_size = 0
                size_overhead_percent = 0.0

            results["binary_size"][benchmark] = {
                "baseline_size": baseline_size,
                "protected_size": protected_size,
                "overhead(%)": size_overhead_percent
            }
            self.logger.info("Benchmark '%s' binary sizes: baseline=%d bytes, protected=%d bytes (overhead=%.2f%%).",
                             benchmark, baseline_size, protected_size, size_overhead_percent)

            # -------------------------
            # Security (Target Set) Evaluation
            # -------------------------
            # Assume the TyPro pipeline outputs a JSON file named "target_set.json"
            # in the protected binary's directory.
            target_set_path: str = os.path.join("benchmarks", benchmark, "typro", "target_set.json")
            avg_target_set_size: float = 0.0
            if os.path.exists(target_set_path):
                try:
                    with open(target_set_path, "r", encoding="utf-8") as ts_file:
                        target_set_mapping: Dict[str, Any] = json.load(ts_file)
                        total_targets: int = 0
                        count_calls: int = 0
                        for call_id, targets in target_set_mapping.items():
                            if isinstance(targets, list):
                                total_targets += len(targets)
                                count_calls += 1
                        if count_calls > 0:
                            avg_target_set_size = total_targets / count_calls
                except Exception as ts_error:
                    self.logger.error("Error reading TargetSet mapping for benchmark '%s': %s", benchmark, ts_error)
            else:
                self.logger.warning("TargetSet mapping file not found for benchmark '%s' at '%s'.", benchmark, target_set_path)

            results["target_set"][benchmark] = avg_target_set_size
            self.logger.info("Benchmark '%s' average target set size: %.2f.", benchmark, avg_target_set_size)

            # -------------------------
            # Dynamic Linking Evaluation (if enabled)
            # -------------------------
            if self.config.get("runtime", {}).get("dynamic_linking", False):
                # For dynamic linking evaluation, assume that the protected binary can be invoked
                # with an extra argument "--dynamic-test" to simulate module loading and runtime updates.
                dynamic_times: List[float] = self._time_runs(protected_binary, extra_args=["--dynamic-test"], runs=5)
                try:
                    avg_dynamic: float = statistics.mean(dynamic_times)
                except statistics.StatisticsError:
                    avg_dynamic = 0.0
                results["dynamic_linking"][benchmark] = {
                    "module_update_time": avg_dynamic
                }
                self.logger.info("Benchmark '%s' dynamic linking module update time: %.4fs.", benchmark, avg_dynamic)

        return results

    def _run_binary(self, binary_path: str, benchmark: str, mode: str) -> str:
        """
        Run the given binary once and capture its output.
        
        Args:
            binary_path (str): Path to the executable binary.
            benchmark (str): Benchmark name (for logging).
            mode (str): Either "baseline" or "protected".
        
        Returns:
            str: The standard output produced by the binary, or an empty string if an error occurs.
        """
        try:
            proc = subprocess.run([binary_path], capture_output=True, text=True, timeout=120)
            output: str = proc.stdout.strip()
            self.logger.debug("%s run output for benchmark '%s': %s", mode.capitalize(), benchmark, output)
            return output
        except subprocess.TimeoutExpired:
            self.logger.error("%s run of benchmark '%s' timed out.", mode.capitalize(), benchmark)
            return "timeout"
        except Exception as e:
            self.logger.error("Error running %s binary for benchmark '%s': %s", mode, benchmark, e)
            return ""

    def _time_runs(self, binary_path: str, extra_args: Optional[List[str]] = None, runs: Optional[int] = None) -> List[float]:
        """
        Execute the binary multiple times and record execution durations.
        
        Args:
            binary_path (str): Path to the executable.
            extra_args (Optional[List[str]]): Additional command line arguments for the run.
            runs (Optional[int]): Number of runs to execute; defaults to self.num_runs.
        
        Returns:
            List[float]: List of execution times in seconds.
        """
        run_times: List[float] = []
        num_runs: int = runs if runs is not None else self.num_runs
        for i in range(num_runs):
            try:
                args: List[str] = [binary_path]
                if extra_args is not None:
                    args.extend(extra_args)
                start_time: float = time.time()
                subprocess.run(args, capture_output=True, text=True, timeout=120)
                end_time: float = time.time()
                elapsed: float = end_time - start_time
                run_times.append(elapsed)
                self.logger.debug("Run %d for '%s' completed in %.4fs.", i + 1, binary_path, elapsed)
            except subprocess.TimeoutExpired:
                self.logger.error("Run %d for binary '%s' timed out.", i + 1, binary_path)
            except Exception as e:
                self.logger.error("Error during run %d for binary '%s': %s", i + 1, binary_path, e)
        return run_times


# -----------------------------------------------------------------------------
# Main block for standalone testing of the Evaluator.
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Setup logging with a basic configuration.
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    evaluator_instance: Evaluator = Evaluator()
    benchmark_results: Dict[str, Any] = evaluator_instance.run_benchmarks()
    
    # Output the aggregated benchmark results in JSON format for easy inspection.
    print("Benchmark Evaluation Results:")
    print(json.dumps(benchmark_results, indent=2))
    
    # Optionally, write the evaluation results to an output file.
    output_file: str = "evaluation_results.json"
    try:
        with open(output_file, "w", encoding="utf-8") as out_file:
            json.dump(benchmark_results, out_file, indent=2)
        logging.getLogger("Evaluator").info("Evaluation results written to '%s'.", output_file)
    except Exception as e:
        logging.getLogger("Evaluator").error("Failed to write evaluation results to '%s': %s", output_file, e)
