# Telemetry-to-Agent Episode Replay

This repository reconstructs the exact 532 BTS agent episodes used in the paper submission. It starts from timestamped building telemetry, builds a read-only analytical database, compiles executable static tasks, and expands them into multi-turn agent interactions with phase-level gold answers, evidence, and deterministic scoring contracts.

No LLM is used to choose telemetry values, tool results, gold answers, clarification replies, or evaluator targets.

```text
raw timestamp/value streams + building metadata
  -> read-only DuckDB tool store and aggregate tables
  -> executable static tasks with tool-derived gold answers
  -> clarification, revision, quality, and evidence obligations
  -> phase-structured agent episodes
  -> 356 train + 87 dev + 89 test = 532 submitted rows
```

**Navigate:** [Start here](#start-here) | [Exact replay from raw](#exact-replay-from-raw) | [Inspect one episode](#inspect-one-submitted-episode) | [Manual construction stages](#run-each-construction-stage-manually) | [Recorded evidence](#recorded-reconstruction-evidence)

## At a Glance

| Item | Submitted artifact |
|---|---|
| Benchmark | 532 episodes across 9 task families |
| Splits | 356 train / 87 dev / 89 test |
| Raw telemetry | 3 ZIP archives; 14,422 matched streams |
| Construction | Raw telemetry -> DuckDB/Parquet -> static tasks -> multi-turn episodes |
| Recorded replay | 2 independent raw preprocesses; 3 exact static-to-final builds |
| Exactness check | Complete task and episode objects, not only final answers |
| LLM involvement | None in construction, gold derivation, or replay verification |

## Start Here

Choose the shortest path that answers your question:

| Goal | Raw download | Command |
|---|:---:|---|
| Read one complete submitted trace and actual model conversation | No | `python scripts/trace_scenario.py test_timestamp_value_lookup_00051 --model-trace reports/model-runs/gpt-5.5/test.jsonl` |
| Verify all packaged release evidence | No | `make verify` |
| Reconstruct the submitted 532 rows from raw telemetry | Yes, about 19 GB | Follow [Exact Replay From Raw](#exact-replay-from-raw) |

The first two paths use files already committed to this repository and do not call a model API. The third path rebuilds the telemetry database and every benchmark row before comparing them with the submitted release.

## Inspect One Submitted Episode

The construction fields below are generated from the submitted files by `scripts/trace_scenario.py`; the recorded conversation is loaded from the retained model-run JSONL. The complete trace is in [`examples/REPLAY_TRACE.md`](examples/REPLAY_TRACE.md).

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --model-trace reports/model-runs/gpt-5.5/test.jsonl
```

### 1. Raw telemetry

```text
archive:      Site_Caa.zip
member:       Site_Caa/2254.pickle
stream:       c24589e8_a1f3_4529_b409_5a56761c9d20
point:        Air_Differential_Pressure_Sensor on BTS_C Zone 005
observations: 194,563

2022-02-03T07:03:23.640000+00:00 -> 12.9457
```

### 2. Static executable task

```text
At 07:03 UTC on February 3, 2022, what reading did the air differential
pressure reading attached to Zone 005 report in BTS_C?
```

Static gold:

```json
{
  "stream_id": "c24589e8_a1f3_4529_b409_5a56761c9d20",
  "observed_timestamp": "2022-02-03T07:03:23.640000+00:00",
  "value": 12.9457,
  "exact_match_found": true
}
```

### 3. Agentic interaction contract

```text
USER: Operator handoff: "What was the air differential pressure reading on
      Zone 005 in BTS_C?" Ask for any missing site or time detail.

CLARIFICATION: I mean 07:03:23.64 UTC on February 3, 2022.

REVISION 1: If I only know it was around 07:03 UTC, give me the nearest reading.
REVISION 2: For that week, would you answer or abstain based on data quality?
REVISION 3: Should I report the public-time reading or abstain?
EVIDENCE:   Which stream or point did you base that on?
```

### 4. Gold tool and phase trace

```text
C1 resolve_point(BTS_C, Air_Differential_Pressure_Sensor, BTS_C Zone 005)
C2 lookup_observation(07:03:23.640, exact)
C3 lookup_observation(07:03:00, exact)
C4 lookup_observation(07:03:00, nearest)
C5 inspect_quality_window(2022-01-31, 2022-02-07, week)

P1 exact reading:      12.9457 at 07:03:23.640
P2 nearest reading:    12.9457, offset 23.64 seconds
P3 quality decision:   answer; coverage 1.0, gap ratio 1.0563
P4 reporting decision: answer; nearest_but_acceptable

FINAL ACTION: {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
EVIDENCE:     c24589e8_a1f3_4529_b409_5a56761c9d20
```

### 5. Actual recorded agent conversation

This is the retained GPT-5.5 paid-run conversation for the same submitted scenario. It is labeled `accomplished` with `protocol_ok=true`. The wording below is the actual user and assistant text; tool messages are shown compactly. The [complete generated trace](examples/REPLAY_TRACE.md) includes the full tool-result JSON and terminal simulator message.

```text
USER: Operator handoff: "What was the air differential pressure reading on
      Zone 005 in BTS_C?" Use the building tools and ask me for any missing
      site or time detail before querying.

ASSISTANT: What exact timestamp should I use for the reading? Please include
           the UTC offset (and fractional seconds if available).

USER: I mean 07:03:23.64 UTC on February 3, 2022.

ASSISTANT -> resolve_point(
  site_id=BTS_C,
  point_class=Air_Differential_Pressure_Sensor,
  equipment_label=Zone 005
)
TOOL -> stream_id=c24589e8_a1f3_4529_b409_5a56761c9d20,
        point=BTS_C Air Differential Pressure Sensor 001,
        equipment=BTS_C Zone 005

ASSISTANT -> lookup_observation(
  stream_id=$e1.stream_id,
  timestamp=2022-02-03T07:03:23.64Z,
  mode=exact
)
TOOL -> observed_timestamp=2022-02-03T07:03:23.640000+00:00,
        value=12.9457, exact_match_found=true

ASSISTANT: At 2022-02-03T07:03:23.640000+00:00, the air differential
           pressure reading on BTS_C Zone 005 was 12.9457.

USER: Now keep the same signal and site, but if I only know it was around
      07:03 UTC on February 3, 2022, give me the nearest available reading.

ASSISTANT -> lookup_observation(
  stream_id=$e1.stream_id,
  timestamp=2022-02-03T07:03:00Z,
  mode=nearest
)
TOOL -> observed_timestamp=2022-02-03T07:03:23.640000+00:00,
        value=12.9457, exact_match_found=false, offset_seconds=23.64

ASSISTANT: There was no exact logged reading at 2022-02-03T07:03:00+00:00.
           The nearest available reading was at
           2022-02-03T07:03:23.640000+00:00 with value 12.9457.

USER: For the week beginning January 31, 2022, would you answer or abstain
      based on data quality?

ASSISTANT -> inspect_quality_window(
  stream_id=$e1.stream_id,
  window_start=2022-01-31T00:00:00Z,
  window_end=2022-02-07T00:00:00Z,
  period=week
)
TOOL -> observed_fraction=1.0, gap_ratio=1.0563

ASSISTANT: I would answer. For the week of 2022-01-31 to 2022-02-07,
           observed coverage was 1.0 and gap ratio was 1.0563, which meet
           the quality thresholds for answering.

USER: Given the public-time reading and the data-quality check, should I
      report it as-is, abstain, or ask for more time detail before reporting it?

ASSISTANT: Report it as-is, but label it as the nearest available reading—not
           an exact 07:03:00 reading. Data quality for that week supports
           answering, so no need to abstain or ask for more time detail.

USER: Which stream or point did you base that on?

ASSISTANT: I used stream_id c24589e8_a1f3_4529_b409_5a56761c9d20:
           BTS_C Air Differential Pressure Sensor 001 on BTS_C Zone 005.
```

After reconstruction, pass the reconstructed final directory explicitly. This compares every field in the submitted and replayed rows:

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --replay-dir ./data/local-build/paper-replay/run_1/final \
  --model-trace reports/model-runs/gpt-5.5/test.jsonl
```

The final block is:

```text
9. REPLAY CHECK
   exact match: YES
```

This is the replayability claim: the pipeline regenerates the task wording, tool plan, interaction turns, phase golds, final action, evidence, verifier fields, and provenance. SHA-256 values are retained only as compact integrity checks.

## Installation

### Requirements

- Python 3.11 on a Unix-like shell
- `curl`, `sha256sum`, `unzip`, and `make`
- at least 25 GB of free disk space; the raw archives are about 19 GB compressed and the recorded replay work directory is about 1.9 GB
- internet access for the initial download, or local copies of the three checksummed BTS ZIP archives

Install the pinned environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

## Use the Released Dataset

The submitted bundle contains the 532 BTS rows, the 204-row XAI4HEAT portability artifact, retained model traces, and runner snapshots:

```bash
unzip release/submitted-dataset-bundle.zip \
  -d ./data/local-build/released
```

Load one split:

```python
import json
from pathlib import Path

path = Path(
    "data/local-build/released/"
    "dataset_bundle/bts_agentbench_532/test.jsonl"
)
rows = [json.loads(line) for line in path.read_text().splitlines() if line]
print(len(rows))              # 89
print(rows[0]["scenario_id"])
print(rows[0]["phase_gold_final_answers"])
```

## Exact Replay From Raw

Complete [Installation](#installation) first. No API key is required.

Download the three attributed raw archives and verify them:

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
```

Run the complete paper reconstruction:

```bash
python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/paper-replay \
  --runs 2 \
  --controller-audit
```

`--work-dir` must be new or empty. The command exits nonzero on an archive checksum mismatch, a missing or duplicate retained candidate, any submitted-row mismatch, a preflight issue, a cross-run difference, or a failed optional controller audit.

A successful two-run command ends with a summary of this form:

```text
[paper-replay] PASS: 2/2 construction runs matched all 532 submitted static and final rows; cross-run equality=true; preflight issues=0; controller=0/532 accomplished
```

The complete machine-readable record is written to:

```text
data/local-build/paper-replay/submission_replay_report.json
```

The orchestrator loads the submitted bundle as a read-only expected-output oracle, but never passes those rows to preprocessing, candidate generation, or episode construction. Comparisons occur only after each reconstructed split has been written. The command:

1. verifies the raw archives, normalized metadata contract, and retained-row contract;
2. decodes and indexes every usable telemetry stream;
3. writes a fresh read-only DuckDB tool store and aggregate tables;
4. reruns all nine static-task builders;
5. finds each of the 532 retained candidate identities exactly once;
6. creates clarification, revision, quality, commitment, and evidence turns;
7. executes read-only tools to derive every phase gold answer;
8. applies typed contract repairs and runs preflight validation;
9. compares the reconstructed static and final rows with the submitted release;
10. repeats the static-to-final construction in a second clean run directory;
11. optionally reruns the frozen construction-exclusion controller.

## Output Layout

```text
data/local-build/paper-replay/
  tool-store/
    tool_store.duckdb
    point_inventory.parquet
    tool_ready_points.parquet
    raw_stream_index.parquet
    quality_metrics.parquet
    daily_aggregates.parquet
    weekly_aggregates.parquet
    monthly_aggregates.parquet
    calendar_profiles.parquet
    stream_previews.parquet
  run_1/
    static/
    e2e/
    agentic/
    canonical-seed/
    canonical-seed-core/
    final/
  run_2/
    static/
    e2e/
    agentic/
    canonical-seed/
    canonical-seed-core/
    final/
  submission_replay_report.json
```

The final benchmark used by the runners is:

```text
data/local-build/paper-replay/run_1/final/
```

## How Raw BTS Becomes a Database

The raw input contains one timestamp/value history per stream UUID. The retained metadata contract maps each UUID to a site, Brick point class, equipment, and location. `bts_agentbench.preprocess.preprocess_raw_archives` joins those two inputs and writes queryable tables.

| Table | Rows | Purpose |
|---|---:|---|
| `point_inventory` | 19,665 | All metadata streams and semantic labels |
| `tool_ready_points` | 14,422 | Metadata streams with usable raw telemetry |
| `raw_stream_index` | 14,422 | ZIP member, time range, and observation count |
| `quality_metrics` | 14,422 | Coverage, sampling interval, gaps, duplicates, NaNs |
| `daily_aggregates` | 16,199,756 | Per-stream daily statistics |
| `weekly_aggregates` | 2,354,644 | Monday-bounded weekly statistics |
| `monthly_aggregates` | 548,797 | Calendar-month statistics |
| `calendar_profiles` | 2,419,416 | Hour-of-week profiles |
| `stream_previews` | 14,422 | Small deterministic stream previews |
| `entities` | 22,997 | Normalized Brick entities |
| `relations` | 26,749 | Normalized semantic relationships |

The runtime opens `tool_store.duckdb` read-only and exposes nine tools: point resolution, point listing, exact/nearest observation lookup, window aggregation, pairwise comparison, ranking, stream quality, and window-bounded quality.

No missing observation is synthesized. Exact lookup requires timestamp equality. Nearest lookup minimizes absolute time distance and reports the selected timestamp and offset. Windows are inclusive at the start and exclusive at the end.

Inspect an aggregate table directly with DuckDB:

```python
import duckdb

db = "data/local-build/paper-replay/tool-store/tool_store.duckdb"
con = duckdb.connect(db, read_only=True)
rows = con.execute(
    """
    SELECT stream_id, window_start, mean_value
    FROM daily_aggregates
    WHERE site_id = 'BTS_C'
    ORDER BY stream_id, window_start
    LIMIT 5
    """
).fetchall()
print(rows)
con.close()
```

Use the same read-only runtime used by task construction and agent evaluation:

```python
from bts_agentbench.runtime import ToolStoreRuntime

runtime = ToolStoreRuntime(
    "data/local-build/paper-replay/tool-store/tool_store.duckdb"
)
point = runtime.execute_tool(
    "resolve_point",
    {
        "site_id": "BTS_C",
        "point_class": "Air_Differential_Pressure_Sensor",
        "equipment_label": "BTS_C Zone 005",
    },
)
reading = runtime.execute_tool(
    "lookup_observation",
    {
        "stream_id": point["stream_id"],
        "timestamp": "2022-02-03T07:03:23.640000+00:00",
        "mode": "exact",
    },
)
print(reading)
runtime.close()
```

That lookup returns the timestamp and value used in the static and phase gold shown above.

## Run Each Construction Stage Manually

The one-command replay is recommended. These commands expose the boundary between stages for auditing or adaptation.

### Stage 1: raw telemetry to tool store

Exact paper replay uses the checked normalized metadata under `data/source/bts-processed-catalog/`:

```bash
python scripts/preprocess_tool_store.py \
  --raw-dir ./data/local-build/raw \
  --processed-dir ./data/source/bts-processed-catalog \
  --out-dir ./data/local-build/manual/tool-store
```

For a new metadata corpus, normalize CSV and Brick TTL inputs first:

```bash
python scripts/build_catalog.py \
  --meta-dir ./path/to/metadata \
  --out-dir ./data/local-build/manual/processed-catalog
```

The maintained metadata compiler sorts files, RDF triples, entities, relations, and candidate targets. The exact paper replay does not replace the submitted normalized mapping because historical unordered RDF traversal cannot reproduce every old first-target choice.

### Stage 2: tool store to 532 static tasks

```bash
python scripts/generate_scenario_benchmark.py \
  --tool-store-db ./data/local-build/manual/tool-store/tool_store.duckdb \
  --raw-dir ./data/local-build/raw \
  --out-dir ./data/local-build/manual/static \
  --heldout-site-id BTS_C \
  --selection-contract ./provenance/submission_static_selection.jsonl
```

Nine family builders query the tool store for eligible points, timestamps, windows, pairs, or candidate groups. Each candidate is materialized as an executable task containing:

- a user query;
- canonical and acceptable read-only tool paths;
- a tool-derived gold answer;
- required evidence streams;
- answer, tool-process, evidence, and protocol checks;
- selection and difficulty metadata.

The selection contract does not contain final conversations or model responses. It identifies the fixed candidates retained in the paper release. Replay fails if a retained identity is missing or matches more than one regenerated candidate.

### Stage 3: static tasks to agent episodes

```bash
python scripts/build_bts_canonical_final.py \
  --static-dir ./data/local-build/manual/static \
  --e2e-out-dir ./data/local-build/manual/e2e \
  --agentic-out-dir ./data/local-build/manual/agentic \
  --canonical-seed-out-dir ./data/local-build/manual/canonical-seed \
  --canonical-seed-core-out-dir ./data/local-build/manual/canonical-seed-core \
  --final-out-dir ./data/local-build/manual/final \
  --tool-store-db ./data/local-build/manual/tool-store/tool_store.duckdb \
  --corpus-name bts \
  --submission-compatible \
  --skip-controller-audit
```

This stage deterministically:

1. masks a recoverable site or time slot when clarification is required;
2. derives the simulator reply from the static task;
3. renders the initial operator request;
4. adds family-specific goal revisions and evidence follow-ups;
5. executes the read-only telemetry tools for each phase;
6. attaches phase gold answers and the final reporting action;
7. aligns streams, windows, fallback policy, quality decisions, and evidence through typed repairs;
8. records every transformation in `generation_history`;
9. runs the final contract preflight.

## Inspect Any Scenario

Show a submitted scenario without rebuilding the dataset:

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051
```

Compare it with a reconstructed final directory:

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --replay-dir ./data/local-build/paper-replay/run_1/final
```

Export the same trace as Markdown or JSON:

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --replay-dir ./data/local-build/paper-replay/run_1/final \
  --format markdown \
  --output ./trace.md

python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --format json \
  --output ./trace.json
```

Representative submitted IDs for all nine families:

| Family | Scenario ID |
|---|---|
| Point disambiguation | `test_point_disambiguation_00003` |
| Day mean lookup | `test_day_mean_lookup_00003` |
| Relative 24-hour mean lookup | `test_relative_24h_mean_lookup_00003` |
| Window mean lookup | `test_window_mean_lookup_00003` |
| Window pairwise comparison | `test_window_pairwise_compare_00003` |
| Window rank | `test_window_rank_00003` |
| Timestamp value lookup | `test_timestamp_value_lookup_00051` |
| Timestamp nearest lookup | `test_timestamp_nearest_lookup_00051` |
| Quality gate | `test_quality_gate_00051` |

## Arguments and What They Change

### Exact replay arguments

| Argument | Meaning | Safe to change for paper replay? |
|---|---|:---:|
| `--raw-dir` | Location of the three raw BTS ZIP files | Yes |
| `--work-dir` | New output directory | Yes |
| `--runs` | Number of clean static-to-final repetitions | Yes |
| `--controller-audit` | Also rerun the frozen exclusion controller | Yes |
| `--tool-store-db` | Use an existing tool store instead of rebuilding raw telemetry | Yes, but skips raw preprocessing |
| `--processed-catalog-dir` | Normalized semantic mapping | No; defines the release input |
| `--selection-contract` | Which 532 candidate identities and splits are retained | No; defines the release sample |
| `--static-reference` | Expected submitted static files | No; verifier input |
| `--submitted-dataset-bundle` | Expected submitted final files | No; verifier input |
| `--submitted-source-bundle` | Submitted source snapshot | No; integrity input |

The replay command requires either `--raw-dir` or `--tool-store-db`, never both. `--work-dir` must be new or empty; the script will not delete an existing nonempty directory.

### Building a different benchmark

`scripts/generate_scenario_benchmark.py` supports:

- repeated `--family FAMILY` to build selected task families;
- repeated `--heldout-site-id SITE` to choose held-out sites;
- omitting `--selection-contract` to select a new release rather than reconstruct the paper release.

The family row targets and balancing limits are declared in `src/bts_agentbench/scenario_benchmark.py` as `train_target`, `dev_target`, `test_target`, `balance_key_fn`, and `max_per_balance_key`. Changing those values creates a new benchmark and must not be reported as a replay of the submitted 532 rows.

After selecting a new fixed release, freeze its identities explicitly:

```bash
python scripts/build_submission_selection_contract.py \
  --static-dir ./path/to/new-static \
  --output ./path/to/new-selection.jsonl \
  --summary ./path/to/new-selection-summary.json
```

## Dataset Families

| Family | Train | Dev | Test | Total |
|---|---:|---:|---:|---:|
| Point disambiguation | 40 | 10 | 10 | 60 |
| Day mean lookup | 40 | 10 | 10 | 60 |
| Relative 24-hour mean lookup | 40 | 10 | 10 | 60 |
| Window mean lookup | 36 | 7 | 10 | 53 |
| Window pairwise comparison | 40 | 10 | 10 | 60 |
| Window rank | 40 | 10 | 10 | 60 |
| Timestamp value lookup | 40 | 10 | 10 | 60 |
| Timestamp nearest lookup | 40 | 10 | 10 | 60 |
| Quality gate | 40 | 10 | 9 | 59 |
| **Total** | **356** | **87** | **89** | **532** |

## Run an Agent

First reconstruct the exact final directory, then set the benchmark and tool-store paths:

```bash
export BTS_BENCHMARK_DIR="$PWD/data/local-build/paper-replay/run_1/final"
export BTS_TOOL_STORE_DB="$PWD/data/local-build/paper-replay/tool-store/tool_store.duckdb"
```

Run one retained runner configuration:

```bash
export OPENAI_API_KEY=...
bash runners/gpt55_bts.sh

export OPENROUTER_API_KEY=...
bash runners/gemini31pro_bts_openrouter.sh
bash runners/opus47_bts_openrouter.sh
```

Provider calls are not part of deterministic construction replay. The exact prompts, tool loop, output caps, stopping rules, fixed paid-run traces, and scoring fields are documented in [`MODEL_EVALUATION.md`](MODEL_EVALUATION.md).

## Recorded Reconstruction Evidence

The checked reports contain two independent raw preprocessing executions and three complete static-to-final builds:

- all 11 exported logical tool-store files matched between the two raw builds;
- all 532 submitted rows were regenerated in every complete episode build;
- all nine families matched row for row in both recorded replay runs;
- preflight reported zero coded contract issues;
- the frozen construction-exclusion controller accomplished 0/532 rows on the exact replay output.

The human-readable family table and representative traces are in [`replay/PAPER_SUBMISSION_REPLAY.md`](replay/PAPER_SUBMISSION_REPLAY.md). Machine-readable records are in [`replay/paper_submission_replay_report.json`](replay/paper_submission_replay_report.json) and [`replay/independent_raw_replays_report.json`](replay/independent_raw_replays_report.json). File hashes are reported there as secondary integrity evidence.

The controller result verifies the predefined construction exclusion rule. It is not presented as an independent model baseline or a general measure of difficulty.

## Repository Map

| Path | Contents |
|---|---|
| `src/bts_agentbench/` | Metadata, raw preprocessing, runtime tools, family builders, contracts, and evaluator |
| `scripts/replay_paper_submission.py` | One-command exact raw-to-final replay |
| `scripts/trace_scenario.py` | Human-readable row-level replay trace |
| `data/source/bts-meta/` | Retained CSV and Brick metadata inputs |
| `data/source/bts-processed-catalog/` | Submitted normalized metadata contract |
| `provenance/submission_static_selection.jsonl` | Fixed 532 retained candidate identities |
| `provenance/submission_repair_profile.json` | Typed repair counts and lineage invariants |
| `release/submitted-static-reference/` | Exact submitted static tasks |
| `release/submitted-dataset-bundle.zip` | Exact 532 final rows and retained model outputs |
| `release/submitted-source-bundle.zip` | Exact supplementary source snapshot |
| `examples/REPLAY_TRACE.md` | Complete raw-to-static-to-agentic trace plus the actual retained agent conversation for one test row |
| `reports/model-runs/` | Fixed GPT, Gemini, and Claude paid-run traces |
| `replay/controller-witnesses/` | Fresh controller witness for each submitted row |
| `replay/` | Checked human- and machine-readable reconstruction reports |

## Detailed Documentation

- [`CONSTRUCTION_WALKTHROUGH.md`](CONSTRUCTION_WALKTHROUGH.md): field-level raw-to-static-to-agentic derivation
- [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md): exact inputs, entry points, outputs, and validation matrix
- [`DATA_SOURCES.md`](DATA_SOURCES.md): upstream attribution, archive sizes, and checksums
- [`PORTABILITY_XAI4HEAT.md`](PORTABILITY_XAI4HEAT.md): common telemetry contract and corpus-specific adapter boundary
- [`MODEL_EVALUATION.md`](MODEL_EVALUATION.md): runner and scorer definitions

## Scope

Construction replay, fixed-trace scoring, and new provider generation are separate claims:

1. fixed inputs and code deterministically reconstruct the submitted benchmark;
2. retained model traces can be rescored against the fixed contracts;
3. new provider responses may vary as hosted models and infrastructure change.

`zero detected issues` means zero findings under the coded preflight checks. It does not mean that automatic validation establishes complete human realism or detects every possible semantic error.

## License

Code is released under MIT. Benchmark artifacts, reports, provenance exports, and documentation are released under CC BY 4.0. The upstream BTS archives are not redistributed; their source and license are listed in [`DATA_SOURCES.md`](DATA_SOURCES.md).
