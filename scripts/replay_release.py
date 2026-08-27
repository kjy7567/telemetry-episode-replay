#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bts_agentbench.preprocess import preprocess_raw_archives  # noqa: E402
from bts_agentbench.scenario_benchmark import generate_scenario_benchmark  # noqa: E402
from build_bts_canonical_final import run_explicit_controller_audit  # noqa: E402


SPLITS = ("train", "dev", "test")
EXPECTED_ROWS = {"train": 356, "dev": 87, "test": 89}
RAW_ARCHIVES = ("Site_Aaa.zip", "Site_Baa.zip", "Site_Caa.zip")
RAW_SHA256 = {
    "Site_Aaa.zip": "ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f",
    "Site_Baa.zip": "fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8",
    "Site_Caa.zip": "fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b",
}
CATALOG_SHA256 = {
    "catalog_summary.json": "a9ebb46dc7293fc17230d7d26a8f00b847b23136a6cbf29749db2b547cfcb722",
    "entities.parquet": "fb63ebf4b4cfd63893fa508afacafa1240f2636a9e3a7c0c5725407958613731",
    "relations.parquet": "4a512fc1bdcc93dbf3e822458eb9193cb576dfadafe45f44d4e7298607d530d3",
    "stream_targets.parquet": "7785971531d03a9f80e2c9620fc34d7c828f9892adbad962d8820b3c95599994",
    "streams.parquet": "3e848eb68be296ca39756aeb9ecb6ea5038ab9788df272be898fc27c822e23b7",
}
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
INTERNAL_VERSION_PATTERN = re.compile(
    r"(?:\b(?:canonical|dal)-v\d+\b|\bv\d+\b|[A-Za-z][A-Za-z0-9_-]*[_-]v\d+[A-Za-z0-9_-]*)",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(bool(line.strip()) for line in handle)


def stage_complete(root: Path, names: tuple[str, ...]) -> bool:
    return all((root / name).is_file() for name in names)


def verify_raw(raw_dir: Path) -> list[dict[str, Any]]:
    records = []
    for name in RAW_ARCHIVES:
        path = raw_dir / name
        if not path.is_file():
            raise SystemExit(f"missing raw archive: {path}")
        digest = sha256(path)
        if digest != RAW_SHA256[name]:
            raise SystemExit(f"raw checksum mismatch: {path}")
        records.append({"name": name, "bytes": path.stat().st_size, "sha256": digest})
    return records


def verify_catalog(catalog_dir: Path) -> list[dict[str, Any]]:
    records = []
    for name, expected_digest in CATALOG_SHA256.items():
        path = catalog_dir / name
        if not path.is_file():
            raise SystemExit(f"missing normalized catalog file: {path}")
        digest = sha256(path)
        if digest != expected_digest:
            raise SystemExit(f"normalized catalog checksum mismatch: {path}")
        records.append({"name": name, "bytes": path.stat().st_size, "sha256": digest})
    return records


def build_final(static_dir: Path, tool_store_db: Path, run_dir: Path) -> None:
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "build_bts_canonical_final.py"),
        "--static-dir", str(static_dir),
        "--e2e-out-dir", str(run_dir / "interaction-contract"),
        "--agentic-out-dir", str(run_dir / "operator-surface"),
        "--canonical-seed-out-dir", str(run_dir / "canonical-seed"),
        "--canonical-seed-core-out-dir", str(run_dir / "canonical-seed-core"),
        "--final-out-dir", str(run_dir / "final"),
        "--tool-store-db", str(tool_store_db),
        "--corpus-name", "bts",
        "--skip-controller-audit",
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def version_findings(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted([*root.rglob("*.json"), *root.rglob("*.jsonl")]):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in INTERNAL_VERSION_PATTERN.finditer(line):
                findings.append(
                    {
                        "file": str(path.relative_to(root)),
                        "line": line_number,
                        "value": match.group(0),
                    }
                )
    return findings


def split_report(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for split in SPLITS:
        path = root / f"{split}.jsonl"
        count = jsonl_count(path)
        if count != EXPECTED_ROWS[split]:
            raise RuntimeError(f"unexpected {split} row count: {count}")
        report[split] = {"rows": count, "bytes": path.stat().st_size, "sha256": sha256(path)}
    return report


def release_hashes(manifest_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_type") != "telemetry-episode-release":
        raise RuntimeError(f"unexpected release manifest: {manifest_path}")
    corpus = manifest["corpora"]["bts"]
    static = {
        split: str(corpus["static_task_splits"][split]["sha256"])
        for split in SPLITS
    }
    final = {
        split: str(corpus["episode_splits"][split]["sha256"])
        for split in SPLITS
    }
    return static, final


def portable_controller_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report[key]
        for key in (
            "report_version",
            "audit_round",
            "controller",
            "totals",
            "by_split",
            "by_family",
            "global_top_issues",
            "conclusion",
        )
        if key in report
    }


def same_files(left: Path, right: Path, names: tuple[str, ...]) -> dict[str, bool]:
    return {name: sha256(left / name) == sha256(right / name) for name in names}


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild and verify the public BTS release from raw telemetry.")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--raw-runs", type=int, default=2)
    parser.add_argument("--selection-contract", type=Path, default=REPO_ROOT / "provenance" / "release_static_selection.jsonl")
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=REPO_ROOT / "release" / "release_manifest.json",
        help="Public release manifest containing expected static and episode split hashes.",
    )
    parser.add_argument(
        "--processed-catalog-dir",
        type=Path,
        default=REPO_ROOT / "data" / "source" / "bts-processed-catalog",
        help="Checksummed normalized metadata contract used to construct the release.",
    )
    parser.add_argument("--controller-audit", action="store_true")
    args = parser.parse_args()

    if args.raw_runs < 2:
        raise SystemExit("--raw-runs must be at least 2 for independent replay verification")
    raw_dir = args.raw_dir.resolve()
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_inventory = verify_raw(raw_dir)
    catalog_dir = args.processed_catalog_dir.resolve()
    catalog_inventory = verify_catalog(catalog_dir)
    expected_static, expected_final = release_hashes(args.release_manifest.resolve())
    os.environ["BTS_RAW_DIR"] = str(raw_dir)

    runs: list[dict[str, Any]] = []
    for index in range(1, args.raw_runs + 1):
        run_dir = work_dir / f"raw-run-{index}"
        tool_store_dir = run_dir / "tool-store"
        static_dir = run_dir / "static"
        final_dir = run_dir / "final"

        if not stage_complete(tool_store_dir, ("tool_store.duckdb", *LOGICAL_TOOL_STORE_FILES)):
            print(f"[release-replay] raw run {index}: telemetry preprocessing", flush=True)
            preprocess_raw_archives(raw_dir, catalog_dir, tool_store_dir)
        if not stage_complete(static_dir, ("manifest.json", "train.jsonl", "dev.jsonl", "test.jsonl")):
            print(f"[release-replay] raw run {index}: static tasks", flush=True)
            generate_scenario_benchmark(
                tool_store_dir / "tool_store.duckdb",
                static_dir,
                heldout_site_ids=["BTS_C"],
                selection_contract_path=args.selection_contract.resolve(),
            )
        if not stage_complete(final_dir, ("manifest.json", "contract_preflight_report.json", "train.jsonl", "dev.jsonl", "test.jsonl")):
            print(f"[release-replay] raw run {index}: episodes", flush=True)
            build_final(static_dir, tool_store_dir / "tool_store.duckdb", run_dir)

        preflight = json.loads((final_dir / "contract_preflight_report.json").read_text(encoding="utf-8"))
        findings = version_findings(static_dir) + version_findings(final_dir)
        if int(preflight["issue_count"]) != 0 or findings:
            raise RuntimeError(f"release verification failed for raw run {index}: preflight={preflight['issue_count']}, internal_versions={len(findings)}")
        static_report = split_report(static_dir)
        final_report = split_report(final_dir)
        runs.append(
            {
                "run": index,
                "tool_store": {name: sha256(tool_store_dir / name) for name in LOGICAL_TOOL_STORE_FILES},
                "static": static_report,
                "final": final_report,
                "release_static_match": {
                    split: static_report[split]["sha256"] == expected_static[split]
                    for split in SPLITS
                },
                "release_final_match": {
                    split: final_report[split]["sha256"] == expected_final[split]
                    for split in SPLITS
                },
                "preflight_issue_count": 0,
                "internal_version_findings": 0,
            }
        )

    first = work_dir / "raw-run-1"
    second = work_dir / "raw-run-2"
    tool_store_matches = same_files(first / "tool-store", second / "tool-store", LOGICAL_TOOL_STORE_FILES)
    static_matches = same_files(first / "static", second / "static", tuple(f"{split}.jsonl" for split in SPLITS))
    final_matches = same_files(first / "final", second / "final", tuple(f"{split}.jsonl" for split in SPLITS))

    controller = None
    if args.controller_audit:
        controller_report_path = first / "final" / "explicit_controller_audit_report.json"
        witness_root = first / "final" / "explicit_controller_witnesses"
        witness_files = tuple(
            f"{split}/phase_complete_stronger_controller.jsonl" for split in SPLITS
        )
        if stage_complete(witness_root, witness_files) and controller_report_path.is_file():
            controller_full = json.loads(controller_report_path.read_text(encoding="utf-8"))
        else:
            controller_full = run_explicit_controller_audit(
                first / "final",
                first / "tool-store" / "tool_store.duckdb",
            )
        controller = portable_controller_summary(controller_full)

    release_static_matches = {
        f"run-{run['run']}/{split}": bool(run["release_static_match"][split])
        for run in runs
        for split in SPLITS
    }
    release_final_matches = {
        f"run-{run['run']}/{split}": bool(run["release_final_match"][split])
        for run in runs
        for split in SPLITS
    }
    passed = all(
        (
            *tool_store_matches.values(),
            *static_matches.values(),
            *final_matches.values(),
            *release_static_matches.values(),
            *release_final_matches.values(),
        )
    )
    if controller is not None:
        passed = passed and int(controller["totals"]["accomplished_count"]) == 0
    report = {
        "report_type": "raw-to-episode-release-replay",
        "raw_archives": raw_inventory,
        "normalized_catalog": catalog_inventory,
        "normalized_catalog_directory": "data/source/bts-processed-catalog",
        "selection_contract": "provenance/release_static_selection.jsonl",
        "selection_contract_sha256": sha256(args.selection_contract.resolve()),
        "release_reference": "release/release_manifest.json",
        "runs": runs,
        "logical_tool_store_files_exact": tool_store_matches,
        "static_splits_exact": static_matches,
        "final_splits_exact": final_matches,
        "release_static_splits_exact": release_static_matches,
        "release_final_splits_exact": release_final_matches,
        "controller_audit": controller,
        "passed": passed,
    }
    report_path = work_dir / "release_replay_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "passed": passed}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
