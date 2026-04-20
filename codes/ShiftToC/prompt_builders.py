from __future__ import annotations

import json
from typing import Sequence

from codes.ShiftToC.language_profiles import LanguageProfile


def _json_example(payload: dict) -> str:
    return json.dumps(payload, indent=4, ensure_ascii=False)


def _build_architecture_example(profile: LanguageProfile) -> str:
    return _json_example(
        {
            "Implementation approach": profile.architecture_example_implementation,
            "File list": list(profile.architecture_example_files),
            "Data structures and interfaces": profile.architecture_example_interfaces,
            "Program call flow": profile.architecture_example_call_flow,
            "Anything UNCLEAR": profile.architecture_example_unclear,
        }
    )


def _build_logic_example(profile: LanguageProfile) -> str:
    return _json_example(
        {
            "Required packages": list(profile.logic_example_required_packages),
            "Required Other language third-party packages": list(
                profile.logic_example_other_packages
            ),
            "Logic Analysis": [list(item) for item in profile.logic_example_analysis],
            "Task list": list(profile.logic_example_task_list),
            "Full API spec": profile.logic_example_api_spec,
            "Shared Knowledge": profile.logic_example_shared_knowledge,
            "Anything UNCLEAR": profile.logic_example_unclear,
        }
    )


def render_done_file_context(
    done_file_lst: Sequence[str],
    done_file_dict: dict[str, str],
    profile: LanguageProfile,
) -> str:
    blocks = []
    for done_file in done_file_lst:
        if done_file == profile.default_config_artifact:
            continue
        if done_file not in done_file_dict:
            continue
        blocks.append(profile.format_file_block(done_file, done_file_dict[done_file]))
    return "\n\n".join(blocks)


