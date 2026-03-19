#!/bin/bash
EVAL_DIR=/home/yantingchi/Desktop/DK/Paper2Code/codes
PROGRAM_NAME=BE-CBO

pushd ./

cd $EVAL_DIR
python3.10  eval_get_running_info.py\
    --paper_name BE-CBO \
    --output_path ../outputs/${PROGRAM_NAME}_repo/eval \
	--paper_format JSON \
	--pdf_json_path ../data/lightweight/${PROGRAM_NAME}/${PROGRAM_NAME}.json \
	--pdf_latex_path ../data/lightweight/${PROGRAM_NAME}/${PROGRAM_NAME}.pdf \
	--gpt_version o3-mini





    # --data_dir ../data \
    # --output_dir ../outputs/${PROGRAM_NAME} \
    # --target_repo_dir ../outputs/${PROGRAM_NAME}_repo \
    # --eval_result_dir ../results \
    # --eval_type ref_free \
    # --generated_n 8 \
    # --papercoder
popd 