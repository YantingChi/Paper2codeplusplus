import json
import argparse
import os
from openai_client import create_openai_client
from codes.ShiftToC.language_profiles import get_language_profile
from codes.ShiftToC.prompt_builders import build_planning_messages
from utils import (
    load_paper_content,
    print_response,
    print_log_cost,
)

parser = argparse.ArgumentParser()

parser.add_argument('--paper_name',type=str)
parser.add_argument('--gpt_version',type=str)
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
planning_stages = build_planning_messages(paper_content, paper_format, profile)

def api_call(msg, gpt_version):
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

responses = []
trajectories = []
total_accumulated_cost = 0
os.makedirs(output_dir, exist_ok=True)

for idx, instruction_msg in enumerate(planning_stages):
    current_stage = ""
    if idx == 0 :
        current_stage = f"[Planning] Overall plan"
    elif idx == 1:
        current_stage = f"[Planning] Architecture design"
    elif idx == 2:
        current_stage = f"[Planning] Logic design"
    elif idx == 3:
        current_stage = f"[Planning] Configuration file generation"
    print(current_stage)

    trajectories.extend(instruction_msg)

    completion = api_call(trajectories, gpt_version)
    
    # response
    completion_json = json.loads(completion.model_dump_json())

    # print and logging
    print_response(completion_json)
    temp_total_accumulated_cost = print_log_cost(completion_json, gpt_version, current_stage, output_dir, total_accumulated_cost)
    total_accumulated_cost = temp_total_accumulated_cost

    responses.append(completion_json)

    # trajectories
    message = completion.choices[0].message
    trajectories.append({'role': message.role, 'content': message.content})


# save
save_accumulated_cost(f"{output_dir}/accumulated_cost.json", total_accumulated_cost)

os.makedirs(output_dir, exist_ok=True)

with open(f'{output_dir}/planning_response.json', 'w') as f:
    json.dump(responses, f)

with open(f'{output_dir}/planning_trajectories.json', 'w') as f:
    json.dump(trajectories, f)
