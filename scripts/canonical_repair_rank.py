#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from bts_agentbench.runtime import ToolStoreRuntime

from canonical_repair_common import clone, load_jsonl, recompute_turn_budget, write_json, write_jsonl
from canonical_repair_commitment import commit_from_timestamp_and_quality
from build_canonical_agentic_final import quality_decision_from_metrics


TARGET_FAMILIES = {"window_pairwise_compare", "window_rank"}


def sync_phase_examples(out: dict[str, Any]) -> dict[str, Any]:
    golds = out.get("phase_gold_final_answers", [])
    phases = clone(out.get("phase_examples", []))
    for idx, gold in enumerate(golds):
        if idx >= len(phases):
            break
        phases[idx]["gold_final_answer"] = clone(gold)
    out["phase_examples"] = phases
    if phases:
        out["final_phase_example"] = clone(phases[-1])
    return out


def pairrank_quality_only_prompt(family: str) -> str:
    if family == "window_pairwise_compare":
        return (
            "Based only on the two time-bounded quality checks for the winning signal from the first and second results "
            "we just discussed, which one would you trust more as a reporting basis: the first or the second?"
        )
    return (
        "Based only on the two month-bounded quality checks for the first and second top-ranked streams in their "
        "respective months, which one would you trust more as a reporting basis: the first or the second?"
    )


def rank_reporting_prompt(include_more_precise_timestamp: bool) -> str:
    if include_more_precise_timestamp:
        return (
            "Considering both the nearest timestamped reading and the month-bounded quality check we just discussed, "
            "should I report it, abstain, or ask you for a more precise timestamp before reporting it?"
        )
    return (
        "Considering both the nearest timestamped reading and the month-bounded quality check we just discussed, "
        "should I report it as-is or abstain?"
    )


def _rank_phase_gold_from_result(result: dict[str, Any]) -> dict[str, Any]:
    ranked = list(result.get("ranked_streams") or [])
    if not ranked:
        return {
            "stream_id": None,
            "mean_value": None,
            "window_start": result.get("window_start"),
            "window_end": result.get("window_end"),
        }
    top = ranked[0]
    return {
        "stream_id": top.get("stream_id"),
        "mean_value": top.get("mean_value"),
        "window_start": result.get("window_start"),
        "window_end": result.get("window_end"),
    }


