# Reproducibility and Audit Guide

## Claim Scope

The reproducibility claim covers deterministic benchmark construction:

```text
checksummed raw telemetry + retained normalized metadata contract + frozen retained-row identities
  -> static executable tasks
  -> multi-turn agent episodes
  -> phase golds, evidence, verifiers, and final JSONL
```

It excludes repeatability of new provider API generations. Fixed traces are deterministically scorable, but provider models and infrastructure can change.

## System Requirements

- Python `3.11`
- a filesystem with at least 25 GB free for the three raw archives and generated working files
- sufficient memory to process per-stream telemetry and construct the canonical artifacts
- the dependencies pinned in `requirements.lock`

Recorded exact replay environment:

| Package | Version |
|---|---|
| Python | 3.11.11 |
| DuckDB | 1.5.0 |
| NumPy | 1.26.4 |
| pandas | 3.0.1 |
| PyArrow | 23.0.1 |
| RDFLib | 7.6.0 |

Install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

## Fixed Inputs

| Input | Expected path or argument | Verification |
|---|---|---|
| Raw telemetry | `--raw-dir` containing three named ZIP files | size, ZIP integrity, SHA-256 |
| CSV and Brick metadata | `data/source/bts-meta/` | retained repository files |
| Normalized metadata contract | `data/source/bts-processed-catalog/` | per-file SHA-256 |
| Retained-row contract | `provenance/submission_static_selection.jsonl` | SHA-256 and per-row identity digests |
| Submitted static reference | `release/submitted-static-reference/` | split byte comparison |
| Submitted source bundle | `release/submitted-source-bundle.zip` | SHA-256 |
| Submitted dataset bundle | `release/submitted-dataset-bundle.zip` | SHA-256 and final split byte comparison |

The submitted dataset bundle is a verifier input only. Builders do not read its rows while constructing output.

## Stage Map

| Stage | Entry point | Required input | Primary output |
|---|---|---|---|
| Metadata adaptation compiler | `scripts/build_catalog.py` | retained or new CSV/TTL metadata | normalized catalog for a new corpus |
| Submitted metadata verification | `scripts/replay_paper_submission.py` | retained normalized Parquet files | checksum-verified paper mapping |
| Raw telemetry preprocessing | `scripts/preprocess_tool_store.py` | raw ZIPs and catalog | read-only tool store and aggregates |
| Submitted static reconstruction | `scripts/generate_scenario_benchmark.py` | tool store, raw access, selection contract | static `train/dev/test.jsonl` |
| Interaction contract | `scripts/build_bts_e2e.py` | static tasks | clarification and follow-up contracts |
| Operator surface | `scripts/build_bts_e2e_agentic.py` | interaction contracts | deterministic operator-facing episodes |
| Canonical phase construction | `scripts/build_canonical_agentic_final.py` | static tasks, episodes, tool store | phase-structured canonical seed |
| Typed family repair | `scripts/build_bts_canonical_final.py --submission-compatible` | canonical seed and tool store | exact submitted final episodes |
| Contract preflight | `scripts/audit_bts_canonical_contract.py` | final episodes and tool store | issue report |
| Full exact replay | `scripts/replay_paper_submission.py` | all fixed inputs | complete run directories and replay report |

## One-Command Exact Replay

Download the raw archives:

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
```

Build the raw tool store once and the static-to-final path twice:

```bash
python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/paper-submission-replay \
  --runs 2
