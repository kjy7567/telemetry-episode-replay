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

from audit_bts_canonical_contract import audit_contract
from build_bts_e2e_agentic import build_agentic_bts_e2e
from canonical_repair_pairwise import transform_row as repair_pairwise
from canonical_repair_quality_rank import transform_quality_row as repair_quality
from canonical_repair_quality_rank import transform_rank_row as repair_rank_semantics
from canonical_repair_rank import transform_row as repair_rank_contract
from canonical_repair_reportability import transform_row as repair_reportability
from canonical_repair_single_stream import transform_row as repair_single_stream
from build_canonical_agentic_final import build_canonical_agentic_final, clone, reporting_commitment_candidate, summarize
from build_explicit_controller_failure_analysis import analyze_rows as analyze_explicit_controller_failures
from build_explicit_controller_round_report import build_report as build_explicit_controller_round_report
from run_bts_explicit_controller_eval import run_controller_suite


STATIC_DEFAULT = REPO_ROOT / "artifacts" / "bts-static-seed"
E2E_OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-e2e-contract"
AGENTIC_OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-agentic-source"
CANONICAL_SEED_OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-canonical-seed"
CANONICAL_SEED_CORE_OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-canonical-seed-core"
FINAL_OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-canonical-final"
TOOL_STORE_DB = REPO_ROOT / "data" / "local-build" / "tool_store" / "tool_store.duckdb"

