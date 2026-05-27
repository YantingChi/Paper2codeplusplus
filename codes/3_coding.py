import json
import os
from tqdm import tqdm
import re
import sys
import copy

from utils import extract_planning, content_to_json, extract_code_from_content, print_response, print_log_cost, load_accumulated_cost, save_accumulated_cost
import argparse

from openai_client import create_openai_client

parser = argparse.ArgumentParser()

parser.add_argument('--paper_name',type=str)
parser.add_argument('--gpt_version',type=str, default="gpt-5.4")
parser.add_argument('--paper_format',type=str, default="JSON", choices=["JSON", "LaTeX"])
parser.add_argument('--pdf_json_path', type=str) # json format
parser.add_argument('--pdf_latex_path', type=str) # latex format
parser.add_argument('--output_dir',type=str, default="")
parser.add_argument('--output_repo_dir',type=str, default="")

args    = parser.parse_args()
client = create_openai_client()

paper_name = args.paper_name
gpt_version = args.gpt_version
paper_format = args.paper_format
pdf_json_path = args.pdf_json_path
pdf_latex_path = args.pdf_latex_path
output_dir = args.output_dir
output_repo_dir = args.output_repo_dir

if paper_format == "JSON":
    with open(f'{pdf_json_path}') as f:
        paper_content = json.load(f)
elif paper_format == "LaTeX":
    with open(f'{pdf_latex_path}') as f:
        paper_content = f.read()
else:
    print(f"[ERROR] Invalid paper format. Please select either 'JSON' or 'LaTeX.")
    sys.exit(0)

with open(f'{output_dir}/planning_config.yaml') as f: 
    config_yaml = f.read()

context_lst = extract_planning(f'{output_dir}/planning_trajectories.json')
# 0: overview, 1: detailed, 2: PRD
# file_list = content_to_json(context_lst[1])
task_list = content_to_json(context_lst[2])

todo_file_lst = task_list['Task list']
done_file_lst = ['config.yaml']
done_file_dict = {}

code_msg = [
    {"role": "system", "content": f"""You are an expert researcher and software engineer with a deep understanding of experimental design and reproducibility in scientific research.
You will receive a research paper in {paper_format} format, an overview of the plan, a Design in JSON format consisting of "Implementation approach", "File list", "Data structures and interfaces", and "Program call flow", followed by a Task in JSON format that includes "Required packages", "Required other language third-party packages", "Logic Analysis", and "Task list", along with a configuration file named "config.yaml". 
Your task is to write code to reproduce the experiments and methodologies described in the paper. 

The code you write must be elegant, modular, and maintainable, adhering to Google-style guidelines.
The code must strictly align with the paper's methodology, experimental setup, and evaluation metrics.
Write code with triple quoto.

Every class definition and every function/method definition MUST be preceded by a Doxygen-style comment block using Python ## syntax:

  ## @brief One-line description.
  # @details Optional longer description.
  # @param paramName Type Description of the parameter.
  # @return Type Description of what is returned.

Rules for Doxygen comments:
- Use ## (double hash) to open the Doxygen block; use # (single hash) for continuation lines.
- Every @param line must state: parameter name, its Python type annotation, and a brief description.
- Include a @param line for EVERY parameter (including those with default values).
- Include @return for every function/method that returns a non-None value.
- Place the Doxygen block IMMEDIATELY before the def or class line with no blank line between them.
- Module-level functions and class methods both require their own block.
- Private helper methods (underscore prefix) require at minimum @brief."""}]

