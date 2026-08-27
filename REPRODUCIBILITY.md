# Reproducibility and Audit Guide

## Reproducibility Claim

The reproducible object is benchmark construction and deterministic rescoring:

```text
checksummed telemetry + normalized metadata + fixed row identities
  -> read-only tool store
  -> executable static tasks
  -> multi-turn episodes
  -> phase golds, final decisions, evidence, and verifiers
  -> exact public JSONL files
```

The claim does not cover repeated sampling from hosted model APIs. Retained API traces are fixed records and can be rescored; a new provider call may differ.

## Environment

The recorded replay used:

| Dependency | Version |
|---|---|
| Python | 3.11.11 |
| DuckDB | 1.5.0 |
| NumPy | 1.26.4 |
| pandas | 3.0.1 |
| PyArrow | 23.0.1 |
| RDFLib | 7.6.0 |

Install the pinned environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

Raw replay needs about 19 GB for the compressed BTS archives and additional space for two generated tool stores. The raw archives are not included in the repository.

## Fixed Inputs

| Input | Public location | Role |
|---|---|---|
| Three raw BTS ZIP archives | downloaded by `scripts/download_raw_archives.sh` | timestamp/value histories |
| BTS CSV and Brick metadata | `data/source/bts-meta/` | source stream/entity relations |
| Normalized BTS catalog | `data/source/bts-processed-catalog/` | fixed stream, point, equipment, and location mapping |
| Release row selection | `provenance/release_static_selection.jsonl` | 532 retained candidate identities and splits |
| Expected split hashes | `release/release_manifest.json` | exact public static and episode outputs |

The normalized catalog is an explicit release boundary. `scripts/build_catalog.py` is the maintained compiler for new metadata inputs; replay of this release verifies and uses the retained catalog files. The DuckDB tool store is not a retained prerequisite. It is rebuilt from the raw archives and catalog.

Every raw and catalog hash is listed in `DATA_SOURCES.md` and encoded in `scripts/replay_release.py`.

## One-Command Replay

Download the raw archives:

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
```

Run two independent builds:

```bash
python scripts/replay_release.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/release-replay \
  --raw-runs 2 \
  --controller-audit
```

The work directory is checkpointed by stage. Reissuing the same command resumes from complete outputs and does not discard completed raw preprocessing.

The command exits nonzero if any of these conditions fails:

1. raw archive or normalized catalog checksums;
2. expected split row counts;
3. contract preflight;
4. obsolete numeric stage-label scan over generated static and episode records;
5. equality of the 11 sorted logical tool-store exports across raw builds;
6. equality of static and episode split files across builds;
7. equality of each rebuilt split SHA-256 with the public release manifest;
8. controller non-completion when `--controller-audit` is requested.

The retained `replay/release_replay_report.json` records `passed: true`. Two independent raw preprocesses each decoded 14,422 streams. All 11 logical exports matched, both 532-row static builds matched, both 532-row episode builds matched, and every rebuilt split matched the public release file. Contract preflight reported zero findings for both builds.

## Exact Stage Order

The replay orchestrator calls the same construction implementations exposed by the individual entry points:

| Order | Entry point | Main implementation | Output |
|---:|---|---|---|
| 1 | `scripts/build_catalog.py` | `bts_agentbench.catalog.build_catalog` | normalized metadata for a new corpus |
| 2 | `scripts/preprocess_tool_store.py` | `bts_agentbench.preprocess.preprocess_raw_archives` | DuckDB and logical aggregate exports |
| 3 | `scripts/generate_scenario_benchmark.py` | `bts_agentbench.scenario_benchmark.generate_scenario_benchmark` | static tasks |
| 4 | `scripts/build_bts_e2e.py` | `bts_agentbench.bts_e2e.build_bts_e2e` | interaction obligations |
| 5 | `scripts/build_bts_e2e_agentic.py` | `build_agentic_bts_e2e` | operator-facing turns |
| 6 | `scripts/build_canonical_agentic_final.py` | `build_canonical_agentic_final` | phases, golds, and verifiers |
| 7 | `scripts/build_bts_canonical_final.py` | family repair functions and `transform_row` | final episodes |
| 8 | `scripts/audit_bts_canonical_contract.py` | `audit_contract` | preflight report |
| 9 | `scripts/run_bts_explicit_controller_eval.py` | `run_controller_suite` | controller witnesses |

`scripts/replay_release.py` contains no task templates or scoring rules. It binds paths, invokes the stages, records hashes, and compares outputs.

## Output Tree

```text
WORK_DIR/
  raw-run-1/
    tool-store/
    static/
    interaction-contract/
    operator-surface/
    canonical-seed/
    canonical-seed-core/
    final/
  raw-run-2/
    ...same stage directories...
  release_replay_report.json
