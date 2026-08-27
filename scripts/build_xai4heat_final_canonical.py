#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bts_agentbench.bts_e2e import build_bts_e2e
from bts_agentbench.runtime import ToolStoreRuntime
from bts_agentbench.scenario_benchmark import generate_scenario_benchmark
from bts_agentbench.tabular_corpus import build_tool_store_from_tabular_corpus
from bts_agentbench.xai4heat import load_xai4heat_tables

from audit_bts_canonical_contract import audit_contract
from build_bts_e2e_agentic import build_agentic_bts_e2e
from canonical_repair_single_stream import transform_row as repair_single_stream_contract
from canonical_repair_reportability import transform_row as repair_reportability_contract
from build_canonical_agentic_final import build_canonical_agentic_final, clone, summarize
from build_explicit_controller_failure_analysis import analyze_rows as analyze_explicit_controller_failures
from build_explicit_controller_round_report import build_report as build_explicit_controller_round_report
from run_bts_explicit_controller_eval import run_controller_suite


RAW_DIR_DEFAULT = REPO_ROOT / "data" / "external" / "xai4heat"
TOOL_STORE_DIR_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "tool-store"
STATIC_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "static"
E2E_OUT_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "e2e"
AGENTIC_OUT_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "agentic"
CANONICAL_SEED_OUT_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "canonical-seed"
CANONICAL_SEED_CORE_OUT_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "canonical-seed-core"
FINAL_OUT_DEFAULT = REPO_ROOT / "data" / "local-build" / "xai4heat" / "final"

CORPUS_NAME = "xai4heat"
E2E_TRACK_VERSION = "xai4heat-interaction-contract"
AGENTIC_TRACK_VERSION = "xai4heat-operator-surface"
FINAL_CANONICAL_VERSION = "xai4heat-final-canonical"
FINAL_LIFT_VERSION = "xai4heat-episode-final"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR_DEFAULT)
    parser.add_argument("--tool-store-dir", type=Path, default=TOOL_STORE_DIR_DEFAULT)
    parser.add_argument("--static-dir", type=Path, default=STATIC_DEFAULT)
    parser.add_argument("--e2e-out-dir", type=Path, default=E2E_OUT_DEFAULT)
    parser.add_argument("--agentic-out-dir", type=Path, default=AGENTIC_OUT_DEFAULT)
    parser.add_argument("--canonical-seed-out-dir", type=Path, default=CANONICAL_SEED_OUT_DEFAULT)
    parser.add_argument("--canonical-seed-core-out-dir", type=Path, default=CANONICAL_SEED_CORE_OUT_DEFAULT)
    parser.add_argument("--final-out-dir", type=Path, default=FINAL_OUT_DEFAULT)
    parser.add_argument("--heldout-site-id", action="append", default=["XAI4HEAT_L17"])
    parser.add_argument("--rebuild-static", action="store_true")
    parser.add_argument("--skip-controller-audit", action="store_true")
    parser.add_argument(
        "--controller-split",
        action="append",
        choices=["train", "dev", "test"],
        help="If set, only run explicit-controller audit for the selected split(s).",
    )
    return parser.parse_args()


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def maybe_rebuild_static(
    raw_dir: Path,
    tool_store_dir: Path,
    static_dir: Path,
    heldout_site_ids: list[str],
    *,
    rebuild_static: bool,
) -> dict[str, Any] | None:
    if static_dir.exists() and (static_dir / "manifest.json").exists() and not rebuild_static:
        return None
    reset_dir(tool_store_dir)
    reset_dir(static_dir)
    metadata, observations = load_xai4heat_tables(raw_dir)
    preprocess_summary = build_tool_store_from_tabular_corpus(
        metadata=metadata,
        observations=observations,
        out_dir=tool_store_dir,
        source_name=CORPUS_NAME,
    )
    benchmark_manifest = generate_scenario_benchmark(
        tool_store_dir / "tool_store.duckdb",
        static_dir,
        heldout_site_ids=heldout_site_ids,
        include_families=[
            "day_mean_lookup",
            "relative_24h_mean_lookup",
            "window_mean_lookup",
            "timestamp_value_lookup",
            "timestamp_nearest_lookup",
        ],
    )
    return {
        "preprocess_summary": preprocess_summary,
        "benchmark_manifest": benchmark_manifest,
    }


