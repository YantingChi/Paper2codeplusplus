import json
import os
from tqdm import tqdm
import copy
from codes.ShiftToC.language_profiles import get_language_profile
from codes.ShiftToC.prompt_builders import build_coding_messages
from utils import (
    content_to_json,
    extract_code_from_content,
    extract_code_from_content2,
    extract_planning,
    get_task_file_list,
    load_paper_content,
    print_response,
    sanitize_artifact_name,
)
from transformers import AutoTokenizer
from vllm import LLM, SamplingParams

import argparse

parser = argparse.ArgumentParser()

parser.add_argument('--paper_name',type=str)

parser.add_argument('--model_name',type=str, default="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct") 
parser.add_argument('--tp_size',type=int, default=2)
parser.add_argument('--temperature',type=float, default=1.0)
parser.add_argument('--max_model_len',type=int, default=128000)

parser.add_argument('--paper_format',type=str, default="JSON", choices=["JSON", "LaTeX"])
parser.add_argument('--pdf_json_path', type=str) # json format
parser.add_argument('--pdf_latex_path', type=str) # latex format

parser.add_argument('--output_dir',type=str, default="")
parser.add_argument('--output_repo_dir',type=str, default="")
parser.add_argument('--target_language', type=str, default="python", choices=["python", "c"])

args    = parser.parse_args()

paper_name = args.paper_name

model_name = args.model_name
tp_size = args.tp_size
max_model_len = args.max_model_len
temperature = args.temperature

paper_format = args.paper_format
pdf_json_path = args.pdf_json_path
pdf_latex_path = args.pdf_latex_path

output_dir = args.output_dir
output_repo_dir = args.output_repo_dir

profile = get_language_profile(args.target_language)
paper_content = load_paper_content(
    paper_format,
    pdf_json_path=pdf_json_path,
    pdf_latex_path=pdf_latex_path,
)

with open(f'{output_dir}/planning_config.yaml') as f: 
    config_yaml = f.read()

context_lst = extract_planning(f'{output_dir}/planning_trajectories.json')
# 0: overview, 1: detailed, 2: PRD
# file_list = content_to_json(context_lst[1])
task_list = content_to_json(context_lst[2])

try:
    todo_file_lst = get_task_file_list(task_list)
except KeyError as exc:
    print(f"[ERROR] {exc}")
    raise SystemExit(0) from exc

done_file_lst = [profile.default_config_artifact]
done_file_dict = {}

model_name = args.model_name
tokenizer = AutoTokenizer.from_pretrained(model_name)


if "Qwen" in model_name:
    llm = LLM(model=model_name, 
            tensor_parallel_size=tp_size, 
            max_model_len=max_model_len,
            gpu_memory_utilization=0.95,
            trust_remote_code=True, enforce_eager=True, 
            rope_scaling={"factor": 4.0, "original_max_position_embeddings": 32768, "type": "yarn"})
    sampling_params = SamplingParams(temperature=temperature, max_tokens=131072)

elif "deepseek" in model_name:
    llm = LLM(model=model_name, 
              tensor_parallel_size=tp_size, 
              max_model_len=max_model_len,
              gpu_memory_utilization=0.95,
              trust_remote_code=True, enforce_eager=True)
    sampling_params = SamplingParams(temperature=temperature, max_tokens=128000, stop_token_ids=[tokenizer.eos_token_id])


def run_llm(msg):
    # vllm
    prompt_token_ids = [tokenizer.apply_chat_template(messages, add_generation_prompt=True) for messages in [msg]]

    outputs = llm.generate(prompt_token_ids=prompt_token_ids, sampling_params=sampling_params)

    completion = [output.outputs[0].text for output in outputs]
    
    return completion[0] 
    

# testing for checking
detailed_logic_analysis_dict = {}
retrieved_section_dict = {}
for todo_file_name in todo_file_lst:
    # simple analysis
    save_todo_file_name = sanitize_artifact_name(todo_file_name)

    if todo_file_name == profile.default_config_artifact:
        continue

    with open(f"{output_dir}/{save_todo_file_name}_simple_analysis_response.json", encoding='utf8') as f:
        detailed_logic_analysis_response = json.load(f)

    detailed_logic_analysis_dict[todo_file_name] = detailed_logic_analysis_response[0]['text']

artifact_output_dir=f'{output_dir}/coding_artifacts'
os.makedirs(artifact_output_dir, exist_ok=True)

for todo_idx, todo_file_name in enumerate(tqdm(todo_file_lst)):
    responses = []
    trajectories = copy.deepcopy(
        build_coding_messages(
            paper_content,
            paper_format,
            context_lst,
            config_yaml,
            todo_file_name,
            detailed_logic_analysis_dict.get(todo_file_name, ""),
            done_file_lst,
            done_file_dict,
            profile,
        )
    )

    current_stage = f"[CODING] {todo_file_name}"
    print(current_stage)

    if todo_file_name == profile.default_config_artifact:
        continue

    completion = run_llm(trajectories)
    
    # response
    completion_json = {
        'text': completion
    }
    responses.append(completion_json)

    # trajectories
    trajectories.append({'role': 'assistant', 'content': completion})

    done_file_lst.append(todo_file_name)

    # save
    # save_dir_name = f"{paper_name}_repo"
    os.makedirs(f'{output_repo_dir}', exist_ok=True)
    save_todo_file_name = sanitize_artifact_name(todo_file_name)

    # print and logging
    print_response(completion_json, is_llm=True)

    # save artifacts
    with open(f'{artifact_output_dir}/{save_todo_file_name}_coding.txt', 'w', encoding='utf-8') as f:
        f.write(completion)

    # extract code save 
    try:
        code = extract_code_from_content(completion)
    except Exception as e:
        code = extract_code_from_content2(completion) 

    if len(code) == 0:
        code = completion

    done_file_dict[todo_file_name] = code
    todo_file_dir = os.path.dirname(todo_file_name)
    if len(todo_file_dir) > 0:
        os.makedirs(f"{output_repo_dir}/{todo_file_dir}", exist_ok=True)

    with open(f"{output_repo_dir}/{todo_file_name}", 'w', encoding='utf-8') as f:
        f.write(code)
