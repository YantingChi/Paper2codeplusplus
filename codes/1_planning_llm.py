import json
import argparse
import os
from codes.ShiftToC.language_profiles import get_language_profile
from codes.ShiftToC.prompt_builders import build_planning_messages
from utils import load_paper_content, print_response
from transformers import AutoTokenizer
from vllm import LLM, SamplingParams

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
paper_format = args.paper_format
pdf_json_path = args.pdf_json_path
pdf_latex_path = args.pdf_latex_path
output_dir = args.output_dir

model_name = args.model_name
tp_size = args.tp_size
max_model_len = args.max_model_len
temperature = args.temperature

profile = get_language_profile(args.target_language)
paper_content = load_paper_content(
    paper_format,
    pdf_json_path=pdf_json_path,
    pdf_latex_path=pdf_latex_path,
)
planning_stages = build_planning_messages(paper_content, paper_format, profile)


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

responses = []
trajectories = []
total_accumulated_cost = 0

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
os.makedirs(output_dir, exist_ok=True)

with open(f'{output_dir}/planning_response.json', 'w') as f:
    json.dump(responses, f)

with open(f'{output_dir}/planning_trajectories.json', 'w') as f:
    json.dump(trajectories, f)
