# Artifact Map

## Repository Layout

| Path | Purpose |
|---|---|
| `src/bts_agentbench/` | catalog, preprocessing, runtime, builders, simulator, and scorer library |
| `scripts/` | executable construction, replay, audit, evaluation, and packaging entry points |
| `data/source/` | BTS metadata and checksummed normalized catalog |
| `artifacts/bts-static-tasks/` | 532 executable tasks before interaction composition |
| `artifacts/bts-agentbench/` | 532 final BTS episodes |
| `artifacts/xai4heat-static-tasks/` | 204 XAI4HEAT static tasks |
| `artifacts/xai4heat-agentbench/` | 204 final XAI4HEAT episodes |
| `provenance/release_static_selection.jsonl` | fixed BTS retained-row identities |
| `provenance/release_stream_lineage.csv` | 212 release streams mapped to raw ZIP members |
| `replay/release_replay_report.json` | two independent raw-to-episode replay results |
| `replay/*-controller-witnesses/` | row-level deterministic controller outputs |
| `reports/model-runs/` | retained GPT-5.5, Gemini 3.1 Pro, and Claude Opus 4.7 traces |
| `runners/` | four model-run command snapshots |
| `release/release_manifest.json` | paths, row counts, byte sizes, and SHA-256 hashes |

## ZIP Bundles

`python scripts/build_public_bundles.py` creates two deterministic archives under `dist/`.

### `source.zip`

Contains all Python construction and evaluation source, runner wrappers, pinned dependencies, documentation, BTS metadata, the normalized catalog, fixed row-selection contract, stream lineage, replay report, and release manifest. It excludes generated episodes, model traces, raw BTS ZIP archives, local DuckDB files, and intermediate build directories.

### `dataset.zip`

Contains:

- all 532 BTS static tasks and final episodes;
- all 204 XAI4HEAT static tasks and final episodes;
- 267 BTS model traces and 41 XAI4HEAT model traces;
- four run configurations and four shell runners;
- the shared Python runner;
- BTS and XAI4HEAT controller audit reports and row witnesses;
- release selection, stream lineage, replay report, and worked example.

Both ZIPs use the same top-level `telemetry-episode-replay/` directory. Shared manifest, runner, and provenance files are byte-identical; use overwrite mode when combining the archives:

```bash
unzip -oq dist/source.zip -d ./public-artifact
unzip -oq dist/dataset.zip -d ./public-artifact
```

Timestamps and file modes are fixed during packaging, making repeated package builds byte-identical when their inputs are unchanged.

## Verify

```bash
python scripts/build_public_bundles.py --output-dir dist
python scripts/verify_packaged_release.py --dist-dir dist --require-bundles
sha256sum --check dist/SHA256SUMS
```

Verification checks the release manifest, 532/204 row counts, split and scenario-ID alignment, controller witness coverage, retained model trace coverage, four runner files, ZIP CRCs, bundle checksums, and the complete worked trace. Both ZIPs must remain below 200 MiB.

Raw BTS archives are not redistributed. Their download URLs, byte sizes, licenses, and checksums are listed in `DATA_SOURCES.md`.
