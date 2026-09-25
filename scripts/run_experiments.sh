#!/usr/bin/env bash
# Full experiment set on the 89-item test split. Each run is its own batch label.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
$PY -m guardrail_lab.eval.run --systems A B C --split test --label v2main
$PY -m guardrail_lab.eval.run --systems C --split test --no-llm-classifier --label v2rulesonly
$PY -m guardrail_lab.eval.run --systems C --split test --no-rules --label v2clsonly
$PY -m guardrail_lab.eval.run --systems C --split test --no-cascade --label v2nocascade
$PY -m guardrail_lab.eval.run --systems C --split test --unconstrained --label v2unconstrained
echo ALL_EXPERIMENTS_DONE
