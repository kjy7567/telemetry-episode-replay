# Runner Snapshots

Use these wrappers from the root of `source.zip`, after installing its pinned
environment and constructing the corresponding benchmark and read-only tool
store.

| Corpus | Model | Wrapper | Prompt profile | Rows |
|---|---|---|---|---:|
| BTS | GPT-5.5 | `gpt55_bts.sh` | `gpt55-bts` | 89 |
| BTS | Gemini 3.1 Pro | `gemini31pro_bts_openrouter.sh` | `bts-guided` | 89 |
| BTS | Claude Opus 4.7 | `opus47_bts_openrouter.sh` | `bts-guided` | 89 |
| XAI4HEAT | GPT-5.5 | `gpt55_xai4heat.sh` | `xai4heat` | 41 |

The adjacent `*.run_config.json` files record endpoint, output cap, seed
handling, turn budget, timeout, and invocation count.
