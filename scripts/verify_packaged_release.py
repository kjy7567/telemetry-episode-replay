#!/usr/bin/env python
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
MODEL_DIRS = (
    "gpt-5.5",
    "gemini-3.1-pro-openrouter",
    "claude-opus-4.7-openrouter",
)
SUBMITTED_DATASET_SHA256 = (
    "70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76"
)
SUBMITTED_SOURCE_SHA256 = (
    "9c5502b3718113fee812818e71b47c23ed54ec1f694d37c35ea29686b2c64496"
)


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


def verify_manifest_file(record: dict, label: str) -> Path:
    path = REPO_ROOT / record["path"]
    if not path.exists():
        raise RuntimeError(f"{label} is missing: {path}")
    if path.stat().st_size != int(record["bytes"]):
        raise RuntimeError(f"{label} byte count mismatch")
    if sha256(path) != record["sha256"]:
        raise RuntimeError(f"{label} hash mismatch")
    return path


def verify_submitted_bundles(
    manifest: dict,
) -> tuple[dict[str, set[str]], str, str]:
    dataset_record = manifest["submitted_dataset_snapshot"]
    dataset_path = verify_manifest_file(dataset_record, "submitted dataset snapshot")
    dataset_hash = sha256(dataset_path)
    if dataset_hash != SUBMITTED_DATASET_SHA256:
        raise RuntimeError("submitted dataset snapshot is not the paper snapshot")

    final_ids_by_split: dict[str, set[str]] = {}
    all_ids: set[str] = set()
    with zipfile.ZipFile(dataset_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submitted dataset snapshot CRC failure")
        for split in SPLITS:
            record = manifest["paper_submission_final"][split]
            payload = archive.read(record["zip_member"])
            rows = load_jsonl_bytes(payload)
            ids = {str(row["scenario_id"]) for row in rows}
            if len(rows) != int(record["rows"]) or len(ids) != len(rows):
                raise RuntimeError(f"submitted final row or ID mismatch: {split}")
            if any(row.get("split") != split for row in rows):
                raise RuntimeError(f"submitted final split field mismatch: {split}")
            if len(payload) != int(record["bytes"]):
                raise RuntimeError(f"submitted final byte count mismatch: {split}")
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise RuntimeError(f"submitted final hash mismatch: {split}")
            if all_ids.intersection(ids):
                raise RuntimeError(f"duplicate submitted final IDs across splits: {split}")
            all_ids.update(ids)
            final_ids_by_split[split] = ids

    source_record = manifest["submitted_source_snapshot"]
    source_path = verify_manifest_file(source_record, "submitted source snapshot")
    source_hash = sha256(source_path)
    if source_hash != SUBMITTED_SOURCE_SHA256:
        raise RuntimeError("submitted source snapshot is not the paper snapshot")
    with zipfile.ZipFile(source_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submitted source snapshot CRC failure")
    return final_ids_by_split, dataset_hash, source_hash


def verify_static_and_selection(
    manifest: dict, final_ids_by_split: dict[str, set[str]]
) -> int:
    static_ids: set[str] = set()
    for split in SPLITS:
        record = manifest["paper_submission_static"][split]
        path = REPO_ROOT / record["path"]
        rows = load_jsonl(path)
        ids = {str(row["scenario_id"]) for row in rows}
        if len(rows) != int(record["rows"]) or len(ids) != len(rows):
            raise RuntimeError(f"submitted static row or ID mismatch: {split}")
        if ids != final_ids_by_split[split]:
            raise RuntimeError(f"submitted static/final ID mismatch: {split}")
        if path.stat().st_size != int(record["bytes"]):
            raise RuntimeError(f"submitted static byte count mismatch: {split}")
        if sha256(path) != record["sha256"]:
            raise RuntimeError(f"submitted static hash mismatch: {split}")
        static_ids.update(ids)

    selection_record = manifest["paper_submission_selection_contract"]
    selection_path = REPO_ROOT / selection_record["path"]
    selection_rows = load_jsonl(selection_path)
    if len(selection_rows) != int(selection_record["rows"]):
        raise RuntimeError("selection-contract row count mismatch")
    if sha256(selection_path) != selection_record["sha256"]:
        raise RuntimeError("selection-contract hash mismatch")
    selection_ids: set[str] = set()
    identity_hashes: set[str] = set()
    for row in selection_rows:
        scenario_id = str(row["scenario_id"])
        digest = str(row["selection_identity_sha256"])
        if scenario_id in selection_ids or digest in identity_hashes:
            raise RuntimeError("duplicate scenario or identity in selection contract")
        if selection_identity_sha256(row["selection_identity"]) != digest:
            raise RuntimeError(f"selection identity digest mismatch: {scenario_id}")
        selection_ids.add(scenario_id)
        identity_hashes.add(digest)
    if selection_ids != static_ids:
        raise RuntimeError("selection contract and submitted static ID sets differ")
    return len(static_ids)


def verify_catalog_and_repairs(manifest: dict) -> None:
    for name, record in manifest["paper_submission_normalized_catalog"].items():
        verify_manifest_file(record, f"normalized catalog {name}")

    repair_record = manifest["paper_submission_repair_profile"]
    repair_path = verify_manifest_file(repair_record, "repair profile")
    repair = json.loads(repair_path.read_text(encoding="utf-8"))
    if int(repair["row_count"]) != 532:
        raise RuntimeError("repair-profile row count mismatch")
    if any(int(count) != 532 for count in repair["lineage_invariants"].values()):
        raise RuntimeError("repair-profile lineage invariant failed")


def verify_replay_reports(manifest: dict) -> tuple[int, int]:
    replay_path = verify_manifest_file(
        manifest["paper_submission_replay_report"], "paper replay report"
    )
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if not replay["passed"] or not replay["cross_run_exact_match"]:
        raise RuntimeError("paper replay report did not pass")
    if replay["tool_store_build"]["mode"] != "raw_archives_to_fresh_tool_store":
        raise RuntimeError("paper replay report was not built from raw archives")
    if len(replay["runs"]) != 2:
        raise RuntimeError("paper replay report must contain two episode builds")
    if (
        replay["selection_contract_sha256"]
        != manifest["paper_submission_selection_contract"]["sha256"]
        or replay["submitted_source_bundle_sha256"]
        != manifest["submitted_source_snapshot"]["sha256"]
        or replay["submitted_dataset_bundle_sha256"]
        != manifest["submitted_dataset_snapshot"]["sha256"]
    ):
        raise RuntimeError("paper replay fixed-input hash mismatch")
    for run in replay["runs"]:
        if not run["passed"] or int(run["preflight_issue_count"]) != 0:
            raise RuntimeError(f"paper replay run failed: {run['run']}")
        for split in SPLITS:
            static = run["static"][split]
            final = run["final"][split]
            if (
                not static["exact_file_match"]
                or static["sha256"]
                != manifest["paper_submission_static"][split]["sha256"]
                or not final["exact_file_match"]
                or not final["exact_json_object_match"]
                or final["sha256"]
                != manifest["paper_submission_final"][split]["sha256"]
            ):
                raise RuntimeError(
                    f"paper replay output mismatch: run {run['run']} {split}"
                )

    independent_path = verify_manifest_file(
        manifest["independent_raw_replays_report"],
        "independent raw replay report",
    )
    independent = json.loads(independent_path.read_text(encoding="utf-8"))
    required_flags = (
        "passed",
        "raw_archive_inventory_match",
        "logical_tool_store_files_exact",
        "fixed_input_hashes_match",
        "downstream_split_hashes_match",
    )
    if not all(bool(independent[name]) for name in required_flags):
        raise RuntimeError("independent raw replay comparison failed")
    if int(independent["logical_tool_store_file_count"]) != 11:
        raise RuntimeError("independent logical-export coverage mismatch")
    if int(independent["total_exact_episode_builds"]) != 3:
        raise RuntimeError("independent episode-build coverage mismatch")
    if any(
        not record["exact_file_match"]
        for record in independent["logical_tool_store_files"].values()
    ):
        raise RuntimeError("independent logical tool-store export mismatch")
    return len(replay["runs"]), int(independent["total_exact_episode_builds"])


def verify_controller(
    manifest: dict, final_ids_by_split: dict[str, set[str]]
) -> int:
    audit_path = verify_manifest_file(
        manifest["paper_submission_controller_audit"], "controller audit"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (
        int(audit["totals"]["scenario_count"]) != 532
        or int(audit["totals"]["accomplished_count"]) != 0
    ):
        raise RuntimeError("controller audit total mismatch")

    failure_path = verify_manifest_file(
        manifest["paper_submission_controller_failure_analysis"],
        "controller failure analysis",
    )
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    if int(failure["row_count"]) != 532:
        raise RuntimeError("controller failure-analysis row count mismatch")

    witness_count = 0
    for split in SPLITS:
        record = manifest["paper_submission_controller_witnesses"][split]
        path = verify_manifest_file(record, f"controller witness {split}")
        rows = load_jsonl(path)
        ids = {str(row["scenario_id"]) for row in rows}
        if len(rows) != int(record["rows"]) or ids != final_ids_by_split[split]:
            raise RuntimeError(f"controller witness coverage mismatch: {split}")
        if any(row.get("label") == "accomplished" for row in rows):
            raise RuntimeError(f"controller accomplished a submitted row: {split}")
        witness_count += len(rows)
    return witness_count


def verify_model_traces(final_test_ids: set[str], submitted_test_sha256: str) -> None:
    for model_dir in MODEL_DIRS:
        rows = load_jsonl(
            REPO_ROOT / "reports" / "model-runs" / model_dir / "test.jsonl"
        )
        ids = {str(row["scenario_id"]) for row in rows}
        if len(rows) != 89 or ids != final_test_ids:
            raise RuntimeError(f"model trace coverage mismatch: {model_dir}")

    audit = json.loads(
        (REPO_ROOT / "reports" / "model-runs" / "trace_audit.json").read_text(
            encoding="utf-8"
        )
    )
    if audit["submitted_test_sha256"] != submitted_test_sha256:
        raise RuntimeError("model trace audit submitted-test hash mismatch")
    if int(audit["submitted_test_rows"]) != 89:
        raise RuntimeError("model trace audit row count mismatch")
    if any(
        not record.get("submitted_contract_binding_verified")
        for record in audit["models"].values()
    ):
        raise RuntimeError("model trace contract binding is not verified")


def verify_replay_trace(manifest: dict) -> None:
    record = manifest["human_readable_replay_trace"]
    path = verify_manifest_file(record, "human-readable replay trace")
    text = path.read_text(encoding="utf-8")
    required_fragments = (
        f"# Replay Trace: `{record['scenario_id']}`",
        "## 1. Raw Telemetry Lineage",
        "Site_Caa/2254.pickle",
        "## 3. Gold Tool Trace",
        "## 5. Phase Gold Trace",
        "## 6. Final Gold",
        "## 7. Actual Recorded Agent Conversation",
        "- Model: `gpt-5.5`",
        "- Label: `accomplished`",
        "What exact timestamp should I use for the reading?",
        "I used stream_id c24589e8_a1f3_4529_b409_5a56761c9d20",
        "Thanks, that's what I needed. ###STOP###",
        "Complete JSON-object equality: **YES**",
    )
    if any(fragment not in text for fragment in required_fragments):
        raise RuntimeError("human-readable replay trace is incomplete")
    model_rows = load_jsonl(
        REPO_ROOT / "reports" / "model-runs" / "gpt-5.5" / "test.jsonl"
    )
    model_row = next(
        row
        for row in model_rows
        if row["scenario_id"] == record["scenario_id"]
    )
    operational_messages = [
        message
        for message in model_row["messages"]
        if message.get("role") != "system"
    ]
    if len(operational_messages) != 21:
        raise RuntimeError("unexpected recorded-conversation message count")
    for message in operational_messages:
        if message.get("role") not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if content and content not in text:
            raise RuntimeError("recorded conversation text is not preserved")


def main() -> None:
    manifest = json.loads(
        (REPO_ROOT / "release" / "release_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if manifest["manifest_version"] != "paper-submission-exact-release-v2":
        raise RuntimeError("unexpected release manifest version")
    final_ids_by_split, dataset_hash, source_hash = verify_submitted_bundles(manifest)
    row_count = verify_static_and_selection(manifest, final_ids_by_split)
    verify_catalog_and_repairs(manifest)
    replay_runs, exact_episode_builds = verify_replay_reports(manifest)
    controller_witness_rows = verify_controller(manifest, final_ids_by_split)
    verify_model_traces(
        final_ids_by_split["test"],
        manifest["paper_submission_final"]["test"]["sha256"],
    )
    verify_replay_trace(manifest)
    if int(manifest["contract_preflight_issue_count"]) != 0:
        raise RuntimeError("contract preflight is not zero")

    print(
        json.dumps(
            {
                "status": "ok",
                "submitted_rows": row_count,
                "submitted_test_rows": len(final_ids_by_split["test"]),
                "model_trace_sets_verified": len(MODEL_DIRS),
                "submitted_dataset_sha256": dataset_hash,
                "submitted_source_sha256": source_hash,
                "paper_replay_runs": replay_runs,
                "independent_raw_builds": 2,
                "exact_episode_builds": exact_episode_builds,
                "human_readable_trace_verified": True,
                "controller_witness_rows": controller_witness_rows,
                "controller_accomplished_rows": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
