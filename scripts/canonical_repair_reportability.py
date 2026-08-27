#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from canonical_repair_common import (
    append_phase_example,
    clone,
    ensure_phase_gold,
    load_jsonl,
    normalized_interaction_mode,
    recompute_turn_budget,
    write_json,
    write_jsonl,
)


TARGET_FAMILIES = {
    "point_disambiguation",
    "window_mean_lookup",
    "timestamp_value_lookup",
    "timestamp_nearest_lookup",
}


def reportability_prompt() -> str:
    return (
        "Given the public-time reading and the data-quality check, should I report it as-is, abstain, "
        "or ask for more time detail before reporting it?"
    )


def resolution_context_candidate(
    timestamp_gold: dict[str, Any],
    quality_gold: dict[str, Any] | None = None,
    *,
    unique_nearest_threshold_seconds: float = 30.0,
) -> dict[str, Any]:
    gold = clone(timestamp_gold)
    exact = bool(gold.get("exact_match_found", False))
    offset = gold.get("offset_seconds")
    if exact:
        status = "exact"
    elif offset is not None and float(offset) <= unique_nearest_threshold_seconds:
        status = "unique_nearest"
    else:
        status = "ambiguous_nearest"
    return {
        "stream_id": gold.get("stream_id"),
        "requested_timestamp": gold.get("requested_timestamp"),
        "observed_timestamp": gold.get("observed_timestamp"),
        "exact_match_found": exact,
        "offset_seconds": offset,
        "resolution_status": status,
        "quality_decision": None if quality_gold is None else quality_gold.get("decision"),
        "quality_reason": None if quality_gold is None else quality_gold.get("reason"),
    }


