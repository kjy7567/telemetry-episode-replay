# Public Artifact Guide

The release is organized into two user-facing bundles and one raw-data dependency.

| Item | Contents | Requires API access? |
|---|---|:---:|
| `source.zip` | Source, pinned environment, replay wrapper, fixed metadata/selection contracts, controller witnesses, replay reports, runner profiles, and documentation | No |
| `dataset.zip` | 532 BTS episodes, 204 XAI4HEAT episodes, retained BTS and XAI4HEAT model traces, controller outputs, manifests, and four runner snapshots | No |
| BTS raw archives | Three upstream ZIP files identified by URL, size, and SHA-256 in `DATA_SOURCES.md` | Download only |

## Verify the packaged evidence

From the source bundle root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
make verify
```

This checks the submitted code/data snapshot hashes, 532 final rows, all 267 BTS and 41 XAI4HEAT model traces, the row-level replay example, 532 controller witnesses, two recorded raw preprocesses, three exact episode builds, and every retained system prompt against its public runner profile.

Build deterministic public bundles into a new output directory with:

```bash
OUTPUT_DIR=dist make bundles
```

## Reconstruct from raw telemetry

```bash
./scripts/download_raw_archives.sh ./data/local-build/raw
python scripts/replay_paper_submission.py \
  --raw-dir ./data/local-build/raw \
  --work-dir ./data/local-build/paper-replay \
  --runs 2 \
  --controller-audit
```

The wrapper contains no construction or scoring logic. It invokes the released stage implementations, records every intermediate directory, and compares newly written train/dev/test files with the paper artifact only after construction.

## Runner map

| Corpus | Model | Wrapper | Test rows |
|---|---|---|---:|
| BTS | GPT-5.5 | `runners/gpt55_bts.sh` | 89 |
| BTS | Gemini 3.1 Pro | `runners/gemini31pro_bts_openrouter.sh` | 89 |
| BTS | Claude Opus 4.7 | `runners/opus47_bts_openrouter.sh` | 89 |
| XAI4HEAT | GPT-5.5 | `runners/gpt55_xai4heat.sh` | 41 |

Provider calls are separate from construction replay. The retained traces can be audited and rescored without an API key.
