# Public Dataset Bundle

`dataset.zip` unpacks under `telemetry-episode-replay/` and contains:

- `artifacts/bts-agentbench/`: 532 episodes, split 356/87/89;
- `artifacts/bts-static-tasks/`: their executable static sources;
- `artifacts/xai4heat-agentbench/`: 204 episodes, split 132/31/41;
- `artifacts/xai4heat-static-tasks/`: their executable static sources;
- `reports/model-runs/`: three BTS runs and one XAI4HEAT run;
- `replay/`: construction replay and controller evidence;
- `runners/` plus `scripts/run_bts_e2e_openai_eval.py`: all four runner snapshots;
- `provenance/`: release selection, raw stream lineage, and worked example.

`release/release_manifest.json` records row counts and SHA-256 hashes. Combine this archive with `source.zip` using `unzip -o` to obtain the complete executable repository; overlapping runner and manifest files are identical. Raw BTS archives and generated DuckDB stores are not redistributed.
