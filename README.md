# Paper2Code++

Paper2Code++ generates a paper-specific reproduction repository from a cleaned
paper input, then can optionally build evaluation rubrics, tests, and Harbor
benchmark tasks around that generated repository.

## How to run it 
PAPER_NAME=adaptive-pruning bash scripts/run_codex.sh 2>&1 | tee temp.log
the current testing is broken and I am fixing it.

## Results
outputs/paperbench_repos contains all the generated repo while outputs/paperbench contains all the planning files

