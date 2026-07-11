#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bts_agentbench.runtime import ToolStoreRuntime

from canonical_repair_common import clone, load_jsonl, recompute_turn_budget, write_json, write_jsonl
from build_canonical_agentic_final import quality_decision_from_metrics


TARGET_FAMILIES = {"quality_gate", "window_pairwise_compare", "window_rank"}


def commit_from_timestamp_and_quality(
    timestamp_gold: dict[str, Any],
    quality_gold: dict[str, Any],
    prompt: str,
    *,
    unique_nearest_threshold_seconds: float = 30.0,
) -> dict[str, Any]:
    if quality_gold.get("decision") == "abstain":
        return {
            "commitment_action": "abstain",
            "reason": str(quality_gold.get("reason") or "marginal_quality"),
        }

    offset = timestamp_gold.get("offset_seconds")
    exact = bool(timestamp_gold.get("exact_match_found", False))
    if not exact and offset is not None and float(offset) > unique_nearest_threshold_seconds:
        if "more precise timestamp" in prompt or "ask you for a more precise timestamp" in prompt:
            return {
                "commitment_action": "re_clarify",
                "reason": "timestamp_too_imprecise",
                "clarification_request": "more_precise_timestamp",
            }

    return {
        "commitment_action": "answer",
        "reason": "exact_timestamp" if exact else "nearest_but_acceptable",
    }


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