def stamp_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["track"] = f"{CORPUS_NAME}_e2e_agentic_canonical"
    metadata = dict(out.get("metadata", {}))
    metadata["final_canonical_artifact"] = FINAL_CANONICAL_VERSION
    metadata["final_lifting_version"] = FINAL_LIFT_VERSION
    out["metadata"] = metadata

    lifting = dict(out.get("agentic_lifting", {}))
    lifting["lifting_version"] = FINAL_LIFT_VERSION
    lifting["final_canonical_artifact"] = FINAL_CANONICAL_VERSION
    out["agentic_lifting"] = lifting

    history = list(out.get("generation_history", []))
    history.append(
        {
            "step_index": len(history),
            "stage": "final_family_contract_alignment",
            "stage_type": "repair",
            "builder": "build_xai4heat_final_canonical",
            "status": "applied",
            "details": {
                "artifact": FINAL_CANONICAL_VERSION,
                "lifting_version": FINAL_LIFT_VERSION,
                "corpus_name": CORPUS_NAME,
            },
        }
    )
    out["generation_history"] = history
    return out


def transform_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    family = str(row.get("task_family", ""))
    out = dict(row)
    if family in {
        "day_mean_lookup",
        "relative_24h_mean_lookup",
        "window_mean_lookup",
        "timestamp_value_lookup",
        "timestamp_nearest_lookup",
    }:
        out = repair_single_stream_contract(out, runtime)
    if family in {"window_mean_lookup", "timestamp_value_lookup", "timestamp_nearest_lookup"}:
        out = repair_reportability_contract(out)
    return stamp_row(out)


def run_explicit_controller_audit(
    benchmark_dir: Path,
    tool_store_db: Path,
    splits: list[str] | None = None,
) -> dict[str, Any]:
    witness_dir = benchmark_dir / "explicit_controller_witnesses"
    reset_dir(witness_dir)
    runtime = ToolStoreRuntime(tool_store_db)
    target_splits = list(splits or ["train", "dev", "test"])
    try:
        for split in target_splits:
            print(f"[controller-audit] split={split}", flush=True)
            out_dir = witness_dir / split
            suite = run_controller_suite(benchmark_dir, split, runtime, ["phase_complete_stronger_controller"])
            out_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                out_dir / "explicit_controller_report.json",
                {
                    "benchmark_dir": str(benchmark_dir),
                    "split": split,
                    "controllers": ["phase_complete_stronger_controller"],
                    "reports": suite["reports"],
                },
            )
            for name, rows in suite["rows_by_controller"].items():
                write_jsonl(out_dir / f"{name}.jsonl", rows)
                write_json(out_dir / f"{name}_summary.json", suite["reports"][name])
    finally:
        runtime.close()

    round_report = build_explicit_controller_round_report(witness_dir, target_splits)
    write_json(benchmark_dir / "explicit_controller_audit_report.json", round_report)

    rows: list[dict[str, Any]] = []
    for split in target_splits:
        rows.extend(load_jsonl(witness_dir / split / "phase_complete_stronger_controller.jsonl"))
    failure_report = analyze_explicit_controller_failures(rows)
    write_json(benchmark_dir / "explicit_controller_failure_analysis.json", failure_report)
    return round_report


