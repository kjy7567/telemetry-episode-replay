#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from bts_agentbench.runtime import ToolStoreRuntime
from canonical_repair_common import load_jsonl, recompute_turn_budget, write_json, write_jsonl
from build_canonical_agentic_final import (
    clone,
    normalized_interaction_mode,
    phase_example_entry,
    quality_decision_from_metrics,
    reporting_commitment_candidate,
)


TARGET_FAMILIES = {"quality_gate", "window_rank"}


def shift_window(start: str, end: str, direction: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    delta = end_ts - start_ts
    return start_ts + direction * delta, end_ts + direction * delta


def zulu(ts_like: str | pd.Timestamp) -> str:
    ts = pd.Timestamp(ts_like)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def sync_phase_examples(out: dict[str, Any]) -> dict[str, Any]:
    phases = clone(out.get("phase_examples", []))
    golds = clone(out.get("phase_gold_final_answers", []))
    if len(phases) != len(golds):
        phases = []
        for idx, gold in enumerate(golds):
            family = (
                phases[idx]["task_family"]
                if idx < len(phases) and isinstance(phases[idx], dict) and phases[idx].get("task_family")
                else None
            )
            phases.append(phase_example_entry(str(family or out["task_family"]), gold))
    for idx, gold in enumerate(golds):
        phases[idx]["gold_final_answer"] = clone(gold)
    out["phase_examples"] = phases
    if phases:
        out["final_phase_example"] = clone(phases[-1])
    return out


def set_phase_required_fields(phase: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    out = clone(phase)
    out["task_accomplish_verifier"] = {
        "final_answer_checks": {
            "required_fields": list(fields),
            "numeric_tolerance": {},
            "categorical_exact_match": [],
        }
    }
    return out


def quality_trend_status(first_quality: dict[str, Any], second_quality: dict[str, Any]) -> str:
    first_decision = str(first_quality.get("decision") or "")
    second_decision = str(second_quality.get("decision") or "")
    first_obs_raw = first_quality.get("observed_fraction")
    second_obs_raw = second_quality.get("observed_fraction")
    first_gap_raw = first_quality.get("gap_ratio")
    second_gap_raw = second_quality.get("gap_ratio")

    first_available = first_obs_raw is not None and first_gap_raw is not None
    second_available = second_obs_raw is not None and second_gap_raw is not None
    if first_available and not second_available:
        return "quality_worsened"
    if second_available and not first_available:
        return "quality_improved"

    if first_decision != second_decision:
        if first_decision == "abstain" and second_decision == "answer":
            return "quality_improved"
        if first_decision == "answer" and second_decision == "abstain":
            return "quality_worsened"

    first_obs = float(first_obs_raw or 0.0)
    second_obs = float(second_obs_raw or 0.0)
    first_gap = float(first_gap_raw or 0.0)
    second_gap = float(second_gap_raw or 0.0)
    obs_delta = second_obs - first_obs
    gap_delta = first_gap - second_gap

    if obs_delta >= 0.02 and gap_delta >= -1.0:
        return "quality_improved"
    if gap_delta >= 0.5 and obs_delta >= -0.05:
        return "quality_improved"
    if obs_delta <= -0.02 and gap_delta <= 1.0:
        return "quality_worsened"
    if gap_delta <= -0.5 and obs_delta <= 0.05:
        return "quality_worsened"
    return "quality_stable"


def quality_trend_gold(first_quality: dict[str, Any], second_quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "stream_id": second_quality.get("stream_id"),
        "trend_status": quality_trend_status(first_quality, second_quality),
        "first_decision": first_quality.get("decision"),
        "second_decision": second_quality.get("decision"),
    }


def rank_stability_gold(first_rank: dict[str, Any], second_rank: dict[str, Any]) -> dict[str, Any]:
    first_stream = str(first_rank.get("stream_id") or "")
    second_stream = str(second_rank.get("stream_id") or "")
    return {
        "first_stream_id": first_stream,
        "second_stream_id": second_stream,
        "stability_status": "same_winner" if first_stream == second_stream else "winner_changed",
    }


def quality_reporting_prompt(window_start: str) -> str:
    start = pd.Timestamp(window_start)
    return (
        f"Given the second week beginning {start.strftime('%B %-d, %Y')} quality result we just discussed, "
        "should I report it as-is or abstain?"
    )


def quality_trend_prompt(previous_start: str, current_start: str) -> str:
    previous = pd.Timestamp(previous_start)
    current = pd.Timestamp(current_start)
    return (
        f"Compared with the week beginning {previous.strftime('%B %-d, %Y')}, was the week beginning "
        f"{current.strftime('%B %-d, %Y')} better, worse, or about the same for reporting quality?"
    )


def rank_quality_prompt(window_start: str) -> str:
    start = pd.Timestamp(window_start)
    return (
        f"Now keep the same site and the winning signal from the second month, and for the month beginning "
        f"{start.strftime('%B %-d, %Y')}, would you answer or abstain based on data quality?"
    )


def rank_stability_prompt(previous_start: str, current_start: str) -> str:
    previous = pd.Timestamp(previous_start)
    current = pd.Timestamp(current_start)
    return (
        f"Compared with the month beginning {previous.strftime('%B %-d, %Y')}, did the top-ranked stream for "
        f"the month beginning {current.strftime('%B %-d, %Y')} stay the same or change?"
    )


def rank_reporting_prompt(window_start: str) -> str:
    start = pd.Timestamp(window_start)
    return (
        f"Given the second month's winner and its month-bounded quality result for the month beginning "
        f"{start.strftime('%B %-d, %Y')} we just discussed, should I report it as-is or abstain?"
    )


def clarify_rank_surface(text: str) -> str:
    out = text
    out = out.replace("position reading", "position sensor")
    out = out.replace("position readings", "position sensors")
    out = out.replace("position measurement", "position sensor")
    out = out.replace("position measurements", "position sensors")
    return out


def transform_quality_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    out = clone(row)
    canonical = clone(out.get("canonical_tool_calls", []))
    quality_call = next((call for call in canonical if call.get("tool_name") == "inspect_quality_window"), None)
    resolve_call = next((call for call in canonical if call.get("tool_name") == "resolve_point"), None)
    if quality_call is None or resolve_call is None:
        return out

    first_quality = clone(out["phase_gold_final_answers"][0])
    stream_id = str(first_quality["stream_id"])
    start = str(quality_call["arguments"]["window_start"])
    end = str(quality_call["arguments"]["window_end"])
    period = str(quality_call["arguments"].get("period") or "week")
    second_start, second_end = shift_window(start, end, -1)
    second_metrics = runtime.inspect_quality_window(
        {
            "stream_id": stream_id,
            "window_start": second_start.isoformat(),
            "window_end": second_end.isoformat(),
            "period": period,
        }
    )
    second_quality = quality_decision_from_metrics(second_metrics, runtime, period, stream_id)
    second_quality["window_start"] = second_start.isoformat()
    second_quality["window_end"] = second_end.isoformat()
    second_quality["period"] = period
    trend_gold = quality_trend_gold(second_quality, first_quality)
    final_gold = reporting_commitment_candidate("quality_gate", second_quality)
    if final_gold is None:
        return out

    out["goal_revision_turns"] = [
        f"Now keep the same site and the same signal, but for the week beginning {second_start.strftime('%B %-d, %Y')}, would you answer or abstain based on data quality?",
        quality_trend_prompt(second_start.isoformat(), start),
        quality_reporting_prompt(second_start.isoformat()),
    ]
    out["phase_gold_final_answers"] = [first_quality, second_quality, trend_gold, final_gold]
    out["phase_examples"] = [
        phase_example_entry("quality_gate", first_quality, ["decision"]),
        phase_example_entry("quality_gate", second_quality, ["decision"]),
        phase_example_entry("quality_trend_assessment", trend_gold, ["trend_status"]),
        phase_example_entry("reporting_commitment", final_gold, ["commitment_action"]),
    ]
    out["gold_final_answer"] = clone(final_gold)
    out["canonical_tool_calls"] = [
        clone(resolve_call),
        clone(quality_call),
        {
            "call_id": "c3",
            "tool_name": "inspect_quality_window",
            "arguments": {
                "stream_id": stream_id,
                "window_start": zulu(second_start),
                "window_end": zulu(second_end),
                "period": period,
            },
        },
    ]
    out["acceptable_tool_call_sets"] = [clone(out["canonical_tool_calls"])]
    out["evidence"] = {"stream_ids": [stream_id]}

    verifier = clone(out.get("task_accomplish_verifier", {}))
    verifier["final_answer_checks"] = {
        "required_fields": ["commitment_action"],
        "numeric_tolerance": {},
        "categorical_exact_match": [],
    }
    verifier["process_checks"] = {
        "required_tools": ["resolve_point", "inspect_quality_window"],
        "allow_additional_tools": True,
    }
    verifier["evidence_checks"] = {"required_stream_ids": [stream_id]}
    out["task_accomplish_verifier"] = verifier

    interaction = clone(out.get("interaction_verifier", {}))
    interaction["require_goal_revision_answer"] = True
    interaction["goal_revision_turn_count"] = 3
    out["interaction_verifier"] = interaction

    metadata = clone(out.get("metadata", {}))
    metadata["phase_topology"] = "quality_revision_then_quality_trend_then_reporting_commitment"
    metadata["cross_axis_phase_type"] = "quality_trend_assessment"
    metadata["reporting_commitment_mode"] = "quality_only"
    metadata["quality_gate_redesign_v13"] = True
    metadata.pop("cross_axis_timestamp_family", None)
    metadata.pop("policy_choice_type", None)
    metadata.pop("policy_choice_gold_policy", None)
    metadata.pop("test_penalty_reporting_commitment", None)
    out["metadata"] = metadata

    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return sync_phase_examples(out)


def transform_rank_row(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
    *,
    submission_compatibility: bool = False,
) -> dict[str, Any]:
    out = clone(row)
    canonical = clone(out.get("canonical_tool_calls", []))
    list_call = next((call for call in canonical if call.get("tool_name") == "list_points"), None)
    rank_calls = [call for call in canonical if call.get("tool_name") == "rank_window"]
    if list_call is None or len(rank_calls) < 2:
        return out

    list_points_result = runtime.list_points(clone(list_call["arguments"]))

    first_rank_args = clone(rank_calls[0]["arguments"])
    first_rank_args["stream_ids"] = list_points_result["stream_ids"]
    first_rank_result = runtime.rank_window(first_rank_args)
    first_ranked = list(first_rank_result.get("ranked_streams") or [])
    if not first_ranked:
        return out
    first_rank = {
        "stream_id": first_ranked[0].get("stream_id"),
        "mean_value": first_ranked[0].get("mean_value"),
        "window_start": first_rank_result.get("window_start"),
        "window_end": first_rank_result.get("window_end"),
    }

    first_start = pd.Timestamp(first_rank["window_start"])
    if first_start.tzinfo is None:
        first_start = first_start.tz_localize("UTC")
    else:
        first_start = first_start.tz_convert("UTC")
    second_start = first_start - pd.DateOffset(months=1)
    second_end = first_start
    revision_direction = "previous"

    second_rank_args = clone(rank_calls[1]["arguments"])
    second_rank_args["stream_ids"] = list_points_result["stream_ids"]
    second_rank_args["window_start"] = second_start.isoformat()
    second_rank_args["window_end"] = second_end.isoformat()
    second_rank_result = runtime.rank_window(second_rank_args)
    second_ranked = list(second_rank_result.get("ranked_streams") or [])
    if not second_ranked:
        if submission_compatibility:
            # The submitted snapshot reused its already-materialized adjacent
            # window while preserving the first phase's original serialization.
            second_rank_args = clone(rank_calls[1]["arguments"])
            second_rank_args["stream_ids"] = list_points_result["stream_ids"]
            second_rank_result = runtime.rank_window(second_rank_args)
            second_ranked = list(second_rank_result.get("ranked_streams") or [])
            if not second_ranked:
                return out
            existing_golds = list(out.get("phase_gold_final_answers") or [])
            if existing_golds and isinstance(existing_golds[0], dict):
                existing_first = existing_golds[0]
                first_rank = {
                    "stream_id": existing_first.get("stream_id"),
                    "mean_value": existing_first.get("mean_value"),
                    "window_start": existing_first.get("window_start"),
                    "window_end": existing_first.get("window_end"),
                }
        else:
            # Fall back to the already materialized adjacent-month revision when
            # the preferred previous month predates this candidate group's data.
            second_rank_args = clone(rank_calls[1]["arguments"])
            second_rank_args["stream_ids"] = list_points_result["stream_ids"]
            second_rank_result = runtime.rank_window(second_rank_args)
            second_ranked = list(second_rank_result.get("ranked_streams") or [])
            if not second_ranked:
                return out
            fallback_start = pd.Timestamp(second_rank_result.get("window_start"))
            if fallback_start.tzinfo is None:
                fallback_start = fallback_start.tz_localize("UTC")
            else:
                fallback_start = fallback_start.tz_convert("UTC")
            revision_direction = "next" if fallback_start > first_start else "alternate"
    second_rank = {
        "stream_id": second_ranked[0].get("stream_id"),
        "mean_value": second_ranked[0].get("mean_value"),
        "window_start": second_rank_result.get("window_start"),
        "window_end": second_rank_result.get("window_end"),
    }

    stability = rank_stability_gold(first_rank, second_rank)
    second_stream = str(second_rank["stream_id"])
    second_start = str(second_rank["window_start"])
    second_end = str(second_rank["window_end"])
    second_metrics = runtime.inspect_quality_window(
        {
            "stream_id": second_stream,
            "window_start": second_start,
            "window_end": second_end,
            "period": "month",
        }
    )
    second_quality = quality_decision_from_metrics(second_metrics, runtime, "month", second_stream)
    second_quality["window_start"] = second_start
    second_quality["window_end"] = second_end
    second_quality["period"] = "month"
    final_gold = reporting_commitment_candidate("quality_gate", second_quality)
    if final_gold is None:
        return out

    if revision_direction == "previous":
        revision_prompt = "Now keep the same candidate group and site, but rank the previous month."
        stability_prompt = rank_stability_prompt(second_start, first_rank["window_start"])
    elif revision_direction == "next":
        revision_prompt = "Now keep the same candidate group and site, but rank the next month."
        stability_prompt = rank_stability_prompt(first_rank["window_start"], second_start)
    else:
        revision_prompt = "Now keep the same candidate group and site, but rank the adjacent available month."
        stability_prompt = rank_stability_prompt(first_rank["window_start"], second_start)

    out["goal_revision_turns"] = [
        revision_prompt,
        stability_prompt,
        rank_quality_prompt(second_start),
        rank_reporting_prompt(second_start),
    ]
    out["phase_gold_final_answers"] = [first_rank, second_rank, stability, second_quality, final_gold]
    out["phase_examples"] = [
        phase_example_entry("window_rank", first_rank, ["stream_id"]),
        phase_example_entry("window_rank", second_rank, ["stream_id"]),
        phase_example_entry("rank_stability_assessment", stability, ["stability_status"]),
        phase_example_entry("quality_gate", second_quality, ["decision"]),
        phase_example_entry("reporting_commitment", final_gold, ["commitment_action"]),
    ]
    out["gold_final_answer"] = clone(final_gold)
    out["canonical_tool_calls"] = [
        clone(list_call),
        {
            "call_id": "c2",
            "tool_name": "rank_window",
            "arguments": {
                "stream_ids": "$c1.stream_ids",
                "metric": "mean_value",
                "window_start": first_rank["window_start"],
                "window_end": first_rank["window_end"],
                "period": "month",
                "order": "desc",
                "topk": 1,
            },
        },
        {
            "call_id": "c3",
            "tool_name": "rank_window",
            "arguments": {
                "stream_ids": "$c1.stream_ids",
                "metric": "mean_value",
                "window_start": second_start,
                "window_end": second_end,
                "period": "month",
                "order": "desc",
                "topk": 1,
            },
        },
        {
            "call_id": "c4",
            "tool_name": "inspect_quality_window",
            "arguments": {
                "stream_id": second_stream,
                "window_start": zulu(second_start),
                "window_end": zulu(second_end),
                "period": "month",
            },
        },
    ]
    out["acceptable_tool_call_sets"] = [clone(out["canonical_tool_calls"])]
    out["evidence"] = {"stream_ids": [second_stream]}

    verifier = clone(out.get("task_accomplish_verifier", {}))
    verifier["final_answer_checks"] = {
        "required_fields": ["commitment_action"],
        "numeric_tolerance": {},
        "categorical_exact_match": [],
    }
    verifier["process_checks"] = {
        "required_tools": ["list_points", "rank_window", "inspect_quality_window"],
        "allow_additional_tools": True,
    }
    verifier["evidence_checks"] = {"required_stream_ids": [second_stream]}
    out["task_accomplish_verifier"] = verifier

    for field in ("query", "initial_user_message", "hidden_user_instruction", "followup_prompt"):
        if isinstance(out.get(field), str):
            out[field] = clarify_rank_surface(out[field])

    metadata = clone(out.get("metadata", {}))
    metadata["phase_topology"] = "rank_revision_then_stability_then_quality_gate_then_reporting_commitment"
    metadata["cross_axis_phase_type"] = "rank_stability_assessment"
    metadata["reporting_commitment_mode"] = "quality_only"
    metadata["window_rank_redesign_v14"] = True
    if revision_direction != "previous":
        metadata["rank_revision_direction"] = revision_direction
    metadata.pop("cross_axis_timestamp_family", None)
    metadata.pop("policy_choice_type", None)
    metadata.pop("policy_choice_gold_policy", None)
    metadata.pop("quality_preference_mode", None)
    metadata.pop("quality_preference_basis", None)
    metadata.pop("test_penalty_reporting_commitment", None)
    metadata.pop("pairwise_rank_phase_split_v12", None)
    metadata.pop("rank_quality_prompt_v12", None)
    out["metadata"] = metadata

    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return sync_phase_examples(out)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality-source-dir", type=Path, required=True)
    parser.add_argument("--rank-source-dir", type=Path, required=True)
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime = ToolStoreRuntime(args.tool_store_db)
    try:
        rows: list[dict[str, Any]] = []
        for row in load_jsonl(args.quality_source_dir / "test.jsonl"):
            if row.get("task_family") == "quality_gate":
                rows.append(transform_quality_row(row, runtime))
        for row in load_jsonl(args.rank_source_dir / "test.jsonl"):
            if row.get("task_family") == "window_rank":
                rows.append(transform_rank_row(row, runtime))
    finally:
        runtime.close()

    rows.sort(key=lambda row: str(row.get("scenario_id") or ""))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train.jsonl", [])
    write_jsonl(args.output_dir / "dev.jsonl", [])
    write_jsonl(args.output_dir / "test.jsonl", rows)
    write_json(
        args.output_dir / "manifest.json",
        {
            "artifact_version": "bts-canonical-seed-test-penalty-experiment-v15-quality-rank",
            "quality_source": str(args.quality_source_dir),
            "rank_source": str(args.rank_source_dir),
            "row_count": len(rows),
            "split_counts": {"train": 0, "dev": 0, "test": len(rows)},
            "target_families": sorted(TARGET_FAMILIES),
            "experiment_policy": {
                "name": "quality_trend_and_rank_stability_v15",
                "repairs": [
                    "quality_gate_two_window_trend_phase_metric_aligned",
                    "quality_gate_quality_only_reporting_commitment",
                    "window_rank_winner_stability_phase_gpt_friendly",
                    "window_rank_revised_month_quality_gate",
                    "window_rank_quality_only_reporting_commitment",
                    "utc_z_window_argument_normalization",
                    "rank_surface_position_sensor_disambiguation",
                ],
            },
        },
    )


if __name__ == "__main__":
    main()
