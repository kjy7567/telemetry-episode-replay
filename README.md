# Telemetry-to-Agent Episode Replay

This repository contains the construction code, public artifacts, retained model traces, and replay evidence for BTS-AgentBench. It converts read-only building telemetry into executable static tasks and then into bounded multi-turn agent episodes with clarification, goal revision, timestamp policy, quality-aware reporting, and evidence attribution.

The construction path does not call an LLM. Telemetry values, tool outputs, user follow-ups, phase targets, final decisions, and evidence IDs are produced by fixed code and checked against typed contracts.

```text
BTS metadata + raw timestamp/value archives
  -> normalized stream/entity catalog
  -> read-only DuckDB tool store and aggregate tables
  -> 532 executable static tasks
  -> deterministic interaction composition and typed repair
  -> 532 scored multi-turn episodes
  -> contract preflight, controller audit, and retained model traces
```

The same downstream construction path is applied to XAI4HEAT after a corpus-specific SCADA adapter, producing 204 additional episodes.

## Release Contents

| Artifact | Contents |
|---|---|
| `artifacts/bts-agentbench/` | 356/87/89 train/dev/test episodes across nine families |
| `artifacts/bts-static-tasks/` | The 532 telemetry-backed tasks before interaction composition |
| `artifacts/xai4heat-agentbench/` | 132/31/41 episodes across five applicable families |
| `reports/model-runs/` | 267 BTS traces and 41 XAI4HEAT traces with run configurations |
| `replay/` | Two-build replay report and row-level controller witnesses |
| `provenance/` | Fixed BTS row selection, raw stream lineage, and a worked trace |
| `data/source/` | BTS metadata and the checksummed normalized catalog |

`release/release_manifest.json` records row counts, family counts, paths, byte sizes, and SHA-256 hashes for the public artifacts.

## Quick Start

Install Python 3.11 dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

Verify the checked-in release without downloading raw telemetry:

```bash
python scripts/verify_packaged_release.py
```

Inspect one complete raw-to-static-to-episode lineage together with its retained GPT-5.5 conversation:

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --model-trace reports/model-runs/gpt-5.5/test.jsonl \
  --format markdown
```

The rendered example is retained in [`examples/REPLAY_TRACE.md`](examples/REPLAY_TRACE.md).

## Exact Replay From Raw Telemetry

The three BTS archives total about 19 GB. Download and checksum them:

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
```

Run two independent raw-to-episode builds and the controller audit:

```bash
python scripts/replay_release.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/release-replay \
  --raw-runs 2 \
  --controller-audit
```

The command is resumable. A completed tool store, static split, or episode split is reused only when all files required for that stage are present; otherwise that stage is rebuilt. It performs the following checks:

1. verifies all three raw archive hashes and the normalized catalog hashes;
2. rebuilds two independent read-only tool stores from the raw archives;
3. reconstructs static candidates and resolves the 532 identities in `provenance/release_static_selection.jsonl`;
4. composes the interaction contract, operator surface, phase targets, final target, evidence, and verifiers;
5. runs contract preflight and scans generated records for obsolete numeric stage labels;
6. compares all 11 sorted logical tool-store exports across the two raw builds;
7. compares both static and final split files across builds;
8. compares every rebuilt split hash with `release/release_manifest.json`;
9. optionally reruns the fixed construction-exclusion controller.

The retained result in `replay/release_replay_report.json` records exact matches for all 11 tool-store exports, all six static/final cross-run comparisons, and all 12 run-to-release split comparisons. Both builds produced 356/87/89 rows with zero contract-preflight findings.

## What Happens at Each Stage

| Stage | Entry point | Input | Output |
|---|---|---|---|
| Metadata normalization | `scripts/build_catalog.py` | BTS CSV and Brick metadata | streams, entities, relations, and stream targets |
| Raw preprocessing | `scripts/preprocess_tool_store.py` | normalized catalog and raw ZIP members | DuckDB runtime plus quality and aggregate tables |
| Static task construction | `scripts/generate_scenario_benchmark.py` | read-only tool store and selection contract | executable family tasks and tool-derived golds |
| Interaction contract | `scripts/build_bts_e2e.py` | static tasks | clarification and follow-up obligations |
| Operator surface | `scripts/build_bts_e2e_agentic.py` | interaction contracts | deterministic operator-facing turns |
| Phase composition | `scripts/build_canonical_agentic_final.py` | static tasks, turns, and tool store | ordered phases, calls, golds, evidence, and verifiers |
| Family repair and release | `scripts/build_bts_canonical_final.py` | composed episodes and tool store | aligned final episodes and preflight report |
| Full replay | `scripts/replay_release.py` | all fixed inputs above | two checked builds and one replay report |

