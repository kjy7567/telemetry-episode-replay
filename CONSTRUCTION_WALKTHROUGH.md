# Raw Telemetry to Agent Episode: Exact Construction Walkthrough

This document specifies the deterministic construction claim made by this artifact. The claim covers benchmark construction from checksummed BTS telemetry and metadata to the 532 JSONL episodes used in the paper submission. It does not claim that a provider API will reproduce the same model text.

To see the complete process as one readable trace before reading the implementation details, run:

```bash
python scripts/trace_scenario.py test_timestamp_value_lookup_00051
```

The checked output with raw lineage, gold calls, interaction turns, phase golds, final action, and replay equality is also available in [`examples/REPLAY_TRACE.md`](examples/REPLAY_TRACE.md).

## 1. Inputs

The exact reconstruction consumes four kinds of fixed input.

| Input | Role | Integrity boundary |
|---|---|---|
| `Site_Aaa.zip`, `Site_Baa.zip`, `Site_Caa.zip` | Per-stream timestamp/value histories | SHA-256 values in `DATA_SOURCES.md` |
| `data/source/bts-processed-catalog/` | Submitted normalized stream/point/equipment/location mapping | Per-file SHA-256 checks in the replay entry point |
| `provenance/submission_static_selection.jsonl` | Identities of the 532 candidates retained in the submitted benchmark | SHA-256 `2487dce6bbb01bb0ab4e1d5b388ff40cc509ab26082770c867ed085e96ecddd6` |
| `release/submitted-dataset-bundle.zip` | Read-only expected output used exclusively by the verifier | SHA-256 `70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76` |

The orchestrator loads the expected output bytes before construction, but does not pass them to any builder. Raw preprocessing, candidate generation, and episode construction receive only their declared source inputs; the expected bytes are used by `verify_run` after each reconstructed split has been written.

## 2. Metadata Resolution Contract

The paper build normalized the source CSV and Brick graphs into streams, entities, relations, and stream-target tables. Exact replay retains those normalized Parquet files as an input contract because the original graph parser selected the first target for some multi-edge point relationships. That historical first-target choice is not recoverable from unordered RDF graph iteration alone across environments.

The retained catalog is checksummed before raw preprocessing. It contains 19,665 streams, 22,997 entities, and 26,749 relations across `BTS_A`, `BTS_B`, and `BTS_C`.

`scripts/build_catalog.py` and `bts_agentbench.catalog.build_catalog` remain available for auditing and adapting a new corpus. The current compiler performs these operations without an LLM:

1. reads each `Site_*_metadata.csv` file in sorted filename order;
2. normalizes column names and stream UUIDs;
3. parses each Brick TTL graph after sorting RDF triples;
4. maps streams to point entities, equipment, and locations;
5. chooses deterministic primary types and relationship targets;
6. writes normalized Parquet tables and a DuckDB catalog.

The retained paper mapping turns a raw stream UUID into a semantic record such as:

```text
site_id:         BTS_C
stream_id:       c24589e8_a1f3_4529_b409_5a56761c9d20
point_class:     Air_Differential_Pressure_Sensor
equipment_label: BTS_C Zone 005
```

## 3. Raw Telemetry Preprocessing

`bts_agentbench.preprocess.preprocess_raw_archives` joins the normalized catalog to ZIP members by stream UUID. For each stream it:

1. decodes timestamps as UTC and values as floating point numbers;
2. sorts observations by timestamp;
3. records the source ZIP and member name;
4. computes count, median positive sampling interval, longest gap, coverage, duplicate, NaN, zero, and constant-value statistics;
5. computes day, Monday-bounded week, calendar-month, and hour-of-week aggregates;
6. exposes the resulting tables through a read-only DuckDB runtime.

The checked raw build processes 8,345 Site A streams, 730 Site B streams, and 5,347 Site C streams. Fourteen Site B `__MACOSX/._*.pickle` AppleDouble members fail pickle decoding and are recorded in `skipped_members.json`; they are archive metadata, not retained telemetry streams.

