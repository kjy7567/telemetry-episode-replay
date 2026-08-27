# Model Evaluation Configuration

## Retained Runs

All four evaluations use `scripts/run_bts_e2e_openai_eval.py`, the same deterministic user simulator, read-only tool interface, stopping protocol, and scorer. Every trace retains its complete system/user/assistant/tool exchange and component scores.

| Corpus | Model | Rows | Accomplished | Prompt profile | Output cap |
|---|---|---:|---:|---|---:|
| BTS | GPT-5.5 | 89 | 79 | `gpt55-bts` | 512 |
| BTS | Gemini 3.1 Pro | 89 | 71 | `bts-guided` | 1536 |
| BTS | Claude Opus 4.7 | 89 | 58 | `bts-guided` | 512 |
| XAI4HEAT | GPT-5.5 | 41 | 41 | `xai4heat` | 512 |

The study records one provider invocation per model-row. Repeated-call variation was not measured. Fixed traces, rather than fresh API calls, are the input to the reported deterministic rescoring results.

Shared requested settings were temperature `0`, reasoning effort `medium`, no parallel tool calls, a base turn budget of `12` raised to the row's required minimum, and a 180-second timeout. Seed `0` and low verbosity were sent where supported. Exact provider arguments are retained in each `reports/model-runs/*/run_config.json`.

The BTS traces are bound to `artifacts/bts-agentbench/test.jsonl` with SHA-256 `91b43d2e424df1146989350a9097a103243b08c1ce1e55f1959bf2913b09dc30`. The XAI4HEAT traces are bound to its public 41-row test split with SHA-256 `860aacd16b1fd8f7114eb05015c18b3a88efba566de3c9330dabed7b7cecb8e9`.

## Runner Commands

After constructing the BTS tool store:

```bash
export BTS_TOOL_STORE_DB="$PWD/data/local-build/release-replay/raw-run-1/tool-store/tool_store.duckdb"
export BTS_BENCHMARK_DIR="$PWD/artifacts/bts-agentbench"

bash runners/gpt55_bts.sh
bash runners/gemini31pro_bts_openrouter.sh
bash runners/opus47_bts_openrouter.sh
```

For XAI4HEAT:

```bash
export XAI4HEAT_TOOL_STORE_DB="$PWD/data/local-build/xai4heat/tool-store/tool_store.duckdb"
export XAI4HEAT_BENCHMARK_DIR="$PWD/artifacts/xai4heat-agentbench"

bash runners/gpt55_xai4heat.sh
```

The wrappers select the complete test split. API keys are required only for new calls.

## Deterministic Trace Audit

Given fresh tool stores, rescore all retained traces with:

```bash
python scripts/audit_model_traces.py \
  --bts-benchmark-dir artifacts/bts-agentbench \
  --bts-tool-store-db data/local-build/release-replay/raw-run-1/tool-store/tool_store.duckdb \
  --bts-raw-dir data/local-build/raw \
  --xai4heat-benchmark-dir artifacts/xai4heat-agentbench \
  --xai4heat-tool-store-db data/local-build/xai4heat/tool-store/tool_store.duckdb
```

The audit verifies:

- every trace scenario ID equals the corresponding public test split;
- family, interaction mode, and first user message equal the clean episode;
- each retained system prompt equals the rendered prompt profile;
- retained calls are reconstructed as `ExecutedCall` objects;
- `verify_prediction` reproduces the stored component dictionary exactly.

`reports/model-runs/trace_audit.json` records 267/267 exact BTS rescoring matches and 41/41 exact XAI4HEAT matches.

## Score Definitions

- **Final** checks required fields in the final phase response against the typed gold. The table value is the macro-average of row scores.
- **Evidence** measures required-stream coverage. Every released episode has an evidence follow-up, so all test rows are included.
- **Phase** is the passing fraction of that row's ordered phase responses. The table value is a row-level macro-average, not a phase micro-average.
- **Task** is the row-level mean of core answer, grounding, temporal, and phase components. `task_ok` requires all four Boolean checks.
- **Protocol** requires completion without a missing clarification, revision, rationale/evidence turn, invalid follow-up, empty message, tool error, or nontermination.
- **Accomplished** is `task_ok AND protocol_ok`.

The runner stores partial component scores for diagnosis, but only `accomplished` contributes to the main success count.
