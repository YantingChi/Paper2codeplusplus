import os
import json
import argparse
import re
import subprocess
import sys

from codes.ShiftToC.language_profiles import get_language_profile
from openai_client import create_openai_client
from codes.ShiftToC.prompt_builders import build_debugging_messages
from utils import content_to_json, extract_planning, get_task_file_list, read_repository_files


def parse_and_apply_changes(responses, debug_dir, save_num=1):
    """Apply SEARCH / REPLACE edits produced by the LLM to files in debug_dir."""
    modified_any = False
    for response in responses:
        # Split into blocks per file
        file_blocks = re.split(r"Filename:\s*([^\n]+)", response)
        # Example: ['', 'file1.py', '...file1 content...', 'file2.py', '...file2 content...', ...]

        if len(file_blocks) < 3:
            print(f"❌ No filename patterns found in response:\n{response[:200]}...\n")
            continue

        # Process blocks per file (odd indices: filename, even indices: diff content)
        for i in range(1, len(file_blocks), 2):
            filename = file_blocks[i].strip()
            file_content_block = file_blocks[i + 1]

            filepath = os.path.join(debug_dir, filename)

            # SEARCH/REPLACE pattern
            search_replace_pattern = (
                r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
            )
            matches = re.findall(search_replace_pattern, file_content_block, re.DOTALL)

            if not matches:
                print(f"❌ No SEARCH/REPLACE patterns found for file: {filename}\n")
                continue

            # Check file existence
            if not os.path.exists(filepath):
                print(f"❌ File does not exist: {filepath}\n")
                continue

            # Read file
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    file_content = f.read()
            except Exception as e:
                print(f"❌ Error reading file {filepath}: {e}\n")
                continue

            modified = False

            # Apply SEARCH/REPLACE
            for idx, (search_text, replace_text) in enumerate(matches, 1):
                search_text = search_text.strip()
                replace_text = replace_text.strip()

                if search_text in file_content:
                    file_content = file_content.replace(search_text, replace_text)
                    modified = True
                    modified_any = True
                    print(f"✅ {filename}: Modification {idx} applied")
                else:
                    print(
                        f"❌ {filename}: Search text for modification {idx} not found:\n"
                        f"{search_text[:200]}...\n"
                    )

            # If modified, create backup and save
            if modified:
                backup_path = f"{filepath}.{save_num:03d}.bak"
                try:
                    os.rename(filepath, backup_path)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(file_content)
                    print(f"💾 {filename}: File saved. Backup: {backup_path}\n")
                except Exception as e:
                    print(f"❌ Error saving file {filepath}: {e}\n")
            else:
                print(f"ℹ️ {filename}: No modifications applied\n")

    return modified_any


def build_repository_context(debug_dir, todo_file_lst, profile):
    repo_files = read_repository_files(
        debug_dir,
        relative_paths=todo_file_lst,
        extra_paths=profile.extra_context_files,
    )
    return "\n\n".join(
        profile.format_file_block(path, content)
        for path, content in repo_files.items()
    )