def main() -> None:
    global args
    args = parse_args()
    static_rebuild = maybe_rebuild_static(
        raw_dir=args.raw_dir,
        tool_store_dir=args.tool_store_dir,
        static_dir=args.static_dir,
        heldout_site_ids=args.heldout_site_id,
        rebuild_static=args.rebuild_static,
    )

    tool_store_db = args.tool_store_dir / "tool_store.duckdb"

    reset_dir(args.e2e_out_dir)
    reset_dir(args.agentic_out_dir)
    reset_dir(args.canonical_seed_out_dir)
    reset_dir(args.canonical_seed_core_out_dir)
    reset_dir(args.final_out_dir)

    e2e_manifest = build_bts_e2e(
        args.static_dir,
        args.e2e_out_dir,
        corpus_name=CORPUS_NAME,
        track_name=f"{CORPUS_NAME}_e2e",
        e2e_track_version=E2E_TRACK_VERSION,
    )
    agentic_manifest = build_agentic_bts_e2e(
        args.e2e_out_dir,
        args.agentic_out_dir,
        corpus_name=CORPUS_NAME,
        track_name=f"{CORPUS_NAME}_e2e_agentic",
        agentic_track_version=AGENTIC_TRACK_VERSION,
        tool_domain_phrase="district-heating telemetry tools",
        handoff_label="Operator handoff",
        ticket_label="Operations ticket",
        review_label="Data-quality review request",
    )
    canonical_seed_manifest = build_canonical_agentic_final(
        static_dir=args.static_dir,
        source_dir=args.agentic_out_dir,
        tool_store_db=tool_store_db,
        out_dir=args.canonical_seed_out_dir,
        core_out_dir=args.canonical_seed_core_out_dir,
        corpus_name=CORPUS_NAME,
        uniform_reference=None,
        use_default_uniform_reference=False,
        canonical_version="xai4heat-canonical-seed",
        lifting_version="xai4heat-episode-seed",
    )

    runtime = ToolStoreRuntime(tool_store_db)
    try:
        transformed_splits: dict[str, list[dict[str, Any]]] = {}
        for split in ("train", "dev", "test"):
            source_rows = load_jsonl(args.canonical_seed_out_dir / f"{split}.jsonl")
            transformed_splits[split] = [transform_row(row, runtime) for row in source_rows]
    finally:
        runtime.close()

    args.final_out_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in transformed_splits.items():
        write_jsonl(args.final_out_dir / f"{split}.jsonl", rows)

    manifest = {
        "track": f"{CORPUS_NAME}_e2e_agentic_canonical",
        "artifact_version": FINAL_CANONICAL_VERSION,
        "lifting_version": FINAL_LIFT_VERSION,
        "corpus_name": CORPUS_NAME,
        "source_static_dir": str(args.static_dir),
        "tool_store_db": str(tool_store_db),
        "seed_artifact_dir": str(args.canonical_seed_out_dir),
        "splits": {split: len(rows) for split, rows in transformed_splits.items()},
        "split_summaries": {split: summarize(rows) for split, rows in transformed_splits.items()},
        "family_repair_policy": {
            "day_mean_lookup": "single-stream-contract-alignment",
            "relative_24h_mean_lookup": "single-stream-contract-alignment",
            "window_mean_lookup": "single-stream-and-reportability-alignment",
            "timestamp_value_lookup": "single-stream-and-reportability-alignment",
            "timestamp_nearest_lookup": "single-stream-and-reportability-alignment",
        },
        "lineage": {
            "static_rebuild": static_rebuild,
            "e2e": e2e_manifest,
            "agentic": agentic_manifest,
            "canonical_seed": canonical_seed_manifest,
        },
    }
    write_json(args.final_out_dir / "manifest.json", manifest)
    write_json(
        args.final_out_dir / "canonical_report.json",
        {
            "artifact_version": FINAL_CANONICAL_VERSION,
            "lifting_version": FINAL_LIFT_VERSION,
            "split_summaries": manifest["split_summaries"],
            "seed_artifact_dir": str(args.canonical_seed_out_dir),
            "source_static_dir": str(args.static_dir),
        },
    )

    preflight = audit_contract(args.final_out_dir, tool_store_db)
    write_json(args.final_out_dir / "contract_preflight_report.json", preflight)
    controller_splits = args.controller_split or ["train", "dev", "test"]
    if args.skip_controller_audit:
        controller_summary = {
            "rounds": [],
            "totals": {
                "scenario_count": 0,
                "task_success_count": 0,
                "protocol_success_count": 0,
                "fully_accomplished_count": 0,
                "partially_accomplished_count": 0,
                "not_accomplished_count": 0,
            },
            "note": "controller audit skipped by request",
            "evaluated_splits": [],
        }
    else:
        controller_summary = run_explicit_controller_audit(
            args.final_out_dir,
            tool_store_db,
            splits=controller_splits,
        )

    write_json(
        args.final_out_dir / "final_build_report.json",
        {
            "artifact_version": FINAL_CANONICAL_VERSION,
            "lifting_version": FINAL_LIFT_VERSION,
            "seed_artifact_dir": str(args.canonical_seed_out_dir),
            "final_artifact_dir": str(args.final_out_dir),
            "preflight_issue_count": preflight["issue_count"],
            "explicit_controller_totals": controller_summary["totals"],
            "explicit_controller_splits": controller_splits if not args.skip_controller_audit else [],
            "explicit_controller_skipped": bool(args.skip_controller_audit),
        },
    )
    print(
        json.dumps(
            {
                "final_out_dir": str(args.final_out_dir),
                "artifact_version": FINAL_CANONICAL_VERSION,
                "preflight_issue_count": preflight["issue_count"],
                "controller_totals": controller_summary["totals"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
