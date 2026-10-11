#!/bin/zsh
cd -- "$(dirname -- "$0")"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m grader serve
else
  exec python3 -m grader serve
fi
