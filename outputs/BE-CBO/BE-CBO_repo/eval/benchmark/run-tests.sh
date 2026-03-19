#!/bin/bash
set -euo pipefail

cd /app
python3 -m pytest /tests/test_outputs.py -rA
