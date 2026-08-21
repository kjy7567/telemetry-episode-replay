# Model Evaluation Configuration

## Shared Harness

All retained configurations use `scripts/run_bts_e2e_openai_eval.py`, the same read-only tools, deterministic user simulator, stopping logic, and scorer. The complete rendered system prompt is retained as the first message of every trace. Explicit prompt profiles reproduce the recorded BTS GPT-5.5 base prompt, the Gemini/Opus family guidance, and the XAI4HEAT corpus guidance.

Shared settings for the retained runs were:

- one retained API trace per test row;
- temperature argument `0`;
- seed argument `0` where the provider accepts it;
- reasoning effort `medium`;
- verbosity `low` where the provider accepts it;
- base turn budget `12`, raised per row to the deterministic minimum required by its tools, phases, clarification, and follow-ups;
- no parallel tool calls;
- timeout `180` seconds per request;
- full 89-row test split, assembled from completed family runs.

The study reports single retained runs. Provider-side nondeterminism remains possible despite temperature and seed controls. The fixed traces, rather than a new API rerun, are the inputs to the reported scorer outputs.

The paper scores are attached to the submitted test JSONL SHA-256 `a7922313934258dce878a8218ce5bfb87b8628be639a52d279fd5a38304d3867`. `scripts/replay_paper_submission.py` reconstructs that file exactly before any optional new model run.

## Provider Settings

| Evaluation | Endpoint | Provider mode | Prompt profile | Output cap |
|---|---|---|---|---:|
| GPT-5.5 on BTS | OpenAI direct | `openai` | `gpt55-bts` | 512 |
| Gemini 3.1 Pro Preview on BTS | OpenRouter | `gemini` | `bts-guided` | 1536 |
| Claude Opus 4.7 on BTS | OpenRouter | `openai` compatibility | `bts-guided` | 512 |
| GPT-5.5 on XAI4HEAT | OpenAI direct | `openai` | `xai4heat` | 512 |

The rendered prompt from each profile has been checked against every retained trace: 89/89 for each BTS configuration and 41/41 for XAI4HEAT.

## Commands

Set `BTS_TOOL_STORE_DB`, point `BTS_BENCHMARK_DIR` at an exact replay, and set the relevant API key:

```bash
export BTS_TOOL_STORE_DB=/absolute/path/to/tool_store.duckdb
export BTS_BENCHMARK_DIR=/absolute/path/to/paper-submission-replay/run_1/final
```

Then run one wrapper:

```bash
bash runners/gpt55_bts.sh
bash runners/gemini31pro_bts_openrouter.sh
bash runners/opus47_bts_openrouter.sh
```

For XAI4HEAT:

```bash
export XAI4HEAT_TOOL_STORE_DB=/absolute/path/to/xai4heat/tool-store/tool_store.duckdb
export XAI4HEAT_BENCHMARK_DIR=/absolute/path/to/xai4heat/final
bash runners/gpt55_xai4heat.sh
```

The BTS wrappers select all 89 test rows; the XAI4HEAT wrapper selects all 41. `--family FAMILY_NAME` can be added to the underlying Python command for family-local execution. New summaries include model/provider arguments, prompt profile, effective token cap, runner hash, benchmark split hash, and system-prompt hashes.

## Scoring

The runner stores every message, tool call, tool result, phase answer, final answer, rationale/evidence reply, issue flag, usage count, and static verifier component. A row is `accomplished` only when its task verifier succeeds and no protocol issue remains. Partial component scores are retained for analysis but do not count as accomplished rows.

- **Final** checks the required fields of the final phase answer against its typed gold target. The reported mean is a macro-average of row-level final scores.
- **Evidence** measures required-stream coverage. Every released E2E row has an evidence obligation, so the reported mean includes all 89 test rows.
- **Phase** is the fraction of that row's ordered phase answers that pass their own typed answer checks; the table-level value is the macro-average over rows, not a phase-level micro-average.
- **Task** is the row-level mean of core answer, grounding, temporal, and phase scores. `task_ok` requires all four Boolean checks; the partial numeric task score is not itself the accomplished label.
- **Protocol** is Boolean and requires the interaction to finish without missing clarification/goal-revision/rationale/evidence turns, invalid follow-ups, empty messages, tool errors, or nontermination.
- **Accomplished** is exactly `task_ok AND protocol_ok`. Process conformance is retained separately and is additionally required only by `strict_label`.
