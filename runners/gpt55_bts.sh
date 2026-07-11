#!/usr/bin/env bash
set -euo pipefail

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY}"
: "${BTS_TOOL_STORE_DB:?Set BTS_TOOL_STORE_DB}"
: "${BTS_BENCHMARK_DIR:?Set BTS_BENCHMARK_DIR to an exact replay final directory}"

python scripts/run_bts_e2e_openai_eval.py \
  --provider openai \
  --api-key-env OPENAI_API_KEY \
  --model gpt-5.5 \
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
  --out-jsonl reports/model-runs/gpt-5.5/recomputed_test.jsonl \
  --out-summary reports/model-runs/gpt-5.5/recomputed_summary.json
