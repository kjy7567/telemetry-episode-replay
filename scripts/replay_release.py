#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
PACKAGES = ("duckdb", "numpy", "openai", "pandas", "pyarrow", "rdflib", "requests")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_hashes(directory: Path) -> dict[str, str]:
    return {split: sha256(directory / f"{split}.jsonl") for split in SPLITS}


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def load_tool_store_provenance(build_report_path: Path, tool_store_db: Path) -> dict[str, Any]:
    build_report_path = build_report_path.resolve()
    if not build_report_path.is_file():
        raise FileNotFoundError(f"tool-store build report does not exist: {build_report_path}")
    expected_db = (build_report_path.parent / "tool-store" / "tool_store.duckdb").resolve()
    if expected_db != tool_store_db:
        raise ValueError(
            "tool-store path is not bound to the supplied build report: "
            f"expected {expected_db}, received {tool_store_db}"
        )
    payload = json.loads(build_report_path.read_text(encoding="utf-8"))
    if payload.get("static_matches_expected") is not True:
        raise ValueError("tool-store build report does not record an exact retained-static match")
    return {
        "build_report_version": payload.get("report_version"),
        "path_binding_verified": True,
        "catalog_mode": payload.get("catalog_mode"),
        "raw_archives": payload.get("raw_archives", []),
        "rebuilt_static_split_hashes": payload.get("static_split_hashes", {}),
        "rebuilt_static_matches_expected": True,
        "public_summary": "replay/raw_to_static_rebuild_report.json",
    }


def run_build(
    *,
    label: str,
    work_dir: Path,
    static_dir: Path,
    tool_store_db: Path,
    controller_audit: bool,
) -> dict[str, Any]:
    run_dir = work_dir / label
    if run_dir.exists():
        shutil.rmtree(run_dir)

    final_dir = run_dir / "final"
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "build_bts_canonical_final.py"),
        "--static-dir",
        str(static_dir),
        "--e2e-out-dir",
        str(run_dir / "e2e"),
        "--agentic-out-dir",
        str(run_dir / "agentic"),
        "--canonical-seed-out-dir",
        str(run_dir / "canonical-seed"),
        "--canonical-seed-core-out-dir",
        str(run_dir / "canonical-seed-core"),
        "--final-out-dir",
        str(final_dir),
        "--tool-store-db",
        str(tool_store_db),
        "--corpus-name",
        "bts",
    ]
    if not controller_audit:
        command.append("--skip-controller-audit")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)

    preflight = json.loads((final_dir / "contract_preflight_report.json").read_text(encoding="utf-8"))
    if int(preflight.get("issue_count", -1)) != 0:
        raise RuntimeError(f"{label}: contract preflight reported issues")

    return {
        "label": label,
        "final_dir": str(final_dir.relative_to(REPO_ROOT)),
        "split_hashes": split_hashes(final_dir),
        "split_counts": {
            split: sum(1 for _ in (final_dir / f"{split}.jsonl").open(encoding="utf-8"))
            for split in SPLITS
        },
        "preflight_issue_count": int(preflight["issue_count"]),
    }


def inspect_existing_build(*, label: str, work_dir: Path) -> dict[str, Any]:
    final_dir = work_dir / label / "final"
    if not final_dir.exists():
        raise FileNotFoundError(f"missing existing replay output: {final_dir}")
    preflight = json.loads((final_dir / "contract_preflight_report.json").read_text(encoding="utf-8"))
    if int(preflight.get("issue_count", -1)) != 0:
        raise RuntimeError(f"{label}: contract preflight reported issues")
    return {
        "label": label,
        "final_dir": str(final_dir.relative_to(REPO_ROOT)),
        "split_hashes": split_hashes(final_dir),
        "split_counts": {
            split: sum(1 for _ in (final_dir / f"{split}.jsonl").open(encoding="utf-8"))
            for split in SPLITS
        },
        "preflight_issue_count": int(preflight["issue_count"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild the frozen BTS release twice and compare exact split hashes."
    )
    parser.add_argument(
        "--tool-store-db",
        type=Path,
        default=Path(os.environ["BTS_TOOL_STORE_DB"]) if os.environ.get("BTS_TOOL_STORE_DB") else None,
        help="Materialized BTS tool_store.duckdb, or set BTS_TOOL_STORE_DB.",
    )
    parser.add_argument("--static-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-static-seed")
    parser.add_argument("--expected-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-canonical-final")
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "data" / "local-build" / "replay")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "replay" / "replay_report.json")
    parser.add_argument("--controller-audit", action="store_true")
    parser.add_argument(
        "--tool-store-build-report",
        type=Path,
        help=(
            "Optional rebuild_from_raw.py report. When supplied, the replay verifies that the tool-store "
            "path belongs to that build and records checksummed raw-input provenance."
        ),
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Verify existing work-dir/run_a and run_b outputs without rebuilding them.",
    )
    args = parser.parse_args()

    if args.tool_store_db is None:
        parser.error("--tool-store-db or BTS_TOOL_STORE_DB is required")
    args.tool_store_db = args.tool_store_db.resolve()
    if not args.tool_store_db.exists():
        parser.error(f"tool store does not exist: {args.tool_store_db}")
    try:
        tool_store_provenance = (
            load_tool_store_provenance(args.tool_store_build_report, args.tool_store_db)
            if args.tool_store_build_report
            else {"kind": "external_tool_store", "build_report_supplied": False}
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.reuse_existing:
        runs = [inspect_existing_build(label=label, work_dir=args.work_dir) for label in ("run_a", "run_b")]
    else:
        runs = [
            run_build(
                label=label,
                work_dir=args.work_dir,
                static_dir=args.static_dir.resolve(),
                tool_store_db=args.tool_store_db,
                controller_audit=args.controller_audit,
            )
            for label in ("run_a", "run_b")
        ]

    expected_hashes = split_hashes(args.expected_dir.resolve())
    replay_match = runs[0]["split_hashes"] == runs[1]["split_hashes"]
    expected_match = runs[0]["split_hashes"] == expected_hashes
    report = {
        "report_version": "bts-release-replay-v2",
        "replay_boundary": "retained static executable task layer -> canonical release",
        "raw_to_static_note": "Raw-to-static reconstruction additionally requires the BTS archives and tool-store build.",
        "tool_store_provenance": tool_store_provenance,
        "python": sys.version.split()[0],
        "packages": package_versions(),
        "static_split_hashes": split_hashes(args.static_dir.resolve()),
        "expected_split_hashes": expected_hashes,
        "runs": runs,
        "run_a_equals_run_b": replay_match,
        "run_a_equals_expected_release": expected_match,
        "controller_audit_recomputed": bool(args.controller_audit),
        "builds_executed_this_invocation": not args.reuse_existing,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not replay_match or not expected_match:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