FINAL_CANONICAL_VERSION = "bts-canonical-final"
FINAL_LIFT_VERSION = "bts-agentic-final"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-dir", type=Path, default=STATIC_DEFAULT)
    parser.add_argument("--e2e-out-dir", type=Path, default=E2E_OUT_DEFAULT)
    parser.add_argument("--agentic-out-dir", type=Path, default=AGENTIC_OUT_DEFAULT)
    parser.add_argument("--canonical-seed-out-dir", type=Path, default=CANONICAL_SEED_OUT_DEFAULT)
    parser.add_argument("--canonical-seed-core-out-dir", type=Path, default=CANONICAL_SEED_CORE_OUT_DEFAULT)
    parser.add_argument("--final-out-dir", type=Path, default=FINAL_OUT_DEFAULT)
    parser.add_argument("--tool-store-db", type=Path, default=TOOL_STORE_DB)
    parser.add_argument("--corpus-name", type=str, default="bts")
    parser.add_argument(
        "--skip-controller-audit",
        action="store_true",
        help="Build and preflight the release without recomputing controller witnesses.",
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


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def stamp_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    metadata = dict(out.get("metadata", {}))
    metadata["final_artifact_version"] = FINAL_CANONICAL_VERSION
    metadata["final_lifting_version"] = FINAL_LIFT_VERSION
    out["metadata"] = metadata

    lifting = dict(out.get("agentic_lifting", {}))
    lifting["lifting_version"] = FINAL_LIFT_VERSION
    lifting["final_artifact_version"] = FINAL_CANONICAL_VERSION
    out["agentic_lifting"] = lifting

    history = list(out.get("generation_history", []))
    history.append(
        {
            "step_index": len(history),
            "stage": "final_family_semantics_repair",
            "stage_type": "repair",
            "builder": "build_bts_canonical_final",
            "status": "applied",
            "details": {
                "artifact_version": FINAL_CANONICAL_VERSION,
                "lifting_version": FINAL_LIFT_VERSION,
            },
        }
    )
    out["generation_history"] = history
    return out


def transform_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    family = str(row.get("task_family", ""))
    out = dict(row)

    if family in {
        "point_disambiguation",
        "day_mean_lookup",
        "relative_24h_mean_lookup",
        "window_mean_lookup",
        "timestamp_value_lookup",
        "timestamp_nearest_lookup",
    }:
        out = repair_single_stream(out, runtime)

    if family in {
        "point_disambiguation",
        "window_mean_lookup",
        "timestamp_value_lookup",
        "timestamp_nearest_lookup",
    }:
        out = repair_reportability(out)

    if family == "window_pairwise_compare":
        out = repair_pairwise(out, runtime)
        phases = out.get("phase_examples", [])
        golds = out.get("phase_gold_final_answers", [])
        if len(phases) >= 5 and len(golds) >= 5 and str(phases[3].get("task_family", "")) == "quality_preference":
            final_gold = reporting_commitment_candidate("quality_preference", golds[3])
            if final_gold is not None:
                golds[4] = clone(final_gold)
                out["phase_gold_final_answers"] = golds
                out["gold_final_answer"] = clone(final_gold)
                phases[4]["gold_final_answer"] = clone(final_gold)
                out["phase_examples"] = phases
                out["final_phase_example"] = clone(phases[4])

    if family == "window_rank":
        out = repair_rank_contract(out, runtime)
        out = repair_rank_semantics(out, runtime)

    if family == "quality_gate":
        out = repair_quality(out, runtime)

    if family == "window_pairwise_compare":
        metadata = dict(out.get("metadata", {}))
        metadata.pop("reporting_commitment_mode", None)
        out["metadata"] = metadata

    out = stamp_row(out)
    if family == "window_pairwise_compare":
        metadata = dict(out.get("metadata", {}))
        metadata["reporting_commitment_mode"] = "quality_only"
        out["metadata"] = metadata
    if family == "window_rank":
        metadata = dict(out.get("metadata", {}))
        metadata["pairwise_rank_phase_split_v12"] = True
        metadata["rank_quality_prompt_v12"] = True
        out["metadata"] = metadata
    return out


def build_seed_artifact(
    *,
    static_dir: Path,
    e2e_out_dir: Path,
    agentic_out_dir: Path,
    canonical_seed_out_dir: Path,
    canonical_seed_core_out_dir: Path,
    tool_store_db: Path,
    corpus_name: str,
) -> dict[str, Any]:
    reset_dir(e2e_out_dir)
    reset_dir(agentic_out_dir)
    reset_dir(canonical_seed_out_dir)
    reset_dir(canonical_seed_core_out_dir)

    e2e_manifest = build_bts_e2e(static_dir, e2e_out_dir)
    agentic_manifest = build_agentic_bts_e2e(e2e_out_dir, agentic_out_dir)
    canonical_manifest = build_canonical_agentic_final(
        static_dir=static_dir,
        source_dir=agentic_out_dir,
        tool_store_db=tool_store_db,
        out_dir=canonical_seed_out_dir,
        core_out_dir=canonical_seed_core_out_dir,
        corpus_name=corpus_name,
        uniform_reference=None,
        use_default_uniform_reference=False,
    )
    return {
        "e2e": e2e_manifest,
        "agentic": agentic_manifest,
        "canonical_seed": canonical_manifest,
    }


def run_explicit_controller_audit(benchmark_dir: Path, tool_store_db: Path) -> dict[str, Any]:
    witness_dir = benchmark_dir / "explicit_controller_witnesses"
    reset_dir(witness_dir)
    runtime = ToolStoreRuntime(tool_store_db)
    try:
        for split in ("train", "dev", "test"):
            out_dir = witness_dir / split
            suite = run_controller_suite(benchmark_dir, split, runtime, ["phase_complete_stronger_controller"])
            out_dir.mkdir(parents=True, exist_ok=True)
            combined_report = {
                "benchmark_dir": str(benchmark_dir),
                "split": split,
                "controllers": ["phase_complete_stronger_controller"],
                "reports": suite["reports"],
            }
            write_json(out_dir / "explicit_controller_report.json", combined_report)
            for name, rows in suite["rows_by_controller"].items():
                write_jsonl(out_dir / f"{name}.jsonl", rows)
                write_json(out_dir / f"{name}_summary.json", suite["reports"][name])
    finally:
        runtime.close()

    round_report = build_explicit_controller_round_report(witness_dir)
    write_json(benchmark_dir / "explicit_controller_audit_round2_report.json", round_report)

    rows: list[dict[str, Any]] = []
    for split in ("train", "dev", "test"):
        rows.extend(load_jsonl(witness_dir / split / "phase_complete_stronger_controller.jsonl"))
    failure_report = analyze_explicit_controller_failures(rows)
    write_json(benchmark_dir / "explicit_controller_failure_analysis.json", failure_report)
    return round_report


def main() -> None:
    args = parse_args()
    seed = build_seed_artifact(
        static_dir=args.static_dir,
        e2e_out_dir=args.e2e_out_dir,
        agentic_out_dir=args.agentic_out_dir,
        canonical_seed_out_dir=args.canonical_seed_out_dir,
        canonical_seed_core_out_dir=args.canonical_seed_core_out_dir,
        tool_store_db=args.tool_store_db,
        corpus_name=args.corpus_name,
    )

    reset_dir(args.final_out_dir)
    runtime = ToolStoreRuntime(args.tool_store_db)
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
        "track": "bts_e2e_agentic_canonical",
        "artifact_version": FINAL_CANONICAL_VERSION,
        "lifting_version": FINAL_LIFT_VERSION,
        "source_static_dir": str(args.static_dir),
        "tool_store_db": str(args.tool_store_db),
        "seed_artifact_dir": str(args.canonical_seed_out_dir),
        "splits": {split: len(rows) for split, rows in transformed_splits.items()},
        "split_summaries": {split: summarize(rows) for split, rows in transformed_splits.items()},
        "family_repair_policy": {
            "point_disambiguation": "stream-revision-and-quality-alignment",
            "day_mean_lookup": "mean-quality-window-alignment",
            "relative_24h_mean_lookup": "mean-quality-window-alignment",
            "window_mean_lookup": "mean-quality-window-and-reportability-alignment",
            "timestamp_value_lookup": "timestamp-reportability-alignment",
            "timestamp_nearest_lookup": "timestamp-reportability-alignment",
            "window_pairwise_compare": "pairwise-quality-commitment-alignment",
            "window_rank": "rank-previous-month-and-quality-alignment",
            "quality_gate": "quality-trend-and-reportability-alignment",
        },
        "lineage": seed,
    }
    write_json(args.final_out_dir / "manifest.json", manifest)
    write_json(
        args.final_out_dir / "canonical_report.json",
        {
            "artifact_version": FINAL_CANONICAL_VERSION,
            "lifting_version": FINAL_LIFT_VERSION,
            "split_summaries": manifest["split_summaries"],
            "seed_artifact_dir": str(args.canonical_seed_out_dir),
        },
    )

    preflight = audit_contract(args.final_out_dir, args.tool_store_db)
    write_json(args.final_out_dir / "contract_preflight_report.json", preflight)
    if args.skip_controller_audit:
        controller_summary = {
            "totals": None,
            "status": "skipped",
            "reason": "--skip-controller-audit",
        }
    else:
        controller_summary = run_explicit_controller_audit(args.final_out_dir, args.tool_store_db)

    write_json(
        args.final_out_dir / "final_build_report.json",
        {
            "artifact_version": FINAL_CANONICAL_VERSION,
            "lifting_version": FINAL_LIFT_VERSION,
            "seed_artifact_dir": str(args.canonical_seed_out_dir),
            "final_artifact_dir": str(args.final_out_dir),
            "preflight_issue_count": preflight["issue_count"],
            "explicit_controller_totals": controller_summary.get("totals"),
            "explicit_controller_status": controller_summary.get("status", "completed"),
        },
    )
    print(
        json.dumps(
            {
                "final_out_dir": str(args.final_out_dir),
                "artifact_version": FINAL_CANONICAL_VERSION,
                "preflight_issue_count": preflight["issue_count"],
                "controller_totals": controller_summary.get("totals"),
                "controller_status": controller_summary.get("status", "completed"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