def _prefer_reference_human_friendly(first_quality: dict[str, Any], second_quality: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    first_decision = str(first_quality.get("decision") or "")
    second_decision = str(second_quality.get("decision") or "")
    if first_decision != second_decision:
        if first_decision == "answer":
            return "first", first_quality
        if second_decision == "answer":
            return "second", second_quality

    first_obs = float(first_quality.get("observed_fraction") or 0.0)
    second_obs = float(second_quality.get("observed_fraction") or 0.0)
    first_gap = float(first_quality.get("gap_ratio") or 1e18)
    second_gap = float(second_quality.get("gap_ratio") or 1e18)
    first_rank = (
        1 if first_decision == "answer" else 0,
        1 if first_obs >= 0.95 else 0,
        round(first_obs, 2),
        -first_gap,
        first_obs,
        1 if str(first_quality.get("reason") or "") == "healthy" else 0,
    )
    second_rank = (
        1 if second_decision == "answer" else 0,
        1 if second_obs >= 0.95 else 0,
        round(second_obs, 2),
        -second_gap,
        second_obs,
        1 if str(second_quality.get("reason") or "") == "healthy" else 0,
    )
    if first_rank >= second_rank:
        return "first", first_quality
    return "second", second_quality


def _set_required_fields(phase: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    out = clone(phase)
    verifier = clone(out.get("task_accomplish_verifier", {}))
    checks = clone(verifier.get("final_answer_checks", {}))
    checks["required_fields"] = list(fields)
    verifier["final_answer_checks"] = checks
    out["task_accomplish_verifier"] = verifier
    return out


def _normalize_rank_month_revision(
    out: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    phases = clone(out.get("phase_examples", []))
    golds = clone(out.get("phase_gold_final_answers", []))
    turns = list(out.get("goal_revision_turns", []))
    canonical = clone(out.get("canonical_tool_calls", []))
    acceptable_sets = clone(out.get("acceptable_tool_call_sets", []))

    if len(phases) < 5 or len(golds) < 5 or len(turns) < 4 or len(canonical) < 7:
        recompute_turn_budget(out)
        return sync_phase_examples(out)

    period = str(canonical[1].get("arguments", {}).get("period", ""))
    if period != "month":
        recompute_turn_budget(out)
        return sync_phase_examples(out)

    first_start = pd.Timestamp(canonical[1]["arguments"]["window_start"])
    if first_start.tzinfo is None:
        first_start = first_start.tz_localize("UTC")
    second_start = first_start - pd.DateOffset(months=1)
    second_end = first_start

    old_requested = pd.Timestamp(golds[2]["requested_timestamp"])
    if old_requested.tzinfo is None:
        old_requested = old_requested.tz_localize("UTC")
    requested_ts = second_start.replace(
        hour=old_requested.hour,
        minute=old_requested.minute,
        second=old_requested.second,
        microsecond=old_requested.microsecond,
    )

    list_points_out = runtime.list_points(clone(canonical[0]["arguments"]))
    first_rank_args = clone(canonical[1]["arguments"])
    first_rank_args["stream_ids"] = list_points_out["stream_ids"]
    second_rank_args = clone(canonical[2]["arguments"])
    second_rank_args["stream_ids"] = list_points_out["stream_ids"]
    second_rank_args["window_start"] = second_start.isoformat()
    second_rank_args["window_end"] = second_end.isoformat()

    first_rank_result = runtime.rank_window(first_rank_args)
    second_rank_result = runtime.rank_window(second_rank_args)
    first_rank_gold = _rank_phase_gold_from_result(first_rank_result)
    second_rank_gold = _rank_phase_gold_from_result(second_rank_result)

    # Some January rank rows have no observations in the preceding December.
    # Leave those rows on their existing valid revision path; the final rank
    # transform will use that path as a deterministic fallback.
    if not first_rank_gold.get("stream_id") or not second_rank_gold.get("stream_id"):
        metadata = clone(out.get("metadata", {}))
        metadata["rank_month_normalization_skipped"] = "empty_adjacent_month"
        out["metadata"] = metadata
        recompute_turn_budget(out)
        return sync_phase_examples(out)

    exact_args = clone(canonical[3]["arguments"])
    exact_args["stream_id"] = second_rank_gold["stream_id"]
    exact_args["timestamp"] = requested_ts.isoformat()
    nearest_args = clone(canonical[4]["arguments"])
    nearest_args["stream_id"] = second_rank_gold["stream_id"]
    nearest_args["timestamp"] = requested_ts.isoformat()
    timestamp_gold = runtime.lookup_observation(nearest_args)

    first_metrics = runtime.inspect_quality_window(
        {
            "stream_id": first_rank_gold["stream_id"],
            "window_start": first_rank_gold["window_start"],
            "window_end": first_rank_gold["window_end"],
            "period": "month",
        }
    )
    second_metrics = runtime.inspect_quality_window(
        {
            "stream_id": second_rank_gold["stream_id"],
            "window_start": second_rank_gold["window_start"],
            "window_end": second_rank_gold["window_end"],
            "period": "month",
        }
    )
    first_quality = quality_decision_from_metrics(first_metrics, runtime, "month", str(first_rank_gold["stream_id"]))
    second_quality = quality_decision_from_metrics(second_metrics, runtime, "month", str(second_rank_gold["stream_id"]))
    preferred_reference, chosen_quality = _prefer_reference_human_friendly(first_quality, second_quality)
    preferred_gold = first_rank_gold if preferred_reference == "first" else second_rank_gold

    turns[2] = pairrank_quality_only_prompt("window_rank")
    turns[3] = rank_reporting_prompt("more precise timestamp" in turns[3])

    phase4_gold = clone(chosen_quality)
    phase4_gold["preferred_reference"] = preferred_reference
    phase4_gold["window_start"] = str(pd.Timestamp(preferred_gold["window_start"]))
    phase4_gold["window_end"] = str(pd.Timestamp(preferred_gold["window_end"]))
    phase4_gold["period"] = "month"
    phase4_gold["stream_id"] = preferred_gold["stream_id"]

    phase5_gold = commit_from_timestamp_and_quality(timestamp_gold, phase4_gold, turns[3])

    golds[0] = first_rank_gold
    golds[1] = second_rank_gold
    golds[2] = timestamp_gold
    golds[3] = phase4_gold
    golds[4] = phase5_gold
    out["phase_gold_final_answers"] = golds
    out["gold_final_answer"] = clone(phase5_gold)

    canonical[2]["arguments"]["window_start"] = second_start.isoformat()
    canonical[2]["arguments"]["window_end"] = second_end.isoformat()
    canonical[3]["arguments"]["stream_id"] = second_rank_gold["stream_id"]
    canonical[3]["arguments"]["timestamp"] = requested_ts.isoformat()
    canonical[4]["arguments"]["stream_id"] = second_rank_gold["stream_id"]
    canonical[4]["arguments"]["timestamp"] = requested_ts.isoformat()
    canonical[5]["arguments"]["stream_id"] = first_rank_gold["stream_id"]
    canonical[5]["arguments"]["window_start"] = first_rank_gold["window_start"]
    canonical[5]["arguments"]["window_end"] = first_rank_gold["window_end"]
    canonical[6]["arguments"]["stream_id"] = second_rank_gold["stream_id"]
    canonical[6]["arguments"]["window_start"] = second_rank_gold["window_start"]
    canonical[6]["arguments"]["window_end"] = second_rank_gold["window_end"]
    out["canonical_tool_calls"] = canonical

    rewritten_sets: list[list[dict[str, Any]]] = []
    for call_set in acceptable_sets:
        new_set = clone(call_set)
        rank_idx = 0
        quality_idx = 0
        for call in new_set:
            tool_name = str(call.get("tool_name", ""))
            args = call.get("arguments", {})
            if tool_name == "rank_window":
                rank_idx += 1
                if rank_idx == 2:
                    args["window_start"] = second_start.isoformat()
                    args["window_end"] = second_end.isoformat()
            elif tool_name == "lookup_observation":
                args["stream_id"] = second_rank_gold["stream_id"]
                args["timestamp"] = requested_ts.isoformat()
            elif tool_name == "inspect_quality_window":
                quality_idx += 1
                if quality_idx == 1:
                    args["stream_id"] = first_rank_gold["stream_id"]
                    args["window_start"] = first_rank_gold["window_start"]
                    args["window_end"] = first_rank_gold["window_end"]
                else:
                    args["stream_id"] = second_rank_gold["stream_id"]
                    args["window_start"] = second_rank_gold["window_start"]
                    args["window_end"] = second_rank_gold["window_end"]
        rewritten_sets.append(new_set)
    out["acceptable_tool_call_sets"] = rewritten_sets

    turns[1] = (
        f"Now keep the same site and the winning signal from the last answer, and if I only know it was around "
        f"{requested_ts.strftime('%H:%M UTC on %B %-d, %Y')}, give me the nearest available reading."
    )
    out["goal_revision_turns"] = turns
    return out


def transform_row(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    out = clone(row)
    family = str(out.get("task_family", ""))
    if family not in TARGET_FAMILIES:
        return out

    phases = clone(out.get("phase_examples", []))
    if len(phases) >= 5:
        if family == "window_pairwise_compare":
            phases[0] = _set_required_fields(phases[0], ["winning_stream_id"])
            phases[1] = _set_required_fields(phases[1], ["winning_stream_id"])
        elif family == "window_rank":
            phases[0] = _set_required_fields(phases[0], ["stream_id"])
            phases[1] = _set_required_fields(phases[1], ["stream_id"])

        phases[3] = _set_required_fields(phases[3], ["preferred_reference"])
        out["phase_examples"] = phases

        turns = list(out.get("goal_revision_turns", []))
        if len(turns) >= 3:
            turns[2] = pairrank_quality_only_prompt(family)
            out["goal_revision_turns"] = turns

        meta = clone(out.get("metadata", {}))
        meta["quality_preference_mode"] = "reference_only"
        meta["pairwise_rank_phase_split"] = True
        meta["quality_preference_basis"] = "quality_only"
        if family == "window_rank":
            meta["rank_quality_prompt_aligned"] = True
        out["metadata"] = meta

    if family == "window_rank":
        out = _normalize_rank_month_revision(out, runtime)

    recompute_turn_budget(out)
    return sync_phase_examples(out)


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
            "artifact_version": "rank-calendar-contract-repair",
            "source_artifact_version": "pairwise-rank-phase-repair",
            "row_count": len(rows),
            "split_counts": {"train": 0, "dev": 0, "test": len(rows)},
            "target_families": sorted(TARGET_FAMILIES),
            "experiment_policy": {
                "name": "rank_quality_and_calendar_alignment",
                "repairs": [
                    "pairwise_rank_phase12_core_only",
                    "pairwise_rank_phase4_quality_only_reference_selection",
                    "rank_month_calendar_window_normalization",
                    "rank_month_bounded_quality_prompting",
                    "rank_reporting_prompt_alignment",
                ],
            },
        },
    )
    print(json.dumps({"output_dir": str(args.output_dir), "row_count": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