def build_planning_messages(
    paper_content: str | dict,
    paper_format: str,
    profile: LanguageProfile,
) -> list[list[dict[str, str]]]:
    plan_msg = [
        {
            "role": "system",
            "content": (
                "You are an expert researcher and strategic planner with a deep "
                "understanding of experimental design and reproducibility in "
                f"scientific research for {profile.display_name} implementations.\n"
                f"You will receive a research paper in {paper_format} format.\n"
                "Your task is to create a detailed and efficient plan to reproduce "
                "the experiments and methodologies described in the paper.\n"
                "This plan should align precisely with the paper's methodology, "
                "experimental setup, and evaluation metrics.\n\n"
                "Instructions:\n\n"
                "1. Align with the Paper: Your plan must strictly follow the "
                "methods, datasets, model configurations, hyperparameters, and "
                "experimental setups described in the paper.\n"
                "2. Be Clear and Structured: Present the plan in a well-organized "
                "and easy-to-follow format, breaking it down into actionable steps.\n"
                "3. Prioritize Efficiency: Optimize the plan for clarity and "
                "practical implementation while ensuring fidelity to the original experiments."
            ),
        },
        {
            "role": "user",
            "content": (
                f"## Paper\n{paper_content}\n\n"
                "## Task\n"
                "1. We want to reproduce the method described in the attached paper.\n"
                "2. The authors did not release any official code, so we have to plan our own implementation.\n"
                f"3. {profile.pre_code_phrase}, please outline a comprehensive plan that covers:\n"
                f"{profile.overall_plan_focus}\n"
                "4. The plan should be as detailed and informative as possible to help us write the final code later.\n\n"
                "## Requirements\n"
                "- You don't need to provide the actual code yet; focus on a thorough, clear strategy.\n"
                "- If something is unclear from the paper, mention it explicitly.\n"
                f"- {profile.overall_plan_avoid}\n\n"
                "## Instruction\n"
                "The response should give us a strong roadmap, making it easier to write the code later."
            ),
        },
    ]

    architecture_msg = [
        {
            "role": "user",
            "content": (
                "Your goal is to create a concise, usable, and complete software "
                "system design for reproducing the paper's method. Use appropriate "
                "open-source libraries and keep the overall architecture simple.\n\n"
                "Based on the plan for reproducing the paper's main method, please "
                "design a concise, usable, and complete software system.\n"
                "Keep the architecture simple and make effective use of open-source libraries.\n\n"
                "-----\n\n"
                "## Format Example\n"
                "[CONTENT]\n"
                f"{_build_architecture_example(profile)}\n"
                "[/CONTENT]\n\n"
                "## Nodes: \"<node>: <type>  # <instruction>\"\n"
                "- Implementation approach: <class 'str'>  # Summarize the chosen solution strategy.\n"
                f"- File list: typing.List[str]  # {profile.architecture_file_list_guidance}\n"
                f"- Data structures and interfaces: typing.Optional[str]  # {profile.architecture_interface_guidance}\n"
                "- Program call flow: typing.Optional[str]  # Use Mermaid sequenceDiagram syntax with a complete and accurate runtime or orchestration flow that matches the interfaces above.\n"
                "- Anything UNCLEAR: <class 'str'>  # Mention ambiguities and ask for clarifications.\n\n"
                "## Constraint\n"
                "Format: output wrapped inside [CONTENT][/CONTENT] like the format example, nothing else.\n\n"
                "## Action\n"
                "Follow the instructions for the nodes, generate the output, and ensure it follows the format example."
            ),
        }
    ]

    logic_msg = [
        {
            "role": "user",
            "content": (
                "Your goal is to break down tasks according to the technical design, "
                "generate a task list, and analyze task dependencies.\n"
                "The Logic Analysis should not only consider the dependencies between "
                "files but also provide detailed descriptions to assist in writing the code.\n\n"
                "-----\n\n"
                "## Format Example\n"
                "[CONTENT]\n"
                f"{_build_logic_example(profile)}\n"
                "[/CONTENT]\n\n"
                "## Nodes: \"<node>: <type>  # <instruction>\"\n"
                f"- Required packages: typing.Optional[typing.List[str]]  # {profile.logic_required_packages_guidance}\n"
                f"- Required Other language third-party packages: typing.List[str]  # {profile.logic_other_packages_guidance}\n"
                f"- Logic Analysis: typing.List[typing.List[str]]  # {profile.logic_analysis_guidance}\n"
                "- Task list: typing.List[str]  # Break down the tasks into a dependency-aware list of filenames. The task list must include the previously generated file list.\n"
                "- Full API spec: <class 'str'>  # Describe any API contracts that must be exposed. If not needed, leave it blank.\n"
                f"- Shared Knowledge: <class 'str'>  # {profile.shared_knowledge_guidance}\n"
                "- Anything UNCLEAR: <class 'str'>  # Mention any unresolved questions or clarifications needed from the paper or project scope.\n\n"
                "## Constraint\n"
                "Format: output wrapped inside [CONTENT][/CONTENT] like the format example, nothing else.\n\n"
                "## Action\n"
                "Follow the node instructions above, generate your output accordingly, and ensure it follows the given format example."
            ),
        }
    ]

    config_msg = [
        {
            "role": "user",
            "content": (
                "You write elegant, modular, and maintainable code.\n\n"
                "Based on the paper, plan, and design specified previously, follow the "
                "\"Format Example\" and generate the configuration artifact.\n"
                f"{profile.config_generation_guidance}\n\n"
                f"You must write `{profile.default_config_artifact}`.\n\n"
                "ATTENTION: Use '##' to SPLIT SECTIONS, not '#'. Your output format must follow the example below exactly.\n\n"
                "-----\n\n"
                f"# Format Example\n## Code: {profile.default_config_artifact}\n"
                "```yaml\n"
                f"{profile.config_example_yaml}\n"
                "```\n\n"
                "-----\n\n"
                f"## Code: {profile.default_config_artifact}\n"
            ),
        }
    ]

    return [plan_msg, architecture_msg, logic_msg, config_msg]


