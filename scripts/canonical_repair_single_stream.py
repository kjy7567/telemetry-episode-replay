#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from bts_agentbench.runtime import ToolStoreRuntime
from canonical_repair_common import (
    append_phase_example,
    append_strict_timestamp_reporting_commitment,
    append_combined_reporting_commitment,
    clone,
    ensure_phase_gold,
    load_jsonl,
    normalized_interaction_mode,
    quality_gold_for_context,
    recompute_turn_budget,
    row_phase_examples,
    write_json,
    write_jsonl,
)


TARGET_FAMILIES = {
    "point_disambiguation",
    "day_mean_lookup",
    "relative_24h_mean_lookup",
    "window_mean_lookup",
    "timestamp_value_lookup",
    "timestamp_nearest_lookup",
}


def phase_families(row: dict[str, Any]) -> list[str]:
    return [str(example.get("task_family", "")) for example in row_phase_examples(row)]


def strip_last_phases(row: dict[str, Any], suffixes: set[str]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    while out.get("phase_examples") and str(out["phase_examples"][-1].get("task_family", "")) in suffixes:
        out["phase_examples"] = out["phase_examples"][:-1]
        out["phase_gold_final_answers"] = out["phase_gold_final_answers"][:-1]
        out["goal_revision_turns"] = list(out.get("goal_revision_turns", []))[:-1]
    if out.get("phase_gold_final_answers"):
        out["gold_final_answer"] = clone(out["phase_gold_final_answers"][-1])
    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return out


def latest_phase_gold(row: dict[str, Any], family: str) -> dict[str, Any] | None:
    phases = row.get("phase_examples") or []
    golds = row.get("phase_gold_final_answers") or []
    for ex, gold in reversed(list(zip(phases, golds))):
        if str(ex.get("task_family", "")) == family:
            return clone(gold)
    return None


def latest_timestamp_gold(row: dict[str, Any]) -> dict[str, Any] | None:
    for fam in ["timestamp_nearest_lookup", "timestamp_value_lookup"]:
        gold = latest_phase_gold(row, fam)
        if gold is not None:
            return gold
    return None


def latest_aggregate_gold(row: dict[str, Any]) -> dict[str, Any] | None:
    for fam in ["window_mean_lookup", "relative_24h_mean_lookup", "day_mean_lookup"]:
        gold = latest_phase_gold(row, fam)
        if gold is not None:
            return gold
    return None


def infer_period(window_start: str, window_end: str) -> str:
    start = pd.Timestamp(window_start)
    end = pd.Timestamp(window_end)
    days = (end - start).total_seconds() / 86400.0
    if abs(days - 1.0) < 0.01:
        return "day"
    if abs(days - 7.0) < 0.01:
        return "week"
    return "month"


def local_quality_prompt(quality_gold: dict[str, Any]) -> str:
    start = pd.Timestamp(str(quality_gold["window_start"]))
    period = str(quality_gold.get("period") or "window")
    if period == "week":
        basis = f"the week beginning {start.strftime('%B %-d, %Y')}"
    elif period == "day":
        basis = start.strftime("%B %-d, %Y")
    else:
        basis = f"the window beginning {start.strftime('%B %-d, %Y')}"
    return f"For {basis}, would you answer or abstain based on data quality?"


def append_quality_gate_phase(row: dict[str, Any], quality_gold: dict[str, Any]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    out["goal_revision_turns"] = list(out.get("goal_revision_turns", [])) + [local_quality_prompt(quality_gold)]
    out["phase_gold_final_answers"] = list(out.get("phase_gold_final_answers", [])) + [clone(quality_gold)]
    append_phase_example(out, "quality_gate", quality_gold)
    out["gold_final_answer"] = clone(quality_gold)
    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return out


def sync_quality_call(row: dict[str, Any], quality_gold: dict[str, Any]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    quality_call = {
        "tool_name": "inspect_quality_window",
        "arguments": {
            "stream_id": quality_gold["stream_id"],
            "window_start": quality_gold["window_start"],
            "window_end": quality_gold["window_end"],
            "period": quality_gold["period"],
        },
    }

    canonical_calls = [clone(call) for call in out.get("canonical_tool_calls", []) if call.get("tool_name") != "inspect_quality_window"]
    quality_call_canonical = clone(quality_call)
    quality_call_canonical["call_id"] = f"c{len(canonical_calls) + 1}"
    canonical_calls.append(quality_call_canonical)
    out["canonical_tool_calls"] = canonical_calls

    variants: list[list[dict[str, Any]]] = []
    for variant in out.get("acceptable_tool_call_sets", []):
        filtered = [clone(call) for call in variant if call.get("tool_name") != "inspect_quality_window"]
        appended = clone(quality_call)
        appended["call_id"] = f"c{len(filtered) + 1}"
        filtered.append(appended)
        variants.append(filtered)
    out["acceptable_tool_call_sets"] = variants
    return out


def align_evidence_to_stream(row: dict[str, Any], stream_id: str) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    evidence = clone(out.get("evidence", {}))
    evidence["stream_ids"] = [stream_id]
    out["evidence"] = evidence
    verifier = clone(out.get("task_accomplish_verifier", {}))
    evidence_checks = clone(verifier.get("evidence_checks", {}))
    evidence_checks["required_stream_ids"] = [stream_id]
    verifier["evidence_checks"] = evidence_checks
    out["task_accomplish_verifier"] = verifier
    return out


def append_quality_for_timestamp_or_point(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    ts_gold = latest_timestamp_gold(out)
    if ts_gold is None:
        return out
    stream_id = ts_gold.get("stream_id")
    observed_timestamp = ts_gold.get("observed_timestamp")
    if not isinstance(stream_id, str) or not isinstance(observed_timestamp, str):
        return out
    ts = pd.Timestamp(observed_timestamp)
    day_start = ts.floor("D")
    week_start = day_start - pd.Timedelta(days=day_start.weekday())
    week_end = week_start + pd.Timedelta(days=7)
    context = {
        "stream_id": stream_id,
        "window_start": week_start.isoformat(),
        "window_end": week_end.isoformat(),
        "period": "week",
    }
    quality_gold = quality_gold_for_context(context, runtime)
    if quality_gold is None:
        return out
    out = append_quality_gate_phase(out, quality_gold)
    out = sync_quality_call(out, quality_gold)
    if str(out.get("task_family", "")) == "point_disambiguation":
        out = align_evidence_to_stream(out, quality_gold["stream_id"])
    return out


def append_quality_for_mean(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    agg_gold = latest_aggregate_gold(out)
    if agg_gold is None:
        return out
    stream_id = agg_gold.get("stream_id")
    window_start = agg_gold.get("window_start")
    window_end = agg_gold.get("window_end")
    if not isinstance(stream_id, str) or not isinstance(window_start, str) or not isinstance(window_end, str):
        return out
    period = infer_period(window_start, window_end)
    context = {
        "stream_id": stream_id,
        "window_start": window_start,
        "window_end": window_end,
        "period": period,
    }
    quality_gold = quality_gold_for_context(context, runtime)
    if quality_gold is None:
        return out
    out = append_quality_gate_phase(out, quality_gold)
    out = sync_quality_call(out, quality_gold)
    return out


def transform_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    family = str(row.get("task_family", ""))
    if family not in TARGET_FAMILIES:
        return clone(row)

    out = strip_last_phases(row, {"reporting_commitment", "quality_preference", "timestamp_preference"})
    if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
        out = append_quality_for_mean(out, runtime)
        out = append_combined_reporting_commitment(out)
    elif family in {"point_disambiguation", "timestamp_value_lookup", "timestamp_nearest_lookup"}:
        out = append_quality_for_timestamp_or_point(out, runtime)
        out = append_strict_timestamp_reporting_commitment(out)
    meta = clone(out.get("metadata", {}))
    meta["test_penalty_v5_intermediate_semantics_only"] = True
    out["metadata"] = meta
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-benchmark-dir", type=Path, required=True)
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime = ToolStoreRuntime(args.tool_store_db)
    try:
        rows = [transform_row(row, runtime) for row in load_jsonl(args.source_benchmark_dir / "test.jsonl")]
    finally:
        runtime.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train.jsonl", [])
    write_jsonl(args.output_dir / "dev.jsonl", [])
    write_jsonl(args.output_dir / "test.jsonl", rows)
    write_json(
        args.output_dir / "manifest.json",
        {
            "artifact_version": "bts-canonical-seed-test-penalty-experiment-v5",
            "source_artifact_version": "bts-canonical-seed-test-penalty-experiment",
            "row_count": len(rows),
            "split_counts": {"train": 0, "dev": 0, "test": len(rows)},
            "target_families": sorted(TARGET_FAMILIES),
            "experiment_policy": {
                "name": "intermediate_semantics_only_v5",
                "repairs": [
                    "replace_quality_or_timestamp_preference_with_quality_gate_for_target_families",
                    "keep_evidence_followup_unchanged",
                ],
            },
        },
    )
    print(json.dumps({"output_dir": str(args.output_dir), "row_count": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