def get_write_msg(todo_file_name, detailed_logic_analysis, done_file_lst, equations_block=""):
    code_files = ""
    for done_file in done_file_lst:
        if done_file.endswith(".yaml"): continue
        code_files += f"""
```python
{done_file_dict[done_file]}
```

"""

    write_msg=[
{'role': 'user', "content": f"""# Context
## Paper
{paper_content}

-----

## Overview of the plan
{context_lst[0]}

-----

## Design
{context_lst[1]}

-----

## Task
{context_lst[2]}

-----

## Configuration file
```yaml
{config_yaml}
```
-----

## Code Files
{code_files}

-----

# Format example
## Code: {todo_file_name}
```python
## {todo_file_name}
...
```

-----

# Instruction
Based on the paper, plan, design, task and configuration file(config.yaml) specified previously, follow "Format example", write the code. 

We have {done_file_lst}.
Next, you must write only the "{todo_file_name}".
1. Only One file: do your best to implement THIS ONLY ONE FILE.
2. COMPLETE CODE: Your code will be part of the entire project, so please implement complete, reliable, reusable code snippets.
3. Set default value: If there is any setting, ALWAYS SET A DEFAULT VALUE, ALWAYS USE STRONG TYPE AND EXPLICIT VARIABLE. AVOID circular import.
4. Follow design: YOU MUST FOLLOW "Data structures and interfaces". DONT CHANGE ANY DESIGN. Do not use public member functions that do not exist in your design.
5. CAREFULLY CHECK THAT YOU DONT MISS ANY NECESSARY CLASS/FUNCTION IN THIS FILE.
6. Before using a external variable/module, make sure you import it first.
7. Write out EVERY CODE DETAIL, DON'T LEAVE TODO.
8. REFER TO CONFIGURATION: you must use configuration from "config.yaml". DO NOT FABRICATE any configuration values.
9. NO PROXIES OR STUBS: Implement the ACTUAL algorithm/method described in the paper. NEVER write a placeholder, stub, mock, "minimal runnable proxy", "simplified", "X-style", or "we do not vendor" implementation, and never leave a comment to that effect. If the paper names an external baseline or method (e.g., a cited GitHub implementation), reproduce its algorithm faithfully in code here.
10. EXACT EQUATIONS: Implement every equation EXACTLY as written in the paper, including all coefficients, scaling factors, constants, and exponents. **Hard-code coefficients as numeric literals** (e.g. write `0.85 * prev + 0.15 * new`, NOT a configurable `self._ema_beta` that defaults to something else). **Preserve the operator FAMILY** — if the paper says KL divergence use `F.kl_div`/log-softmax, NOT MSE; if it says cross-entropy use CE, NOT MSE; never silently substitute one loss/operator for another. Match every equation term-for-term; do NOT approximate, generalize, or "simplify".
11. PRESERVE SEMANTICS: Keep the precise granularity and meaning of each operation (e.g., distinguish pruning of attention heads vs. neurons vs. hidden dimensions; do not collapse distinct mask types into a single generic per-element operation).
12. CORRECT PLACEMENT: Invoke each operation at the exact point in the pipeline the paper specifies. If a transformation must happen before inference/evaluation (e.g., merging adapters/LoRA into the weights), perform it there in the evaluation path — not only inside export/saving code.
13. VERIFIABLE HYPERPARAMETERS: When the paper's tables specify hyperparameters for a given dataset or method, make those EXACT values present and resolvable in the code/config you write for that dataset/method, so they are concretely enforced rather than merely default-able. Cover EVERY hyperparameter the paper gives, not just some — including learning rate, **batch size**, epochs, warmup, weight decay, distillation/recovery split, and any **target-sparsity or pruning schedule**.
14. REPORT THE EXACT METRICS: Implement and report precisely the evaluation metric(s) the paper specifies for each dataset, in the evaluation code (and write them to the results/outputs). For example: dev/test F1 (and EM where stated) for SQuAD, ROUGE-1/2/L on the test set for CNN/DM, and accuracy for GLUE tasks. Do not silently substitute a different or partial metric.

## Equations this file MUST implement literally
The list below was extracted from the paper for THIS file. Every equation MUST appear as a
recognizable Python expression in the function named in `target_function`. Transcribe the symbols
and constants verbatim — coefficients (`0.85`, `2`, ...) must be hard-coded numeric literals (NOT
configurable parameters defaulting to a different value), and the loss/operator FAMILY (KL vs MSE
vs CE vs cosine) must match `note` exactly. If an equation can only be referenced here (implemented
elsewhere), mark the closest related line with a comment `# eq: <eq_id>` so it can be verified.
{equations_block}

{detailed_logic_analysis}

## Code: {todo_file_name}"""}]
    return write_msg


def api_call(msg):
    # Use deep reasoning for o3-mini and gpt-5.* — equation fidelity benefits from more
    # per-call deliberation (the paper is already in the prompt; what's scarce is attention).
    # Mirrors codes/4_debugging.py which sets reasoning_effort="high" on the same proxy.
    if "o3-mini" in gpt_version or gpt_version.startswith("gpt-5"):
        completion = client.chat.completions.create(
            model=gpt_version,
            reasoning_effort="high",
            messages=msg
        )
    else:
        completion = client.chat.completions.create(
            model=gpt_version,
            messages=msg
        )
    return completion


# Extract the <EQUATIONS>...</EQUATIONS> JSON block that stage 2 appends to each file's logic
# analysis. Returns a list of equation dicts (possibly empty). Never raises.
def parse_equations_block(analysis_text):
    m = re.search(r"<EQUATIONS>\s*(.*?)\s*</EQUATIONS>", analysis_text or "", re.DOTALL)
    if not m:
        return []
    raw = m.group(1).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    except Exception:
        return []


