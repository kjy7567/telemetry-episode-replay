#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bts_agentbench.preprocess import preprocess_raw_archives  # noqa: E402
from bts_agentbench.scenario_benchmark import generate_scenario_benchmark  # noqa: E402
from build_bts_canonical_final import run_explicit_controller_audit  # noqa: E402


SPLITS = ("train", "dev", "test")
RAW_ARCHIVES = ("Site_Aaa.zip", "Site_Baa.zip", "Site_Caa.zip")
RAW_SHA256 = {
    "Site_Aaa.zip": "ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f",
    "Site_Baa.zip": "fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8",
    "Site_Caa.zip": "fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b",
}
SUBMITTED_SOURCE_SHA256 = "9c5502b3718113fee812818e71b47c23ed54ec1f694d37c35ea29686b2c64496"
SUBMITTED_DATASET_SHA256 = "70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76"
SUBMISSION_SELECTION_SHA256 = "2487dce6bbb01bb0ab4e1d5b388ff40cc509ab26082770c867ed085e96ecddd6"
PACKAGES = ("duckdb", "numpy", "pandas", "pyarrow", "rdflib")
CATALOG_SHA256 = {
    "catalog_summary.json": "a9ebb46dc7293fc17230d7d26a8f00b847b23136a6cbf29749db2b547cfcb722",
    "entities.parquet": "fb63ebf4b4cfd63893fa508afacafa1240f2636a9e3a7c0c5725407958613731",
    "relations.parquet": "4a512fc1bdcc93dbf3e822458eb9193cb576dfadafe45f44d4e7298607d530d3",
    "stream_targets.parquet": "7785971531d03a9f80e2c9620fc34d7c828f9892adbad962d8820b3c95599994",
    "streams.parquet": "3e848eb68be296ca39756aeb9ecb6ea5038ab9788df272be898fc27c822e23b7",
}


