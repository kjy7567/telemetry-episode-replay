#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_public_bundles import verify_dataset_zip, verify_source_zip  # noqa: E402
from build_release_manifest import build_manifest  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest() -> dict:
    path = REPO_ROOT / "release" / "release_manifest.json"
    retained = json.loads(path.read_text(encoding="utf-8"))
    current = build_manifest()
    if retained != current:
        raise RuntimeError("release manifest does not match repository artifacts")
    if retained.get("manifest_type") != "telemetry-episode-release":
        raise RuntimeError("unexpected release manifest type")
    replay_path = REPO_ROOT / retained["construction_replay"]["path"]
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if not replay.get("passed"):
        raise RuntimeError("recorded release replay did not pass")
    if not replay.get("release_static_splits_exact") or not replay.get("release_final_splits_exact"):
        raise RuntimeError("recorded replay does not match the public release splits")
    return retained


def verify_worked_example() -> None:
    path = REPO_ROOT / "examples" / "REPLAY_TRACE.md"
    text = path.read_text(encoding="utf-8")
    required = (
        "# Replay Trace: `test_timestamp_value_lookup_00051`",
        "## 1. Raw Telemetry Lineage",
        "Site_Caa/2254.pickle",
        "## 3. Gold Tool Trace",
        "## 5. Phase Gold Trace",
        "## 6. Final Gold",
        "## 7. Actual Recorded Agent Conversation",
        "Complete JSON-object equality: **YES**",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise RuntimeError(f"worked replay trace is incomplete: {missing}")


def verify_bundles(dist_dir: Path, *, required: bool) -> dict[str, str] | None:
    source_zip = dist_dir / "source.zip"
    dataset_zip = dist_dir / "dataset.zip"
    sums_path = dist_dir / "SHA256SUMS"
    if not all(path.is_file() for path in (source_zip, dataset_zip, sums_path)):
        if required:
            raise RuntimeError(f"public bundles are missing under {dist_dir}")
        return None
    verify_source_zip(source_zip)
    verify_dataset_zip(dataset_zip)
    expected = {
        line.split(maxsplit=1)[1].strip(): line.split(maxsplit=1)[0]
        for line in sums_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    actual = {"source.zip": sha256(source_zip), "dataset.zip": sha256(dataset_zip)}
    if expected != actual:
        raise RuntimeError(f"bundle checksum mismatch: expected={expected}, actual={actual}")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify public release records and optional ZIP bundles.")
    parser.add_argument("--dist-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--require-bundles", action="store_true")
    args = parser.parse_args()
    manifest = verify_manifest()
    verify_worked_example()
    bundle_hashes = verify_bundles(args.dist_dir, required=args.require_bundles)
    print(
        json.dumps(
            {
                "status": "ok",
                "bts_rows": manifest["corpora"]["bts"]["episode_rows"],
                "xai4heat_rows": manifest["corpora"]["xai4heat"]["episode_rows"],
                "bts_model_traces": 267,
                "xai4heat_model_traces": 41,
                "bundles": bundle_hashes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
