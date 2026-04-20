import json
import os
from tqdm import tqdm
from codes.ShiftToC.language_profiles import get_language_profile
from codes.ShiftToC.prompt_builders import build_analysis_messages
from utils import (
    build_logic_analysis_map,
    content_to_json,
    extract_planning,
    get_task_file_list,
    load_paper_content,
    print_response,
    sanitize_artifact_name,
)
import copy
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
if os.path.exists(f'{output_dir}/task_list.json'):
    with open(f'{output_dir}/task_list.json') as f:
        task_list = json.load(f)
else:
    task_list = content_to_json(context_lst[2])

try:
    todo_file_lst = get_task_file_list(task_list)
except KeyError as exc:
    print(f"[ERROR] {exc}")
    raise SystemExit(0) from exc

done_file_lst = [profile.default_config_artifact]
try:
    logic_analysis_dict = build_logic_analysis_map(task_list)
except KeyError as exc:
    print(f"[ERROR] {exc}")
    raise SystemExit(0) from exc



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

artifact_output_dir=f'{output_dir}/analyzing_artifacts'
os.makedirs(artifact_output_dir, exist_ok=True)

for todo_file_name in tqdm(todo_file_lst):
    responses = []
    trajectories = copy.deepcopy(
        build_analysis_messages(
            paper_content,
            paper_format,
            context_lst,
            config_yaml,
            todo_file_name,
            logic_analysis_dict.get(todo_file_name, ""),
            profile,
        )
    )

    current_stage=f"[ANALYSIS] {todo_file_name}"
    print(current_stage)
    if todo_file_name == profile.default_config_artifact:
        continue
        
    completion = run_llm(trajectories)
    
    # response
    completion_json = {
        'text': completion
    }

    # print and logging
    print_response(completion_json, is_llm=True)

    responses.append(completion_json)
    
    # trajectories
    trajectories.append({'role': 'assistant', 'content': completion})


    # save
    safe_todo_file_name = sanitize_artifact_name(todo_file_name)
    with open(f'{artifact_output_dir}/{safe_todo_file_name}_simple_analysis.txt', 'w', encoding='utf-8') as f:
        f.write(completion)

    done_file_lst.append(todo_file_name)

    # save for next stage(coding)
    with open(f'{output_dir}/{safe_todo_file_name}_simple_analysis_response.json', 'w', encoding='utf-8') as f:
        json.dump(responses, f)

    with open(f'{output_dir}/{safe_todo_file_name}_simple_analysis_trajectories.json', 'w', encoding='utf-8') as f:
        json.dump(trajectories, f)
