# Telemetry-to-Agent Episode Replay Artifact

This repository contains the construction code, frozen inputs, intermediate artifacts, 532 released BTS episodes, deterministic checks, and model traces for a read-only building-telemetry agent benchmark.

The construction path is:

```text
raw telemetry archives + normalized metadata catalog
  -> read-only telemetry tool store
  -> 532 static executable tasks
  -> deterministic multi-turn interaction contracts
  -> operator-facing episode surfaces
  -> phase targets and typed semantic repairs
  -> released benchmark episodes
```

## Reproducibility Boundaries

Three boundaries must not be conflated.

1. **Release replay:** the retained static tasks and a BTS tool store deterministically rebuild the released episodes. `scripts/replay_release.py` performs two clean builds, runs contract preflight on each, and compares exact SHA-256 hashes for `train`, `dev`, and `test`.
2. **Raw-to-static reconstruction:** the three upstream BTS ZIP archives and the frozen normalized catalog rebuild the read-only tool store and static tasks. `scripts/rebuild_from_raw.py` is the single entry point and checks its output against the retained static split hashes.
3. **Model evaluation:** API generation is not claimed to be deterministic. The released model traces are auditable records; scoring a fixed trace against a fixed contract is programmatic.

The normalized catalog used by the released build is retained under `data/source/bts-processed-catalog/`. The metadata compiler is also included and now applies an explicit ordering policy for RDF triples and multi-edge candidates. Recompiling metadata is useful for auditing or adapting the recipe, while exact release reconstruction uses the frozen catalog as its input contract.

Recorded verification reports are retained under `replay/`: `raw_to_static_rebuild_report.json` records an independent rebuild from the three checksummed raw archives, and `replay_report.json` records two clean static-to-release builds driven by the tool store produced in that raw rebuild. The recorded raw rebuild matched all three retained static split hashes; both downstream release builds reported zero preflight issues and matched one another and all three retained release split hashes.

See [REPRODUCIBILITY.md](REPRODUCIBILITY.md) for every stage, input, output, and command.
Upstream file IDs, licensing, sizes, and checksums are listed in [DATA_SOURCES.md](DATA_SOURCES.md).

## Included Artifacts

| Path | Contents |
|---|---|
| `artifacts/bts-static-seed/` | 532 executable single-turn tasks |
| `artifacts/bts-e2e-contract/` | fixed clarification and follow-up contracts |
| `artifacts/bts-agentic-source/` | operator-facing multi-turn surfaces |
| `artifacts/bts-canonical-final/` | released phase-structured episodes |
| `data/source/bts-meta/` | BTS CSV and Brick metadata inputs |
| `data/source/bts-processed-catalog/` | frozen normalized metadata catalog |
| `provenance/` | stream-to-raw and row-level lineage exports |
| `reports/controller/` | construction-time deterministic audit traces |
| `reports/model-runs/` | fixed paid-run traces and summaries |
| `human_validation/` | blind domain-practitioner protocol and response tools |

The approximately 18 GB raw ZIP archives and approximately 1.7 GB materialized tool store are not committed. The download script records the upstream archive URLs, and the tool store is reconstructed locally.

## Environment

Python 3.11 was used for the recorded replay. Install the pinned environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

## One-Command Release Replay

Given an existing tool store:

```bash
export BTS_TOOL_STORE_DB=/absolute/path/to/tool_store.duckdb
make replay
```

The command rebuilds the 532 episodes twice under `data/local-build/replay/`, verifies zero contract-preflight issues, compares both builds byte-for-byte, compares them with the retained release, and writes `replay/replay_report.json`. A mismatch exits nonzero.

When the tool store was produced by `scripts/rebuild_from_raw.py`, bind its build report to the downstream replay:

```bash
python scripts/replay_release.py \
  --tool-store-db ./data/local-build/from-raw/tool-store/tool_store.duckdb \
  --tool-store-build-report ./data/local-build/from-raw/rebuild_report.json
```

The replay rejects a build report whose sibling tool-store path does not match the supplied database or whose static hashes did not match the retained static tasks.

For a tool-store-free integrity check of the packaged artifacts and all three model-trace ID sets:

```bash
make verify
```

## One-Command Raw-to-Final Rebuild