def announce(message: str) -> None:
    print(f"[paper-replay] {message}", flush=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl_bytes(payload: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def canonical_jsonl_sha256(rows: list[dict[str, Any]]) -> str:
    payload = "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    ).encode("utf-8")
    return sha256_bytes(payload)


def normalize_report_paths(
    value: Any,
    replacements: list[tuple[str, str]],
) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize_report_paths(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_report_paths(item, replacements) for item in value]
    if isinstance(value, str):
        normalized = value
        for source, replacement in replacements:
            normalized = normalized.replace(source, replacement)
        return normalized
    return value


def require_new_directory(path: Path) -> None:
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
            raise SystemExit(
                f"raw archive checksum mismatch for {path}: expected {RAW_SHA256[name]}, found {digest}"
            )
        inventory.append(
            {"name": name, "size_bytes": path.stat().st_size, "sha256": digest}
        )
    return inventory


def catalog_inventory(catalog_dir: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for name, expected_digest in CATALOG_SHA256.items():
        path = catalog_dir / name
        if not path.exists():
            raise SystemExit(f"missing retained catalog input: {path}")
        digest = sha256(path)
        if digest != expected_digest:
            raise SystemExit(
                f"retained catalog checksum mismatch for {path}: "
                f"expected {expected_digest}, found {digest}"
            )
        inventory.append(
            {"name": name, "size_bytes": path.stat().st_size, "sha256": digest}
        )
    return inventory


def submitted_final_payloads(bundle: Path) -> dict[str, bytes]:
    if sha256(bundle) != SUBMITTED_DATASET_SHA256:
        raise SystemExit(f"submitted dataset bundle checksum mismatch: {bundle}")
    with zipfile.ZipFile(bundle) as archive:
        return {
            split: archive.read(
                f"dataset_bundle/bts_agentbench_532/{split}.jsonl"
            )
            for split in SPLITS
        }


def build_final(
    *,
    static_dir: Path,
    tool_store_db: Path,
    run_dir: Path,
    raw_dir: Path | None,
) -> float:
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
        str(run_dir / "final"),
        "--tool-store-db",
        str(tool_store_db),
        "--corpus-name",
        "bts",
        "--submission-compatible",
        "--skip-controller-audit",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(REPO_ROOT / "scripts")]
    )
    if raw_dir is not None:
        env["BTS_RAW_DIR"] = str(raw_dir)
    started = time.monotonic()
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
    return time.monotonic() - started


def verify_run(
    *,
    run_dir: Path,
    static_reference: Path,
    submitted_payloads: dict[str, bytes],
) -> dict[str, Any]:
    static_report: dict[str, Any] = {}
    final_report: dict[str, Any] = {}
    for split in SPLITS:
        static_payload = (run_dir / "static" / f"{split}.jsonl").read_bytes()
        static_expected = (static_reference / f"{split}.jsonl").read_bytes()
        static_report[split] = {
            "row_count": len(load_jsonl_bytes(static_payload)),
            "sha256": sha256_bytes(static_payload),
            "expected_sha256": sha256_bytes(static_expected),
            "exact_file_match": static_payload == static_expected,
        }

        final_payload = (run_dir / "final" / f"{split}.jsonl").read_bytes()
        final_expected = submitted_payloads[split]
        final_rows = load_jsonl_bytes(final_payload)
        expected_rows = load_jsonl_bytes(final_expected)
        final_report[split] = {
            "row_count": len(final_rows),
            "sha256": sha256_bytes(final_payload),
            "expected_sha256": sha256_bytes(final_expected),
            "canonical_jsonl_sha256": canonical_jsonl_sha256(final_rows),
            "expected_canonical_jsonl_sha256": canonical_jsonl_sha256(expected_rows),
            "exact_json_object_match": final_rows == expected_rows,
            "exact_file_match": final_payload == final_expected,
        }

    preflight = json.loads(
        (run_dir / "final" / "contract_preflight_report.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "static": static_report,
        "final": final_report,
        "preflight_issue_count": int(preflight["issue_count"]),
        "passed": (
            all(item["exact_file_match"] for item in static_report.values())
            and all(item["exact_file_match"] for item in final_report.values())
            and int(preflight["issue_count"]) == 0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconstruct the exact BTS benchmark rows used in the paper submission."
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--raw-dir",
        type=Path,
        help="Directory containing the three checksummed BTS raw ZIP archives.",
    )
    input_group.add_argument(
        "--tool-store-db",
        type=Path,
        help="Existing tool store for a faster static-to-final replay.",
    )
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--controller-audit", action="store_true")
    parser.add_argument(
        "--processed-catalog-dir",
        type=Path,
        default=REPO_ROOT / "data" / "source" / "bts-processed-catalog",
        help="Retained normalized metadata contract used by the submitted build.",
    )
    parser.add_argument(
        "--selection-contract",
        type=Path,
        default=REPO_ROOT / "provenance" / "submission_static_selection.jsonl",
    )
    parser.add_argument(
        "--static-reference",
        type=Path,
        default=REPO_ROOT / "release" / "submitted-static-reference",
    )
    parser.add_argument(
        "--submitted-dataset-bundle",
        type=Path,
        default=REPO_ROOT / "release" / "submitted-dataset-bundle.zip",
    )
    parser.add_argument(
        "--submitted-source-bundle",
        type=Path,
        default=REPO_ROOT / "release" / "submitted-source-bundle.zip",
    )
    args = parser.parse_args()

    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    work_dir = args.work_dir.resolve()
    require_new_directory(work_dir)

    if sha256(args.submitted_source_bundle) != SUBMITTED_SOURCE_SHA256:
        raise SystemExit(
            f"submitted source bundle checksum mismatch: {args.submitted_source_bundle}"
        )
    if sha256(args.selection_contract) != SUBMISSION_SELECTION_SHA256:
        raise SystemExit(
            f"submitted selection-contract checksum mismatch: {args.selection_contract}"
        )
    expected_final = submitted_final_payloads(args.submitted_dataset_bundle)
    announce("verified supplementary bundles and retained-row contract inputs")

    raw_dir = args.raw_dir.resolve() if args.raw_dir is not None else None
    build_report: dict[str, Any]
    if raw_dir is not None:
        inventory = raw_inventory(raw_dir)
        catalog_dir = args.processed_catalog_dir.resolve()
        retained_catalog_inventory = catalog_inventory(catalog_dir)
        tool_store_dir = work_dir / "tool-store"
        catalog_summary = json.loads(
            (catalog_dir / "catalog_summary.json").read_text(encoding="utf-8")
        )
        announce("verified the retained normalized metadata contract")
        announce("preprocessing raw ZIP telemetry into the read-only tool store")
        started = time.monotonic()
        preprocess_summary = preprocess_raw_archives(
            raw_dir, catalog_dir, tool_store_dir
        )
        preprocess_seconds = time.monotonic() - started
        tool_store_db = tool_store_dir / "tool_store.duckdb"
        announce("fresh tool store completed")
        build_report = {
            "mode": "raw_archives_to_fresh_tool_store",
            "raw_archives": inventory,
            "retained_catalog": retained_catalog_inventory,
            "catalog_summary": catalog_summary,
            "preprocess_summary": preprocess_summary,
            "preprocess_seconds": round(preprocess_seconds, 3),
        }
    else:
        tool_store_db = args.tool_store_db.resolve()
        if not tool_store_db.exists():
            raise SystemExit(f"tool store does not exist: {tool_store_db}")
        build_report = {
            "mode": "existing_tool_store",
            "tool_store_db": str(tool_store_db),
        }

    run_reports: list[dict[str, Any]] = []
    for index in range(args.runs):
        run_dir = work_dir / f"run_{index + 1}"
        static_dir = run_dir / "static"
        announce(f"run {index + 1}/{args.runs}: regenerating submitted static tasks")
        started = time.monotonic()
        static_manifest = generate_scenario_benchmark(
            tool_store_db,
            static_dir,
            heldout_site_ids=["BTS_C"],
            selection_contract_path=args.selection_contract.resolve(),
        )
        static_seconds = time.monotonic() - started
        final_seconds = build_final(
            static_dir=static_dir,
            tool_store_db=tool_store_db,
            run_dir=run_dir,
            raw_dir=raw_dir,
        )
        verification = verify_run(
            run_dir=run_dir,
            static_reference=args.static_reference.resolve(),
            submitted_payloads=expected_final,
        )
        run_reports.append(
            {
                "run": index + 1,
                "static_manifest": static_manifest,
                "static_seconds": round(static_seconds, 3),
                "final_seconds": round(final_seconds, 3),
                **verification,
            }
        )
        announce(f"run {index + 1}/{args.runs}: exact static/final verification passed")

    cross_run_exact_match = True
    if len(run_reports) > 1:
        first_root = work_dir / "run_1"
        for index in range(1, len(run_reports)):
            other_root = work_dir / f"run_{index + 1}"
            for stage in ("static", "final"):
                for split in SPLITS:
                    cross_run_exact_match = cross_run_exact_match and (
                        (first_root / stage / f"{split}.jsonl").read_bytes()
                        == (other_root / stage / f"{split}.jsonl").read_bytes()
                    )

    controller_report: dict[str, Any] | None = None
    if args.controller_audit:
        announce("running the frozen deterministic controller audit on run 1")
        controller_report = run_explicit_controller_audit(
            work_dir / "run_1" / "final", tool_store_db
        )

    report = {
        "report_version": "paper-submission-exact-replay-v1",
        "claim_scope": "deterministic benchmark construction; provider model outputs are excluded",
        "python": sys.version.split()[0],
        "packages": {
            package: importlib.metadata.version(package) for package in PACKAGES
        },
        "selection_contract": str(args.selection_contract.resolve()),
        "selection_contract_sha256": sha256(args.selection_contract.resolve()),
        "submitted_source_bundle_sha256": sha256(args.submitted_source_bundle),
        "submitted_dataset_bundle_sha256": sha256(args.submitted_dataset_bundle),
        "tool_store_build": build_report,
        "runs": run_reports,
        "cross_run_exact_match": cross_run_exact_match,
        "controller_audit": controller_report,
        "passed": (
            all(run["passed"] for run in run_reports)
            and cross_run_exact_match
            and (
                controller_report is None
                or int(controller_report["totals"]["accomplished_count"]) == 0
            )
        ),
    }
    replacements = [(str(work_dir), "<WORK_DIR>"), (str(REPO_ROOT), "<REPO_ROOT>")]
    if raw_dir is not None:
        replacements.append((str(raw_dir), "<RAW_DIR>"))
    else:
        replacements.append((str(tool_store_db), "<TOOL_STORE_DB>"))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    stored_report = normalize_report_paths(report, replacements)
    report_path = work_dir / "submission_replay_report.json"
    report_path.write_text(
        json.dumps(stored_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stored_report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
