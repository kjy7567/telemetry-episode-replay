#!/usr/bin/env bash
set -euo pipefail

: "${OPENROUTER_API_KEY:?Set OPENROUTER_API_KEY}"
: "${BTS_TOOL_STORE_DB:?Set BTS_TOOL_STORE_DB}"
: "${BTS_BENCHMARK_DIR:?Set BTS_BENCHMARK_DIR to an exact replay final directory}"

python scripts/run_bts_e2e_openai_eval.py \
  --provider openai \
  --prompt-profile bts-guided \
  --base-url https://openrouter.ai/api/v1 \
  --api-key-env OPENROUTER_API_KEY \
  --model anthropic/claude-opus-4.7 \
  --benchmark-dir "$BTS_BENCHMARK_DIR" \
  --tool-store-db "$BTS_TOOL_STORE_DB" \
  --split test \
  --max-turns 12 \
  --max-completion-tokens 512 \
  --max-scenarios 89 \
  --max-per-mode 1000 \
  --reasoning-effort medium \
  --verbosity low \
  --temperature 0 \
  --seed 0 \
  --timeout-seconds 180 \
  --out-jsonl reports/model-runs/claude-opus-4.7-openrouter/recomputed_test.jsonl \
  --out-summary reports/model-runs/claude-opus-4.7-openrouter/recomputed_summary.json