def _prefer_reference(
    first_quality: dict[str, Any],
    second_quality: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    first_decision = str(first_quality.get("decision") or "")
    second_decision = str(second_quality.get("decision") or "")
    if first_decision != second_decision:
        if first_decision == "answer":
            return "first", first_quality
        if second_decision == "answer":
            return "second", second_quality
    first_obs = float(first_quality.get("observed_fraction") or 0.0)
    second_obs = float(second_quality.get("observed_fraction") or 0.0)
    if abs(first_obs - second_obs) > 1e-9:
        if first_obs > second_obs:
            return "first", first_quality
        return "second", second_quality
    first_gap = float(first_quality.get("gap_ratio") or 1e18)
    second_gap = float(second_quality.get("gap_ratio") or 1e18)
    if first_gap <= second_gap:
        return "first", first_quality
    return "second", second_quality


def _rewrite_rank_calls(
    call_sets: list[list[dict[str, Any]]],
    *,
    first_rank_gold: dict[str, Any],
    second_rank_gold: dict[str, Any],
    timestamp_gold: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    out_sets: list[list[dict[str, Any]]] = []
    for call_set in call_sets:
        new_set = clone(call_set)
        quality_idx = 0
        for call in new_set:
            tool_name = str(call.get("tool_name", ""))
            args = call.get("arguments", {})
            if tool_name == "rank_window":
                if args.get("window_start") == first_rank_gold["window_start"] and args.get("window_end") == first_rank_gold["window_end"]:
                    args["window_start"] = first_rank_gold["window_start"]
                    args["window_end"] = first_rank_gold["window_end"]
                elif args.get("window_start") == second_rank_gold["window_start"] and args.get("window_end") == second_rank_gold["window_end"]:
                    args["window_start"] = second_rank_gold["window_start"]
                    args["window_end"] = second_rank_gold["window_end"]
            elif tool_name == "lookup_observation":
                args["stream_id"] = second_rank_gold["stream_id"]
                args["timestamp"] = timestamp_gold["requested_timestamp"]
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
        out_sets.append(new_set)
    return out_sets


def realign_rank_quality(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    out = clone(row)
    phases = out.get("phase_examples", [])
    golds = out.get("phase_gold_final_answers", [])
    if len(phases) < 5:
        return out
    if str(phases[0].get("task_family", "")) != "window_rank":
        return out
    if str(phases[3].get("task_family", "")) != "quality_preference":
        return out

    canonical = clone(out.get("canonical_tool_calls", []))
    if len(canonical) < 7:
        return sync_phase_examples(out)

    list_points_out = runtime.list_points(clone(canonical[0]["arguments"]))
    first_rank_args = clone(canonical[1]["arguments"])
    first_rank_args["stream_ids"] = list_points_out["stream_ids"]
    second_rank_args = clone(canonical[2]["arguments"])
    second_rank_args["stream_ids"] = list_points_out["stream_ids"]

    first_rank_result = runtime.rank_window(first_rank_args)
    second_rank_result = runtime.rank_window(second_rank_args)
    first_rank_gold = _rank_phase_gold_from_result(first_rank_result)
    second_rank_gold = _rank_phase_gold_from_result(second_rank_result)

    nearest_args = clone(canonical[4]["arguments"])
    nearest_args["stream_id"] = second_rank_gold["stream_id"]
    timestamp_gold = runtime.lookup_observation(nearest_args)

    period = str(golds[3].get("period", "month"))
    first_metrics = runtime.inspect_quality_window(
        {
            "stream_id": first_rank_gold["stream_id"],
            "window_start": first_rank_gold["window_start"],
            "window_end": first_rank_gold["window_end"],
            "period": period,
        }
    )
    second_metrics = runtime.inspect_quality_window(
        {
            "stream_id": second_rank_gold["stream_id"],
            "window_start": second_rank_gold["window_start"],
            "window_end": second_rank_gold["window_end"],
            "period": period,
        }
    )
    first_quality = quality_decision_from_metrics(
        first_metrics,
        runtime,
        period,
        str(first_rank_gold["stream_id"]),
    )
    second_quality = quality_decision_from_metrics(
        second_metrics,
        runtime,
        period,
        str(second_rank_gold["stream_id"]),
    )
    preferred_reference, chosen_quality = _prefer_reference(first_quality, second_quality)
    preferred_gold = first_rank_gold if preferred_reference == "first" else second_rank_gold
    new_quality = clone(chosen_quality)
    preferred_args = first_rank_args if preferred_reference == "first" else second_rank_args
    new_quality["window_start"] = preferred_args["window_start"]
    new_quality["window_end"] = preferred_args["window_end"]
    new_quality["period"] = period
    new_quality["preferred_reference"] = preferred_reference

    prompt = out.get("goal_revision_turns", [])[-1] if out.get("goal_revision_turns") else ""
    new_final = commit_from_timestamp_and_quality(timestamp_gold, new_quality, prompt)

    golds[0] = first_rank_gold
    golds[1] = second_rank_gold
    golds[2] = timestamp_gold
    golds[3] = new_quality
    golds[4] = new_final
    out["phase_gold_final_answers"] = golds
    out["gold_final_answer"] = clone(new_final)
    out["canonical_tool_calls"] = _rewrite_rank_calls(
        [clone(canonical)],
        first_rank_gold=first_rank_gold,
        second_rank_gold=second_rank_gold,
        timestamp_gold=timestamp_gold,
    )[0]
    out["acceptable_tool_call_sets"] = _rewrite_rank_calls(
        clone(out.get("acceptable_tool_call_sets", [])),
        first_rank_gold=first_rank_gold,
        second_rank_gold=second_rank_gold,
        timestamp_gold=timestamp_gold,
    )
    return sync_phase_examples(out)


def fix_pairwise_commitment(row: dict[str, Any]) -> dict[str, Any]:
    out = clone(row)
    phases = out.get("phase_examples", [])
    golds = out.get("phase_gold_final_answers", [])
    if len(phases) < 5:
        return out
    if str(phases[0].get("task_family", "")) != "window_pairwise_compare":
        return out
    if str(phases[3].get("task_family", "")) != "quality_preference":
        return out

    timestamp_gold = clone(golds[2])
    quality_gold = clone(golds[3])
    prompt = out.get("goal_revision_turns", [])[-1] if out.get("goal_revision_turns") else ""
    new_final = commit_from_timestamp_and_quality(timestamp_gold, quality_gold, prompt)
    golds[4] = new_final
    out["phase_gold_final_answers"] = golds
    out["gold_final_answer"] = clone(new_final)
    return sync_phase_examples(out)


def transform_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    fam = str(row.get("task_family", ""))
    if fam not in TARGET_FAMILIES:
        return clone(row)
    out = clone(row)
    if fam == "window_pairwise_compare":
        out = fix_pairwise_commitment(out)
    elif fam == "window_rank":
        out = realign_rank_quality(out, runtime)
    meta = clone(out.get("metadata", {}))
    meta["test_penalty_v8_rest_alignment"] = True
    out["metadata"] = meta
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
            "artifact_version": "bts-canonical-seed-test-penalty-experiment-v9",
            "source_artifact_version": "bts-canonical-seed-test-penalty-experiment-v7",
            "row_count": len(rows),
            "split_counts": {"train": 0, "dev": 0, "test": len(rows)},
            "target_families": sorted(TARGET_FAMILIES),
            "experiment_policy": {
                "name": "rest_family_alignment_v9",
                "repairs": [
                    "quality_gate_rationale_followup_alignment",
                    "window_pairwise_commitment_alignment_to_quality",
                    "window_rank_quality_phase_realignment_to_timestamp_reference",
                    "phase_example_gold_sync",
                    "window_rank_canonical_call_realignment",
                ],
            },
        },
    )
    print(json.dumps({"output_dir": str(args.output_dir), "row_count": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
