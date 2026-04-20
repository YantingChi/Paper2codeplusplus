import json
import os
from tqdm import tqdm
import sys
from openai_client import create_openai_client
from codes.ShiftToC.language_profiles import get_language_profile
from codes.ShiftToC.prompt_builders import build_analysis_messages
from utils import (
    build_logic_analysis_map,
    content_to_json,
    extract_planning,
    get_task_file_list,
    load_paper_content,
    print_log_cost,
    print_response,
    load_accumulated_cost,
    sanitize_artifact_name,
    save_accumulated_cost,
)
import copy

import argparse

parser = argparse.ArgumentParser()

parser.add_argument('--paper_name',type=str)
parser.add_argument('--gpt_version',type=str, default="o3-mini")
parser.add_argument('--paper_format',type=str, default="JSON", choices=["JSON", "LaTeX"])
parser.add_argument('--pdf_json_path', type=str) # json format
parser.add_argument('--pdf_latex_path', type=str) # latex format
parser.add_argument('--output_dir',type=str, default="")
parser.add_argument('--target_language', type=str, default="python", choices=["python", "c"])

args    = parser.parse_args()

client = create_openai_client()

paper_name = args.paper_name
gpt_version = args.gpt_version
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
    sys.exit(0)

done_file_lst = [profile.default_config_artifact]
try:
    logic_analysis_dict = build_logic_analysis_map(task_list)
except KeyError as exc:
    print(f"[ERROR] {exc}")
    sys.exit(0)


def api_call(msg):
    if "o3-mini" in gpt_version:
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


artifact_output_dir=f'{output_dir}/analyzing_artifacts'
os.makedirs(artifact_output_dir, exist_ok=True)

total_accumulated_cost = load_accumulated_cost(f"{output_dir}/accumulated_cost.json")
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
        
    completion = api_call(trajectories)
    
    # response
    completion_json = json.loads(completion.model_dump_json())
    responses.append(completion_json)
    
    # trajectories
    message = completion.choices[0].message
    trajectories.append({'role': message.role, 'content': message.content})

    # print and logging
    print_response(completion_json)
    temp_total_accumulated_cost = print_log_cost(completion_json, gpt_version, current_stage, output_dir, total_accumulated_cost)
    total_accumulated_cost = temp_total_accumulated_cost

    # save
    safe_todo_file_name = sanitize_artifact_name(todo_file_name)
    with open(f'{artifact_output_dir}/{safe_todo_file_name}_simple_analysis.txt', 'w', encoding='utf-8') as f:
        f.write(completion_json['choices'][0]['message']['content'])


    done_file_lst.append(todo_file_name)

    # save for next stage(coding)
    with open(f'{output_dir}/{safe_todo_file_name}_simple_analysis_response.json', 'w', encoding='utf-8') as f:
        json.dump(responses, f)

    with open(f'{output_dir}/{safe_todo_file_name}_simple_analysis_trajectories.json', 'w', encoding='utf-8') as f:
        json.dump(trajectories, f)

save_accumulated_cost(f"{output_dir}/accumulated_cost.json", total_accumulated_cost)
