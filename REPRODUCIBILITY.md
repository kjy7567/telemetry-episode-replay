# Reproducibility and Audit Guide

## Stage Map

| Stage | Required input | Entry point | Primary output |
|---|---|---|---|
| Metadata normalization | BTS CSV and Brick files | `scripts/build_catalog.py` | catalog Parquet files |
| Telemetry preprocessing | three raw ZIPs + frozen catalog | `scripts/preprocess_tool_store.py` | `tool_store.duckdb` and aggregate tables |
| Static task construction | tool store + raw ZIP access | `scripts/generate_scenario_benchmark.py` | `train/dev/test.jsonl` static tasks |
| Interaction contract | static tasks | `scripts/build_bts_e2e.py` | clarification and follow-up contracts |
| Operator surface | interaction contracts | `scripts/build_bts_e2e_agentic.py` | operator-facing episodes |
| Canonicalization | static tasks + operator episodes + tools | `scripts/build_canonical_agentic_final.py` | phase-structured seed |
| Typed repair and audit | canonical seed + tools | `scripts/build_bts_canonical_final.py` | released final episodes |
| Two-run replay | retained static tasks + tools | `scripts/replay_release.py` | exact A/B hash report |

`scripts/rebuild_from_raw.py` orchestrates the complete path. `scripts/replay_release.py` isolates the paper's fixed-contract replay boundary and executes it twice.

The checked reports in `replay/raw_to_static_rebuild_report.json` and `replay/replay_report.json` record the two boundaries separately. The former was produced by rebuilding the tool store and 532 static tasks from the three checksummed raw archives; all retained static split hashes matched. The latter records two clean static-to-release builds; A and B matched each other and the retained release for every split, with zero preflight issues in both runs.

## Inputs

### Raw telemetry

`scripts/download_raw_archives.sh` downloads the three upstream files under their expected names:

- `Site_Aaa.zip`
- `Site_Baa.zip`
- `Site_Caa.zip`

Each archive contains per-stream pickle payloads with a stream token, timestamp array, and value array. The downloader resumes partial transfers and runs `ZipFile.testzip()` before accepting a file.

### Metadata

`data/source/bts-meta/` contains the site CSV metadata and Brick graphs. The release also retains their normalized catalog under `data/source/bts-processed-catalog/`. Exact reconstruction uses this frozen catalog so point/equipment/location aliases are part of the construction contract rather than inferred again.

The current metadata compiler sorts RDF triples, graph edges, and relation rows. Its purpose is deterministic adaptation to a new input corpus. Because the originally retained catalog is a frozen release input, compiler maintenance does not silently alter the released task population.

## Raw to Static

Telemetry preprocessing performs these deterministic operations:

1. join normalized stream metadata to raw ZIP members by normalized stream UUID;
2. parse timestamps as UTC and values as `float64`;
3. compute point count, median positive sampling step, longest gap, observed fraction, NaN/zero/constant fractions, and duplicate timestamp fraction;
4. compute day, Monday-bounded week, calendar-month, and hour-of-week summaries;
5. write Parquet tables and expose them through read-only DuckDB tools.

Static builders then query the tool store for eligible points and windows. Candidate queries contain explicit `ORDER BY` clauses. `take_by_split` consumes this ordered stream, holds `BTS_C` out as test, assigns every fifth non-test candidate to dev, and stops at fixed family targets. Balance caps prevent one point class/quarter/decision/location combination from filling a split.

| Family | Target train/dev/test | Balance key | Maximum per key train/dev/test |
|---|---:|---|---:|
| Point disambiguation | 40/10/10 | point class | 12/4/4 |
| Day mean | 40/10/10 | point class, quarter | 4/2/2 |
| Relative 24h mean | 40/10/10 | point class, quarter | 4/2/2 |
| Window mean | 40/10/10 target; 53 retained | point class, quarter | 4/2/2 |
| Pairwise compare | 40/10/10 | point class, quarter | 4/2/2 |
| Window rank | 40/10/10 | point class, location type, quarter | 3/2/2 |
| Exact timestamp | 40/10/10 | point class, quarter | 4/2/2 |
| Nearest timestamp | 40/10/10 | point class, quarter | 4/2/2 |
| Quality gate | 40/10/9 | decision, point class, quarter | 4/2/2 |

Gold answers are direct outputs of the same read-only tools named in `canonical_tool_calls`. Exact timestamp tasks require equality at the requested timestamp. Nearest tasks minimize absolute timestamp distance and record the offset. Ranking uses descending mean and fixed top-k. Ties follow deterministic stream ordering. Quality gates apply the thresholds stored by the runtime to exact requested windows.

## Static to Final Episodes

The lifting stage does not call an LLM. For each static row it deterministically:

1. selects an interaction mode from family and temporal eligibility;
2. masks only the site or time slots declared by that mode;
3. attaches fixed simulator answers for missing slots;
4. adds family-specific goal revision, timestamp policy, quality decision, reporting commitment, and evidence turns;
5. executes the required tools to materialize phase gold answers;
6. writes acceptable tool paths and task/protocol verifiers;
7. applies typed family repairs and records each construction stage in `generation_history`.

Repairs are constrained transformations over typed fields. They may realign a time window, stream reference, phase target, required field, or final commitment when an earlier conversion made those fields inconsistent. The final preflight rechecks the repaired row; repair is not accepted solely because a script completed.

## Exact Replay

With a tool store available:

```bash
export BTS_TOOL_STORE_DB=/absolute/path/to/tool_store.duckdb
python scripts/replay_release.py
```

The replay script creates independent `run_a` and `run_b` directories. For each run it reconstructs all intermediate stages from the retained static layer, checks `contract_preflight_report.json`, counts rows, and hashes each split. It exits nonzero unless:

- both preflights report zero covered issues;
- A and B hashes match for all splits;
- A matches the retained release hashes.

Use `--controller-audit` to recompute controller witnesses as part of both runs. It is omitted by default because it is an additional exclusion audit, not part of episode semantics.

## Trace One Scenario

```bash
python scripts/export_release_stream_lineage.py \
  --tool-store-db "$BTS_TOOL_STORE_DB"

python scripts/trace_scenario.py test_timestamp_value_lookup_00051 \
  --output provenance/examples/test_timestamp_value_lookup_00051.json
```

The resulting JSON exposes the raw ZIP/member, static task, canonical calls, acceptable alternatives, final episode, phase targets, evidence, verifier, and complete generation history for the same `scenario_id`.

## Model Traces

The runner code and fixed output traces are retained under `scripts/` and `reports/model-runs/`. Provider sampling, infrastructure, and model revisions can change an API response, so rerunning a provider is not part of the deterministic construction claim. Given a fixed trace, the benchmark evaluator applies the fixed final-answer, evidence, phase, task, and protocol checks programmatically.