def build_analysis_messages(
    paper_content: str | dict,
    paper_format: str,
    context_lst: Sequence[str],
    config_yaml: str,
    todo_file_name: str,
    todo_file_desc: str,
    profile: LanguageProfile,
) -> list[dict[str, str]]:
    draft_desc = (
        f"Write the logic analysis in '{todo_file_name}', which is intended for '{todo_file_desc}'."
    )
    if len(todo_file_desc.strip()) == 0:
        draft_desc = f"Write the logic analysis in '{todo_file_name}'."

    return [
        {
            "role": "system",
            "content": (
                "You are an expert researcher, strategic analyzer, and software engineer "
                "with a deep understanding of experimental design and reproducibility.\n"
                f"You will receive a research paper in {paper_format} format, an overview "
                "of the plan, a design in JSON format, a task breakdown in JSON format, "
                "and a configuration file named config.yaml.\n\n"
                "Your task is to conduct a comprehensive logic analysis that accurately "
                "guides the implementation.\n\n"
                "1. Align with the Paper: strictly follow the methods, datasets, model or "
                "runtime configurations, hyperparameters, and experimental setups described in the paper.\n"
                "2. Be Clear and Structured: present the analysis in a logical, well-organized, and actionable format.\n"
                "3. Prioritize Efficiency: optimize the analysis for clarity and practical implementation.\n"
                "4. Follow design: YOU MUST FOLLOW \"Data structures and interfaces\". Do not change the design.\n"
                "5. Refer to configuration: always reference settings from config.yaml. "
                "Do not invent scientific values that are not supported by the paper.\n"
                f"6. Language-specific focus: {profile.analysis_guidance}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"## Paper\n{paper_content}\n\n"
                "-----\n\n"
                f"## Overview of the plan\n{context_lst[0]}\n\n"
                "-----\n\n"
                f"## Design\n{context_lst[1]}\n\n"
                "-----\n\n"
                f"## Task\n{context_lst[2]}\n\n"
                "-----\n\n"
                "## Configuration file\n"
                f"```yaml\n{config_yaml}\n```\n"
                "-----\n\n"
                "## Instruction\n"
                "Conduct a Logic Analysis to assist in writing the code, based on the "
                "paper, the plan, the design, the task, and the previously specified configuration file.\n"
                "You DON'T need to provide the actual code yet; focus on a thorough, clear analysis.\n\n"
                f"{draft_desc}\n\n"
                "-----\n\n"
                f"## Logic Analysis: {todo_file_name}"
            ),
        },
    ]


def build_coding_messages(
    paper_content: str | dict,
    paper_format: str,
    context_lst: Sequence[str],
    config_yaml: str,
    todo_file_name: str,
    detailed_logic_analysis: str,
    done_file_lst: Sequence[str],
    done_file_dict: dict[str, str],
    profile: LanguageProfile,
) -> list[dict[str, str]]:
    code_files = render_done_file_context(done_file_lst, done_file_dict, profile)
    fence = profile.code_fence(todo_file_name)

    return [
        {
            "role": "system",
            "content": (
                "You are an expert researcher and software engineer with a deep "
                "understanding of experimental design and reproducibility.\n"
                f"You will receive a research paper in {paper_format} format, an overview "
                "of the plan, a design in JSON format, a task breakdown in JSON format, "
                "and a configuration file named config.yaml.\n"
                "Your task is to write code that reproduces the experiments and methodologies described in the paper.\n\n"
                "The code must be elegant, modular, maintainable, and aligned with the designed interfaces.\n"
                f"Language-specific guidance: {profile.coding_guidance}"
            ),
        },
        {
            "role": "user",
            "content": (
                "# Context\n"
                f"## Paper\n{paper_content}\n\n"
                "-----\n\n"
                f"## Overview of the plan\n{context_lst[0]}\n\n"
                "-----\n\n"
                f"## Design\n{context_lst[1]}\n\n"
                "-----\n\n"
                f"## Task\n{context_lst[2]}\n\n"
                "-----\n\n"
                "## Configuration file\n"
                f"```yaml\n{config_yaml}\n```\n"
                "-----\n\n"
                f"## Code Files\n{code_files}\n\n"
                "-----\n\n"
                f"# Format example\n## Code: {todo_file_name}\n"
                f"```{fence}\n## {todo_file_name}\n...\n```\n\n"
                "-----\n\n"
                "# Instruction\n"
                "Based on the paper, plan, design, task, and configuration file specified previously, follow the format example and write the code.\n\n"
                f"We already have {list(done_file_lst)}.\n"
                f'Next, you must write only the "{todo_file_name}".\n'
                "1. Only one file: implement this file only.\n"
                "2. Complete code: the file must be complete, reliable, and reusable.\n"
                "3. Deterministic defaults: if a non-scientific implementation default is needed, set an explicit and conservative value.\n"
                "4. Follow design: you must follow \"Data structures and interfaces\". Do not change the design.\n"
                "5. Carefully check that you do not miss any necessary class, function, target, or symbol in this file.\n"
                "6. Before using an external variable or module, make sure you import or declare it first.\n"
                "7. Write out every implementation detail. Do not leave TODOs.\n"
                "8. Refer to configuration: use configuration from config.yaml and do not fabricate scientific values that are absent from it.\n\n"
                f"{detailed_logic_analysis}\n\n"
                f"## Code: {todo_file_name}"
            ),
        },
    ]


