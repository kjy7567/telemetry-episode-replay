#!/usr/bin/env bash
set -euo pipefail

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY}"
: "${XAI4HEAT_TOOL_STORE_DB:?Set XAI4HEAT_TOOL_STORE_DB}"
: "${XAI4HEAT_BENCHMARK_DIR:?Set XAI4HEAT_BENCHMARK_DIR to the final XAI4HEAT directory}"

python scripts/run_bts_e2e_openai_eval.py \
  --provider openai \
  --prompt-profile xai4heat \
  --api-key-env OPENAI_API_KEY \
  --model gpt-5.5 \
  --benchmark-dir "$XAI4HEAT_BENCHMARK_DIR" \
  --tool-store-db "$XAI4HEAT_TOOL_STORE_DB" \
  --split test \
  --max-turns 12 \
  --max-completion-tokens 512 \
  --max-scenarios 41 \
  --max-per-mode 1000 \
  --reasoning-effort medium \
  --verbosity low \
  --temperature 0 \
  --seed 0 \
  --timeout-seconds 180 \
  --out-jsonl reports/model-runs/gpt-5.5-xai4heat/recomputed_test.jsonl \
  --out-summary reports/model-runs/gpt-5.5-xai4heat/recomputed_summary.json
