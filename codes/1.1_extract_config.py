import json
import re
import os
import argparse
import shutil
from utils import extract_last_fenced_block, extract_planning, content_to_json, format_json_data

parser = argparse.ArgumentParser()

parser.add_argument('--paper_name',type=str)
parser.add_argument('--output_dir',type=str, default="")

args    = parser.parse_args()

output_dir = args.output_dir

with open(f'{output_dir}/planning_trajectories.json', encoding='utf8') as f:
    traj = json.load(f)

yaml_raw_content = ""
for turn in reversed(traj):
    if turn.get("role") != "assistant":
        continue
    content = turn.get("content", "")
    if "```yaml" in content or "```yaml\\n" in content:
        yaml_raw_content = content
        break

if "</think>" in yaml_raw_content:
    yaml_raw_content = yaml_raw_content.split("</think>")[-1]

yaml_content = extract_last_fenced_block(yaml_raw_content, fence_languages=("yaml",))
if yaml_content:
    with open(f'{output_dir}/planning_config.yaml', 'w', encoding='utf8') as f:
        f.write(yaml_content)
else:
    print("No YAML content found.")

# ---------------------------------------

artifact_output_dir=f"{output_dir}/planning_artifacts"

os.makedirs(artifact_output_dir, exist_ok=True)

context_lst = extract_planning(f'{output_dir}/planning_trajectories.json')

arch_design = content_to_json(context_lst[1])
logic_design = content_to_json(context_lst[2])

formatted_arch_design = format_json_data(arch_design)
formatted_logic_design = format_json_data(logic_design)

with open(f"{artifact_output_dir}/1.1_overall_plan.txt", "w", encoding="utf-8") as f:
    f.write(context_lst[0])

with open(f"{artifact_output_dir}/1.2_arch_design.txt", "w", encoding="utf-8") as f:
    f.write(formatted_arch_design)

with open(f"{artifact_output_dir}/1.3_logic_design.txt", "w", encoding="utf-8") as f:
    f.write(formatted_logic_design)

shutil.copy(f"{output_dir}/planning_config.yaml", f"{artifact_output_dir}/1.4_config.yaml")
