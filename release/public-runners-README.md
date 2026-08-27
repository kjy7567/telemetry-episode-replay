# Runner Snapshots

All wrappers call `scripts/run_bts_e2e_openai_eval.py` and require a benchmark directory, a matching read-only tool store, and the relevant provider key.

| Corpus | Model | Wrapper | Prompt profile | Rows |
|---|---|---|---|---:|
| BTS | GPT-5.5 | `runners/gpt55_bts.sh` | `gpt55-bts` | 89 |
| BTS | Gemini 3.1 Pro | `runners/gemini31pro_bts_openrouter.sh` | `bts-guided` | 89 |
| BTS | Claude Opus 4.7 | `runners/opus47_bts_openrouter.sh` | `bts-guided` | 89 |
| XAI4HEAT | GPT-5.5 | `runners/gpt55_xai4heat.sh` | `xai4heat` | 41 |

Each retained run directory contains `run_config.json`, the full test trace, and its summary. New provider calls are optional; the retained traces can be inspected and rescored without an API call.
