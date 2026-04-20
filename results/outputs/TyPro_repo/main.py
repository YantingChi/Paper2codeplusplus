"""main.py

This module implements the Main class that orchestrates the complete TyPro pipeline.
It loads configuration from config.yaml, extracts AST facts from C source files,
optimizes the facts, runs the Soufflé Datalog solver to compute allowed target sets,
transforms the LLVM IR to replace indirect calls with switch-case constructs,
optionally invokes dynamic linking enforcement, and finally runs benchmark evaluations.
"""

import os
import sys
import json
import subprocess
import logging
from typing import Any, Dict, List, Optional

# Import shared configuration and logging utilities.
from utils import parse_config, get_logger, setup_logging

# Import pipeline modules.
from ast_extractor import ASTExtractor
from fact_optimizer import FactOptimizer
from datalog_solver import DatalogSolver
from code_transformer import CodeTransformer
from evaluation import Evaluator

# -----------------------------------------------------------------------------
# DynamicLinkerEnforcer Stub
# -----------------------------------------------------------------------------
class DynamicLinkerEnforcer:
    """
    DynamicLinkerEnforcer provides runtime support for dynamic linking.
    Its update_target_sets(module_summary: dict) function updates target sets
    at runtime by processing module summaries (as described in the paper).
    
    In this stub we simply log the update; in a full implementation, this would
    invoke the C++ runtime enforcer and trigger just-in-time recompilation.
    """
    def __init__(self) -> None:
        self.logger: logging.Logger = get_logger("DynamicLinkerEnforcer")
    
    def update_target_sets(self, module_summary: Dict[str, Any]) -> None:
        self.logger.info("DynamicLinkerEnforcer: Updating target sets with module summary: %s", module_summary)
        # In a complete implementation, here the runtime library would reload and re-run
        # the target set computation and update switch-case constructs.
        # For now, this is only a stub.

# -----------------------------------------------------------------------------
# Main Class Definition
# -----------------------------------------------------------------------------
class Main:
    """
    Main class: Orchestrates the complete TyPro pipeline.
    
    Methods:
        __init__(config: dict) -> None
        run_pipeline() -> None
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize Main with configuration. If config is not provided,
        load configuration from "config.yaml" via utils.parse_config.
        """
        self.config: Dict[str, Any] = config if config is not None else parse_config("config.yaml")
        self.logger: logging.Logger = get_logger("Main")
        self.logger.info("Main pipeline initialized with configuration: %s", json.dumps(self.config, indent=2))
    
    def run_pipeline(self) -> None:
        """
        Execute the TyPro pipeline:
          1. Verify build environment.
          2. Extract AST facts from the source directory.
          3. Optimize the extracted facts.
          4. Run the Soufflé Datalog solver to compute the TargetSet mapping.
          5. Transform the LLVM IR using the computed target sets.
          6. Optionally update dynamic linking via DynamicLinkerEnforcer.
          7. Run evaluation benchmarks.
        """
        self.logger.info("Starting TyPro pipeline...")
        
        # Step 1: Verify Build Environment (Clang version check)
        required_clang_version: str = self.config.get("build", {}).get("clang_version", "10")
        try:
            clang_version_output: str = subprocess.check_output(["clang", "--version"], stderr=subprocess.STDOUT, text=True)
            first_line: str = clang_version_output.splitlines()[0]
            if required_clang_version not in first_line:
                self.logger.warning("Required Clang version '%s' not found. Detected version: %s", required_clang_version, first_line)
            else:
                self.logger.info("Clang version check passed: %s", first_line)
        except Exception as e:
            self.logger.error("Failed to verify Clang version: %s", e)
        
        # Step 2.1: AST Fact Extraction
        source_dir: str = self.config.get("source_directory", "src")
        self.logger.info("Extracting AST facts from source directory: '%s'", source_dir)
        clang_library: str = self.config.get("clang_path", "/usr/lib/llvm-10/lib/libclang.so")
        ast_extractor: ASTExtractor = ASTExtractor(clang_path=clang_library)
        extracted_facts: List[Any] = ast_extractor.extract_facts(source_dir)
        self.logger.info("Extracted %d facts from AST.", len(extracted_facts))
        
        # Step 2.2: Fact Optimization
        fact_optimizer: FactOptimizer = FactOptimizer()
        optimized_facts: List[Any] = fact_optimizer.optimize(extracted_facts)
        self.logger.info("Optimized facts: reduced to %d facts from %d original facts.", len(optimized_facts), len(extracted_facts))
        
        # Step 2.3: Datalog Analysis & Target Set Computation
        rules_file: str = self.config.get("rules_file", "rules.dl")
        souffle_path: str = self.config.get("souffle_path", "souffle")
        datalog_solver: DatalogSolver = DatalogSolver(rules_file=rules_file, souffle_path=souffle_path)
        target_set_mapping: Dict[str, List[int]] = datalog_solver.solve(optimized_facts)
        self.logger.info("Computed TargetSet mapping: %s", json.dumps(target_set_mapping, indent=2))
        
        # Step 2.4: LLVM IR Transformation
        llvm_ir_file: str = self.config.get("llvm_ir_file", "input.ll")
        if not os.path.exists(llvm_ir_file):
            self.logger.warning("LLVM IR file '%s' not found. Using default sample IR.", llvm_ir_file)
            sample_ir: str = (
                "; ModuleID = 'sample_module'\n"
                "define i32 @main() {\n"
                "entry:\n"
                "  %1 = call i32 %ptr_func(i32 10) ; typro_id:call1\n"
                "  ret i32 %1\n"
                "}\n"
                "declare void @__typro_fail()\n"
                "declare i32 @f10(i32)\n"
                "declare i32 @f20(i32)\n"
            )
            llvm_ir_content: str = sample_ir
        else:
            with open(llvm_ir_file, "r", encoding="utf-8") as ir_file:
                llvm_ir_content = ir_file.read()
        code_transformer: CodeTransformer = CodeTransformer(llvm_ir=llvm_ir_content)
        transformed_ir: str = code_transformer.transform(target_set_mapping)
        transformed_ir_file: str = self.config.get("transformed_ir_file", "transformed.ll")
        with open(transformed_ir_file, "w", encoding="utf-8") as out_file:
            out_file.write(transformed_ir)
        self.logger.info("Transformed LLVM IR written to '%s'.", transformed_ir_file)
        
        # Step 2.5: Dynamic Linking Enforcement (Optional)
        dynamic_linking_enabled: bool = self.config.get("runtime", {}).get("dynamic_linking", True)
        if dynamic_linking_enabled:
            self.logger.info("Dynamic linking enabled. Invoking DynamicLinkerEnforcer.")
            dynamic_enforcer: DynamicLinkerEnforcer = DynamicLinkerEnforcer()
            # For this example, we pass an empty module summary.
            dummy_module_summary: Dict[str, Any] = {}
            dynamic_enforcer.update_target_sets(dummy_module_summary)
        else:
            self.logger.info("Dynamic linking disabled. Skipping dynamic linking enforcement.")
        
        # Step 2.6: Evaluation & Benchmarking
        evaluator: Evaluator = Evaluator()
        evaluation_results: Dict[str, Any] = evaluator.run_benchmarks()
        self.logger.info("Evaluation results:\n%s", json.dumps(evaluation_results, indent=2))
        
        self.logger.info("TyPro pipeline completed successfully.")

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Setup the logging configuration (default level DEBUG, using utils.setup_logging).
    setup_logging()
    main_instance: Main = Main()
    main_instance.run_pipeline()
