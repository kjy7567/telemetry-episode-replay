#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bts_agentbench.catalog import build_catalog
from bts_agentbench.preprocess import preprocess_raw_archives
from bts_agentbench.scenario_benchmark import generate_scenario_benchmark


SPLITS = ("train", "dev", "test")
RAW_ARCHIVES = ("Site_Aaa.zip", "Site_Baa.zip", "Site_Caa.zip")
RAW_SHA256 = {
    "Site_Aaa.zip": "ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f",
    "Site_Baa.zip": "fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8",
    "Site_Caa.zip": "fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b",
}
PACKAGES = ("duckdb", "numpy", "openai", "pandas", "pyarrow", "rdflib", "requests")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_hashes(directory: Path) -> dict[str, str]:
    return {split: sha256(directory / f"{split}.jsonl") for split in SPLITS}


def versions() -> dict[str, str]:
    return {package: importlib.metadata.version(package) for package in PACKAGES}


def require_empty(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise SystemExit(f"work directory must be new or empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def raw_inventory(raw_dir: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for name in RAW_ARCHIVES:
        path = raw_dir / name
        if not path.exists():
            raise SystemExit(f"missing raw archive: {path}")
        digest = sha256(path)
        if digest != RAW_SHA256[name]:
            raise SystemExit(f"raw archive checksum mismatch: {path}")
        inventory.append(
            {
                "name": name,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
            }
        )
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild BTS metadata, tool store, static tasks, and final episodes from raw archives."
    )
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument(
        "--meta-dir", type=Path, default=REPO_ROOT / "data" / "source" / "bts-meta"
    )
    parser.add_argument(
        "--processed-catalog-dir",
        type=Path,
        default=REPO_ROOT / "data" / "source" / "bts-processed-catalog",
        help="Frozen normalized catalog used by the released BTS build.",
    )
    parser.add_argument(
        "--rebuild-catalog",
        action="store_true",
        help="Recompile metadata instead of using the frozen release catalog.",
    )
    parser.add_argument(
        "--work-dir", type=Path, default=REPO_ROOT / "data" / "local-build" / "from-raw"
    )
    parser.add_argument(
        "--expected-static-dir",
        type=Path,
        default=REPO_ROOT / "artifacts" / "bts-static-seed",
    )
    parser.add_argument(
        "--expected-final-dir",
        type=Path,
        default=REPO_ROOT / "artifacts" / "bts-canonical-final",
    )
    parser.add_argument(
        "--selection-contract",
        type=Path,
        help="Frozen retained-row contract for reconstructing an immutable submitted release.",
    )
    parser.add_argument("--stop-after-static", action="store_true")
    parser.add_argument("--controller-audit", action="store_true")
    args = parser.parse_args()

    raw_dir = args.raw_dir.resolve()
    meta_dir = args.meta_dir.resolve()
    work_dir = args.work_dir.resolve()
    archive_inventory = raw_inventory(raw_dir)
    require_empty(work_dir)

    if args.rebuild_catalog:
        processed_dir = work_dir / "processed-catalog"
        catalog_summary = build_catalog(meta_dir, processed_dir)
        catalog_mode = "recompiled_deterministic_catalog"
    else:
        processed_dir = args.processed_catalog_dir.resolve()
        catalog_summary = json.loads(
            (processed_dir / "catalog_summary.json").read_text(encoding="utf-8")
        )
        catalog_mode = "frozen_release_catalog"
    tool_store_dir = work_dir / "tool-store"
    static_dir = work_dir / "static-seed"
    final_dir = work_dir / "final"

    preprocess_summary = preprocess_raw_archives(raw_dir, processed_dir, tool_store_dir)
    os.environ["BTS_RAW_DIR"] = str(raw_dir)
    static_manifest = generate_scenario_benchmark(
        tool_store_dir / "tool_store.duckdb",
        static_dir,
        heldout_site_ids=["BTS_C"],
        selection_contract_path=args.selection_contract,
    )
    static_hashes = split_hashes(static_dir)
    expected_static_hashes = split_hashes(args.expected_static_dir.resolve())

    report: dict[str, Any] = {
        "report_version": "raw-to-final-rebuild-v1",
        "raw_archives": archive_inventory,
        "metadata_directory": str(meta_dir),
        "processed_catalog_directory": str(processed_dir),
        "catalog_mode": catalog_mode,
        "python": sys.version.split()[0],
        "packages": versions(),
        "catalog_summary": catalog_summary,
        "preprocess_summary": preprocess_summary,
        "static_manifest": static_manifest,
        "static_split_hashes": static_hashes,
        "expected_static_split_hashes": expected_static_hashes,
        "static_matches_expected": static_hashes == expected_static_hashes,
    }

    if not args.stop_after_static:
        command = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_bts_canonical_final.py"),
            "--static-dir",
            str(static_dir),
            "--e2e-out-dir",
            str(work_dir / "e2e"),
            "--agentic-out-dir",
            str(work_dir / "agentic"),
            "--canonical-seed-out-dir",
            str(work_dir / "canonical-seed"),
            "--canonical-seed-core-out-dir",
            str(work_dir / "canonical-seed-core"),
            "--final-out-dir",
            str(final_dir),
            "--tool-store-db",
            str(tool_store_dir / "tool_store.duckdb"),
            "--corpus-name",
            "bts",
        ]
        if not args.controller_audit:
            command.append("--skip-controller-audit")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
        final_hashes = split_hashes(final_dir)
        expected_final_hashes = split_hashes(args.expected_final_dir.resolve())
        report.update(
            {
                "final_split_hashes": final_hashes,
                "expected_final_split_hashes": expected_final_hashes,
                "final_matches_expected": final_hashes == expected_final_hashes,
            }
        )

    report_path = work_dir / "rebuild_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["static_matches_expected"] or report.get("final_matches_expected") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