def build_run_script_messages(
    config_yaml: str,
    todo_file_name: str,
    done_file_lst: Sequence[str],
    done_file_dict: dict[str, str],
    profile: LanguageProfile,
) -> list[dict[str, str]]:
    code_files = render_done_file_context(done_file_lst, done_file_dict, profile)
    fence = profile.code_fence(todo_file_name)

    return [
        {
            "role": "system",
            "content": (
                "You are an expert software engineer.\n"
                "You will receive a configuration file named config.yaml and an implemented code repository.\n"
                "Your task is to write a Bash script that can run the repository from scratch.\n"
                f"Language-specific guidance: {profile.run_script_guidance}"
            ),
        },
        {
            "role": "user",
            "content": (
                "# Context\n\n"
                "## Configuration file\n"
                f"```yaml\n{config_yaml}\n```\n"
                "-----\n\n"
                f"## Code Files\n{code_files}\n\n"
                "-----\n\n"
                f"# Format example\n## Code: {todo_file_name}\n"
                f"```{fence}\n## {todo_file_name}\n...\n```\n\n"
                "-----\n\n"
                "# Instruction\n"
                "Based on the configuration file and implemented repository, follow the format example and write the code.\n\n"
                f"We already have {list(done_file_lst)}.\n"
                f'Next, you must write only the "{todo_file_name}".\n\n'
                f"## Code: {todo_file_name}"
            ),
        },
    ]


def build_debugging_messages(
    codes: str,
    execution_error_msg: str,
    profile: LanguageProfile,
    verification_commands: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    verification_section = ""
    if verification_commands:
        verification_section = (
            "### Verification Commands\n"
            + "\n".join(f"- {command}" for command in verification_commands)
            + "\n\n--\n\n"
        )

    return [
        {
            "role": "system",
            "content": (
                "You are a highly capable code assistant specializing in debugging real-world code repositories.\n"
                "You will be provided with repository files and one or more execution, build, or test failures.\n\n"
                "Your objective is to debug the code so that it executes successfully.\n"
                "This may involve identifying root causes, modifying faulty logic or syntax, handling missing dependencies, or correcting build configuration issues.\n\n"
                "Guidelines:\n"
                "- Provide the exact lines or file changes needed to resolve the issue.\n"
                "- Show only the modified lines using SEARCH/REPLACE blocks.\n"
                "- If multiple fixes are needed, provide them sequentially with clear separation.\n"
                "- Do not make speculative edits without justification.\n"
                "- Prioritize minimal and effective fixes that preserve the original intent of the code.\n"
                f"- Language-specific focus: {profile.debugging_guidance}\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"### Code Repository\n{codes}\n\n--\n\n"
                f"{verification_section}"
                f"### Execution Error Messages\n{execution_error_msg}\n\n"
                "--\n\n"
                "## Instruction\n"
                "Now, debug the repository so that it runs without errors. Identify the cause of the failure and modify the code appropriately.\n\n"
                "--\n\n"
                "## Format Example\n"
                "Filename: train.py\n"
                "<<<<<<< SEARCH\n"
                "result = model.predict(input_data)\n"
                "=======\n"
                "result = model(input_data)\n"
                ">>>>>>> REPLACE\n\n"
                "--\n\n"
                "## Answer\n"
            ),
        },
    ]
