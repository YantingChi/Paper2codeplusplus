from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
import shutil


def code_fence_for_file(file_path: str) -> str:
    """Return the most appropriate markdown fence for a repository file."""
    path = Path(file_path)
    filename = path.name
    suffix = path.suffix.lower()

    if filename == "Dockerfile":
        return "dockerfile"
    if filename == "Makefile":
        return "makefile"
    if filename == "CMakeLists.txt":
        return "cmake"
    if filename == "requirements.txt":
        return "text"

    fence_by_suffix = {
        ".py": "python",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".hh": "cpp",
        ".rs": "rust",
        ".sh": "bash",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".md": "markdown",
        ".toml": "toml",
        ".ini": "ini",
        ".cfg": "ini",
        ".txt": "text",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".js": "javascript",
        ".ts": "typescript",
    }
    return fence_by_suffix.get(suffix, "text")


def _build_python_verification_commands(repo_dir: str) -> list[str]:
    del repo_dir
    python_bin = "python3" if shutil.which("python3") else "python"
    return [f"{python_bin} -m compileall ."]


def _build_c_verification_commands(repo_dir: str) -> list[str]:
    repo = Path(repo_dir)

    if (repo / "CMakeLists.txt").exists():
        return [
            "cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DCMAKE_C_STANDARD=11 -DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            "cmake --build build",
            "if [ -f build/CTestTestfile.cmake ] || [ -d build/Testing ]; then ctest --test-dir build --output-on-failure; else echo '[INFO] No CTest tests detected; skipping ctest.'; fi",
        ]

    if (repo / "Makefile").exists():
        return [
            "make",
            "if make -qn test >/dev/null 2>&1; then make test; else echo '[INFO] No make test target detected; skipping make test.'; fi",
        ]

    if (repo / "reproduce.sh").exists():
        return ["bash reproduce.sh"]

    c_sources = sorted(
        path
        for path in repo.rglob("*.c")
        if "build" not in path.parts and ".git" not in path.parts
    )
    if c_sources:
        include_flag = "-Iinclude" if (repo / "include").is_dir() else ""
        return [
            "mkdir -p build && "
            f"cc -std=c11 -Wall -Wextra {include_flag} "
            "$(find . -path './build' -prune -o -name '*.c' -print | sort) "
            "-o build/paper2code_app"
        ]

    return ["printf '[ERROR] No C build artifact or C source files were found.\\n' && exit 1"]


@dataclass(frozen=True)
class LanguageProfile:
    name: str
    display_name: str
    pre_code_phrase: str
    overall_plan_focus: str
    overall_plan_avoid: str
    architecture_example_implementation: str
    architecture_example_files: Sequence[str]
    architecture_example_interfaces: str
    architecture_example_call_flow: str
    architecture_example_unclear: str
    architecture_file_list_guidance: str
    architecture_interface_guidance: str
    logic_example_required_packages: Sequence[str]
    logic_example_other_packages: Sequence[str]
    logic_example_analysis: Sequence[Sequence[str]]
    logic_example_task_list: Sequence[str]
    logic_example_api_spec: str
    logic_example_shared_knowledge: str
    logic_example_unclear: str
    logic_required_packages_guidance: str
    logic_other_packages_guidance: str
    logic_analysis_guidance: str
    shared_knowledge_guidance: str
    config_generation_guidance: str
    config_example_yaml: str
    analysis_guidance: str
    coding_guidance: str
    run_script_guidance: str
    debugging_guidance: str
    verification_command_builder: Callable[[str], list[str]]
    default_config_artifact: str = "config.yaml"
    extra_context_files: Sequence[str] = (
        "config.yaml",
        "reproduce.sh",
        "CMakeLists.txt",
        "Makefile",
        "build.sh",
        "Dockerfile",
    )

    def code_fence(self, file_path: str) -> str:
        return code_fence_for_file(file_path)

    def format_file_block(self, file_path: str, content: str) -> str:
        fence = self.code_fence(file_path)
        return f"```{fence}\n## File name: {file_path}\n{content}\n```"

    def build_verification_commands(self, repo_dir: str) -> list[str]:
        return self.verification_command_builder(repo_dir)


