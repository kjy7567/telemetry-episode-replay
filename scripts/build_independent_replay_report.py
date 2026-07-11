#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SPLITS = ("train", "dev", "test")
LOGICAL_TOOL_STORE_FILES = (
    "calendar_profiles.parquet",
    "daily_aggregates.parquet",
    "monthly_aggregates.parquet",
    "point_inventory.parquet",
    "preprocess_summary.json",
    "quality_metrics.parquet",
    "raw_stream_index.parquet",
    "skipped_members.json",
    "stream_previews.parquet",
    "tool_ready_points.parquet",
    "weekly_aggregates.parquet",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_archive_inventory(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "name": record["name"],
                "size_bytes": int(record["size_bytes"]),
                "sha256": record["sha256"],
            }
            for record in records
        ],
        key=lambda record: record["name"],
    )


def raw_archive_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    if "raw_archives" in report:
        return report["raw_archives"]
    tool_store_build = report.get("tool_store_build", {})
    if tool_store_build.get("mode") == "raw_archives_to_fresh_tool_store":
        return tool_store_build["raw_archives"]
    raise ValueError("report does not describe a fresh raw telemetry build")


def replay_summary(report: dict[str, Any]) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for run in report["runs"]:
        runs.append(
            {
                "run": int(run["run"]),
                "passed": bool(run["passed"]),
                "preflight_issue_count": int(run["preflight_issue_count"]),
                "static": {
                    split: {
                        "sha256": run["static"][split]["sha256"],
                        "expected_sha256": run["static"][split]["expected_sha256"],
                        "exact_file_match": bool(
                            run["static"][split]["exact_file_match"]
                        ),
                    }
                    for split in SPLITS
                },
                "final": {
                    split: {
                        "sha256": run["final"][split]["sha256"],
                        "expected_sha256": run["final"][split]["expected_sha256"],
                        "exact_file_match": bool(
                            run["final"][split]["exact_file_match"]
                        ),
                    }
                    for split in SPLITS
                },
            }
        )
    return {
        "passed": bool(report["passed"]),
        "selection_contract_sha256": report["selection_contract_sha256"],
        "submitted_source_bundle_sha256": report["submitted_source_bundle_sha256"],
        "submitted_dataset_bundle_sha256": report["submitted_dataset_bundle_sha256"],
        "run_count": len(runs),
        "cross_run_exact_match": bool(report["cross_run_exact_match"]),
        "runs": runs,
    }


def all_exact(summary: dict[str, Any]) -> bool:
    return bool(summary["passed"]) and all(
        run["passed"]
        and run["preflight_issue_count"] == 0
        and all(item["exact_file_match"] for item in run["static"].values())
        and all(item["exact_file_match"] for item in run["final"].values())
        for run in summary["runs"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare independent raw tool-store builds and their exact paper replays."
    )
    parser.add_argument("--run-a-tool-store", type=Path, required=True)
    parser.add_argument("--run-a-raw-report", type=Path, required=True)
    parser.add_argument("--run-a-replay-report", type=Path, required=True)
    parser.add_argument("--run-b-tool-store", type=Path, required=True)
    parser.add_argument("--run-b-replay-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw_a = load_json(args.run_a_raw_report)
    replay_a_raw = load_json(args.run_a_replay_report)
    replay_b_raw = load_json(args.run_b_replay_report)
    if replay_b_raw["tool_store_build"]["mode"] != "raw_archives_to_fresh_tool_store":
        raise SystemExit("run B replay report is not a fresh raw tool-store build")

    archives_a = normalized_archive_inventory(raw_archive_records(raw_a))
    archives_b = normalized_archive_inventory(raw_archive_records(replay_b_raw))
    logical_files: dict[str, dict[str, Any]] = {}
    for name in LOGICAL_TOOL_STORE_FILES:
        path_a = args.run_a_tool_store / name
        path_b = args.run_b_tool_store / name
        digest_a = sha256(path_a)
        digest_b = sha256(path_b)
        logical_files[name] = {
            "run_a_bytes": path_a.stat().st_size,
            "run_b_bytes": path_b.stat().st_size,
            "run_a_sha256": digest_a,
            "run_b_sha256": digest_b,
            "exact_file_match": digest_a == digest_b
            and path_a.stat().st_size == path_b.stat().st_size,
        }

    database_a = args.run_a_tool_store / "tool_store.duckdb"
    database_b = args.run_b_tool_store / "tool_store.duckdb"
    database_digest_a = sha256(database_a)
    database_digest_b = sha256(database_b)
    database_container = {
        "run_a_bytes": database_a.stat().st_size,
        "run_b_bytes": database_b.stat().st_size,
        "run_a_sha256": database_digest_a,
        "run_b_sha256": database_digest_b,
        "exact_file_match": database_digest_a == database_digest_b,
        "included_in_pass_decision": False,
        "reason": (
            "DuckDB physical container layout is not a canonical serialization; "
            "determinism is checked on the sorted exported logical tables."
        ),
    }

    replay_a = replay_summary(replay_a_raw)
    replay_b = replay_summary(replay_b_raw)
    fixed_input_hashes_match = all(
        replay_a[key] == replay_b[key]
        for key in (
            "selection_contract_sha256",
            "submitted_source_bundle_sha256",
            "submitted_dataset_bundle_sha256",
        )
    )
    downstream_hashes_match = all(
        replay_a["runs"][0][stage][split]["sha256"]
        == replay_b["runs"][0][stage][split]["sha256"]
        for stage in ("static", "final")
        for split in SPLITS
    )
    report = {
        "report_version": "independent-raw-to-submission-replay-v1",
        "claim_scope": (
            "two independent raw telemetry preprocessing executions followed by "
            "three exact static-to-final paper reconstruction runs"
        ),
        "raw_archive_inventory_match": archives_a == archives_b,
        "raw_archives": archives_b,
        "logical_tool_store_file_count": len(logical_files),
        "logical_tool_store_files": logical_files,
        "logical_tool_store_files_exact": all(
            record["exact_file_match"] for record in logical_files.values()
        ),
        "duckdb_physical_container": database_container,
        "fixed_input_hashes_match": fixed_input_hashes_match,
        "downstream_split_hashes_match": downstream_hashes_match,
        "raw_build_a_replay": replay_a,
        "raw_build_b_replay": replay_b,
        "total_exact_episode_builds": replay_a["run_count"] + replay_b["run_count"],
    }
    report["passed"] = (
        report["raw_archive_inventory_match"]
        and report["logical_tool_store_files_exact"]
        and fixed_input_hashes_match
        and downstream_hashes_match
        and all_exact(replay_a)
        and all_exact(replay_b)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "passed": report["passed"]}, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
