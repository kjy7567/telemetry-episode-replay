# Telemetry-to-Agent Episode Replay Artifact

This repository reconstructs the 532 BTS benchmark episodes used in the paper submission from checksummed building telemetry and metadata. It contains the construction code, immutable retained-row contract, submitted supplementary bundles, fixed model traces, deterministic audits, and a single raw-to-final replay entry point.

```text
three BTS raw ZIPs + retained normalized CSV/Brick catalog
  -> read-only telemetry tool store and aggregates
  -> submitted static candidate identities (356/87/89)
  -> deterministic interaction contracts
  -> operator-facing multi-turn episodes
  -> phase golds, typed repairs, and verifiers
  -> exact paper-submission JSONL files
```

No LLM is used to construct telemetry values, tool results, gold targets, simulator replies, or scores.

## Verified Result

The exact submission replay currently verifies all 532 rows and zero contract-preflight issues.

| Split | Static rows | Static SHA-256 | Final SHA-256 |
|---|---:|---|---|
| Train | 356 | `65f0384bf97318b628ff9431c8bdbd36a2347fcb0ee4a521169fbf3a22b7d825` | `9e5afdf45fafcd28c408d131216950800717a891b2e530eae15c645db7720a65` |
| Dev | 87 | `294f394147a27eba052d1421b8cff5814cdeeab8246194670a7b4a5b93c72b8d` | `4082a8625ede78bb7528bf544fc8e896bff9dfa929d5f999dad5b64a557339d6` |
| Test | 89 | `1f561e93dbcd748bc1f94aa00827512869c8e6e220b15b3943f6c8a5af45120e` | `a7922313934258dce878a8218ce5bfb87b8628be639a52d279fd5a38304d3867` |

These are raw-file hashes, not only semantic hashes. The replay also records sorted-key canonical JSON hashes and exact object equality.

## Run From Raw Telemetry

Install Python 3.11 dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

Download and checksum the upstream archives:

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
```

Run two complete reconstruction passes:

```bash
python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/paper-submission-replay \
  --runs 2
```

The command validates the submitted normalized metadata contract, rebuilds the tool store, regenerates static candidates, reconstructs every intermediate episode stage twice, runs contract preflight, and compares both outputs byte for byte with the submitted benchmark. It exits nonzero on any mismatch.

To rerun the construction-time controller audit once on the first exact reconstruction, add `--controller-audit`.

## Fast Replay

With an existing tool store:

```bash
python scripts/replay_paper_submission.py \
  --tool-store-db /absolute/path/to/tool_store.duckdb \
  --work-dir ./data/local-build/paper-submission-replay-fast \
  --runs 2
```

This still regenerates and verifies the submitted static and final splits. It skips only metadata and raw telemetry preprocessing.

## Construction Details

[CONSTRUCTION_WALKTHROUGH.md](CONSTRUCTION_WALKTHROUGH.md) follows one real test row from `Site_Caa/2254.pickle` and observation `2022-02-03T07:03:23.640Z = 12.9457` through:

1. metadata and stream resolution;
2. raw quality and aggregate computation;
3. static family selection and gold generation;
4. clarification and operator-surface compilation;
5. phase-level exact/nearest/quality/commitment targets;
6. typed repair, preflight, and exact replay checks.

[REPRODUCIBILITY.md](REPRODUCIBILITY.md) lists every entry point, input, output, and audit boundary. [DATA_SOURCES.md](DATA_SOURCES.md) records upstream attribution, sizes, and checksums.

[PORTABILITY_XAI4HEAT.md](PORTABILITY_XAI4HEAT.md) separates the reusable telemetry contract from the XAI4HEAT-specific seven-signal adapter and states the five-family portability boundary.

## Immutable Selection Contract

`provenance/submission_static_selection.jsonl` freezes the candidate identities retained in the paper. It does not contain final episodes or model outputs. During replay, each family builder recomputes eligible candidates from the fresh tool store and fails unless each frozen identity matches exactly once.

This contract is necessary because the paper evaluates one fixed sample. It also prevents later SQL ordering maintenance from silently replacing training rows while claiming to reproduce the submitted benchmark.

## Artifact Map

| Path | Contents |
|---|---|
| `data/source/bts-meta/` | Retained CSV and Brick metadata inputs |
| `data/source/bts-processed-catalog/` | Checksummed normalized metadata contract used by exact replay |
| `provenance/submission_static_selection.jsonl` | 532 retained candidate identities and digests |
| `release/submitted-static-reference/` | Expected submitted static splits |
| `release/submitted-source-bundle.zip` | Exact source supplementary supplied with the paper |
| `release/submitted-dataset-bundle.zip` | Exact dataset and model-run supplementary supplied with the paper |
| `scripts/replay_paper_submission.py` | Raw-to-final exact replay entry point |
| `provenance/examples/` | Row-level raw/static/final traces |
| `reports/model-runs/` | Fixed paid-run traces and summaries |
| `reports/controller/` | Deterministic construction-exclusion witnesses |
| `human_validation/` | Blind domain-practitioner packet and analysis code |
| `replay/` | Checked machine-readable and human-readable replay reports |

The approximately 18 GB raw archives and generated tool store are not committed. The tool store is rebuilt locally from the attributed raw files and retained normalized metadata contract.

## Validation Boundaries

Three claims are separate.

1. **Construction replay:** fixed inputs and code reconstruct the submitted static and final JSONL files exactly.
2. **Fixed-trace scoring:** a retained model trace is scored programmatically against fixed final, evidence, phase, task, and protocol checks.
3. **Provider generation:** a new API response is not claimed to be deterministic because providers can revise models and infrastructure.

`zero detected issues` means zero findings under the coded contract-preflight checks. It does not assert complete human realism or an error-free corpus.

The deterministic controller is a construction exclusion rule. Its `0/532` result confirms application of that rule; it is not a standalone hardness baseline.

## Paper Test Results

| System | Accomplished | Test rows | Percent |
|---|---:|---:|---:|
| GPT-5.5 | 79 | 89 | 88.8% |
| Gemini 3.1 Pro via OpenRouter | 71 | 89 | 79.8% |
| Claude Opus 4.7 via OpenRouter | 58 | 89 | 65.2% |

The fixed traces, run settings, and scorer decomposition are documented in [MODEL_EVALUATION.md](MODEL_EVALUATION.md). API credentials are not included.

## Submission Snapshot and Maintenance

Exact reconstruction is the primary path. A separate maintenance path makes later tie ordering and two January rank-direction corrections explicit. Maintenance output must not be substituted for the paper snapshot when reproducing paper tables or model traces.

The exact compatibility branch preserves two submitted training contracts whose visible `previous month` wording and executable available-month fallback are not aligned. They do not occur in dev or test. The maintenance path corrects the wording; the submission replay preserves the evaluated artifact.

## Human Validation

`human_validation/` contains a blind two-stage authoring and review protocol. The analyzer rejects incomplete packets and requires at least two practitioners, 18 responses, and two responses per family. Blank templates are not represented as completed validation.

## License

Code is released under MIT. Benchmark artifacts, reports, provenance exports, and documentation are released under CC BY 4.0. The upstream BTS archives are not redistributed; their source and license are listed in [DATA_SOURCES.md](DATA_SOURCES.md).