PYTHON_PROFILE = LanguageProfile(
    name="python",
    display_name="Python",
    pre_code_phrase="Before writing any Python code",
    overall_plan_focus=(
        "- Key details from the paper's Methodology.\n"
        "- Important aspects of Experiments, including dataset requirements, experimental settings, hyperparameters, and evaluation metrics.\n"
        "- The Python package layout, experiment entry points, major data/model/training/evaluation modules, and any helper scripts needed for reproducibility."
    ),
    overall_plan_avoid=(
        "Mention lower-level systems concerns only if the paper genuinely depends on them."
    ),
    architecture_example_implementation=(
        "We will implement a compact Python training and evaluation pipeline with explicit data loading, model, trainer, evaluation, and entry-point modules."
    ),
    architecture_example_files=(
        "main.py",
        "dataset_loader.py",
        "model.py",
        "trainer.py",
        "evaluation.py",
    ),
    architecture_example_interfaces=(
        "\nclassDiagram\n"
        "    class Main {\n"
        "        +__init__(config: dict)\n"
        "        +run_experiment() None\n"
        "    }\n"
        "    class DatasetLoader {\n"
        "        +__init__(config: dict)\n"
        "        +load_data() Any\n"
        "    }\n"
        "    class Model {\n"
        "        +__init__(params: dict)\n"
        "        +forward(x: Tensor) Tensor\n"
        "    }\n"
        "    class Trainer {\n"
        "        +__init__(model: Model, data: Any)\n"
        "        +train() None\n"
        "    }\n"
        "    class Evaluation {\n"
        "        +__init__(model: Model, data: Any)\n"
        "        +evaluate() dict\n"
        "    }\n"
        "    Main --> DatasetLoader\n"
        "    Main --> Trainer\n"
        "    Main --> Evaluation\n"
        "    Trainer --> Model\n"
    ),
    architecture_example_call_flow=(
        "\nsequenceDiagram\n"
        "    participant M as Main\n"
        "    participant DL as DatasetLoader\n"
        "    participant MD as Model\n"
        "    participant TR as Trainer\n"
        "    participant EV as Evaluation\n"
        "    M->>DL: load_data()\n"
        "    DL-->>M: return dataset\n"
        "    M->>MD: initialize_model(config)\n"
        "    M->>TR: train(model, dataset)\n"
        "    TR->>MD: forward(batch)\n"
        "    MD-->>TR: predictions\n"
        "    TR-->>M: checkpoints + metrics\n"
        "    M->>EV: evaluate(model, dataset)\n"
        "    EV->>MD: forward(batch)\n"
        "    MD-->>EV: predictions\n"
        "    EV-->>M: evaluation metrics\n"
    ),
    architecture_example_unclear=(
        "Clarify any dataset preprocessing conventions or hyperparameters that are not explicit in the paper."
    ),
    architecture_file_list_guidance=(
        "Only relative paths. For Python-first projects, include a clear executable entry point such as main.py or app.py unless the paper clearly implies a different package layout."
    ),
    architecture_interface_guidance=(
        "Use Mermaid classDiagram syntax with concrete classes/functions, strong type hints where possible, and accurate relationships between modules."
    ),
    logic_example_required_packages=("numpy==1.21.0", "torch==1.9.0"),
    logic_example_other_packages=("No third-party dependencies required",),
    logic_example_analysis=(
        ("dataset_loader.py", "Load datasets, tokenize or preprocess data, and expose train/validation/test iterators used across the pipeline."),
        ("model.py", "Define the core model, initialization path, forward pass, and any reusable helper layers."),
        ("trainer.py", "Implement optimization, checkpointing, logging, and epoch/step scheduling using the model and dataset loader."),
        ("evaluation.py", "Compute paper-aligned metrics and optional inference utilities."),
        ("main.py", "Parse config, construct modules, orchestrate training/evaluation, and write outputs."),
    ),
    logic_example_task_list=(
        "dataset_loader.py",
        "model.py",
        "trainer.py",
        "evaluation.py",
        "main.py",
    ),
    logic_example_api_spec="",
    logic_example_shared_knowledge=(
        "Shared config schema, dataset field names, checkpoint locations, and common utility functions used by the trainer and evaluation modules."
    ),
    logic_example_unclear=(
        "Clarification may be needed for undocumented data splits or unavailable hyperparameters."
    ),
    logic_required_packages_guidance=(
        "Provide Python packages in requirements.txt format, including versions when the paper or implementation makes them important."
    ),
    logic_other_packages_guidance=(
        "List required non-Python packages only if the project truly needs them; otherwise state that none are required."
    ),
    logic_analysis_guidance=(
        "Provide per-file implementation notes covering classes, functions, imports, data flow, algorithmic responsibilities, and dependency-aware task ordering."
    ),
    shared_knowledge_guidance=(
        "Capture shared utility functions, config variables, dataset schemas, checkpoint conventions, and reusable evaluation logic."
    ),
    config_generation_guidance=(
        "Write config.yaml with paper-grounded experiment settings such as datasets, hyperparameters, checkpoints, and runtime options. Do not invent scientific values that are not supported by the paper."
    ),
    config_example_yaml=(
        "## config.yaml\n"
        "project:\n"
        "  target_language: python\n"
        "training:\n"
        "  learning_rate: 0.0001\n"
        "  batch_size: 32\n"
        "  epochs: 10\n"
        "runtime:\n"
        "  device: cuda\n"
        "  seed: 42\n"
        "evaluation:\n"
        "  metrics:\n"
        "    - accuracy\n"
    ),
    analysis_guidance=(
        "Explain the logic needed to implement each file, including data contracts, tensor/array shapes when relevant, algorithm steps, imports, config usage, and how the file interacts with the rest of the repository."
    ),
    coding_guidance=(
        "Use the correct file fence for each file, keep imports explicit, follow the designed interfaces, implement complete code without TODO stubs, and do not fabricate configuration values absent from config.yaml."
    ),
    run_script_guidance=(
        "Write a self-contained Bash script that creates the Python environment, installs dependencies, and runs the correct entry point or evaluation workflow from scratch."
    ),
    debugging_guidance=(
        "Use Python tracebacks, import errors, missing dependency messages, and config mismatches to produce minimal fixes that preserve the intended architecture."
    ),
    verification_command_builder=_build_python_verification_commands,
)