# Render an equation list as a readable checklist for the coding prompt.
def render_equations(eqs):
    if not eqs:
        return "(this file implements no equations)"
    lines = []
    for e in eqs:
        lines.append(
            f"- [{e.get('eq_id','?')}] target={e.get('target_function','?')} ({e.get('paper_section','?')})\n"
            f"    latex: {e.get('latex','')}\n"
            f"    must-have: {e.get('note','')}"
        )
    return "\n".join(lines)


# Best-effort JSON-object extraction from an LLM reply (handles ```json fences / surrounding prose).
def _loads_loose(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


# Bounded equation self-verify: confirm each listed equation is literally present in the just-written
# file; if any is reported missing, do AT MOST ONE targeted regeneration. Returns (code, total_cost).
# Cost is logged via print_log_cost so accumulated_cost.json stays accurate. Never raises.
def verify_and_fix_equations(todo_file_name, code, eqs, output_dir, total_cost):
    if not eqs:
        return code, total_cost
    eq_compact = [{k: e.get(k) for k in ("eq_id", "latex", "target_function", "note")} for e in eqs]
    verify_msg = [
        {"role": "system", "content": "You verify whether a source file implements a list of paper equations. For EACH equation id, decide if it appears as a recognizable Python expression in the file (coefficients as numeric literals; correct operator family such as KL vs MSE vs CE). Output STRICT JSON only: {\"results\":[{\"eq_id\":\"...\",\"status\":\"found\"|\"missing\",\"evidence\":\"<one code line or empty>\"}]}. No prose, no markdown."},
        {"role": "user", "content": f"## File: {todo_file_name}\n```python\n{code}\n```\n\n## Equations to verify\n{json.dumps(eq_compact, ensure_ascii=False)}"},
    ]
    try:
        vc = api_call(verify_msg)
        total_cost = print_log_cost(json.loads(vc.model_dump_json()), gpt_version, f"[VERIFY] {todo_file_name}", output_dir, total_cost)
        vjson = _loads_loose(vc.choices[0].message.content)
        missing = [r for r in (vjson or {}).get("results", []) if isinstance(r, dict) and r.get("status") == "missing"]
    except Exception as e:
        print(f"[VERIFY] {todo_file_name}: verifier failed ({e}); keeping file as-is.")
        return code, total_cost
    if not missing:
        print(f"[VERIFY] {todo_file_name}: all {len(eqs)} equation(s) found.")
        return code, total_cost
    print(f"[VERIFY] {todo_file_name}: {len(missing)} equation(s) missing -> one targeted regen.")
    fix_msg = [
        {"role": "system", "content": "You fix ONE source file so it implements the listed paper equations LITERALLY (coefficients as numeric literals; correct operator family). Return the COMPLETE corrected file inside a single ```python code block, preserving all other behavior, classes, and interfaces."},
        {"role": "user", "content": f"## Paper\n{paper_content}\n\n## Current file: {todo_file_name}\n```python\n{code}\n```\n\n## Equations currently MISSING/wrong — implement each literally:\n{json.dumps(missing, ensure_ascii=False)}\n\nReturn the complete corrected {todo_file_name}."},
    ]
    try:
        fc = api_call(fix_msg)
        total_cost = print_log_cost(json.loads(fc.model_dump_json()), gpt_version, f"[VERIFY-FIX] {todo_file_name}", output_dir, total_cost)
        fixed = extract_code_from_content(fc.choices[0].message.content)
        if len(fixed) > 0:
            return fixed, total_cost
        print(f"[VERIFY] {todo_file_name}: regen produced no fenced code; keeping original.")
    except Exception as e:
        print(f"[VERIFY] {todo_file_name}: regen failed ({e}); keeping original.")
    return code, total_cost


# testing for checking
detailed_logic_analysis_dict = {}
equations_by_file = {}
retrieved_section_dict = {}
for todo_file_name in todo_file_lst:
    # simple analysis
    save_todo_file_name = todo_file_name.replace("/", "_")

    if todo_file_name == "config.yaml":
        continue

    with open(f"{output_dir}/{save_todo_file_name}_simple_analysis_response.json") as f:
        detailed_logic_analysis_response = json.load(f)
    detailed_logic_analysis_dict[todo_file_name] = detailed_logic_analysis_response[0]['choices'][0]['message']['content']
    equations_by_file[todo_file_name] = parse_equations_block(detailed_logic_analysis_dict[todo_file_name])

artifact_output_dir=f'{output_dir}/coding_artifacts'
os.makedirs(artifact_output_dir, exist_ok=True)

total_accumulated_cost = load_accumulated_cost(f"{output_dir}/accumulated_cost.json")
for todo_idx, todo_file_name in enumerate(tqdm(todo_file_lst)):
    responses = []
    trajectories = copy.deepcopy(code_msg)

    current_stage = f"[CODING] {todo_file_name}"
    print(current_stage)

    if todo_file_name == "config.yaml":
        continue

    instruction_msg = get_write_msg(todo_file_name, detailed_logic_analysis_dict[todo_file_name], done_file_lst, equations_block=render_equations(equations_by_file.get(todo_file_name, [])))
    trajectories.extend(instruction_msg)

    completion = api_call(trajectories)
    # print(completion.choices[0].message)
    
    # response
    completion_json = json.loads(completion.model_dump_json())
    responses.append(completion_json)

    # trajectories
    message = completion.choices[0].message
    trajectories.append({'role': message.role, 'content': message.content})

    done_file_lst.append(todo_file_name)

    # save
    # save_dir_name = f"{paper_name}_repo"
    os.makedirs(f'{output_repo_dir}', exist_ok=True)
    save_todo_file_name = todo_file_name.replace("/", "_")


    # print and logging
    print_response(completion_json)
    temp_total_accumulated_cost = print_log_cost(completion_json, gpt_version, current_stage, output_dir, total_accumulated_cost)
    total_accumulated_cost = temp_total_accumulated_cost

    # save artifacts
    with open(f'{artifact_output_dir}/{save_todo_file_name}_coding.txt', 'w') as f:
        f.write(completion_json['choices'][0]['message']['content'])


    # extract code save 
    code = extract_code_from_content(message.content)
    if len(code) == 0:
        code = message.content 

    if save_todo_file_name != todo_file_name:
        todo_file_dir = '/'.join(todo_file_name.split("/")[:-1])
        os.makedirs(f"{output_repo_dir}/{todo_file_dir}", exist_ok=True)

    # Equation self-verify (bounded: at most one targeted regen for files with equations).
    code, total_accumulated_cost = verify_and_fix_equations(
        todo_file_name, code, equations_by_file.get(todo_file_name, []), output_dir, total_accumulated_cost)

    done_file_dict[todo_file_name] = code
    with open(f"{output_repo_dir}/{todo_file_name}", 'w') as f:
        f.write(code)


# write_requirements_txt: materialize the planning stage's "Required packages"
# list into <repo>/requirements.txt. The planning Task list almost never names
# requirements.txt as a file to generate, so without this the generated repo
# ships code that imports torch/transformers/etc. but has no dependency
# manifest — and scripts/run_tests_local.sh then skips installation and pytest
# collection fails with ModuleNotFoundError. The "Required packages" entries are
# already in pip-installable form (e.g. "torch>=2.1.0"), so we just clean and
# write them. Returns nothing; prints a clear message on success or on the
# (degenerate) empty-list case so a missing manifest is never silent.
def write_requirements_txt(task_list_dict, repo_dir):
    raw_packages = task_list_dict.get('Required packages') or []
    # Drop blanks and the common "no dependencies" sentinel the planner emits.
    # Also drop interpreter pins like "python==3.10.*": the planner often lists
    # the Python version among "Required packages", but `pip install python==...`
    # is impossible and makes pip abort the ENTIRE `-r requirements.txt` install
    # atomically — which silently leaves torch/numpy/etc. uninstalled and breaks
    # test collection. The interpreter is provisioned by the venv, not by pip.
    cleaned = []
    seen = set()
    for entry in raw_packages:
        if not isinstance(entry, str):
            continue
        pkg = entry.strip()
        if not pkg:
            continue
        if pkg.lower().startswith('no ') and 'depend' in pkg.lower():
            continue
        # Extract the distribution name (text before any version specifier).
        name = re.split(r'[=<>~!;\[\s]', pkg, maxsplit=1)[0].strip().lower()
        if name in ('python', 'python3'):
            print(f"[CODING][requirements] skipping interpreter pin '{pkg}' "
                  f"(not pip-installable)")
            continue
        if pkg in seen:
            continue
        seen.add(pkg)
        cleaned.append(pkg)

    req_path = f"{repo_dir}/requirements.txt"
    if not cleaned:
        print(f"[CODING][requirements] WARNING: planning 'Required packages' was "
              f"empty; writing an empty {req_path}. Tests may fail to import deps.")
    try:
        with open(req_path, 'w') as f:
            f.write("\n".join(cleaned) + ("\n" if cleaned else ""))
    except OSError as e:
        print(f"[CODING][requirements] ERROR: could not write {req_path}: {e}")
        raise
    print(f"[CODING][requirements] wrote {len(cleaned)} package(s) to {req_path}")


write_requirements_txt(task_list, output_repo_dir)

save_accumulated_cost(f"{output_dir}/accumulated_cost.json", total_accumulated_cost)
