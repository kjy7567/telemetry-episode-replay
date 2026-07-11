#!/usr/bin/env python
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    manifest_path = REPO_ROOT / "release" / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenario_ids: set[str] = set()
    for layer in ("static", "final"):
        for split in SPLITS:
            record = manifest[layer][split]
            path = REPO_ROOT / record["path"]
            rows = load_jsonl(path)
            if len(rows) != int(record["rows"]):
                raise RuntimeError(f"row count mismatch: {layer}/{split}")
            if sha256(path) != record["sha256"]:
                raise RuntimeError(f"hash mismatch: {layer}/{split}")
            if layer == "final":
                for row in rows:
                    scenario_id = str(row["scenario_id"])
                    if scenario_id in scenario_ids:
                        raise RuntimeError(f"duplicate scenario_id: {scenario_id}")
                    scenario_ids.add(scenario_id)
                    if row.get("split") != split:
                        raise RuntimeError(f"split mismatch: {scenario_id}")

    archive_record = manifest["archive"]
    archive_path = REPO_ROOT / archive_record["path"]
    if sha256(archive_path) != archive_record["sha256"]:
        raise RuntimeError("release archive hash mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("release archive CRC failure")
    human_archive_record = manifest["human_validation_archive"]
    human_archive_path = REPO_ROOT / human_archive_record["path"]
    if sha256(human_archive_path) != human_archive_record["sha256"]:
        raise RuntimeError("human-validation archive hash mismatch")
    with zipfile.ZipFile(human_archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("human-validation archive CRC failure")

    final_test_ids = {
        row["scenario_id"] for row in load_jsonl(REPO_ROOT / manifest["final"]["test"]["path"])
    }
    for model_dir in (
        "gpt-5.5",
        "gemini-3.1-pro-openrouter",
        "claude-opus-4.7-openrouter",
    ):
        trace_ids = {
            row["scenario_id"]
            for row in load_jsonl(REPO_ROOT / "reports" / "model-runs" / model_dir / "test.jsonl")
        }
        if trace_ids != final_test_ids:
            raise RuntimeError(f"model trace coverage mismatch: {model_dir}")

    if int(manifest["contract_preflight_issue_count"]) != 0:
        raise RuntimeError("contract preflight is not zero")
    print(
        json.dumps(
            {
                "status": "ok",
                "final_rows": len(scenario_ids),
                "test_rows": len(final_test_ids),
                "model_trace_sets_verified": 3,
                "archive_sha256": archive_record["sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