C_PROFILE = LanguageProfile(
    name="c",
    display_name="C",
    pre_code_phrase="Before writing any C code",
    overall_plan_focus=(
        "- Key details from the paper's Methodology.\n"
        "- Important aspects of Experiments, including dataset or input requirements, runtime settings, evaluation metrics, and benchmark assumptions.\n"
        "- Modules or subsystems, runtime architecture, external libraries, OS or platform assumptions, concurrency model, data layout, ownership and lifetime rules, error propagation, and build or runtime requirements."
    ),
    overall_plan_avoid=(
        "Do not default to Python or ML-only terminology such as trainer, dataset_loader, or PyTorch unless the paper truly requires them."
    ),
    architecture_example_implementation=(
        "We will implement a buildable C codebase with explicit public headers, private source files, unit tests, and a real build target defined through CMake."
    ),
    architecture_example_files=(
        "include/hashmap.h",
        "include/wal.h",
        "src/hashmap.c",
        "src/wal.c",
        "src/main.c",
        "tests/test_hashmap.c",
        "CMakeLists.txt",
        "reproduce.sh",
    ),
    architecture_example_interfaces=(
        "\nclassDiagram\n"
        "    class HashmapAPI {\n"
        "        +hashmap_t* hashmap_create(size_t capacity)\n"
        "        +void hashmap_destroy(hashmap_t* map)\n"
        "        +int hashmap_put(hashmap_t* map, const char* key, const void* value, size_t value_size)\n"
        "        +const void* hashmap_get(const hashmap_t* map, const char* key, size_t* value_size)\n"
        "        +int hashmap_remove(hashmap_t* map, const char* key)\n"
        "    }\n"
        "    class WalAPI {\n"
        "        +wal_t* wal_open(const char* path)\n"
        "        +void wal_close(wal_t* wal)\n"
        "        +int wal_append(wal_t* wal, const wal_record_t* record)\n"
        "    }\n"
        "    class HashmapState {\n"
        "        -bucket_t* buckets\n"
        "        -size_t capacity\n"
        "        -size_t size\n"
        "    }\n"
        "    class ServerMain {\n"
        "        +int main(int argc, char** argv)\n"
        "    }\n"
        "    HashmapAPI --> HashmapState : opaque ownership\n"
        "    ServerMain --> HashmapAPI : uses\n"
        "    ServerMain --> WalAPI : uses\n"
    ),
    architecture_example_call_flow=(
        "\nsequenceDiagram\n"
        "    participant CLI as main.c\n"
        "    participant CFG as config loader\n"
        "    participant HM as hashmap module\n"
        "    participant WAL as wal module\n"
        "    participant TEST as test target\n"
        "    CLI->>CFG: parse runtime arguments and config\n"
        "    CLI->>HM: hashmap_create(capacity)\n"
        "    CLI->>WAL: wal_open(path)\n"
        "    CLI->>HM: put/get/remove operations\n"
        "    HM-->>CLI: status codes + owned data contracts\n"
        "    CLI->>WAL: wal_append(record)\n"
        "    CLI->>HM: hashmap_destroy()\n"
        "    CLI->>WAL: wal_close()\n"
        "    TEST->>HM: validate API, ownership, and error paths\n"
    ),
    architecture_example_unclear=(
        "Clarify platform assumptions, unavailable ABI details, or undocumented external library requirements."
    ),
    architecture_file_list_guidance=(
        "Only relative paths. Include public headers, source files, tests, and at least one real build artifact such as CMakeLists.txt or Makefile. Do not force main.c when the project is really a library; represent executable, library, and test targets explicitly."
    ),
    architecture_interface_guidance=(
        "Use Mermaid to describe C modules, structs, APIs, ownership boundaries, and build targets. The content must reflect header/source separation, opaque structs, initialization/shutdown order, and public versus private interfaces rather than pretending everything is a Python class."
    ),
    logic_example_required_packages=(),
    logic_example_other_packages=("cmake>=3.20", "No third-party dependencies required"),
    logic_example_analysis=(
        ("include/hashmap.h", "Define the public API, opaque handle policy, error codes, ownership of returned pointers, include requirements, and thread-safety guarantees."),
        ("include/wal.h", "Declare WAL record structs, lifecycle APIs, append semantics, and cleanup requirements."),
        ("src/hashmap.c", "Implement the hashmap contract, bucket allocation, cleanup on partial failure, include dependencies, and internal helper functions."),
        ("src/wal.c", "Implement WAL file IO, initialization and shutdown order, fsync or flush policy, and error propagation."),
        ("src/main.c", "Wire runtime configuration, initialize subsystems, route errors, and tear down resources in reverse order."),
        ("tests/test_hashmap.c", "Exercise success cases, edge cases, ownership guarantees, and error handling paths."),
        ("CMakeLists.txt", "Declare library, executable, and test targets, include directories, compile options, and link dependencies."),
        ("reproduce.sh", "Build the project from scratch, run tests, and execute the main target or benchmark entry point."),
    ),
    logic_example_task_list=(
        "include/hashmap.h",
        "include/wal.h",
        "src/hashmap.c",
        "src/wal.c",
        "src/main.c",
        "tests/test_hashmap.c",
        "CMakeLists.txt",
        "reproduce.sh",
    ),
    logic_example_api_spec="",
    logic_example_shared_knowledge=(
        "Shared macros, error codes, allocator conventions, struct layouts, compile definitions, sanitizer settings, and thread-safety assumptions used across headers, sources, and tests."
    ),
    logic_example_unclear=(
        "Clarify undocumented platform constraints, external library versions, or benchmark harness details when the paper leaves them implicit."
    ),
    logic_required_packages_guidance=(
        "Python packages are optional helper dependencies only. Leave this empty when the C project builds without Python-specific runtime requirements."
    ),
    logic_other_packages_guidance=(
        "List non-Python toolchains or third-party libraries required to build or link the C project, such as cmake, pthread, OpenSSL, or libuv."
    ),
    logic_analysis_guidance=(
        "The ordered task list must reflect buildability: declare public headers before dependent source files, then libraries or executables, then tests, then build and run scripts. For each file, describe include dependencies, compile units, target dependencies, ownership, error conventions, allocator usage, thread-safety assumptions, feature flags, and any generated artifacts."
    ),
    shared_knowledge_guidance=(
        "Capture shared macros, common structs, enums, error codes, allocator or lifetime rules, compile definitions, feature flags, thread-safety assumptions, and ABI constraints."
    ),
    config_generation_guidance=(
        "Write config.yaml as machine-readable project metadata for the C implementation. Include compiler choice, C standard, build type, compile flags, include paths, link libraries, sanitizer flags, runtime arguments, test commands, and environment assumptions. When the paper omits toolchain details, choose conservative deterministic build defaults needed for a compilable repository, but do not invent scientific results."
    ),
    config_example_yaml=(
        "## config.yaml\n"
        "project:\n"
        "  target_language: c\n"
        "  project_type: executable\n"
        "build:\n"
        "  build_system: cmake\n"
        "  compiler: cc\n"
        "  c_standard: c11\n"
        "  build_type: Debug\n"
        "  include_dirs:\n"
        "    - include\n"
        "  compile_flags:\n"
        "    - -Wall\n"
        "    - -Wextra\n"
        "  link_libraries: []\n"
        "  sanitizer_flags:\n"
        "    - -fsanitize=address\n"
        "    - -fsanitize=undefined\n"
        "runtime:\n"
        "  executable_target: app\n"
        "  args: []\n"
        "tests:\n"
        "  command: ctest --output-on-failure\n"
        "environment:\n"
        "  os: linux\n"
        "  toolchain: gcc-or-clang\n"
    ),
    analysis_guidance=(
        "For headers, describe the public API, structs, typedefs, enums, include guards or pragma once policy, ownership of returned objects, nullability, thread-safety or reentrancy guarantees, and required includes. For source files, explain how the implementation satisfies the header contract, which helpers remain private, allocation versus cleanup order, invariants, failure paths, ABI assumptions, and interactions with sibling modules."
    ),
    coding_guidance=(
        "Use the correct fence for each file type. For C headers and sources, keep interfaces and implementations consistent, include the right headers, declare explicit prototypes, use deterministic defaults when needed, follow consistent error handling, avoid circular includes, keep ownership rules explicit, and provide full implementations without TODO stubs. Build files and shell scripts must also be complete and runnable."
    ),
    run_script_guidance=(
        "Write a Bash script that configures the build, compiles the C targets, runs tests when available, and executes the correct binary or benchmark entry point from a clean checkout."
    ),
    debugging_guidance=(
        "Use compiler diagnostics, linker errors, failing tests, runtime crashes, and sanitizer reports as first-class debugging inputs. Fix broken headers, missing includes, symbol mismatches, ownership bugs, target wiring, and build script issues with minimal but complete edits."
    ),
    verification_command_builder=_build_c_verification_commands,
)


LANGUAGE_PROFILES = {
    PYTHON_PROFILE.name: PYTHON_PROFILE,
    C_PROFILE.name: C_PROFILE,
}


def get_language_profile(language_name: str) -> LanguageProfile:
    normalized = language_name.strip().lower()
    if normalized not in LANGUAGE_PROFILES:
        valid = ", ".join(sorted(LANGUAGE_PROFILES))
        raise ValueError(f"Unsupported target language '{language_name}'. Expected one of: {valid}")
    return LANGUAGE_PROFILES[normalized]
