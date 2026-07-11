#!/usr/bin/env python
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from build_submission_replay_audit import verify_recorded_audit


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
SUBMITTED_DATASET_SHA256 = "70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76"
SUBMITTED_SOURCE_SHA256 = "9c5502b3718113fee812818e71b47c23ed54ec1f694d37c35ea29686b2c64496"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_jsonl_bytes(payload: bytes) -> list[dict]:
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def selection_identity_sha256(identity: dict) -> str:
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    submitted_record = manifest["submitted_dataset_snapshot"]
    submitted_path = REPO_ROOT / submitted_record["path"]
    submitted_hash = sha256(submitted_path)
    if submitted_hash != submitted_record["sha256"] or submitted_hash != SUBMITTED_DATASET_SHA256:
        raise RuntimeError("submitted dataset snapshot hash mismatch")
    with zipfile.ZipFile(submitted_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submitted dataset snapshot CRC failure")
        expected = {
            f"dataset_bundle/bts_agentbench_532/{split}.jsonl" for split in SPLITS
        }
        if not expected.issubset(archive.namelist()):
            raise RuntimeError("submitted dataset snapshot is missing BTS split files")
        for split in SPLITS:
            record = manifest["paper_submission_final"][split]
            payload = archive.read(record["zip_member"])
            rows = load_jsonl_bytes(payload)
            if len(rows) != int(record["rows"]):
                raise RuntimeError(f"submitted final row count mismatch: {split}")
            if len(payload) != int(record["bytes"]):
                raise RuntimeError(f"submitted final byte count mismatch: {split}")
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise RuntimeError(f"submitted final hash mismatch: {split}")

    submitted_source_record = manifest["submitted_source_snapshot"]
    submitted_source_path = REPO_ROOT / submitted_source_record["path"]
    submitted_source_hash = sha256(submitted_source_path)
    if (
        submitted_source_hash != submitted_source_record["sha256"]
        or submitted_source_hash != SUBMITTED_SOURCE_SHA256
    ):
        raise RuntimeError("submitted source snapshot hash mismatch")
    with zipfile.ZipFile(submitted_source_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submitted source snapshot CRC failure")

    selection_record = manifest["paper_submission_selection_contract"]
    selection_path = REPO_ROOT / selection_record["path"]
    selection_rows = load_jsonl(selection_path)
    if len(selection_rows) != int(selection_record["rows"]):
        raise RuntimeError("paper submission selection-contract row count mismatch")
    if sha256(selection_path) != selection_record["sha256"]:
        raise RuntimeError("paper submission selection-contract hash mismatch")
    scenario_selection_ids: set[str] = set()
    identity_hashes: set[str] = set()
    for row in selection_rows:
        scenario_id = str(row["scenario_id"])
        digest = str(row["selection_identity_sha256"])
        if scenario_id in scenario_selection_ids:
            raise RuntimeError(f"duplicate selection scenario ID: {scenario_id}")
        if digest in identity_hashes:
            raise RuntimeError(f"duplicate selection identity: {digest}")
        if selection_identity_sha256(row["selection_identity"]) != digest:
            raise RuntimeError(f"selection identity digest mismatch: {scenario_id}")
        scenario_selection_ids.add(scenario_id)
        identity_hashes.add(digest)

    submitted_static_ids: set[str] = set()
    for split in SPLITS:
        record = manifest["paper_submission_static"][split]
        path = REPO_ROOT / record["path"]
        rows = load_jsonl(path)
        if len(rows) != int(record["rows"]):
            raise RuntimeError(f"paper submission static row count mismatch: {split}")
        if path.stat().st_size != int(record["bytes"]):
            raise RuntimeError(f"paper submission static byte count mismatch: {split}")
        if sha256(path) != record["sha256"]:
            raise RuntimeError(f"paper submission static hash mismatch: {split}")
        submitted_static_ids.update(str(row["scenario_id"]) for row in rows)
    if submitted_static_ids != scenario_selection_ids:
        raise RuntimeError("paper submission static and selection-contract ID sets differ")

    for name, record in manifest["paper_submission_normalized_catalog"].items():
        path = REPO_ROOT / record["path"]
        if path.stat().st_size != int(record["bytes"]):
            raise RuntimeError(f"paper submission catalog byte count mismatch: {name}")
        if sha256(path) != record["sha256"]:
            raise RuntimeError(f"paper submission catalog hash mismatch: {name}")

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
    submission_replay_audit = verify_recorded_audit(REPO_ROOT)
    print(
        json.dumps(
            {
                "status": "ok",
                "final_rows": len(scenario_ids),
                "test_rows": len(final_test_ids),
                "model_trace_sets_verified": 3,
                "archive_sha256": archive_record["sha256"],
                "submitted_snapshot_sha256": submitted_hash,
                "submitted_source_snapshot_sha256": submitted_source_hash,
                "paper_submission_selection_rows": len(selection_rows),
                "paper_submission_catalog_files": len(
                    manifest["paper_submission_normalized_catalog"]
                ),
                "submission_replay_audit": submission_replay_audit,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