```

`--work-dir` must be new or empty. The script never deletes a nonempty user-supplied directory.

The command performs these operations in order:

1. checks all raw, source-bundle, dataset-bundle, and selection-contract hashes;
2. verifies the retained normalized metadata contract used by the submitted build;
3. preprocesses the raw ZIP histories into a fresh tool store;
4. enumerates static candidates and resolves all 532 retained identities;
5. writes and byte-checks the submitted static splits;
6. constructs E2E, agentic, canonical-seed, and final stages;
7. runs contract preflight;
8. checks every reconstructed final object and raw JSONL byte against the submitted bundle;
9. repeats steps 4-8 in a second independent run directory;
10. compares run 1 and run 2 byte for byte.

The process exits nonzero after any mismatch.

The checked repository report also compares this fresh tool store with an earlier independent raw preprocessing execution. All 11 sorted Parquet/JSON logical exports match byte for byte, and exact downstream reconstruction succeeds from both stores. The noncanonical DuckDB container bytes are recorded but excluded from the determinism decision; both containers expose the same exported logical tables and produce the same submitted split hashes.

To repeat the complete raw boundary independently, run the command above in two new work directories and compare them:

```bash
python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/raw-replay-a \
  --runs 1

python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/raw-replay-b \
  --runs 1

python scripts/build_independent_replay_report.py \
  --run-a-tool-store ./data/local-build/raw-replay-a/tool-store \
  --run-a-raw-report ./data/local-build/raw-replay-a/submission_replay_report.json \
  --run-a-replay-report ./data/local-build/raw-replay-a/submission_replay_report.json \
  --run-b-tool-store ./data/local-build/raw-replay-b/tool-store \
  --run-b-replay-report ./data/local-build/raw-replay-b/submission_replay_report.json \
  --output ./data/local-build/independent_raw_replays_report.json
```

### Optional controller audit

```bash
python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/paper-submission-replay-with-controller \
  --runs 2 \
  --controller-audit
```

The controller is rerun once against run 1 after both exact builds. Its witnesses are not used to construct gold values.

### Fast replay from an existing tool store

```bash
python scripts/replay_paper_submission.py \
  --tool-store-db /absolute/path/to/tool_store.duckdb \
  --work-dir ./data/local-build/paper-submission-replay-fast \
  --runs 2
```

This skips retained-catalog verification and raw telemetry preprocessing only by accepting an existing tool store. Candidate enumeration, static generation, every episode stage, preflight, and exact final comparison still run.

## Output Tree

The full command writes:

```text
WORK_DIR/
  tool-store/
    tool_store.duckdb
    raw_stream_index.parquet
    quality_metrics.parquet
    daily_aggregates.parquet
    weekly_aggregates.parquet
    monthly_aggregates.parquet
    hour_of_week_profiles.parquet
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

Each stage contains split JSONL files and a manifest. Final also contains `contract_preflight_report.json` and `paper_final_build_report.json`.

## Raw to Static Details

### Metadata normalization boundary

The submitted normalized catalog is retained under `data/source/bts-processed-catalog/` as a versioned exact-replay input. Replay checks and uses its streams, entities, relations, and stream-target Parquet files; `scripts/build_catalog.py` is provided to compile a normalized mapping for a new release.

`bts_agentbench.catalog.build_catalog` is the deterministic adaptation compiler for a new corpus. It sorts source filenames, RDF triples, entity identifiers, relation rows, and candidate targets before writing outputs. Recompiling with that maintained policy is useful for portability experiments, but it is not substituted for the paper's retained mapping.

### Telemetry preprocessing

`bts_agentbench.preprocess.preprocess_raw_archives` indexes raw ZIP members and computes:

- UTC timestamp and numeric value histories;
- point counts and positive sampling intervals;
- longest gaps, coverage, duplicate, NaN, zero, and constant fractions;
- day, week, month, and hour-of-week aggregates.

Read-only runtime methods implement point resolution, list, exact/nearest lookup, aggregation, comparison, ranking, and quality inspection.

### Candidate and gold construction

Each family builder executes explicit DuckDB eligibility queries, constructs canonical tool calls, and derives gold answers from the same tool runtime. Exact and nearest timestamp semantics, inclusive/exclusive windows, ranking order, and quality thresholds are encoded in source rather than delegated to an LLM.