def reportability_commitment(resolution_gold: dict[str, Any]) -> dict[str, Any]:
    quality_decision = str(resolution_gold.get("quality_decision") or "")
    quality_reason = str(resolution_gold.get("quality_reason") or "")
    if quality_decision:
        if quality_decision == "abstain":
            if quality_reason in {"low_coverage", "long_gap"}:
                return {"commitment_action": "abstain", "reason": quality_reason}
            return {"commitment_action": "abstain", "reason": "marginal_quality"}
        if quality_decision != "answer" or quality_reason != "healthy":
            return {"commitment_action": "abstain", "reason": "marginal_quality"}

    status = str(resolution_gold.get("resolution_status") or "")
    if status == "exact":
        return {"commitment_action": "answer", "reason": "exact_timestamp"}
    if status == "unique_nearest":
        return {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
    return {"commitment_action": "re_clarify", "reason": "timestamp_too_imprecise"}


def latest_phase_gold(row: dict[str, Any], family: str) -> dict[str, Any] | None:
    phases = row.get("phase_examples") or []
    golds = row.get("phase_gold_final_answers") or []
    for ex, gold in reversed(list(zip(phases, golds))):
        if str(ex.get("task_family", "")) == family:
            return clone(gold)
    return None


def latest_timestamp_gold(row: dict[str, Any]) -> dict[str, Any] | None:
    for fam in ("timestamp_nearest_lookup", "timestamp_value_lookup"):
        gold = latest_phase_gold(row, fam)
        if gold is not None:
            return gold
    return None


def latest_quality_gold(row: dict[str, Any]) -> dict[str, Any] | None:
    return latest_phase_gold(row, "quality_gate")


def collapse_timestamp_value_variants(row: dict[str, Any]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    if str(out.get("task_family", "")) != "timestamp_value_lookup":
        return out

    extra_variants: list[list[dict[str, Any]]] = []
    for variant in out.get("acceptable_tool_call_sets", []):
        lookups = [idx for idx, call in enumerate(variant) if call.get("tool_name") == "lookup_observation"]
        if len(lookups) < 3:
            continue
        calls = [variant[idx] for idx in lookups]
        first, second, third = calls[:3]
        second_args = second.get("arguments", {})
        third_args = third.get("arguments", {})
        if (
            second_args.get("stream_id") == third_args.get("stream_id")
            and second_args.get("timestamp") == third_args.get("timestamp")
            and second_args.get("mode") == "exact"
            and third_args.get("mode") == "nearest"
        ):
            collapsed: list[dict[str, Any]] = []
            for idx, call in enumerate(variant):
                if idx == lookups[1]:
                    continue
                collapsed.append(clone(call))
            for i, call in enumerate(collapsed, start=1):
                call["call_id"] = f"c{i}"
            extra_variants.append(collapsed)
    if extra_variants:
        out["acceptable_tool_call_sets"] = list(out.get("acceptable_tool_call_sets", [])) + extra_variants
    return out


def transform_row(row: dict[str, Any]) -> dict[str, Any]:
    family = str(row.get("task_family", ""))
    if family not in TARGET_FAMILIES:
        return clone(row)

    out = ensure_phase_gold(clone(row))
    phase_examples = list(out.get("phase_examples", []))
    phase_gold = list(out.get("phase_gold_final_answers", []))
    goal_turns = list(out.get("goal_revision_turns", []))

    if family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
        if len(phase_examples) >= 2 and str(phase_examples[-1].get("task_family", "")) == "reporting_commitment":
            phase_examples = phase_examples[:-1]
            phase_gold = phase_gold[:-1]
            goal_turns = goal_turns[:-1]
        if len(phase_examples) >= 2 and str(phase_examples[-1].get("task_family", "")) == "timestamp_resolution_context":
            phase_examples = phase_examples[:-1]
            phase_gold = phase_gold[:-1]
            goal_turns = goal_turns[:-1]
    else:
        if phase_examples and str(phase_examples[-1].get("task_family", "")) == "reporting_commitment":
            phase_examples = phase_examples[:-1]
            phase_gold = phase_gold[:-1]
            goal_turns = goal_turns[:-1]

    out["phase_examples"] = phase_examples
    out["phase_gold_final_answers"] = phase_gold
    out["goal_revision_turns"] = goal_turns

    timestamp_gold = latest_timestamp_gold(out)
    quality_gold = latest_quality_gold(out)
    if timestamp_gold is None or quality_gold is None:
        out["interaction_mode"] = normalized_interaction_mode(out)
        recompute_turn_budget(out)
        return out

    resolution_gold = resolution_context_candidate(timestamp_gold, quality_gold)
    commitment_gold = reportability_commitment(resolution_gold)

    out["goal_revision_turns"] = list(out.get("goal_revision_turns", [])) + [reportability_prompt()]
    out["phase_gold_final_answers"] = list(out.get("phase_gold_final_answers", [])) + [clone(commitment_gold)]
    append_phase_example(out, "timestamp_reportability_decision", commitment_gold)
    out["gold_final_answer"] = clone(commitment_gold)
    out["final_phase_example"] = clone(out["phase_examples"][-1])
    out["final_phase_example"]["task_accomplish_verifier"] = {
        "final_answer_checks": {
            "required_fields": ["commitment_action"],
            "numeric_tolerance": {},
            "categorical_exact_match": [],
        }
    }
    out["phase_examples"][-1] = clone(out["final_phase_example"])

    meta = clone(out.get("metadata", {}))
    meta["timestamp_reportability_contract_aligned"] = True
    out["metadata"] = meta
    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    out = collapse_timestamp_value_variants(out)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-benchmark-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = [transform_row(row) for row in load_jsonl(args.source_benchmark_dir / "test.jsonl")]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train.jsonl", [])
    write_jsonl(args.output_dir / "dev.jsonl", [])
    write_jsonl(args.output_dir / "test.jsonl", rows)
    write_json(
        args.output_dir / "manifest.json",
        {
            "artifact_version": "timestamp-reportability-contract-repair",
            "source_artifact_version": "single-stream-contract-repair",
            "row_count": len(rows),
            "split_counts": {"train": 0, "dev": 0, "test": len(rows)},
            "target_families": sorted(TARGET_FAMILIES),
            "experiment_policy": {
                "name": "timestamp_reportability_contract_alignment",
                "repairs": [
                    "replace_reporting_commitment_with_single_reportability_decision_phase",
                    "keep_evidence_followup_unchanged",
                    "expand_timestamp_value_acceptable_paths_for_agent_friendly_nearest_probe",
                ],
            },
        },
    )
    print(json.dumps({"output_dir": str(args.output_dir), "row_count": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
