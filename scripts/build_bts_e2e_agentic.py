#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from bts_agentbench.bts_e2e import (
    FOLLOWUP_PROMPT,
    FOLLOWUP_PROMPT_MULTI,
    BtsE2EScenario,
    evidence_stream_ids,
    eligible_for_time_clarification,
    load_jsonl,
    temporal_clarification_class,
    write_jsonl,
)
from bts_agentbench.clarify_policy import clarify_policy_manifest_fields


AGENTIC_TRACK_VERSION = "bts-operator-surface"


def agentic_mode_for_row(row: dict[str, Any]) -> str:
    family = row["task_family"]

    if family == "quality_gate":
        return "clarify_site_then_quality_decision_then_rationale_then_evidence"
    if family == "point_disambiguation":
        return "clarify_site_then_evidence"
    if temporal_clarification_class(row) == "nearest_fallback":
        return "implicit_nearest_then_evidence"
    if eligible_for_time_clarification(row):
        return "clarify_time_then_evidence"
    return "direct_then_evidence"


def wrap_operator_note(
    text: str,
    row: dict[str, Any],
    ordinal: int,
    *,
    tool_domain_phrase: str = "building tools",
    handoff_label: str = "Operator handoff",
    ticket_label: str = "Ops ticket",
    review_label: str = "Data-quality review request",
) -> str:
    family = row["task_family"]
    wrappers = [
        f'{handoff_label}: "{{text}}" Use the {tool_domain_phrase} and ask me for any missing site or time detail before querying.',
        'I only have this ticket note: "{text}" Please handle it end to end, and do not guess missing context.',
        'Can you take over this building-ops request? {text} I need the final result, and I may ask what evidence you used.',
    ]
    if family == "quality_gate":
        return (
            f'{review_label}: "{text}" '
            "If the site is missing, ask for it first; then tell me whether you would answer or abstain."
        )
    if family == "window_rank":
        return (
            f'Operator note: "{text}" '
            "Rank across the relevant candidate streams rather than picking a single point by name."
        )
    if family == "timestamp_nearest_lookup":
        if row["interaction_mode"] == "implicit_nearest_then_evidence":
            return (
                f'{ticket_label}: "{text}" Use the building telemetry tools '
                "and report the logged reading you can justify."
            )
        return (
            f'{ticket_label}: "{text}" '
            "Use nearest-only behavior only when the request actually asks for a nearby logged reading."
        )
    return wrappers[ordinal % len(wrappers)].format(text=text)


def make_agentic_row(
    base: dict[str, Any],
    split: str,
    ordinal: int,
    *,
    track_name: str,
    agentic_track_version: str,
    tool_domain_phrase: str,
    handoff_label: str,
    ticket_label: str,
    review_label: str,
) -> dict[str, Any]:
    mode = agentic_mode_for_row(base)
    row = BtsE2EScenario(base=base, interaction_mode=mode).as_dict()
    row["e2e_track_version"] = agentic_track_version
    row["track"] = track_name
    row["query"] = wrap_operator_note(
        row["initial_user_message"],
        row,
        ordinal,
        tool_domain_phrase=tool_domain_phrase,
        handoff_label=handoff_label,
        ticket_label=ticket_label,
        review_label=review_label,
    )
    row["initial_user_message"] = row["query"]
    metadata = dict(row.get("metadata", {}))
    metadata.update(
        {
            "e2e_track_version": agentic_track_version,
            "agentic_surface_version": "operator-handoff",
            "rule_solver_stressors": [
                "noncanonical_wrapper",
                "missing_slot_dialogue",
                "paraphrased_operator_surface",
            ],
            "required_clarification_slot_count": len(row.get("required_clarification_slots", [])),
        }
    )
    row["metadata"] = metadata
    row["difficulty_proxy"] = dict(row.get("difficulty_proxy", {}))
    row["difficulty_proxy"]["agentic_turn_requirements"] = len(row.get("required_clarification_slots", [])) + len(
        row.get("post_answer_user_turns", [])
    )
    row["difficulty_proxy"]["requires_multi_slot_clarification"] = len(row.get("required_clarification_slots", [])) > 1
    history = clone(base.get("generation_history", []))
    history.append(
        {
            "stage": "agentic_operator_surface_generation",
            "stage_type": "surface",
            "builder": "build_bts_e2e_agentic",
            "status": "generated",
            "details": {
                "agentic_track_version": agentic_track_version,
                "agentic_surface_version": "operator-handoff",
                "source_e2e_scenario_id": base.get("scenario_id"),
                "interaction_mode": row.get("interaction_mode"),
                "initial_user_message": row.get("initial_user_message"),
                "required_clarification_slots": row.get("required_clarification_slots", []),
                "goal_revision_turn_count": len(row.get("goal_revision_turns", [])),
                "post_answer_user_turn_count": len(row.get("post_answer_user_turns", [])),
            },
        }
    )
    row["generation_history"] = history
    return row