The submitted release is a fixed sample. `submission_static_selection.jsonl` stores a digest over each retained candidate's family, site, canonical calls, gold, and evidence. Replay recomputes candidates and fails on a missing, duplicate, or digest-mismatched identity.

The resulting submitted static hashes are:

| Split | Rows | SHA-256 |
|---|---:|---|
| Train | 356 | `65f0384bf97318b628ff9431c8bdbd36a2347fcb0ee4a521169fbf3a22b7d825` |
| Dev | 87 | `294f394147a27eba052d1421b8cff5814cdeeab8246194670a7b4a5b93c72b8d` |
| Test | 89 | `1f561e93dbcd748bc1f94aa00827512869c8e6e220b15b3943f6c8a5af45120e` |

## Static to Agent Episode Details

Construction is predicate-driven and deterministic:

1. family and time predicates choose an interaction mode;
2. one recoverable site or time slot is masked when clarification is required;
3. the deterministic simulator returns the value already present in the static contract;
4. an operator wrapper renders the initial request;
5. family-specific goal revisions add temporal, comparison, ranking, or quality obligations;
6. read-only tools execute to produce phase gold answers;
7. typed repairs align streams, windows, alternatives, and commitments;
8. evidence and final/phase/task/protocol verifiers are attached;
9. every stage is recorded in `generation_history`.

The final submitted hashes are:

| Split | Rows | SHA-256 |
|---|---:|---|
| Train | 356 | `9e5afdf45fafcd28c408d131216950800717a891b2e530eae15c645db7720a65` |
| Dev | 87 | `4082a8625ede78bb7528bf544fc8e896bff9dfa929d5f999dad5b64a557339d6` |
| Test | 89 | `a7922313934258dce878a8218ce5bfb87b8628be639a52d279fd5a38304d3867` |

The full field-level example is in [CONSTRUCTION_WALKTHROUGH.md](CONSTRUCTION_WALKTHROUGH.md).

## Submission Compatibility Boundary

The submitted component entry points produced the paper artifact. The public release profile binds their order, inputs, and final deterministic settings in one testable command:

- final operator wrapper wording;
- the existing available-month path for two January training rank rows with no preceding-month data;
- final rank metadata insertion order required for byte-identical JSONL.

These settings reproduce the immutable paper files. The ordinary maintenance mode separately corrects the visible month direction in the two affected training rows and uses complete SQL tie ordering. Maintenance files are not used to reproduce paper scores.

## Validation Matrix

| Check | Verifies | Does not verify |
|---|---|---|
| Input checksum | Exact raw and supplementary files | Upstream collection validity |
| Selection identity | Retained candidates exist uniquely after recomputation | Representativeness of the retained sample |
| Static byte hash | Exact submitted static rows and order | Human realism |
| Contract preflight | Coded schema, alignment, gold, evidence, verifier, and tool-argument rules | All possible semantic errors |
| Final object equality | Every JSON value equals the submitted row | Provider response repeatability |
| Final byte hash | Key order, rows, values, and serialization equal submitted JSONL | Model quality |
| Cross-run comparison | Run 1 and run 2 construction outputs are identical | Cross-version API behavior |
| Controller audit | Fixed exclusion controller accomplishes zero retained rows | Independent benchmark hardness |

## Trace One Scenario

After creating a tool store:

```bash
python scripts/export_release_stream_lineage.py \
  --tool-store-db /absolute/path/to/tool_store.duckdb

python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --output provenance/examples/test_timestamp_value_lookup_00051.json
```

The trace contains source ZIP/member records, static calls and gold, final user turns, phase targets, evidence, verifier fields, and generation history for one `scenario_id`.

## Model Evaluation Boundary

Runner code and submitted model outputs are retained under `runners/` and `reports/model-runs/`. A fixed trace is scored by deterministic checks. A fresh provider call may differ because model aliases, serving infrastructure, and sampling behavior can change. Construction replay never calls GPT, Gemini, or Claude.
