#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bts_agentbench.evaluator import _materialize_phase_example
from bts_agentbench.operator_answer import check_operator_answer, render_operator_answer
from bts_agentbench.runtime import ToolStoreRuntime
from build_canonical_agentic_final import (
    CANONICAL_VERSION,
    LIFT_VERSION,
    combined_reporting_commitment_candidate_for_row,
    normalized_interaction_mode,
    quality_preference_candidate_for_row,
    reporting_commitment_candidate,
    strict_timestamp_quality_reporting_commitment_candidate_for_row,
    timestamp_preference_candidate_for_row,
)


def load_rows(benchmark_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in ("train", "dev", "test"):
        path = benchmark_dir / f"{split}.jsonl"
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            row["_split"] = split
            rows.append(row)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def quality_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return any(
        phrase in text
        for phrase in (
            "answer or abstain",
            "answer-or-abstain",
            "would you answer or abstain",
            "make the same answer-or-abstain decision",
            "make the answer-or-abstain decision",
        )
    )


def point_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return any(
        phrase in text
        for phrase in (
            "which stream should i use",
            "use the first point we talked about",
            "if the operator meant",
            "alternate one",
        )
    )


def timestamp_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return any(phrase in text for phrase in ("nearest available reading", "nearest", "nearby reading", "around "))


def quality_preference_prompt_looks_consistent(prompt: str, *, reference_only: bool = False) -> bool:
    text = prompt.lower()
    if reference_only:
        return "first and second" in text and "trust more" in text and "reporting" in text
    return (
        "first and second" in text
        and "trust more" in text
        and "reporting" in text
        and "answer or abstain" in text
        and (
            "using the first result's" in text
            or "using the shared week beginning" in text
            or "using the shared day of" in text
            or "using the shared month of" in text
            or "using the same two time ranges" in text
            or "week beginning" in text
            or "month of" in text
            or "day of" in text
        )
    )


def timestamp_preference_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return "first and second timestamp" in text and "trust more" in text and "reporting" in text


def reporting_commitment_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return "report it as-is" in text and "abstain" in text and (
        "more precise timestamp" in text or "narrower time range" in text
    )


def quality_only_reporting_commitment_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return "report it as-is" in text and "abstain" in text and "quality" in text


def combined_reporting_commitment_prompt_looks_consistent(prompt: str) -> bool:
    text = prompt.lower()
    return "abstain" in text and (
        "timestamped reading" in text
        or "data-quality check" in text
        or "data-quality result" in text
    )


def audit_contract(benchmark_dir: Path, tool_store_db: Path) -> dict[str, Any]:
    rows = load_rows(benchmark_dir)
    manifest_path = benchmark_dir / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    runtime = ToolStoreRuntime(tool_store_db)
    issues: list[dict[str, Any]] = []
    issue_type_counts: Counter[str] = Counter()
    family_issue_counts: dict[str, Counter[str]] = defaultdict(Counter)
    split_issue_counts: dict[str, Counter[str]] = defaultdict(Counter)

    def add_issue(row: dict[str, Any], kind: str, detail: dict[str, Any], phase_index: int | None = None) -> None:
        record = {
            "scenario_id": row["scenario_id"],
            "split": row["_split"],
            "task_family": row["task_family"],
            "kind": kind,
            "detail": detail,
        }
        if phase_index is not None:
            record["phase_index"] = phase_index
        issues.append(record)
        issue_type_counts[kind] += 1
        family_issue_counts[row["task_family"]][kind] += 1
        split_issue_counts[row["_split"]][kind] += 1

    try:
        for row in rows:
            phases = row.get("phase_examples") or []
            turns = row.get("goal_revision_turns") or []

            if len(phases) != 1 + len(turns):
                add_issue(
                    row,
                    "phase_count_mismatch",
                    {"phase_examples": len(phases), "goal_revision_turns": len(turns)},
                )

            if row.get("final_phase_example") != (phases[-1] if phases else None):
                add_issue(row, "final_phase_example_mismatch", {})

            normalized = normalized_interaction_mode(row)
            if row.get("interaction_mode") != normalized:
                add_issue(
                    row,
                    "interaction_mode_mismatch",
                    {"stored": row.get("interaction_mode"), "normalized": normalized},
                )

            for idx, phase in enumerate(phases, start=1):
                phase_materialized = _materialize_phase_example(row, phase)
                gold = phase.get("gold_final_answer", {})
                phase_required_fields = (
                    phase.get("task_accomplish_verifier", {})
                    .get("final_answer_checks", {})
                    .get("required_fields", [])
                )

                if not phase_required_fields:
                    add_issue(
                        row,
                        "phase_required_fields_empty",
                        {"phase_task_family": phase.get("task_family"), "phase_index": idx},
                        idx,
                    )

                try:
                    rendered = render_operator_answer(phase_materialized, gold, runtime)
                    ok, _, phase_issues = check_operator_answer(
                        phase_materialized,
                        rendered,
                        runtime,
                        [],
                        include_core_fields=True,
                        include_reporting_fields=False,
                    )
                    if not ok:
                        add_issue(
                            row,
                            "gold_render_fails_phase_verifier",
                            {
                                "phase_task_family": phase.get("task_family"),
                                "rendered_answer": rendered,
                                "phase_issues": phase_issues,
                                "gold_final_answer": gold,
                            },
                            idx,
                        )
                except Exception as exc:
                    add_issue(
                        row,
                        "gold_render_exception",
                        {
                            "phase_task_family": phase.get("task_family"),
                            "error": f"{type(exc).__name__}:{exc}",
                        },
                        idx,
                    )

                phase_family = str(phase.get("task_family", row["task_family"]))

                if phase_family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
                    stream_id = gold.get("stream_id")
                    requested_timestamp = gold.get("requested_timestamp")
                    if not stream_id or not requested_timestamp:
                        add_issue(
                            row,
                            "timestamp_phase_missing_gold_fields",
                            {"phase_task_family": phase_family, "gold_final_answer": gold},
                            idx,
                        )
                    else:
                        try:
                            exact = runtime.lookup_observation(
                                {"stream_id": stream_id, "timestamp": requested_timestamp, "mode": "exact"}
                            )
                            nearest = runtime.lookup_observation(
                                {"stream_id": stream_id, "timestamp": requested_timestamp, "mode": "nearest"}
                            )
                            expected = exact if exact.get("exact_match_found") else nearest
                            if bool(gold.get("exact_match_found", False)) != bool(exact.get("exact_match_found", False)):
                                add_issue(
                                    row,
                                    "timestamp_exact_match_mismatch",
                                    {
                                        "phase_task_family": phase_family,
                                        "gold": gold.get("exact_match_found"),
                                        "runtime": exact.get("exact_match_found"),
                                        "requested_timestamp": requested_timestamp,
                                    },
                                    idx,
                                )
                            if str(gold.get("observed_timestamp")) != str(expected.get("observed_timestamp")):
                                add_issue(
                                    row,
                                    "timestamp_observed_timestamp_mismatch",
                                    {
                                        "phase_task_family": phase_family,
                                        "gold": gold.get("observed_timestamp"),
                                        "runtime": expected.get("observed_timestamp"),
                                        "requested_timestamp": requested_timestamp,
                                    },
                                    idx,
                                )
                            gold_value = gold.get("value")
                            runtime_value = expected.get("value")
                            if gold_value is None or runtime_value is None or abs(float(gold_value) - float(runtime_value)) > 1e-9:
                                add_issue(
                                    row,
                                    "timestamp_value_mismatch",
                                    {
                                        "phase_task_family": phase_family,
                                        "gold": gold_value,
                                        "runtime": runtime_value,
                                        "requested_timestamp": requested_timestamp,
                                    },
                                    idx,
                                )
                            gold_fallback = gold.get("fallback_reason")
                            runtime_fallback = None if exact.get("exact_match_found") else expected.get(
                                "fallback_reason", "nearest_available_observation"
                            )
                            if gold_fallback != runtime_fallback:
                                add_issue(
                                    row,
                                    "timestamp_fallback_reason_mismatch",
                                    {
                                        "phase_task_family": phase_family,
                                        "gold": gold_fallback,
                                        "runtime": runtime_fallback,
                                        "requested_timestamp": requested_timestamp,
                                    },
                                    idx,
                                )
                        except Exception as exc:
                            add_issue(
                                row,
                                "timestamp_runtime_exception",
                                {"phase_task_family": phase_family, "error": f"{type(exc).__name__}:{exc}"},
                                idx,
                            )

                if phase_family == "quality_gate":
                    observed_fraction = gold.get("observed_fraction")
                    gap_ratio = gold.get("gap_ratio")
                    decision = gold.get("decision")
                    reason = gold.get("reason")
                    quality_period = str(gold.get("period") or row.get("metadata", {}).get("period") or "week")
                    quality_ref = runtime.window_quality_reference(quality_period)

                    low_coverage = (
                        observed_fraction is not None
                        and quality_ref.get("abstain_observed_fraction_below") is not None
                        and float(observed_fraction) < float(quality_ref["abstain_observed_fraction_below"])
                    )
                    high_gap = (
                        gap_ratio is not None
                        and quality_ref.get("abstain_gap_ratio_above") is not None
                        and float(gap_ratio) > float(quality_ref["abstain_gap_ratio_above"])
                    )
                    answer_ok = (
                        observed_fraction is not None
                        and quality_ref.get("answer_observed_fraction_at_least") is not None
                        and float(observed_fraction) >= float(quality_ref["answer_observed_fraction_at_least"])
                        and (
                            quality_ref.get("answer_gap_ratio_at_most") is None
                            or gap_ratio is None
                            or float(gap_ratio) <= float(quality_ref["answer_gap_ratio_at_most"])
                        )
                    )

                    if decision == "answer" and not answer_ok:
                        add_issue(
                            row,
                            "quality_answer_threshold_mismatch",
                            {
                                "decision": decision,
                                "reason": reason,
                                "observed_fraction": observed_fraction,
                                "gap_ratio": gap_ratio,
                                "quality_reference": quality_ref,
                            },
                            idx,
                        )
                    if decision == "abstain" and answer_ok and reason != "marginal_quality":
                        add_issue(
                            row,
                            "quality_abstain_threshold_mismatch",
                            {
                                "decision": decision,
                                "reason": reason,
                                "observed_fraction": observed_fraction,
                                "gap_ratio": gap_ratio,
                                "quality_reference": quality_ref,
                            },
                            idx,
                        )
                    if idx >= 2 and idx - 2 < len(turns):
                        prompt = str(turns[idx - 2])
                        if not quality_prompt_looks_consistent(prompt):
                            add_issue(row, "quality_prompt_semantic_mismatch", {"prompt": prompt}, idx)

                if phase_family == "quality_preference":
                    expected = quality_preference_candidate_for_row(row, runtime)
                    if expected is None:
                        add_issue(row, "quality_preference_missing_expected_candidate", {}, idx)
                    else:
                        for field in (
                            "preferred_reference",
                            "decision",
                            "reason",
                            "observed_fraction",
                            "gap_ratio",
                            "stream_id",
                            "window_start",
                            "window_end",
                            "period",
                        ):
                            if field in {"window_start", "window_end"}:
                                # For quality_preference, the window identity is already
                                # anchored by stream_id/period/quality metrics. Some
                                # artifacts store equivalent timestamp strings in
                                # different textual forms ("T" vs space). Treat those
                                # as non-blocking here to avoid formatting-only false
                                # positives.
                                continue
                            gold_value = gold.get(field)
                            expected_value = expected.get(field)
                            if str(gold_value) != str(expected_value):
                                add_issue(
                                    row,
                                    "quality_preference_gold_mismatch",
                                    {
                                        "field": field,
                                        "gold": gold.get(field),
                                        "runtime": expected.get(field),
                                    },
                                    idx,
                                )
                    if idx >= 2 and idx - 2 < len(turns):
                        prompt = str(turns[idx - 2])
                        if not quality_preference_prompt_looks_consistent(
                            prompt,
                            reference_only=row.get("metadata", {}).get("quality_preference_mode") == "reference_only",
                        ):
                            add_issue(row, "quality_preference_prompt_semantic_mismatch", {"prompt": prompt}, idx)

                if phase_family == "timestamp_nearest_lookup" and idx >= 2 and idx - 2 < len(turns):
                    prompt = str(turns[idx - 2])
                    if not timestamp_prompt_looks_consistent(prompt):
                        add_issue(row, "timestamp_prompt_semantic_mismatch", {"prompt": prompt}, idx)

                if phase_family == "timestamp_preference":
                    expected = timestamp_preference_candidate_for_row(row)
                    if expected is None:
                        add_issue(row, "timestamp_preference_missing_expected_candidate", {}, idx)
                    else:
                        for field in (
                            "preferred_reference",
                            "observed_timestamp",
                            "value",
                            "exact_match_found",
                            "fallback_reason",
                            "stream_id",
                            "requested_timestamp",
                        ):
                            if str(gold.get(field)) != str(expected.get(field)):
                                add_issue(
                                    row,
                                    "timestamp_preference_gold_mismatch",
                                    {
                                        "field": field,
                                        "gold": gold.get(field),
                                        "runtime": expected.get(field),
                                    },
                                    idx,
                                )
                    if idx >= 2 and idx - 2 < len(turns):
                        prompt = str(turns[idx - 2])
                        if not timestamp_preference_prompt_looks_consistent(prompt):
                            add_issue(row, "timestamp_preference_prompt_semantic_mismatch", {"prompt": prompt}, idx)

                if phase_family == "reporting_commitment":
                    if idx < 2:
                        add_issue(row, "reporting_commitment_missing_previous_phase", {}, idx)
                    else:
                        previous_phase = phases[idx - 2]
                        previous_family = str(previous_phase.get("task_family", ""))
                        previous_gold = previous_phase.get("gold_final_answer", {})
                        if previous_family in {"quality_trend_assessment", "rank_stability_assessment"} and idx >= 3:
                            fallback_phase = phases[idx - 3]
                            fallback_family = str(fallback_phase.get("task_family", ""))
                            fallback_gold = fallback_phase.get("gold_final_answer", {})
                            if fallback_family == "quality_gate":
                                previous_family = fallback_family
                                previous_gold = fallback_gold
                        commitment_policy = row.get("metadata", {}).get("test_penalty_reporting_commitment")
                        if commitment_policy == "combined_evidence":
                            expected = combined_reporting_commitment_candidate_for_row(row)
                        elif commitment_policy == "timestamp_quality_strict":
                            expected = strict_timestamp_quality_reporting_commitment_candidate_for_row(row)
                        else:
                            expected = reporting_commitment_candidate(previous_family, previous_gold)
                        if expected is None:
                            add_issue(
                                row,
                                "reporting_commitment_missing_expected_candidate",
                                {"previous_phase_family": previous_family},
                                idx,
                            )
                        else:
                            for field, expected_value in expected.items():
                                if str(gold.get(field)) != str(expected_value):
                                    add_issue(
                                        row,
                                        "reporting_commitment_gold_mismatch",
                                        {
                                            "field": field,
                                            "gold": gold.get(field),
                                            "runtime": expected_value,
                                            "previous_phase_family": previous_family,
                                        },
                                        idx,
                                    )
                    if idx >= 2 and idx - 2 < len(turns):
                        prompt = str(turns[idx - 2])
                        commitment_policy = row.get("metadata", {}).get("test_penalty_reporting_commitment")
                        commitment_mode = row.get("metadata", {}).get("reporting_commitment_mode")
                        if commitment_policy in {"combined_evidence", "timestamp_quality_strict"}:
                            prompt_ok = combined_reporting_commitment_prompt_looks_consistent(prompt)
                        elif commitment_mode == "quality_only":
                            prompt_ok = quality_only_reporting_commitment_prompt_looks_consistent(prompt)
                        else:
                            prompt_ok = reporting_commitment_prompt_looks_consistent(prompt)
                        if not prompt_ok:
                            add_issue(row, "reporting_commitment_prompt_semantic_mismatch", {"prompt": prompt}, idx)

                if phase_family == "point_disambiguation" and idx >= 2 and idx - 2 < len(turns):
                    prompt = str(turns[idx - 2])
                    if not point_prompt_looks_consistent(prompt):
                        add_issue(row, "point_prompt_semantic_mismatch", {"prompt": prompt}, idx)
    finally:
        runtime.close()

    return {
        "report_version": "contract-preflight-v5",
        "artifact_version": manifest.get("artifact_version", CANONICAL_VERSION),
        "lifting_version": manifest.get("lifting_version", LIFT_VERSION),
        "row_count": len(rows),
        "issue_count": len(issues),
        "issue_type_counts": dict(issue_type_counts),
        "family_issue_counts": {family: dict(counter) for family, counter in sorted(family_issue_counts.items())},
        "split_issue_counts": {split: dict(counter) for split, counter in sorted(split_issue_counts.items())},
        "sample_issues": issues[:100],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", type=Path, required=True)
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = audit_contract(args.benchmark_dir, args.tool_store_db)
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