The normalized BTS catalog is a fixed release input, not hidden local state. The DuckDB tool store is always an output of raw preprocessing. `DATA_SOURCES.md` records the raw archive and catalog hashes.

## How Programmatic Interaction Construction Works

The pipeline does not invent open-ended dialogue. It starts from a task whose arguments and answer are already executable, then applies declared predicates:

- a missing site or time slot can be withheld only when the static task contains the recoverable value;
- the simulator returns that exact value when the agent asks for it;
- a family rule can add an adjacent window, alternate point, exact-to-nearest fallback, or quality inspection;
- any added telemetry operation is executed against the same read-only runtime;
- its output updates the phase call, phase gold, final commitment, evidence, and verifier together;
- surface templates render equipment labels, point classes, dates, and policy wording from typed fields;
- preflight renders each stored gold through the scorer and rechecks runtime-sensitive values.

For example, a raw observation of `12.9457` at `2022-02-03T07:03:23.640Z` becomes an exact timestamp task. The episode initially withholds the timestamp, supplies it through clarification, asks for the nearest observation at `07:03`, checks the surrounding week's quality, requests a report-or-abstain decision, and finally asks for the stream ID. Every value and timestamp in those phases is re-executed from the source stream. See [`CONSTRUCTION_WALKTHROUGH.md`](CONSTRUCTION_WALKTHROUGH.md) for the complete transition.

## Determinism Boundary

Construction and scoring are deterministic for the fixed inputs and pinned environment. A release row is reproduced only when the complete serialized JSONL matches its recorded SHA-256, which covers wording, turns, tool calls, phase golds, final action, evidence, verifiers, construction history, row order, and serialization.

New hosted-model generations are outside that boundary. Provider services can change. The retained traces are therefore preserved and can be deterministically rescored against the clean release. See [`MODEL_EVALUATION.md`](MODEL_EVALUATION.md).

## XAI4HEAT Portability

`src/bts_agentbench/xai4heat.py` maps row-oriented district-heating SCADA tables into the normalized site/stream/point/equipment/timestamp/value representation. `src/bts_agentbench/tabular_corpus.py` builds the read-only tool store. The same static builder, interaction, phase, repair, audit, runner, and scorer code is then reused for the five families supported by that corpus.

The released XAI4HEAT artifact contains 204 episodes. Its held-out 41-row test split has retained results of 0/41 for the construction-exclusion controller and 41/41 for GPT-5.5. The measured portability boundary is between these two telemetry corpora; it is not a claim about unrelated event-log structures. Details and commands are in [`PORTABILITY_XAI4HEAT.md`](PORTABILITY_XAI4HEAT.md).

## Model Runners

Four retained configurations are provided:

```bash
bash runners/gpt55_bts.sh
bash runners/gemini31pro_bts_openrouter.sh
bash runners/opus47_bts_openrouter.sh
bash runners/gpt55_xai4heat.sh
```

Each wrapper calls `scripts/run_bts_e2e_openai_eval.py`. Its adjacent `run_config.json` records endpoint mode, prompt profile, output cap, seed handling, turn budget, timeout, and invocation count. API keys are required only for new provider calls, not for release verification or trace inspection.

## Public ZIP Bundles

Build deterministic packages:

```bash
python scripts/build_public_bundles.py --output-dir dist
python scripts/verify_packaged_release.py --dist-dir dist --require-bundles
```

- `dist/source.zip` contains construction, replay, audit, scoring, and runner source plus fixed metadata contracts and documentation.
- `dist/dataset.zip` contains the 532 BTS episodes, 204 XAI4HEAT episodes, static tasks, controller witnesses, all four retained model runs, and all four runners.
- `dist/SHA256SUMS` records both bundle hashes.

Both archives unpack under the same `telemetry-episode-replay/` directory and are required to remain below 200 MiB.

## Documentation

- [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md): fixed inputs, commands, output tree, and validation matrix
- [`CONSTRUCTION_WALKTHROUGH.md`](CONSTRUCTION_WALKTHROUGH.md): one complete raw-to-scored-episode example
- [`ARTIFACTS.md`](ARTIFACTS.md): repository and ZIP artifact map
- [`DATA_SOURCES.md`](DATA_SOURCES.md): upstream attribution and checksums
- [`MODEL_EVALUATION.md`](MODEL_EVALUATION.md): retained configurations and score definitions
- [`PORTABILITY_XAI4HEAT.md`](PORTABILITY_XAI4HEAT.md): second-corpus adapter and shared code path

## License and Attribution

Repository code is distributed under `LICENSE`. BTS source data remains under its upstream CC BY 4.0 terms and is not redistributed here. XAI4HEAT users must follow its upstream terms. See `DATA_SOURCES.md` before redistributing derived artifacts.