```

DuckDB container bytes need not match because physical layout is not a canonical serialization. Replay compares all 11 sorted logical exports, then proves that both stores generate the same static and final JSONL bytes.

## Raw Telemetry to Static Tasks

### Metadata and history join

The catalog maps stream UUIDs to site, point class, equipment, and location entities. Raw ZIP members provide timestamp/value histories. Preprocessing admits a tool-ready point only when a metadata stream ID has a matching raw history. It stores ZIP/member lineage, UTC range, observation count, quality metrics, and day/week/month summaries. Missing observations are not imputed.

### Runtime tools

`ToolStoreRuntime` exposes read-only operations:

- `resolve_point` and `list_points`;
- exact or nearest `lookup_observation`;
- `aggregate_window`;
- `compare_window` and `rank_window`;
- `inspect_quality_window`.

### Candidate generation

Family builders use explicit SQL and Python predicates. They choose eligible points, observed timestamps, adjacent intervals, windows, equipment pairs, and ranking groups before rendering language. Canonical tool calls execute against the runtime, and their structured outputs become gold fields. Evidence stores the contributing stream IDs.

The BTS release is fixed by `provenance/release_static_selection.jsonl`. Each record contains family, split, scenario ID, and a digest over the telemetry-backed candidate identity. Replay regenerates candidates and requires each retained identity to match exactly once.

## Static Tasks to Agent Episodes

The conversion is a sequence of typed transformations rather than free-form generation:

1. a family predicate selects a clarification and follow-up topology;
2. an existing site or time field may be masked as a recoverable slot;
3. the simulator answer is rendered from that stored field;
4. operator templates render the initial request and later revision turns;
5. a revision rule may add an adjacent window, alternate point, timestamp fallback, comparison, ranking, or quality operation;
6. added operations execute through `ToolStoreRuntime`;
7. the resulting call, phase gold, final commitment, evidence, and verifier are updated as one contract;
8. each applied stage records its predicate, status, and before/after contract summary in `generation_history`;
9. preflight renders the gold answers through the scorer and rechecks runtime-sensitive targets.

Surface-only wording changes do not touch telemetry outputs. A stage that changes an executable operation must recompute the affected target. No model output participates in construction or retention.

The complete worked row in `CONSTRUCTION_WALKTHROUGH.md` shows how a single raw value becomes an exact lookup, nearest fallback, quality decision, reporting commitment, evidence follow-up, and final score.

## What Equality Covers

Split hash equality covers the complete UTF-8 JSONL bytes, including:

- scenario IDs, split, family, and source backlink;
- initial user message, clarification answers, revisions, and follow-ups;
- canonical and acceptable tool-call sets;
- phase and final gold targets;
- evidence stream IDs;
- task, temporal, grounding, phase, and protocol verifier fields;
- typed transformation history and provenance;
- row order, key order, numeric rendering, and newline serialization.

It is not only a comparison of final answers.

## Validation Matrix

| Check | Verifies | Does not establish |
|---|---|---|
| Raw/catalog checksum | exact fixed inputs | upstream collection validity |
| Selection identity | retained candidate exists once | population representativeness |
| Tool-export equality | logical raw preprocessing equality | DuckDB container-byte equality |
| Static split hash | exact tasks, calls, golds, and order | natural-language realism |
| Contract preflight | coded turn/phase linkage, runtime targets, gold rendering, and prompt alignment | every possible authoring error |
| Final split hash | complete released episode bytes | provider response repeatability |
| Controller audit | declared construction exclusion is satisfied | universal task hardness |
| Trace rescore | fixed model trace receives the retained score | repeated-call variance |

## Retained Model Trace Rescoring

The report in `reports/model-runs/trace_audit.json` was generated with:

```bash
python scripts/audit_model_traces.py \
  --bts-benchmark-dir ./artifacts/bts-agentbench \
  --bts-tool-store-db ./data/local-build/release-replay/raw-run-1/tool-store/tool_store.duckdb \
  --bts-raw-dir ./data/local-build/raw \
  --xai4heat-benchmark-dir ./artifacts/xai4heat-agentbench \
  --xai4heat-tool-store-db ./data/local-build/xai4heat/tool-store/tool_store.duckdb
```

It verifies scenario bindings, first user turns, exact system prompt profiles, and deterministic scorer output. The retained result is 267/267 exact BTS rescoring matches and 41/41 exact XAI4HEAT matches.

## Public Package Verification

```bash
python scripts/build_public_bundles.py --output-dir dist
python scripts/verify_packaged_release.py --dist-dir dist --require-bundles
```

The verifier reconstructs the release manifest from repository files, checks all corpus/model/controller scenario sets, validates the worked example, tests ZIP CRCs and row counts, checks four runner snapshots, and verifies `dist/SHA256SUMS`.
