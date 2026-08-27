#!/usr/bin/env python
from __future__ import annotations

"""Shared helpers for typed canonical episode repairs."""

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from bts_agentbench.runtime import ToolStoreRuntime
from build_canonical_agentic_final import (
    CANONICAL_VERSION,
    LIFT_VERSION,
    append_phase_example,
    append_quality_preference_phase,
    combined_reporting_commitment_candidate_for_row,
    combined_reporting_commitment_prompt_for_row,
    clone,
    ensure_phase_gold,
    normalized_interaction_mode,
    quality_gold_for_context,
    row_phase_examples,
    strict_timestamp_quality_reporting_commitment_candidate_for_row,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


TIMESTAMP_PHASES = {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"}
QUALITY_PHASES = {"quality_gate", "quality_preference"}


def phase_families(row: dict[str, Any]) -> list[str]:
    return [str(example.get("task_family", "")) for example in row_phase_examples(row)]


def phase_golds(row: dict[str, Any]) -> list[dict[str, Any]]:
    return list(row.get("phase_gold_final_answers", []))


def last_phase_match(row: dict[str, Any], families: set[str]) -> tuple[str, dict[str, Any]] | None:
    for example, gold in reversed(list(zip(row_phase_examples(row), phase_golds(row)))):
        family = str(example.get("task_family", ""))
        if family in families:
            return family, clone(gold)
    return None


def recompute_turn_budget(row: dict[str, Any]) -> None:
    verifier = clone(row.get("interaction_verifier", {}))
    verifier["require_goal_revision_answer"] = bool(row.get("goal_revision_turns"))
    verifier["goal_revision_turn_count"] = len(row.get("goal_revision_turns", []))
    verifier["max_user_turns"] = (
        2
        + len(row.get("required_clarification_slots", []))
        + len(row.get("post_answer_user_turns", []))
        + len(row.get("goal_revision_turns", []))
    )
    row["interaction_verifier"] = verifier

    difficulty = clone(row.get("difficulty_proxy", {}))
    difficulty["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    difficulty["state_carryover_required"] = bool(row.get("goal_revision_turns"))
    row["difficulty_proxy"] = difficulty

    contract = clone(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    contract["terminal_phase_index"] = max(0, len(row.get("phase_gold_final_answers", [])) - 1)
    row["goal_revision_contract"] = contract

    if row.get("phase_examples"):
        row["final_phase_example"] = clone(row["phase_examples"][-1])
        final_checks = clone(row["final_phase_example"].get("task_accomplish_verifier", {}).get("final_answer_checks", {}))
        task_verifier = clone(row.get("task_accomplish_verifier", {}))
        task_verifier["final_answer_checks"] = final_checks
        row["task_accomplish_verifier"] = task_verifier


def strip_terminal_reporting_commitment(row: dict[str, Any]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    families = phase_families(out)
    if not families or families[-1] != "reporting_commitment":
        return out
    out["phase_examples"] = out["phase_examples"][:-1]
    out["phase_gold_final_answers"] = out["phase_gold_final_answers"][:-1]
    out["goal_revision_turns"] = list(out.get("goal_revision_turns", []))[:-1]
    if out["phase_gold_final_answers"]:
        out["gold_final_answer"] = clone(out["phase_gold_final_answers"][-1])
    metadata = clone(out.get("metadata", {}))
    metadata["terminal_reporting_removed_for_test_penalty"] = True
    out["metadata"] = metadata
    recompute_turn_budget(out)
    return out


def needs_quality_penalty_repair(row: dict[str, Any]) -> bool:
    families = phase_families(row)
    has_quality = any(family in QUALITY_PHASES for family in families)
    has_timestamp = any(family in TIMESTAMP_PHASES for family in families)
    return has_timestamp and not has_quality


def timestamp_phase_count(row: dict[str, Any]) -> int:
    return sum(1 for family in phase_families(row) if family in {"timestamp_value_lookup", "timestamp_nearest_lookup"})


def append_combined_reporting_commitment(row: dict[str, Any]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    commitment_gold = combined_reporting_commitment_candidate_for_row(out)
    out["goal_revision_turns"] = list(out.get("goal_revision_turns", [])) + [
        combined_reporting_commitment_prompt_for_row(out, commitment_gold)
    ]
    out["phase_gold_final_answers"] = list(out.get("phase_gold_final_answers", [])) + [clone(commitment_gold)]
    append_phase_example(out, "reporting_commitment", commitment_gold)
    out["gold_final_answer"] = clone(commitment_gold)
    metadata = clone(out.get("metadata", {}))
    metadata["test_penalty_reporting_commitment"] = "combined_evidence"
    out["metadata"] = metadata
    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return out


def latest_timestamp_gold(row: dict[str, Any]) -> dict[str, Any] | None:
    match = last_phase_match(row, {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"})
    return None if match is None else match[1]


def week_window_for_timestamp(observed_timestamp: str) -> tuple[str, str]:
    ts = pd.Timestamp(observed_timestamp)
    day_start = ts.floor("D")
    week_start = day_start - pd.Timedelta(days=day_start.weekday())
    week_end = week_start + pd.Timedelta(days=7)
    return week_start.isoformat(), week_end.isoformat()


def timestamp_local_quality_prompt(row: dict[str, Any], quality_gold: dict[str, Any]) -> str:
    start = pd.Timestamp(str(quality_gold["window_start"]))
    return (
        f"Now thinking about the week beginning {start.strftime('%B %-d, %Y')} that contains that observed timestamp, "
        "would you answer or abstain for reporting based on data quality?"
    )


def append_timestamp_local_quality_phase(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    timestamp_gold = latest_timestamp_gold(out)
    if timestamp_gold is None:
        return out
    stream_id = timestamp_gold.get("stream_id")
    observed_timestamp = timestamp_gold.get("observed_timestamp")
    if not isinstance(stream_id, str) or not isinstance(observed_timestamp, str):
        return out

    window_start, window_end = week_window_for_timestamp(observed_timestamp)
    quality_context = {
        "stream_id": stream_id,
        "window_start": window_start,
        "window_end": window_end,
        "period": "week",
    }
    quality_gold = quality_gold_for_context(quality_context, runtime)
    if quality_gold is None:
        return out

    out["goal_revision_turns"] = list(out.get("goal_revision_turns", [])) + [timestamp_local_quality_prompt(out, quality_gold)]
    out["phase_gold_final_answers"] = list(out.get("phase_gold_final_answers", [])) + [clone(quality_gold)]
    append_phase_example(out, "quality_gate", quality_gold)
    out["gold_final_answer"] = clone(quality_gold)

    canonical_calls = clone(out.get("canonical_tool_calls", []))
    canonical_calls.append(
        {
            "call_id": f"c{len(canonical_calls) + 1}",
            "tool_name": "inspect_quality_window",
            "arguments": {
                "stream_id": stream_id,
                "window_start": window_start,
                "window_end": window_end,
                "period": "week",
            },
        }
    )
    out["canonical_tool_calls"] = canonical_calls

    expanded_variants: list[list[dict[str, Any]]] = []
    for variant in out.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_copy.append(
            {
                "call_id": f"c{len(variant_copy) + 1}",
                "tool_name": "inspect_quality_window",
                "arguments": {
                    "stream_id": stream_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "period": "week",
                },
            }
        )
        expanded_variants.append(variant_copy)
    out["acceptable_tool_call_sets"] = expanded_variants

    metadata = clone(out.get("metadata", {}))
    metadata["test_penalty_timestamp_quality_window"] = {
        "stream_id": stream_id,
        "window_start": window_start,
        "window_end": window_end,
        "period": "week",
    }
    out["metadata"] = metadata
    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return out


def append_strict_timestamp_reporting_commitment(row: dict[str, Any]) -> dict[str, Any]:
    out = ensure_phase_gold(clone(row))
    commitment_gold = strict_timestamp_quality_reporting_commitment_candidate_for_row(out)
    out["goal_revision_turns"] = list(out.get("goal_revision_turns", [])) + [
        combined_reporting_commitment_prompt_for_row(out, commitment_gold)
    ]
    out["phase_gold_final_answers"] = list(out.get("phase_gold_final_answers", [])) + [clone(commitment_gold)]
    append_phase_example(out, "reporting_commitment", commitment_gold)
    out["gold_final_answer"] = clone(commitment_gold)
    metadata = clone(out.get("metadata", {}))
    metadata["test_penalty_reporting_commitment"] = "timestamp_quality_strict"
    out["metadata"] = metadata
    out["interaction_mode"] = normalized_interaction_mode(out)
    recompute_turn_budget(out)
    return out


def transform_test_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    out = strip_terminal_reporting_commitment(row)
    family = str(out.get("task_family", ""))

    if family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
        out = append_timestamp_local_quality_phase(out, runtime)
        out = append_strict_timestamp_reporting_commitment(out)
        return out
    if needs_quality_penalty_repair(out):
        out = append_quality_preference_phase(out, runtime)
    out = append_combined_reporting_commitment(out)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-benchmark-dir", type=Path, required=True)
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source_benchmark_dir
    out_dir = args.output_dir

    runtime = ToolStoreRuntime(args.tool_store_db)
    try:
        test_rows = [transform_test_row(row, runtime) for row in load_jsonl(source / "test.jsonl")]
    finally:
        runtime.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "train.jsonl", [])
    write_jsonl(out_dir / "dev.jsonl", [])
    write_jsonl(out_dir / "test.jsonl", test_rows)

    manifest = {
        "artifact_version": f"{CANONICAL_VERSION}-test-penalty-experiment",
        "lifting_version": LIFT_VERSION,
        "source_artifact_version": CANONICAL_VERSION,
        "row_count": len(test_rows),
        "split_counts": {"train": 0, "dev": 0, "test": len(test_rows)},
        "experiment_policy": {
            "name": "telemetry_contract_alignment",
            "repairs": [
                "timestamp_rows_add_local_quality_gate",
                "timestamp_rows_use_strict_timestamp_quality_reporting_commitment",
                "non_timestamp_rows_add_quality_phase_when_timestamp_present_but_quality_absent",
                "replace_terminal_reporting_commitment_with_combined_evidence_commitment",
            ],
        },
    }
    write_json(out_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