The runtime does not synthesize missing observations. `lookup_observation(mode="exact")` requires timestamp equality. `mode="nearest"` minimizes absolute time distance and reports the offset. Window aggregation uses an inclusive start and exclusive end.

### Concrete raw record

The paper test scenario `test_timestamp_value_lookup_00051` is backed by:

```text
archive: Site_Caa.zip
member:  Site_Caa/2254.pickle
stream:  c24589e8_a1f3_4529_b409_5a56761c9d20
```

The decoded member contains 194,563 observations between `2020-05-22T00:17:44.162Z` and `2024-01-18T10:11:25.003Z`. One retained observation is:

```text
timestamp = 2022-02-03T07:03:23.640000+00:00
value     = 12.9457
```

No language model chooses or rewrites this value.

## 4. Static Candidate Construction

`src/bts_agentbench/scenario_benchmark.py` implements nine family builders. Each builder first enumerates eligible points, timestamps, windows, pairs, or candidate groups from the tool store. It then materializes a static task with:

- an operator query;
- canonical and acceptable read-only tool paths;
- a tool-derived gold answer;
- stream evidence;
- final-answer, process, and evidence checks;
- a difficulty proxy and selection metadata.

The candidate population is much larger than the release. The paper records 4,263 point-resolution candidates, 2,193,431 day and relative-window candidates, 315,929 window-mean candidates, 5,989,083 pairwise candidates, 1,084 rank candidates, 2,123 candidates for each timestamp family, and 315 quality candidates before retention controls.

### Why a retained-row contract is required

The submitted benchmark is an immutable sample, not a request to resample a new benchmark on every replay. The original pairwise and rank SQL contained equal-valued candidates whose database order was not a complete release identifier. A later total-order maintenance change selected ten different training rows even though dev and test were unchanged.

Exact paper reconstruction therefore separates two deterministic operations:

1. recompute each family builder's deterministic candidate pool and associated telemetry facts;
2. retain the candidate identities frozen in `submission_static_selection.jsonl`.

Each retained identity contains the family, site, canonical calls, gold answer, and evidence streams plus a SHA-256 digest. During replay, every family builder regenerates candidates from the fresh tool store. The replay fails unless every frozen identity matches exactly one regenerated candidate. It does not copy a submitted final episode or model response.

The contract fixes 532 family ordinals and split assignments:

| Family | Train | Dev | Test | Total |
|---|---:|---:|---:|---:|
| Point disambiguation | 40 | 10 | 10 | 60 |
| Day mean lookup | 40 | 10 | 10 | 60 |
| Relative 24h mean lookup | 40 | 10 | 10 | 60 |
| Window mean lookup | 36 | 7 | 10 | 53 |
| Window pairwise compare | 40 | 10 | 10 | 60 |
| Window rank | 40 | 10 | 10 | 60 |
| Timestamp value lookup | 40 | 10 | 10 | 60 |
| Timestamp nearest lookup | 40 | 10 | 10 | 60 |
| Quality gate | 40 | 10 | 9 | 59 |
| **Total** | **356** | **87** | **89** | **532** |

`BTS_C` is held out for test. The submitted ordinals preserve the original non-test train/dev assignment and family balancing.

The checked selection profile in `provenance/submission_static_selection_summary.json` exposes coverage rather than treating the retained count as sufficient evidence by itself:

| Family | Site rows A/B/C | Unique evidence streams | Point classes | Retained/candidate population |
|---|---:|---:|---:|---:|
| Point disambiguation | 16/34/10 | 60 | 7 | 60/4,263 |
| Day mean lookup | 26/24/10 | 16 | 13 | 60/2,193,431 |
| Relative 24h mean lookup | 26/24/10 | 16 | 13 | 60/2,193,431 |
| Window mean lookup | 22/21/10 | 53 | 23 | 53/315,929 |
| Window pairwise compare | 25/25/10 | 30 | 6 | 60/5,989,083 |
| Window rank | 24/26/10 | 34 | 5 | 60/1,084 |
| Timestamp value lookup | 6/44/10 | 60 | 19 | 60/2,123 |
| Timestamp nearest lookup | 6/44/10 | 60 | 20 | 60/2,123 |
| Quality gate | 30/20/9 | 20 | 5 | 59/315 |