def run_repository_verification(debug_dir, profile):
    commands = profile.build_verification_commands(debug_dir)
    log_lines = []

    for command in commands:
        log_lines.append(f"$ {command}")
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=debug_dir,
            text=True,
            capture_output=True,
        )
        if completed.stdout:
            log_lines.append(completed.stdout)
        if completed.stderr:
            log_lines.append(completed.stderr)
        log_lines.append(f"[exit_code={completed.returncode}]")
        if completed.returncode != 0:
            return False, "\n".join(log_lines), commands

    return True, "\n".join(log_lines), commands


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug a generated repository given an error log and planning artifacts."
    )
    parser.add_argument(
        "--error_file_name",
        type=str,
        default="",
        help="Path to a text file containing the execution error message.",
    )

    # Either provide output_dir directly, or let the script construct it from the dataset style
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help=(
            "Root output directory that contains planning_trajectories.json and the debug directory."
        ),
    )
    parser.add_argument(
        "--paper_name",
        type=str,
        required=True,
        help="Paper name for output_dir.",
    )
    parser.add_argument(
        "--output_repo_dir",
        type=str,
        required=True,
        help="Path to the generated repository that should be debugged.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="o4-mini",
        help="OpenAI chat model used for debugging.",
    )
    parser.add_argument(
        "--save_num",
        type=int,
        default=1,
        required=True,
        help="Backup index appended as .<save_num>.bak when saving modified files.",
    )
    parser.add_argument(
        "--target_language",
        type=str,
        default="python",
        choices=["python", "c"],
        help="Target language used to choose repository context, code fences, and verification commands.",
    )
    parser.add_argument(
        "--run_verification",
        action="store_true",
        help="Run language-aware verification commands and feed the resulting output into the debugging loop.",
    )
    parser.add_argument(
        "--max_rounds",
        type=int,
        default=1,
        help="Maximum number of verify-and-repair rounds when --run_verification is enabled.",
    )
    return parser.parse_args()


args = parse_args()
client = create_openai_client()
profile = get_language_profile(args.target_language)

if len(args.error_file_name.strip()) == 0 and not args.run_verification:
    raise ValueError("Provide --error_file_name or enable --run_verification.")

seed_error_msg = ""
if len(args.error_file_name.strip()) > 0:
    if not os.path.exists(args.error_file_name):
        raise FileNotFoundError(f"Error file not found: {args.error_file_name}")
    with open(args.error_file_name, "r", encoding="utf-8") as f:
        seed_error_msg = f.read()

# --------------------------------------------------
# Resolve output_dir and debug_dir
# --------------------------------------------------
output_dir = os.path.abspath(args.output_dir)
debug_dir = os.path.abspath(args.output_repo_dir)

# --------------------------------------------------
# Load planning trajectories and task list
# --------------------------------------------------
planning_traj_path = os.path.join(
    output_dir, f"planning_trajectories.json"
)
if not os.path.exists(planning_traj_path):
    print(f"❌ Planning trajectories not found: {planning_traj_path}", file=sys.stderr)
    sys.exit(1)

context_lst = extract_planning(planning_traj_path)
# context_lst indices: 0 overview, 1 detailed, 2 PRD (per your original comment)

task_list = content_to_json(context_lst[2])
try:
    todo_file_lst = get_task_file_list(task_list)
except KeyError as exc:
    print(f"❌ {exc}", file=sys.stderr)
    sys.exit(1)

round_limit = args.max_rounds if args.run_verification else 1

for round_idx in range(round_limit):
    verification_commands = []
    current_error_msg = seed_error_msg

    if args.run_verification:
        is_success, verification_log, verification_commands = run_repository_verification(
            debug_dir,
            profile,
        )
        if is_success:
            print("✅ Verification passed. No debugging changes were required.")
            sys.exit(0)
        current_error_msg = verification_log
        if len(seed_error_msg.strip()) > 0:
            current_error_msg = (
                seed_error_msg.strip()
                + "\n\n[Verification output]\n"
                + verification_log
            )

    codes = build_repository_context(debug_dir, todo_file_lst, profile)
    msg = build_debugging_messages(
        codes,
        current_error_msg,
        profile,
        verification_commands=verification_commands,
    )

    response = client.chat.completions.create(
        model=args.model,
        messages=msg,
        reasoning_effort="high",
    )

    answer = response.choices[0].message.content
    modified = parse_and_apply_changes(
        [answer],
        debug_dir,
        save_num=args.save_num + round_idx,
    )

    if not args.run_verification or not modified:
        break

if args.run_verification:
    final_success, final_log, _ = run_repository_verification(debug_dir, profile)
    if final_success:
        print("✅ Verification passed after applying fixes.")
    else:
        print("❌ Verification still failing after the debug loop.")
        print(final_log)