Download and validate the upstream raw archives:

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
```

Run the complete build in a new output directory:

```bash
python scripts/rebuild_from_raw.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/from-raw
```

This command performs the following operations without an LLM:

1. reads the frozen normalized point/equipment/location catalog;
2. validates and indexes raw stream payloads;
3. computes quality statistics and day/week/month aggregates;
4. materializes read-only DuckDB tools;
5. deterministically selects and balances static tasks;
6. compiles fixed interaction contracts and phase targets;
7. applies typed family repairs;
8. runs contract preflight and compares split hashes.

Use `--stop-after-static` to audit only raw-to-static construction. Use `--rebuild-catalog` to recompile the metadata catalog with the deterministic current compiler instead of the frozen release catalog.

## Row-Level Traceability

Export the raw archive and member associated with every stream referenced by the release:

```bash
export BTS_TOOL_STORE_DB=/absolute/path/to/tool_store.duckdb
make lineage
```

Inspect one complete row:

```bash
make trace SCENARIO_ID=test_timestamp_value_lookup_00051
```

The trace links raw archive/member records, the static query and executable calls, the final multi-turn episode, gold phase targets, evidence, verifiers, and generation history. A checked example is in `provenance/examples/`.

## Static Task Selection

All family builders execute ordered DuckDB queries and then apply fixed split targets. `BTS_C` is held out for test; non-held-out candidates are assigned train/dev in a deterministic 4:1 sequence. Eight families target `40/10/10` train/dev/test rows. `quality_gate` targets `40/10/9`, and `window_mean_lookup` retains 53 valid rows after constraints, producing `356/87/89 = 532` rows overall.

Selection is not uniform random sampling. Each family applies explicit eligibility rules and caps repeated combinations of point class, quarter, decision, and location type. The exact SQL, ordering, balance keys, and per-key caps are in `src/bts_agentbench/scenario_benchmark.py` and summarized in `REPRODUCIBILITY.md`.

## Contract and Scoring Checks

Each episode contains:

- canonical and acceptable tool-call paths;
- required clarification slots and deterministic user replies;
- ordered goal revisions and policy/quality turns;
- phase-level gold answers;
- final answer, evidence, task, and protocol verifiers;
- generation and repair history.

`scripts/audit_bts_canonical_contract.py` checks schema, phase/turn alignment, gold consistency, evidence references, verifier fields, and executable tool arguments. The reported `0 issues` means no issue covered by those checks was found; it is not a claim of complete semantic or human realism.

The deterministic controller is a construction-time exclusion audit. Rows that its fixed shortcuts fully solved were excluded. Consequently, `0/532` is evidence that the exclusion rule was applied, not an independent estimate of benchmark difficulty.

## Released Test Results

| System | Accomplished | Total | Percent |
|---|---:|---:|---:|
| GPT-5.5 | 79 | 89 | 88.8% |
| Gemini 3.1 Pro via OpenRouter | 71 | 89 | 79.8% |
| Claude Opus 4.7 via OpenRouter | 58 | 89 | 65.2% |

The corresponding fixed traces and family summaries are under `reports/model-runs/`. API credentials are not included.
Exact harness behavior, retained run configurations, and copyable provider commands are documented in [MODEL_EVALUATION.md](MODEL_EVALUATION.md) and `runners/`.

## Domain-Practitioner Validation

The repository includes a two-stage blind authoring and review packet with two cards per family:

```bash
make human-packet
```

Participants first write realistic requests and follow-ups from structured telemetry intents without seeing benchmark wording. They then review the corresponding canonical interaction. `scripts/analyze_human_validation.py` refuses incomplete data and requires at least two practitioners, 18 responses, and two responses per family before producing results. No blank template is represented as completed human validation.

## Maintenance Note

The replay maintenance release makes two construction choices explicit:

- eight training pairwise rows now use stream IDs as the final tie-break for equal-gap candidates;
- two January training rank contracts use the available adjacent month when the requested previous month predates the data and state that direction explicitly.

The dev and 89-row test static splits are byte-identical to the evaluated release. The reported model traces and scores are unchanged.

## License

Source code is released under the MIT License. Benchmark artifacts, reports, provenance exports, and documentation are released under CC BY 4.0. The upstream raw BTS archives are not redistributed; their source, license, and checksums are recorded in `DATA_SOURCES.md`.