The same profile records per-family temporal ranges, decision counts, and min/median/max difficulty-proxy values. These statistics expose the submitted sample's concentration and are not presented as evidence of random representativeness.

### Ambiguity, ties, and empty results

- single-point families retain only metadata keys that resolve to one stream;
- pairwise candidates require a nonzero mean difference, so a tied comparison is not released as having an arbitrary winner;
- `rank_window` orders by the requested metric and then stream ID, with the declared ascending or descending direction;
- nearest lookup orders by absolute offset and then observation timestamp, choosing the earlier timestamp for an equal offset;
- exact lookup reports no exact match instead of substituting a nearby value;
- empty aggregate/rank candidates are rejected or handled by the declared adjacent-window compatibility branch rather than assigned a fabricated gold.

Acceptable alternative tool paths are generated from explicit equivalences such as `week` versus the same custom interval, swapped left/right resolution followed by the corresponding compare arguments, and exact-then-nearest fallback. They are not authored by a model.

### Concrete static task

The raw observation above becomes this executable contract:

```json
{
  "scenario_id": "test_timestamp_value_lookup_00051",
  "query": "At 07:03 UTC on February 3, 2022, what reading did the air differential pressure reading attached to Zone 005 report in BTS_C?",
  "canonical_tool_calls": [
    {
      "tool_name": "resolve_point",
      "arguments": {
        "site_id": "BTS_C",
        "point_class": "Air_Differential_Pressure_Sensor",
        "equipment_label": "BTS_C Zone 005"
      }
    },
    {
      "tool_name": "lookup_observation",
      "arguments": {
        "stream_id": "$c1.stream_id",
        "timestamp": "2022-02-03T07:03:23.640000+00:00",
        "mode": "exact"
      }
    }
  ],
  "gold_final_answer": {
    "stream_id": "c24589e8_a1f3_4529_b409_5a56761c9d20",
    "observed_timestamp": "2022-02-03T07:03:23.640000+00:00",
    "value": 12.9457,
    "exact_match_found": true
  }
}
```

## 5. Static Task to Interaction Contract

`bts_agentbench.bts_e2e.build_bts_e2e` adds a deterministic interaction contract. Family and temporal predicates choose whether the episode needs site clarification, time clarification, a direct answer, a quality rationale, or an evidence follow-up. The simulator answer is derived from the static row; it is not generated by an LLM.

For the concrete example, the time anchor is masked from the first turn:

```text
Initial user:
Operator handoff: "What was the air differential pressure reading on Zone 005 in BTS_C?"
Use the building tools and ask me for any missing site or time detail before querying.

Required clarification slot: time_reference
Deterministic reply: I mean 07:03:23.64 UTC on February 3, 2022.
```

`scripts/build_bts_e2e_agentic.py` then applies deterministic operator-facing wrappers. This changes presentation, not telemetry facts or gold values.

## 6. Contract to Phase-Structured Episode

`scripts/build_canonical_agentic_final.py` executes the read-only tools and attaches typed interaction phases. Family repair modules then align stream references, windows, policy branches, and reporting commitments.

The example is expanded into four scored phases:

| Phase | User obligation | Recomputed gold |
|---|---|---|
| P1 | Answer the clarified exact timestamp request | exact observation `12.9457` at `07:03:23.640` |
| P2 | Revise to public minute precision | nearest observation `12.9457`, offset `23.64 s` |
| P3 | Judge week-bounded quality | `answer`, coverage `1.0`, gap ratio `1.0563` |
| P4 | Make the reporting commitment | `answer`, reason `nearest_but_acceptable` |