def clone(payload: Any) -> Any:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def summarize_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "interaction_modes": dict(Counter(row["interaction_mode"] for row in rows)),
        "task_families": dict(Counter(row["task_family"] for row in rows)),
        "required_clarification_slots": dict(
            Counter(len(row.get("required_clarification_slots", [])) for row in rows)
        ),
    }


def build_agentic_bts_e2e(
    static_dir: Path,
    out_dir: Path,
    *,
    corpus_name: str = "bts",
    track_name: str | None = None,
    agentic_track_version: str | None = None,
    tool_domain_phrase: str = "building tools",
    handoff_label: str = "Operator handoff",
    ticket_label: str = "Ops ticket",
    review_label: str = "Data-quality review request",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_track_name = track_name or f"{corpus_name}_e2e_agentic"
    resolved_agentic_track_version = agentic_track_version or AGENTIC_TRACK_VERSION
    splits: dict[str, list[dict[str, Any]]] = {}
    all_rows: list[dict[str, Any]] = []

    for split in ["train", "dev", "test"]:
        rows = [
            make_agentic_row(
                base,
                split=split,
                ordinal=idx,
                track_name=resolved_track_name,
                agentic_track_version=resolved_agentic_track_version,
                tool_domain_phrase=tool_domain_phrase,
                handoff_label=handoff_label,
                ticket_label=ticket_label,
                review_label=review_label,
            )
            for idx, base in enumerate(load_jsonl(static_dir / f"{split}.jsonl"))
        ]
        splits[split] = rows
        all_rows.extend(rows)
        write_jsonl(out_dir / f"{split}.jsonl", rows)

    interaction_summary = Counter(row["interaction_mode"] for row in all_rows)
    family_summary = Counter(row["task_family"] for row in all_rows)
    family_interaction_summary: dict[str, dict[str, int]] = {}
    for row in all_rows:
        family = row["task_family"]
        family_interaction_summary.setdefault(family, {})
        mode = row["interaction_mode"]
        family_interaction_summary[family][mode] = family_interaction_summary[family].get(mode, 0) + 1

    manifest = {
        "track": resolved_track_name,
        "e2e_track_version": resolved_agentic_track_version,
        "corpus_name": corpus_name,
        "source_static_dir": str(static_dir),
        "splits": {split: len(rows) for split, rows in splits.items()},
        "split_summaries": {split: summarize_split(rows) for split, rows in splits.items()},
        "task_families": dict(family_summary),
        "interaction_modes": dict(interaction_summary),
        "family_interaction_modes": family_interaction_summary,
        "deterministic_user_simulator": True,
        "followup_prompt_policy": {
            "single_stream": FOLLOWUP_PROMPT,
            "multi_stream": FOLLOWUP_PROMPT_MULTI,
        },
        **clarify_policy_manifest_fields(
            clarify_count=sum(1 for row in all_rows if row.get("required_clarification_slots")),
            clarify_scope="recoverable-slot masking with temporal fallback disentanglement",
        ),
        "agentic_changes": {
            "noncanonical_operator_handoff_query_field": True,
            "all_point_disambiguation_requires_site_clarification": True,
            "time_clarification_requires_anchor_missing_eligibility": True,
            "nearest_fallback_tasks_do_not_require_time_clarification": True,
            "quality_gate_requires_site_clarification_and_rationale": True,
            "multi_slot_clarification_enabled": True,
        },
        "clarification_capability_count": sum(1 for row in all_rows if row["required_clarification_slots"]),
        "multi_slot_clarification_count": sum(
            1 for row in all_rows if len(row.get("required_clarification_slots", [])) > 1
        ),
        "quality_abstention_capability_count": sum(
            1
            for row in all_rows
            if row["task_family"] == "quality_gate" and row["gold_final_answer"].get("decision") == "abstain"
        ),
        "clarify_plus_quality_mode_count": sum(
            1 for row in all_rows if "quality_decision" in row["interaction_mode"] and row["required_clarification_slots"]
        ),
        "avg_evidence_stream_count": round(
            sum(len(evidence_stream_ids(row)) for row in all_rows) / len(all_rows), 4
        )
        if all_rows
        else 0.0,
        "row_level_generation_history": {
            "enabled": True,
            "history_version": "generation-history",
            "stages": [
                "seed_static_executable_task",
                "deterministic_e2e_contract_generation",
                "agentic_operator_surface_generation",
            ],
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    tool_registry = static_dir / "scenario_tool_registry.json"
    if tool_registry.exists():
        (out_dir / "scenario_tool_registry.json").write_text(tool_registry.read_text(encoding="utf-8"), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-dir", type=Path, default=Path("data/scenario_benchmark"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/bts_e2e_agentic"))
    args = parser.parse_args()
    manifest = build_agentic_bts_e2e(args.static_dir, args.out_dir)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