The final episode also requires an evidence answer naming stream `c24589e8_a1f3_4529_b409_5a56761c9d20`.

Typed repairs are deterministic functions over named fields. Examples include:

- replacing a stale phase stream with the winner from the preceding comparison;
- changing an exact timestamp call to an exact-then-nearest acceptable path;
- aligning a quality window with the period named in the user turn;
- deriving `answer` or `abstain` from fixed quality thresholds;
- replacing a provisional final field with a typed reporting commitment.

Every row records the applied stages in `generation_history`. A repair is accepted only if the final contract preflight passes.

### Submitted repair profile

`provenance/submission_repair_profile.json` is computed from the submitted final rows and their retained static sources.

| Typed stage | Modified | No-op | Applied |
|---|---:|---:|---:|
| Timestamp policy surface | 120 | 412 | 0 |
| Nearest contract humanization | 60 | 472 | 0 |
| Timestamp-value revision contract | 60 | 472 | 0 |
| Goal-revision contract | 293 | 239 | 0 |
| Point-target revision contract | 60 | 472 | 0 |
| Goal-revision clarification | 60 | 472 | 0 |
| Controller-proxy repair round | 0 | 532 | 0 |
| Multi-axis composition | 472 | 60 | 0 |
| Reporting-commitment composition | 532 | 0 | 0 |
| Final family-semantics stamp | 0 | 0 | 532 |

All 532 final rows preserve the backing static scenario ID, source static query, task family, and site ID under the coded lineage checks. These counts show what the repair program changed and what it left untouched; they do not replace human semantic-validity review.

## 7. Submission Compatibility and Maintenance

The exact paper snapshot and the later maintenance behavior are intentionally separate.

The submitted artifact used three final deterministic settings that were not bound by one executable command in the submitted source bundle: the final operator wrapper wording, preservation of the existing adjacent-month path for two January training rank rows with no preceding-month data, and final rank metadata insertion order. `--submission-compatible` names and freezes those settings so the paper files can be reconstructed byte for byte.

The two January rows retain the submitted training text even though their executable fallback month is the available following month. They do not occur in dev or test. The ordinary maintenance path states the fallback direction explicitly. Exact replay preserves the paper snapshot; it does not silently replace it with the maintenance correction.

## 8. Validation and Audit

The exact replay performs four distinct checks.

1. **Input integrity:** raw archives, retained normalized catalog files, submitted source bundle, submitted dataset bundle, and selection contract must match their SHA-256 values.
2. **Selection integrity:** every retained static identity must match one and only one freshly generated candidate.
3. **Contract preflight:** schema, phase/turn alignment, gold structure, evidence references, verifier fields, and executable call arguments are checked. `zero detected issues` applies only to these coded checks.
4. **Exact output comparison:** reconstructed static and final JSONL files are compared byte for byte with the retained paper artifacts. Canonical sorted-key JSON hashes are also recorded.

The deterministic controller is a separate construction-exclusion audit. Its `0/532` result confirms that the predefined controller exclusion rule remains satisfied. It is not an independent estimate of task difficulty.

## 9. Run the Complete Replay

```bash
python scripts/replay_paper_submission.py \
  --raw-dir /absolute/path/to/bts/raw \
  --work-dir ./data/local-build/paper-submission-replay \
  --runs 2
```

The command exits nonzero on any input checksum, candidate identity, static hash, final hash, cross-run, or preflight mismatch. Add `--controller-audit` to rerun the controller once on the first exact reconstruction.

For a faster replay with an already reconstructed tool store:

```bash
python scripts/replay_paper_submission.py \
  --tool-store-db /absolute/path/to/tool_store.duckdb \
  --work-dir ./data/local-build/paper-submission-replay-fast \
  --runs 2
```

Provider model calls are outside this deterministic claim. The fixed submitted traces can be rescored programmatically, but a new provider call may vary with model and infrastructure revisions.
