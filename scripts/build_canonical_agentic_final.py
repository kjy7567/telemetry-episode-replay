from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from collections import Counter, defaultdict
from hashlib import sha1
from pathlib import Path
from typing import Any

import pandas as pd

from bts_agentbench.bts_e2e import (
    SITE_CLARIFICATION_SLOT,
    TIME_CLARIFICATION_SLOT,
    audit_bts_e2e,
    evidence_followup_prompt,
    humanize_visible_prompt,
    load_jsonl,
    make_implicit_nearest_prompt,
    remove_site_mentions,
    remove_time_mentions,
    render_time_reference,
    write_jsonl,
)
from bts_agentbench.runtime import ToolStoreRuntime


REPO_ROOT = Path(__file__).resolve().parents[1]

STATIC_DEFAULT = REPO_ROOT / "artifacts" / "bts-static-seed"
SOURCE_DEFAULT = REPO_ROOT / "artifacts" / "bts-agentic-source"
UNIFORM_REFERENCE = REPO_ROOT / "artifacts" / "bts-agentic-uniform-reference"
TOOL_STORE_DB = REPO_ROOT / "data" / "local-build" / "tool_store" / "tool_store.duckdb"
OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-canonical-seed"
CORE_OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-canonical-seed-core"
EXPLICIT_CONTROLLER_WITNESS_DIR = REPO_ROOT / "reports" / "controller" / "explicit_controller_witnesses"

LIFT_VERSION = "bts-agentic-seed"
CANONICAL_VERSION = "bts-canonical-seed"

DSL_ATOMS = [
    "ASK_SITE",
    "ASK_TIME",
    "RESOLVE_SINGLE_STREAM",
    "RESOLVE_STREAM_PAIR",
    "RESOLVE_RANK_SCOPE",
    "AGGREGATE_WINDOW",
    "COMPARE_WINDOW",
    "RANK_WINDOW",
    "PROBE_EXACT",
    "PROBE_NEAREST",
    "DECIDE_QUALITY",
    "ANSWER_INITIAL",
    "REVISE_SAME_CONTEXT",
    "ANSWER_FINAL",
    "ABSTAIN_FINAL",
    "ANSWER_RATIONALE",
    "ANSWER_EVIDENCE",
    "REVISE_SAME_CONTEXT_MULTI",
]

DSL_INTERPRETER_ORDER = {atom: idx for idx, atom in enumerate(DSL_ATOMS)}

STREAM_HISTORY_CACHE: dict[str, pd.DataFrame] = {}
STREAM_FIRST_TIMESTAMP_CACHE: dict[str, pd.Timestamp | None] = {}
TIMESTAMP_POLICY_CANDIDATE_CACHE: dict[tuple[str, str | None], dict[str, Any] | None] = {}
QUALITY_PHASE_CANDIDATE_CACHE: dict[tuple[str, str, str | None], dict[str, Any] | None] = {}

DSL_SOLVER_CLASSES: dict[str, dict[str, Any]] = {
    "RS0_public_single_pass": {
        "max_atoms": 3,
        "forbidden_atoms": ["ASK_SITE", "ASK_TIME", "ANSWER_INITIAL", "REVISE_SAME_CONTEXT", "REVISE_SAME_CONTEXT_MULTI", "ANSWER_RATIONALE", "ANSWER_EVIDENCE"],
    },
    "RS1_public_slot_template": {
        "max_atoms": 4,
        "forbidden_atoms": ["ASK_SITE", "ASK_TIME", "ANSWER_INITIAL", "REVISE_SAME_CONTEXT", "REVISE_SAME_CONTEXT_MULTI", "ANSWER_RATIONALE", "ANSWER_EVIDENCE"],
    },
    "RS2_exact_timestamp_only": {
        "max_atoms": 6,
        "forbidden_atoms": ["PROBE_NEAREST"],
    },
    "RS3_no_abstain_branch": {
        "max_atoms": 6,
        "forbidden_atoms": ["ABSTAIN_FINAL"],
    },
    "RS4_no_followup_commitment": {
        "max_atoms": 6,
        "forbidden_atoms": ["ANSWER_RATIONALE", "ANSWER_EVIDENCE"],
    },
    "RS5_no_state_carryover": {
        "max_atoms": 6,
        "forbidden_atoms": ["REVISE_SAME_CONTEXT", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS6_no_goal_revision": {
        "max_atoms": 5,
        "forbidden_atoms": ["ANSWER_INITIAL", "REVISE_SAME_CONTEXT", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS7_no_quality_decision": {
        "max_atoms": 6,
        "forbidden_atoms": ["DECIDE_QUALITY"],
    },
    "RS8_stateful_single_stream_revision": {
        "max_atoms": 6,
        "forbidden_atoms": ["RESOLVE_STREAM_PAIR", "RESOLVE_RANK_SCOPE", "COMPARE_WINDOW", "RANK_WINDOW", "DECIDE_QUALITY", "PROBE_NEAREST", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS9_stateful_pairwise_revision": {
        "max_atoms": 6,
        "forbidden_atoms": ["RESOLVE_SINGLE_STREAM", "RESOLVE_RANK_SCOPE", "AGGREGATE_WINDOW", "RANK_WINDOW", "DECIDE_QUALITY", "PROBE_NEAREST", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS10_rank_template_solver": {
        "max_atoms": 6,
        "forbidden_atoms": ["RESOLVE_SINGLE_STREAM", "RESOLVE_STREAM_PAIR", "AGGREGATE_WINDOW", "COMPARE_WINDOW", "DECIDE_QUALITY", "PROBE_NEAREST", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS11_quality_gate_template_solver": {
        "max_atoms": 5,
        "forbidden_atoms": ["RESOLVE_STREAM_PAIR", "RESOLVE_RANK_SCOPE", "AGGREGATE_WINDOW", "COMPARE_WINDOW", "RANK_WINDOW", "PROBE_NEAREST", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS12_timestamp_policy_template": {
        "max_atoms": 4,
        "forbidden_atoms": ["RESOLVE_STREAM_PAIR", "RESOLVE_RANK_SCOPE", "AGGREGATE_WINDOW", "COMPARE_WINDOW", "RANK_WINDOW", "DECIDE_QUALITY", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS13_short_memory_controller": {
        "max_atoms": 7,
        "forbidden_atoms": ["RESOLVE_STREAM_PAIR", "RESOLVE_RANK_SCOPE", "COMPARE_WINDOW", "RANK_WINDOW", "DECIDE_QUALITY", "REVISE_SAME_CONTEXT_MULTI"],
    },
    "RS14_point_target_revision_template": {
        "max_atoms": 5,
        "forbidden_atoms": ["RESOLVE_STREAM_PAIR", "RESOLVE_RANK_SCOPE", "AGGREGATE_WINDOW", "COMPARE_WINDOW", "RANK_WINDOW", "DECIDE_QUALITY", "PROBE_EXACT", "PROBE_NEAREST", "REVISE_SAME_CONTEXT_MULTI"],
    },
}

DECLARED_SOLVER_CLASSES: dict[str, dict[str, Any]] = {
    "RS0_public_single_pass": {
        "view": "initial_user_message only",
        "memory": "none",
        "interaction": "cannot ask clarification questions and cannot answer follow-up turns",
        "tool_policy": "fixed deterministic program emitted in one pass",
        "hardness_scope": "impossible when the row requires clarification, goal revision, or post-answer commitment",
    },
    "RS1_public_slot_template": {
        "view": "initial_user_message only",
        "memory": "none",
        "interaction": "no dialogue adaptation",
        "tool_policy": "slot parser plus fixed family template",
        "hardness_scope": "impossible on observational-collision groups with different gold answers",
    },
    "RS2_exact_timestamp_only": {
        "view": "initial_user_message only",
        "memory": "none",
        "interaction": "no fallback branch",
        "tool_policy": "exact timestamp lookup only",
        "hardness_scope": "impossible on rows that require exact-probe then nearest fallback reporting",
    },
    "RS3_no_abstain_branch": {
        "view": "initial_user_message and tool outputs",
        "memory": "single trajectory",
        "interaction": "can answer but cannot abstain",
        "tool_policy": "always commits to an answer if a stream is found",
        "hardness_scope": "impossible on quality-gate rows whose gold decision is abstain",
    },
    "RS4_no_followup_commitment": {
        "view": "initial_user_message and tool outputs",
        "memory": "single trajectory",
        "interaction": "does not answer evidence or rationale follow-up turns",
        "tool_policy": "final-answer only",
        "hardness_scope": "impossible on rows with required evidence or rationale follow-up",
    },
    "RS5_no_state_carryover": {
        "view": "all visible user turns and tool outputs",
        "memory": "cannot bind a later request to a previously resolved stream or site",
        "interaction": "treats each user turn as an independent fresh task",
        "tool_policy": "single-turn execution template only",
        "hardness_scope": "impossible on goal-revision rows that require reusing the same stream across turns",
    },
    "RS6_no_goal_revision": {
        "view": "all visible user turns and tool outputs",
        "memory": "single task only",
        "interaction": "does not revise the objective after an initial answer",
        "tool_policy": "commits after the first completed task",
        "hardness_scope": "impossible on rows whose terminal gold task is only revealed by a later deterministic revision turn",
    },
    "RS7_no_quality_decision": {
        "view": "all visible user turns and tool outputs",
        "memory": "single trajectory",
        "interaction": "can ground a stream but cannot execute an answer-versus-abstain quality branch",
        "tool_policy": "retrieval-only controller with no decision rule",
        "hardness_scope": "impossible on rows that require quality-gated answer or abstain decisions",
    },
    "RS8_stateful_single_stream_revision": {
        "view": "all visible user turns and tool outputs",
        "memory": "one resolved stream plus one adjacent-window revision",
        "interaction": "can handle direct single-stream revision but not larger clarified contracts",
        "tool_policy": "single-stream aggregate/revision template with bounded controller size",
        "hardness_scope": "difficult or impossible on clarified single-stream revision rows whose contract exceeds the bounded template",
    },
    "RS9_stateful_pairwise_revision": {
        "view": "all visible user turns and tool outputs",
        "memory": "one resolved stream pair plus one adjacent-window revision",
        "interaction": "can handle direct pairwise comparison revision but not broader clarified contracts",
        "tool_policy": "pairwise compare/revision template with bounded controller size",
        "hardness_scope": "difficult or impossible on clarified pairwise revision rows whose contract exceeds the bounded template",
    },
    "RS10_rank_template_solver": {
        "view": "all visible user turns and tool outputs",
        "memory": "one listed ranking scope plus one adjacent-window revision",
        "interaction": "can rank a fixed scope but not larger clarified ranking contracts",
        "tool_policy": "list-then-rank template with bounded controller size",
        "hardness_scope": "difficult or impossible on clarified ranking rows whose contract exceeds the bounded template",
    },
    "RS11_quality_gate_template_solver": {
        "view": "all visible user turns and tool outputs",
        "memory": "one resolved stream and one quality decision",
        "interaction": "can execute direct quality gate decisions but not larger clarified contracts",
        "tool_policy": "resolve-inspect-decide-rationale-evidence template with bounded controller size",
        "hardness_scope": "difficult or impossible on clarified quality-gate rows whose contract exceeds the bounded template",
    },
    "RS12_timestamp_policy_template": {
        "view": "all visible user turns and tool outputs",
        "memory": "one resolved stream and one exact-to-nearest fallback branch",
        "interaction": "can execute direct timestamp fallback policy but not larger clarified contracts",
        "tool_policy": "resolve-exact-nearest-answer-evidence template with bounded controller size",
        "hardness_scope": "difficult or impossible on clarified timestamp-policy rows whose contract exceeds the bounded template",
    },
    "RS13_short_memory_controller": {
        "view": "all visible user turns and tool outputs",
        "memory": "one resolved single stream plus one adjacent-window revision under a bounded controller",
        "interaction": "bounded short-memory controller for direct single-stream revision tasks",
        "tool_policy": "single-stream short-memory template only",
        "hardness_scope": "difficult or impossible on rows that require broader contracts than the bounded short-memory controller can express",
    },
    "RS14_point_target_revision_template": {
        "view": "all visible user turns and tool outputs",
        "memory": "one resolved site-conditioned stream plus one revised target in the same site",
        "interaction": "can answer a single point selection but not a bounded target-revision contract",
        "tool_policy": "site clarify plus one-shot point resolution template only",
        "hardness_scope": "difficult or impossible on point-disambiguation rows that require a second revised target under the same site context",
    },
}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    interaction_modes = Counter(str(row.get("interaction_mode", "")) for row in rows)
    task_families = Counter(str(row.get("task_family", "")) for row in rows)
    clarify_cardinality = Counter(len(row.get("required_clarification_slots", [])) for row in rows)
    tool_lengths = Counter(len(row.get("canonical_tool_calls", [])) for row in rows)
    post_lengths = Counter(len(row.get("post_answer_user_turns", [])) for row in rows)
    goal_revision_lengths = Counter(len(row.get("goal_revision_turns", [])) for row in rows)
    phase_count_histogram = Counter(len(row_phase_examples(row)) for row in rows)
    phase_patterns = Counter(
        " -> ".join(phase.get("task_family", "") for phase in row_phase_examples(row))
        for row in rows
    )
    return {
        "count": len(rows),
        "interaction_modes": dict(interaction_modes),
        "task_families": dict(task_families),
        "required_clarification_slot_cardinality": {str(key): value for key, value in clarify_cardinality.items()},
        "canonical_tool_call_lengths": {str(key): value for key, value in tool_lengths.items()},
        "post_answer_user_turn_lengths": {str(key): value for key, value in post_lengths.items()},
        "goal_revision_turn_lengths": {str(key): value for key, value in goal_revision_lengths.items()},
        "phase_count_histogram": {str(key): value for key, value in phase_count_histogram.items()},
        "phase_patterns": dict(phase_patterns),
    }


def quality_gate_summary(test_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in test_rows if row.get("task_family") == "quality_gate"]
    return {
        "count": len(rows),
        "site_counts": dict(Counter(str(row.get("site_id", "")) for row in rows)),
        "decision_counts": dict(Counter(str(row.get("gold_final_answer", {}).get("decision", "")) for row in rows)),
    }


def split_interaction_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    overall = Counter(str(row.get("interaction_mode", "")) for row in rows)
    for row in rows:
        by_family[str(row.get("task_family", ""))][str(row.get("interaction_mode", ""))] += 1
    return {
        "interaction_modes": dict(overall),
        "family_interaction_modes": {family: dict(counter) for family, counter in by_family.items()},
    }


def clone(payload: Any) -> Any:
    return json.loads(json.dumps(payload))


def canonical_tool_names(row: dict[str, Any]) -> list[str]:
    return [str(call.get("tool_name", "")) for call in row.get("canonical_tool_calls", [])]


def row_contract_summary(row: dict[str, Any]) -> dict[str, Any]:
    gold = row.get("gold_final_answer", {})
    return {
        "scenario_id": row.get("scenario_id"),
        "task_family": row.get("task_family"),
        "interaction_mode": row.get("interaction_mode"),
        "phase_count": len(row_phase_examples(row)),
        "phase_task_families": sorted(phase_task_families(row)),
        "required_clarification_slots": sorted(row.get("required_clarification_slots", [])),
        "goal_revision_turn_count": len(row.get("goal_revision_turns", [])),
        "post_answer_user_turn_count": len(row.get("post_answer_user_turns", [])),
        "canonical_tool_names": canonical_tool_names(row),
        "canonical_tool_count": len(row.get("canonical_tool_calls", [])),
        "policy_choice_contract": bool(row.get("policy_choice_contract")),
        "quality_decision": gold.get("decision"),
        "gold_keys": sorted(gold.keys()),
    }


def dsl_solver_class_satisfies_row(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    required = set(dsl_required_atoms(row))
    forbidden = set(spec["forbidden_atoms"])
    if required & forbidden:
        return False
    return len(required) <= int(spec["max_atoms"])


def row_dsl_audit_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    required_atoms = sorted(dsl_required_atoms(row))
    satisfiable: list[str] = []
    blocked: list[str] = []
    for solver_class, spec in DSL_SOLVER_CLASSES.items():
        if dsl_solver_class_satisfies_row(row, spec):
            satisfiable.append(solver_class)
        else:
            blocked.append(solver_class)
    return {
        "required_atoms": required_atoms,
        "satisfiable_solver_classes": satisfiable,
        "blocked_solver_classes": blocked,
    }


def append_history_entry(
    history: list[dict[str, Any]],
    stage: str,
    stage_type: str,
    status: str,
    details: dict[str, Any],
    *,
    builder: str,
) -> None:
    history.append(
        {
            "step_index": len(history),
            "stage": stage,
            "stage_type": stage_type,
            "builder": builder,
            "status": status,
            "details": details,
        }
    )


def apply_repair_step(
    row: dict[str, Any],
    history: list[dict[str, Any]],
    step_name: str,
    step_type: str,
    builder_name: str,
    repair_fn: Any,
) -> dict[str, Any]:
    before_summary = row_contract_summary(row)
    before_audit = row_dsl_audit_snapshot(row)
    updated = repair_fn(row)
    after_summary = row_contract_summary(updated)
    after_audit = row_dsl_audit_snapshot(updated)
    append_history_entry(
        history,
        stage=step_name,
        stage_type=step_type,
        status="modified" if before_summary != after_summary else "noop",
        builder=builder_name,
        details={
            "before_contract": before_summary,
            "after_contract": after_summary,
            "before_dsl_audit": before_audit,
            "after_dsl_audit": after_audit,
        },
    )
    return updated


def controller_proxy_audit_snapshot(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    family = str(row.get("task_family", ""))
    goal_revision_count = len(row.get("goal_revision_turns", []))
    if family in {"point_disambiguation", "day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup", "timestamp_value_lookup"}:
        cluster = "single_stream_stateful_revision"
        would_solve = goal_revision_count <= 1
    elif family == "timestamp_nearest_lookup":
        cluster = "timestamp_fallback_policy"
        would_solve = goal_revision_count == 0
    elif family in {"window_pairwise_compare", "window_rank"}:
        cluster = "pairwise_or_rank_group_reasoning"
        would_solve = goal_revision_count <= 1
    elif family == "quality_gate":
        cluster = "quality_decision_branch"
        would_solve = goal_revision_count == 0
    else:
        cluster = "unsupported"
        would_solve = False
    return {
        "controller": "composite_explicit_controller_proxy",
        "audit_mode": "capability_bound_proxy",
        "controller_cluster": cluster,
        "goal_revision_turn_count": goal_revision_count,
        "label": "accomplished" if would_solve else "partially_accomplished",
        "strict_label": "accomplished" if would_solve else "partially_accomplished",
        "contradicts_declared_dsl": False,
        "contradicted_dsl_classes": [],
        "interaction_issues": [] if would_solve else ["exceeds_controller_bound"],
        "verification": {
            "process_score": 1.0 if would_solve else 0.0,
            "task_score": 1.0 if would_solve else 0.0,
            "temporal_score": 1.0 if would_solve else 0.0,
            "grounding_score": 1.0 if would_solve else 0.0,
            "issues": [] if would_solve else ["exceeds_controller_bound"],
        },
    }


def apply_controller_repair_step(
    row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
    history: list[dict[str, Any]],
    step_name: str,
    step_type: str,
    builder_name: str,
    repair_fn: Any,
) -> dict[str, Any]:
    before_summary = row_contract_summary(row)
    before_dsl = row_dsl_audit_snapshot(row)
    before_controller = controller_proxy_audit_snapshot(row, runtime)
    updated = repair_fn(row, static_row, runtime, before_controller)
    after_summary = row_contract_summary(updated)
    after_dsl = row_dsl_audit_snapshot(updated)
    after_controller = controller_proxy_audit_snapshot(updated, runtime)
    append_history_entry(
        history,
        stage=step_name,
        stage_type=step_type,
        status="modified" if before_summary != after_summary else "noop",
        builder=builder_name,
        details={
            "before_contract": before_summary,
            "after_contract": after_summary,
            "before_dsl_audit": before_dsl,
            "after_dsl_audit": after_dsl,
            "before_explicit_controller_audit": before_controller,
            "after_explicit_controller_audit": after_controller,
        },
    )
    return updated


def next_call_id(calls: list[dict[str, Any]]) -> str:
    max_num = 0
    for call in calls:
        call_id = str(call.get("call_id", ""))
        if call_id.startswith("c"):
            try:
                max_num = max(max_num, int(call_id[1:]))
            except Exception:
                pass
    return f"c{max_num + 1}"


def dedupe_call_variants_local(call_sets: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    seen: set[str] = set()
    deduped: list[list[dict[str, Any]]] = []
    for variant in call_sets:
        key = json.dumps(variant, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(variant)
    return deduped


def drop_trailing_lookup_calls(
    variant: list[dict[str, Any]],
    *,
    timestamp: str,
    drop_exact: bool,
    drop_nearest: bool,
) -> list[dict[str, Any]]:
    trimmed = clone(variant)
    while trimmed:
        call = trimmed[-1]
        if call.get("tool_name") != "lookup_observation":
            break
        args = call.get("arguments", {})
        if not isinstance(args, dict) or args.get("timestamp") != timestamp:
            break
        mode = args.get("mode")
        if mode == "exact" and drop_exact:
            trimmed.pop()
            continue
        if mode == "nearest" and drop_nearest:
            trimmed.pop()
            continue
        break
    return trimmed


def drop_exact_before_nearest_for_timestamp(
    variant: list[dict[str, Any]],
    *,
    timestamp: str,
) -> list[dict[str, Any]]:
    trimmed = clone(variant)
    for idx in range(len(trimmed) - 1):
        first = trimmed[idx]
        second = trimmed[idx + 1]
        if first.get("tool_name") != "lookup_observation" or second.get("tool_name") != "lookup_observation":
            continue
        first_args = first.get("arguments", {})
        second_args = second.get("arguments", {})
        if not isinstance(first_args, dict) or not isinstance(second_args, dict):
            continue
        if (
            first_args.get("timestamp") == timestamp
            and second_args.get("timestamp") == timestamp
            and first_args.get("mode") == "exact"
            and second_args.get("mode") == "nearest"
        ):
            return trimmed[:idx] + [trimmed[idx + 1]] + trimmed[idx + 2 :]
    return trimmed


def add_timestamp_cached_reuse_variants(
    row: dict[str, Any],
    *,
    original_timestamp: str,
    revised_timestamp: str | None,
) -> dict[str, Any]:
    row = clone(row)
    variants = [clone(variant) for variant in row.get("acceptable_tool_call_sets", [])]
    expanded: list[list[dict[str, Any]]] = [clone(variant) for variant in variants]

    for variant in variants:
        no_final = drop_trailing_lookup_calls(
            variant,
            timestamp=original_timestamp,
            drop_exact=True,
            drop_nearest=True,
        )
        expanded.append(no_final)
        if revised_timestamp is not None:
            expanded.append(drop_exact_before_nearest_for_timestamp(variant, timestamp=revised_timestamp))
            expanded.append(drop_exact_before_nearest_for_timestamp(no_final, timestamp=revised_timestamp))

    row["acceptable_tool_call_sets"] = dedupe_call_variants_local(expanded)
    metadata = dict(row.get("metadata", {}))
    metadata["cached_timestamp_reuse_allowed"] = True
    row["metadata"] = metadata
    return row



def has_lookup_observation_semantics(row: dict[str, Any]) -> bool:
    return "lookup_observation" in canonical_tool_names(row) and any(
        family in {"timestamp_value_lookup", "timestamp_nearest_lookup"} for family in phase_task_families(row)
    )


def has_aggregate_window_semantics(row: dict[str, Any]) -> bool:
    return "aggregate_window" in canonical_tool_names(row) and any(
        family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"} for family in phase_task_families(row)
    )


def has_compare_window_semantics(row: dict[str, Any]) -> bool:
    return "compare_window" in canonical_tool_names(row) and "window_pairwise_compare" in phase_task_families(row)


def has_rank_window_semantics(row: dict[str, Any]) -> bool:
    return "rank_window" in canonical_tool_names(row) and "window_rank" in phase_task_families(row)


def has_quality_decision_semantics(row: dict[str, Any]) -> bool:
    return bool({"quality_gate", "quality_preference"} & phase_task_families(row))


def has_rationale_followup(row: dict[str, Any]) -> bool:
    verifier = row.get("interaction_verifier", {})
    if verifier.get("require_rationale_followup"):
        return True
    return any("reliable enough" in str(turn).lower() for turn in row.get("post_answer_user_turns", []))


def eligible_for_timestamp_policy_choice(static_row: dict[str, Any], agentic_row: dict[str, Any]) -> bool:
    phase_examples = row_phase_examples(agentic_row)
    return any(
        phase.get("task_family") in {"timestamp_value_lookup", "timestamp_nearest_lookup"}
        and "exact_match_found" in phase.get("gold_final_answer", {})
        for phase in phase_examples
    )


def eligible_for_goal_revision(static_row: dict[str, Any]) -> bool:
    return str(static_row.get("task_family", "")) in {
        "point_disambiguation",
        "day_mean_lookup",
        "relative_24h_mean_lookup",
        "window_mean_lookup",
        "window_pairwise_compare",
        "window_rank",
        "timestamp_value_lookup",
        "timestamp_nearest_lookup",
        "quality_gate",
    }


def parse_utc(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def stream_history(runtime: ToolStoreRuntime, stream_id: str) -> pd.DataFrame:
    if stream_id not in STREAM_HISTORY_CACHE:
        history = runtime._load_history_for_stream(stream_id).copy()
        history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True)
        STREAM_HISTORY_CACHE[stream_id] = history
    return STREAM_HISTORY_CACHE[stream_id]


def floor_to_public_minute(value: str) -> pd.Timestamp:
    return parse_utc(value).floor("min")


def shift_window(start: str, end: str, direction: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_ts = parse_utc(start)
    end_ts = parse_utc(end)
    delta = end_ts - start_ts
    return start_ts + direction * delta, end_ts + direction * delta


def adjacent_window_with_data(
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any] | None:
    if not eligible_for_goal_revision(static_row):
        return None
    if has_compare_window_semantics(static_row):
        return adjacent_compare_window_with_data(static_row, runtime)
    if has_rank_window_semantics(static_row):
        return adjacent_rank_window_with_data(static_row, runtime)
    if not has_aggregate_window_semantics(static_row):
        return None
    gold = static_row.get("gold_final_answer", {})
    stream_id = gold.get("stream_id")
    window_start = gold.get("window_start")
    window_end = gold.get("window_end")
    if not isinstance(stream_id, str) or not isinstance(window_start, str) or not isinstance(window_end, str):
        return None
    period = static_row.get("metadata", {}).get("period")
    if not isinstance(period, str):
        period = "custom"
    metric = "mean_value"

    candidates: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    prev_start, prev_end = shift_window(window_start, window_end, -1)
    candidates.append(("previous", prev_start, prev_end))
    next_start, next_end = shift_window(window_start, window_end, 1)
    candidates.append(("next", next_start, next_end))

    for direction, start_ts, end_ts in candidates:
        out = runtime.aggregate_window(
            {
                "stream_id": stream_id,
                "metric": metric,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
            }
        )
        if out.get("mean_value") is not None and int(out.get("count", 0) or 0) > 0:
            return {
                "kind": "aggregate_window",
                "direction": direction,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
                "mean_value": float(out["mean_value"]),
                "count": int(out["count"]),
            }
    return None


def adjacent_compare_window_with_data(static_row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    gold = static_row.get("gold_final_answer", {})
    left_stream_id = gold.get("left_stream_id")
    right_stream_id = gold.get("right_stream_id")
    compare_call = next((call for call in static_row.get("canonical_tool_calls", []) if call.get("tool_name") == "compare_window"), None)
    if not isinstance(left_stream_id, str) or not isinstance(right_stream_id, str) or compare_call is None:
        return None
    args = compare_call.get("arguments", {})
    window_start = args.get("window_start")
    window_end = args.get("window_end")
    period = args.get("period")
    metric = args.get("metric", "mean_value")
    if not isinstance(window_start, str) or not isinstance(window_end, str):
        return None
    candidates: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    prev_start, prev_end = shift_window(window_start, window_end, -1)
    candidates.append(("previous", prev_start, prev_end))
    next_start, next_end = shift_window(window_start, window_end, 1)
    candidates.append(("next", next_start, next_end))
    for direction, start_ts, end_ts in candidates:
        out = runtime.compare_window(
            {
                "left_stream_id": left_stream_id,
                "right_stream_id": right_stream_id,
                "metric": metric,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
            }
        )
        if out.get("winning_stream_id") is not None and out.get("left_value") is not None and out.get("right_value") is not None:
            return {
                "kind": "compare_window",
                "direction": direction,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
                "winning_stream_id": out["winning_stream_id"],
                "left_stream_id": left_stream_id,
                "right_stream_id": right_stream_id,
                "left_mean_value": float(out["left_value"]),
                "right_mean_value": float(out["right_value"]),
            }
    return None


def adjacent_rank_window_with_data(static_row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    rank_call = next((call for call in static_row.get("canonical_tool_calls", []) if call.get("tool_name") == "rank_window"), None)
    list_call = next((call for call in static_row.get("canonical_tool_calls", []) if call.get("tool_name") == "list_points"), None)
    if rank_call is None or list_call is None:
        return None
    rank_args = rank_call.get("arguments", {})
    list_args = list_call.get("arguments", {})
    window_start = rank_args.get("window_start")
    window_end = rank_args.get("window_end")
    period = rank_args.get("period")
    metric = rank_args.get("metric", "mean_value")
    order = rank_args.get("order", "desc")
    topk = int(rank_args.get("topk", 1))
    if not isinstance(window_start, str) or not isinstance(window_end, str):
        return None
    listed = runtime.list_points(list_args)
    stream_ids = listed.get("stream_ids", [])
    if not stream_ids:
        return None
    candidates: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    prev_start, prev_end = shift_window(window_start, window_end, -1)
    candidates.append(("previous", prev_start, prev_end))
    next_start, next_end = shift_window(window_start, window_end, 1)
    candidates.append(("next", next_start, next_end))
    for direction, start_ts, end_ts in candidates:
        out = runtime.rank_window(
            {
                "stream_ids": stream_ids,
                "metric": metric,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
                "order": order,
                "topk": topk,
            }
        )
        ranked = out.get("ranked_streams") or []
        if ranked:
            top = ranked[0]
            return {
                "kind": "rank_window",
                "direction": direction,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
                "stream_id": top["stream_id"],
                "mean_value": float(top.get("mean_value")),
            }
    return None


def goal_revision_prompt(static_row: dict[str, Any], revision: dict[str, Any]) -> str:
    direction = revision["direction"]
    family = static_row["task_family"]
    period = static_row.get("metadata", {}).get("period")
    if family == "window_pairwise_compare":
        unit = "previous week" if direction == "previous" else "next week"
        return f"Now keep the same two signals and site, but compare the {unit}."
    if family == "window_rank":
        if period == "month":
            unit = "previous month" if direction == "previous" else "next month"
        elif period == "week":
            unit = "previous week" if direction == "previous" else "next week"
        else:
            unit = "previous comparable window" if direction == "previous" else "next comparable window"
        return f"Now keep the same candidate group and site, but rank the {unit}."
    if family == "day_mean_lookup":
        unit = "previous day" if direction == "previous" else "next day"
        return f"Now keep the same signal and site, but give me the {unit}."
    if family == "relative_24h_mean_lookup":
        shift = "back by one day" if direction == "previous" else "forward by one day"
        return f"Now keep the same signal and site, but shift that 24-hour window {shift}."
    if family == "window_mean_lookup" and period == "week":
        unit = "previous week" if direction == "previous" else "next week"
        return f"Now keep the same signal and site, but give me the {unit}."
    if family == "window_mean_lookup" and period == "month":
        unit = "previous month" if direction == "previous" else "next month"
        return f"Now keep the same signal and site, but give me the {unit}."
    unit = "previous comparable window" if direction == "previous" else "next comparable window"
    return f"Now keep the same signal and site, but give me the {unit}."


def append_goal_revision_contract(
    agentic_row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    revision = adjacent_window_with_data(static_row, runtime)
    if revision is None:
        return agentic_row

    row = clone(agentic_row)
    original_gold = clone(row["gold_final_answer"])
    row["phase_gold_final_answers"] = [original_gold]
    row["goal_revision_turns"] = [goal_revision_prompt(static_row, revision)]
    row["goal_revision_contract"] = {
        "type": f"adjacent_{revision['kind']}",
        "reuse_stream_context": True,
        "goal_revision_count": 1,
        "direction": revision["direction"],
        "terminal_phase_index": 1,
        "terminal_window_start": revision["window_start"],
        "terminal_window_end": revision["window_end"],
    }

    revised_gold = clone(original_gold)
    if revision["kind"] == "aggregate_window":
        revised_gold["window_start"] = revision["window_start"]
        revised_gold["window_end"] = revision["window_end"]
        revised_gold["mean_value"] = round(float(revision["mean_value"]), 4)
    elif revision["kind"] == "compare_window":
        revised_gold["winning_stream_id"] = revision["winning_stream_id"]
        revised_gold["left_stream_id"] = revision["left_stream_id"]
        revised_gold["right_stream_id"] = revision["right_stream_id"]
        revised_gold["left_mean_value"] = round(float(revision["left_mean_value"]), 4)
        revised_gold["right_mean_value"] = round(float(revision["right_mean_value"]), 4)
    elif revision["kind"] == "rank_window":
        revised_gold["stream_id"] = revision["stream_id"]
        revised_gold["mean_value"] = round(float(revision["mean_value"]), 4)
        revised_gold["window_start"] = revision["window_start"]
        revised_gold["window_end"] = revision["window_end"]
    else:
        return agentic_row
    row["phase_gold_final_answers"].append(clone(revised_gold))
    row["gold_final_answer"] = revised_gold

    canonical_calls = clone(row["canonical_tool_calls"])
    new_call = clone(canonical_calls[-1])
    new_call["call_id"] = next_call_id(canonical_calls)
    new_call["arguments"]["window_start"] = revision["window_start"]
    new_call["arguments"]["window_end"] = revision["window_end"]
    canonical_calls.append(new_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_new_call = clone(variant_copy[-1])
        variant_new_call["call_id"] = next_call_id(variant_copy)
        variant_new_call["arguments"]["window_start"] = revision["window_start"]
        variant_new_call["arguments"]["window_end"] = revision["window_end"]
        variant_copy.append(variant_new_call)
        acceptable_sets.append(variant_copy)

        if variant_copy and variant_copy[0].get("tool_name") == "resolve_point" and revision["kind"] == "aggregate_window":
            second_variant = clone(variant)
            second_resolve = clone(second_variant[0])
            second_resolve["call_id"] = next_call_id(second_variant)
            second_variant.append(second_resolve)
            second_agg = clone(variant_new_call)
            second_agg["call_id"] = next_call_id(second_variant)
            second_agg["arguments"]["stream_id"] = f"${second_resolve['call_id']}.stream_id"
            second_variant.append(second_agg)
            acceptable_sets.append(second_variant)
        if variant_copy and variant_copy[0].get("tool_name") == "resolve_point" and revision["kind"] == "compare_window":
            left_resolve = clone(variant[0])
            right_resolve = clone(variant[1]) if len(variant) > 1 and variant[1].get("tool_name") == "resolve_point" else None
            if right_resolve is not None:
                second_variant = clone(variant)
                left_resolve["call_id"] = next_call_id(second_variant)
                second_variant.append(left_resolve)
                right_resolve["call_id"] = next_call_id(second_variant)
                second_variant.append(right_resolve)
                second_compare = clone(variant_new_call)
                second_compare["call_id"] = next_call_id(second_variant)
                second_compare["arguments"]["left_stream_id"] = f"${left_resolve['call_id']}.stream_id"
                second_compare["arguments"]["right_stream_id"] = f"${right_resolve['call_id']}.stream_id"
                second_variant.append(second_compare)
                acceptable_sets.append(second_variant)
        if variant_copy and variant_copy[0].get("tool_name") == "list_points" and revision["kind"] == "rank_window":
            second_variant = clone(variant)
            second_list = clone(variant[0])
            second_list["call_id"] = next_call_id(second_variant)
            second_variant.append(second_list)
            second_rank = clone(variant_new_call)
            second_rank["call_id"] = next_call_id(second_variant)
            second_rank["arguments"]["stream_ids"] = f"${second_list['call_id']}.stream_ids"
            second_variant.append(second_rank)
            acceptable_sets.append(second_variant)
    row["acceptable_tool_call_sets"] = acceptable_sets

    row["interaction_verifier"] = clone(row.get("interaction_verifier", {}))
    row["interaction_verifier"]["require_goal_revision_answer"] = True
    row["interaction_verifier"]["goal_revision_turn_count"] = len(row["goal_revision_turns"])
    row["interaction_verifier"]["max_user_turns"] = int(row["interaction_verifier"].get("max_user_turns", 0)) + len(
        row["goal_revision_turns"]
    )

    milestones = clone(row.get("interaction_milestones", []))
    insert_at = next((idx for idx, milestone in enumerate(milestones) if milestone.get("name") == "provide_stream_evidence"), len(milestones))
    milestones[insert_at:insert_at] = [
        {
            "name": "receive_goal_revision",
            "type": "user_goal_revision",
            "required": True,
        },
        {
            "name": "deliver_revised_operator_answer",
            "type": "agent_answer",
            "required": True,
        },
    ]
    row["interaction_milestones"] = milestones

    row["interaction_mode"] = row["interaction_mode"].replace("_then_evidence", "_then_goal_revision_then_evidence")
    metadata = dict(row.get("metadata", {}))
    metadata["goal_revision_type"] = f"adjacent_{revision['kind']}"
    metadata["goal_revision_direction"] = revision["direction"]
    metadata["goal_revision_window_start"] = revision["window_start"]
    metadata["goal_revision_window_end"] = revision["window_end"]
    row["metadata"] = metadata
    difficulty = dict(row.get("difficulty_proxy", {}))
    difficulty["goal_revision_count"] = 1
    difficulty["state_carryover_required"] = True
    row["difficulty_proxy"] = difficulty
    sync_primary_phase_examples(row)
    return row


def wrap_policy_prompt(prompt: str, required_slots: list[str]) -> str:
    if required_slots:
        return f'Operator handoff: "{prompt}" Use the building tools and ask for any missing site or time detail before querying.'
    return f'Ops ticket: "{prompt}" Use the building telemetry tools and report the logged reading you can justify.'


def clean_surface_prompt(prompt: str, family: str) -> str:
    text = prompt.replace(",,", ",").replace(" ,", ",")
    text = re.sub(r"\s{2,}", " ", text).strip()
    if family == "timestamp_value_lookup":
        text = re.sub(
            r"^What reading did the (?P<subject>.+?)\?$",
            lambda m: f"What was the reading for the {m.group('subject')}?",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"^What was the reading for the (?P<subject>.+?) reading(?P<suffix> on .+?)\?$",
            lambda m: f"What was the {m.group('subject').strip()} reading{m.group('suffix')}?",
            text,
            flags=re.IGNORECASE,
        )
    if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
        text = re.sub(r",\s*,", ", ", text)
    return text


def normalize_surface_fields(row: dict[str, Any], family: str) -> dict[str, Any]:
    normalized = clone(row)
    for key in ("initial_user_message", "hidden_user_instruction", "query"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = clean_surface_prompt(value, family)
    if isinstance(normalized.get("goal_revision_turns"), list):
        normalized["goal_revision_turns"] = [
            clean_surface_prompt(turn, family) if isinstance(turn, str) else turn
            for turn in normalized["goal_revision_turns"]
        ]
    if isinstance(normalized.get("post_answer_user_turns"), list):
        normalized["post_answer_user_turns"] = [
            clean_surface_prompt(turn, family) if isinstance(turn, str) else turn
            for turn in normalized["post_answer_user_turns"]
        ]
    return normalized


def phase_required_fields(task_family: str, gold_final_answer: dict[str, Any] | None = None) -> list[str]:
    gold = gold_final_answer or {}
    if task_family == "point_disambiguation":
        return ["stream_id"]
    if task_family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
        return ["mean_value"]
    if task_family == "window_pairwise_compare":
        return ["winning_stream_id", "left_mean_value", "right_mean_value"]
    if task_family == "window_rank":
        return ["stream_id", "mean_value"]
    if task_family == "rank_stability_assessment":
        return ["stability_status"]
    if task_family == "timestamp_value_lookup":
        return ["observed_timestamp", "value"]
    if task_family == "timestamp_nearest_lookup":
        fields = ["observed_timestamp", "value", "fallback_reason"]
        if bool(gold.get("exact_match_found", False)):
            fields = ["observed_timestamp", "value"]
        return fields
    if task_family == "quality_gate":
        return ["decision", "observed_fraction", "gap_ratio"]
    if task_family == "quality_trend_assessment":
        return ["trend_status"]
    if task_family == "quality_preference":
        return ["preferred_reference", "decision", "reason", "observed_fraction", "gap_ratio"]
    if task_family == "timestamp_preference":
        fields = ["preferred_reference", "observed_timestamp", "value"]
        if not bool(gold.get("exact_match_found", False)):
            fields.append("fallback_reason")
        return fields
    if task_family == "timestamp_resolution_context":
        return ["resolution_status", "observed_timestamp", "offset_seconds"]
    if task_family == "reporting_commitment":
        # The user-facing commitment prompt asks for the reporting action, not
        # an explicit restatement of the latent rationale token.
        fields = ["commitment_action"]
        if str(gold.get("commitment_action") or "") == "re_clarify":
            fields.append("clarification_request")
        return fields
    return []


def phase_example_entry(task_family: str, gold_final_answer: dict[str, Any], required_fields: list[str] | None = None) -> dict[str, Any]:
    phase_required = list(required_fields) if required_fields is not None else phase_required_fields(task_family, gold_final_answer)
    return {
        "task_family": task_family,
        "gold_final_answer": clone(gold_final_answer),
        "task_accomplish_verifier": {
            "final_answer_checks": {
                "required_fields": phase_required,
                "numeric_tolerance": {},
                "categorical_exact_match": [],
            }
        },
    }


def row_phase_examples(row: dict[str, Any]) -> list[dict[str, Any]]:
    phase_examples = row.get("phase_examples")
    if isinstance(phase_examples, list) and phase_examples:
        return phase_examples
    phase_gold = row.get("phase_gold_final_answers") or [clone(row.get("gold_final_answer", {}))]
    family = str(row.get("task_family", ""))
    return [phase_example_entry(family, gold) for gold in phase_gold]


def phase_task_families(row: dict[str, Any]) -> set[str]:
    families = {str(row.get("task_family", ""))}
    for phase in row_phase_examples(row):
        families.add(str(phase.get("task_family", "")))
    return {family for family in families if family}


def ensure_phase_gold(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("phase_gold_final_answers"):
        row["phase_gold_final_answers"] = [clone(row.get("gold_final_answer", {}))]
    if not row.get("phase_examples"):
        row["phase_examples"] = row_phase_examples(row)
    row["final_phase_example"] = clone(row["phase_examples"][-1])
    return row


def append_phase_example(row: dict[str, Any], task_family: str, gold_final_answer: dict[str, Any], required_fields: list[str] | None = None) -> None:
    phase_examples = row_phase_examples(row)
    phase_examples.append(phase_example_entry(task_family, gold_final_answer, required_fields))
    row["phase_examples"] = phase_examples
    row["final_phase_example"] = clone(phase_examples[-1])


def sync_primary_phase_examples(row: dict[str, Any]) -> None:
    family = str(row.get("task_family", ""))
    row["phase_examples"] = [
        phase_example_entry(family, gold)
        for gold in row.get("phase_gold_final_answers", [])
    ]
    if row["phase_examples"]:
        row["final_phase_example"] = clone(row["phase_examples"][-1])


def goal_revision_clarification_profile(row: dict[str, Any]) -> str:
    slots = set(row.get("required_clarification_slots", []))
    if not slots:
        return "time_only"
    if slots == {TIME_CLARIFICATION_SLOT}:
        return "time_only"
    if slots == {SITE_CLARIFICATION_SLOT}:
        return "site_only"
    if slots == {SITE_CLARIFICATION_SLOT, TIME_CLARIFICATION_SLOT}:
        return "site_time"
    return "keep"


def goal_revision_prompt_surface(static_row: dict[str, Any], family: str, profile: str) -> str:
    prompt_source = static_row.get("query", "")
    if profile in {"site_only", "site_time"}:
        prompt_source = remove_site_mentions(prompt_source, static_row["site_id"])
    if profile in {"time_only", "site_time"}:
        prompt_source = remove_time_mentions(prompt_source, family)
    visible = clean_surface_prompt(humanize_visible_prompt(prompt_source, family, "direct_then_evidence"), family)
    if profile == "direct":
        return (
            f'Operator handoff: "{visible}" Use the building tools, keep track of the same site and stream if the request changes, '
            "and report the result you can justify."
        )
    return f'Operator handoff: "{visible}" Use the building tools and ask me for any missing site or time detail before querying.'


def update_goal_revision_clarification_contract(static_row: dict[str, Any], agentic_row: dict[str, Any]) -> dict[str, Any]:
    if not agentic_row.get("goal_revision_turns"):
        return agentic_row
    profile = goal_revision_clarification_profile(agentic_row)
    if profile == "keep":
        return agentic_row

    family = str(static_row.get("task_family", agentic_row.get("task_family", "")))
    row = clone(agentic_row)
    if profile == "direct":
        required_slots: list[str] = []
        mode = "direct_then_goal_revision_then_evidence"
    elif profile == "site_only":
        required_slots = [SITE_CLARIFICATION_SLOT]
        mode = "clarify_site_then_goal_revision_then_evidence"
    elif profile == "time_only":
        required_slots = [TIME_CLARIFICATION_SLOT]
        mode = "clarify_time_then_goal_revision_then_evidence"
    else:
        required_slots = [SITE_CLARIFICATION_SLOT, TIME_CLARIFICATION_SLOT]
        mode = "clarify_site_time_then_goal_revision_then_evidence"

    row["interaction_mode"] = mode
    row["required_clarification_slots"] = required_slots
    row["initial_user_message"] = goal_revision_prompt_surface(static_row, family, profile)
    row["query"] = row["initial_user_message"]
    row["hidden_user_instruction"] = clean_surface_prompt(humanize_visible_prompt(static_row.get("query", ""), family, "direct_then_evidence"), family)

    clarification_answers: dict[str, str] = {}
    clarification_questions: dict[str, str] = {}
    if SITE_CLARIFICATION_SLOT in required_slots:
        clarification_answers[SITE_CLARIFICATION_SLOT] = f"It's in {static_row['site_id']}."
        clarification_questions[SITE_CLARIFICATION_SLOT] = "Which BTS site should I use?"
    if TIME_CLARIFICATION_SLOT in required_slots:
        clarification_answers[TIME_CLARIFICATION_SLOT] = render_time_reference(static_row)
        clarification_questions[TIME_CLARIFICATION_SLOT] = "Which date, week, month, time window, or exact timestamp should I use?"
    row["clarification_answers"] = clarification_answers
    row["clarification_questions"] = clarification_questions

    milestones = [m for m in row.get("interaction_milestones", []) if m.get("slot") not in {SITE_CLARIFICATION_SLOT, TIME_CLARIFICATION_SLOT}]
    insertion: list[dict[str, Any]] = []
    if SITE_CLARIFICATION_SLOT in required_slots:
        insertion.append({"name": "collect_site_id", "type": "user_clarification", "slot": SITE_CLARIFICATION_SLOT, "required": True})
    if TIME_CLARIFICATION_SLOT in required_slots:
        insertion.append({"name": "collect_time_reference", "type": "user_clarification", "slot": TIME_CLARIFICATION_SLOT, "required": True})
    row["interaction_milestones"] = insertion + milestones

    verifier = dict(row.get("interaction_verifier", {}))
    verifier["required_clarification_slots"] = required_slots
    verifier["max_user_turns"] = 2 + len(required_slots) + len(row.get("post_answer_user_turns", [])) + len(row.get("goal_revision_turns", []))
    row["interaction_verifier"] = verifier

    metadata = dict(row.get("metadata", {}))
    metadata["goal_revision_clarification_profile"] = profile
    row["metadata"] = metadata
    return row


def append_timestamp_value_revision_contract(
    agentic_row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    if static_row.get("task_family") != "timestamp_value_lookup":
        return agentic_row
    if agentic_row.get("goal_revision_turns"):
        return agentic_row

    gold = dict(agentic_row.get("gold_final_answer", {}))
    stream_id = gold.get("stream_id")
    requested_timestamp = gold.get("requested_timestamp")
    observed_timestamp = gold.get("observed_timestamp")
    if not isinstance(stream_id, str) or not isinstance(requested_timestamp, str) or not isinstance(observed_timestamp, str):
        return agentic_row

    requested_ts = parse_utc(requested_timestamp)
    base_minute = requested_ts.floor("min")
    minute_candidates: list[pd.Timestamp] = []
    for delta_minutes in [0, -1, 1, -2, 2, -3, 3]:
        candidate = base_minute + pd.Timedelta(minutes=delta_minutes)
        if candidate.date() == requested_ts.date():
            minute_candidates.append(candidate)

    revision_lookup: dict[str, Any] | None = None
    for candidate in minute_candidates:
        exact = runtime.lookup_observation({"stream_id": stream_id, "timestamp": candidate.isoformat(), "mode": "exact"})
        if exact.get("exact_match_found"):
            continue
        nearest = runtime.lookup_observation({"stream_id": stream_id, "timestamp": candidate.isoformat(), "mode": "nearest"})
        if nearest.get("observed_timestamp") is None:
            continue
        revision_lookup = nearest
        break
    if revision_lookup is None:
        return agentic_row

    row = clone(agentic_row)
    original_gold = clone(row["gold_final_answer"])
    row["phase_gold_final_answers"] = [original_gold]
    public_ts = pd.Timestamp(revision_lookup["requested_timestamp"])
    row["goal_revision_turns"] = [
        f"Now keep the same signal and site, but if I only know it was around {public_ts.strftime('%H:%M UTC on %B %-d, %Y')}, give me the nearest available reading."
    ]
    row["goal_revision_contract"] = {
        "type": "timestamp_policy_revision",
        "reuse_stream_context": True,
        "goal_revision_count": 1,
        "terminal_phase_index": 1,
        "public_requested_timestamp": revision_lookup["requested_timestamp"],
    }

    revised_gold = clone(original_gold)
    revised_gold["requested_timestamp"] = revision_lookup["requested_timestamp"]
    revised_gold["observed_timestamp"] = revision_lookup["observed_timestamp"]
    revised_gold["value"] = revision_lookup["value"]
    revised_gold["exact_match_found"] = False
    revised_gold["fallback_reason"] = revision_lookup.get("fallback_reason", "nearest_available_observation")
    revised_gold["offset_seconds"] = revision_lookup.get("offset_seconds")
    row["phase_gold_final_answers"].append(clone(revised_gold))
    row["gold_final_answer"] = revised_gold

    canonical_calls = clone(row["canonical_tool_calls"])
    resolve_call = next((call for call in canonical_calls if call.get("tool_name") == "resolve_point"), None)
    if resolve_call is None:
        return agentic_row
    exact_call = {
        "call_id": next_call_id(canonical_calls),
        "tool_name": "lookup_observation",
        "arguments": {
            "stream_id": f"${resolve_call['call_id']}.stream_id",
            "timestamp": revision_lookup["requested_timestamp"],
            "mode": "exact",
        },
    }
    canonical_calls.append(exact_call)
    nearest_call = {
        "call_id": next_call_id(canonical_calls),
        "tool_name": "lookup_observation",
        "arguments": {
            "stream_id": f"${resolve_call['call_id']}.stream_id",
            "timestamp": revision_lookup["requested_timestamp"],
            "mode": "nearest",
        },
    }
    canonical_calls.append(nearest_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_resolve = next((call for call in variant_copy if call.get("tool_name") == "resolve_point"), None)
        if variant_resolve is None:
            continue
        appended_exact = clone(exact_call)
        appended_exact["call_id"] = next_call_id(variant_copy)
        appended_exact["arguments"]["stream_id"] = f"${variant_resolve['call_id']}.stream_id"
        variant_copy.append(appended_exact)
        appended_nearest = clone(nearest_call)
        appended_nearest["call_id"] = next_call_id(variant_copy)
        appended_nearest["arguments"]["stream_id"] = f"${variant_resolve['call_id']}.stream_id"
        variant_copy.append(appended_nearest)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    row["interaction_verifier"] = clone(row.get("interaction_verifier", {}))
    row["interaction_verifier"]["require_goal_revision_answer"] = True
    row["interaction_verifier"]["goal_revision_turn_count"] = 1
    row["interaction_verifier"]["max_user_turns"] = int(row["interaction_verifier"].get("max_user_turns", 0)) + 1

    milestones = clone(row.get("interaction_milestones", []))
    insert_at = next((idx for idx, milestone in enumerate(milestones) if milestone.get("name") == "provide_stream_evidence"), len(milestones))
    milestones[insert_at:insert_at] = [
        {"name": "receive_goal_revision", "type": "user_goal_revision", "required": True},
        {"name": "deliver_revised_operator_answer", "type": "agent_answer", "required": True},
    ]
    row["interaction_milestones"] = milestones

    row["interaction_mode"] = row["interaction_mode"].replace("_then_evidence", "_then_goal_revision_then_evidence")
    row["policy_choice_contract"] = {
        "type": "timestamp_observation_resolution",
        "declared_actions": ["clarify", "exact_probe", "nearest_fallback"],
        "reuse_requested_timestamp_after_clarification": True,
        "gold_policy": "nearest_after_exact_miss",
        "gold_exact_match_found": False,
        "fallback_reason": revised_gold.get("fallback_reason"),
        "public_requested_timestamp": revised_gold["requested_timestamp"],
        "public_timestamp_granularity": "minute",
    }

    metadata = dict(row.get("metadata", {}))
    metadata["goal_revision_type"] = "timestamp_policy_revision"
    metadata["goal_revision_requested_timestamp"] = revised_gold["requested_timestamp"]
    metadata["policy_choice_type"] = "timestamp_observation_resolution"
    metadata["policy_choice_gold_policy"] = "nearest_after_exact_miss"
    row["metadata"] = metadata
    difficulty = dict(row.get("difficulty_proxy", {}))
    difficulty["goal_revision_count"] = 1
    difficulty["state_carryover_required"] = True
    row["difficulty_proxy"] = difficulty
    sync_primary_phase_examples(row)
    return row


def point_revision_descriptor(site_id: str, point: dict[str, Any]) -> str:
    equipment = str(point.get("equipment_label") or "").strip()
    location = str(point.get("location_label") or "").strip()
    prefix = f"{site_id} "
    if equipment.casefold().startswith(prefix.casefold()):
        equipment = equipment[len(prefix):]
    if location.casefold().startswith(prefix.casefold()):
        location = location[len(prefix):]
    if equipment and location:
        return f"{equipment} in {location}"
    return equipment or location or "the other matching point"


def append_point_target_revision_contract(
    agentic_row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    if static_row.get("task_family") != "point_disambiguation":
        return agentic_row
    if agentic_row.get("goal_revision_turns"):
        return agentic_row

    resolve_call = next((call for call in static_row.get("canonical_tool_calls", []) if call.get("tool_name") == "resolve_point"), None)
    if resolve_call is None:
        return agentic_row
    args = resolve_call.get("arguments", {})
    site_id = str(args.get("site_id") or static_row.get("site_id") or "")
    point_class = args.get("point_class")
    if not site_id or not isinstance(point_class, str):
        return agentic_row
    listed = runtime.list_points({"site_id": site_id, "point_class": point_class})
    current_stream = str(agentic_row.get("gold_final_answer", {}).get("stream_id") or "")
    points = [point for point in listed.get("points", []) if str(point.get("stream_id")) != current_stream]
    if not points:
        return agentic_row
    alt = points[0]

    row = clone(agentic_row)
    original_gold = clone(row["gold_final_answer"])
    row["phase_gold_final_answers"] = [original_gold]
    descriptor = point_revision_descriptor(site_id, alt)
    row["goal_revision_turns"] = [
        f"Now keep the same site and measurement type, but if the operator meant {descriptor} instead, which stream should I use?"
    ]
    row["goal_revision_contract"] = {
        "type": "point_target_revision",
        "reuse_site_context": True,
        "goal_revision_count": 1,
        "terminal_phase_index": 1,
        "revised_target_descriptor": descriptor,
    }
    revised_gold = {"stream_id": alt["stream_id"]}
    row["phase_gold_final_answers"].append(clone(revised_gold))
    row["gold_final_answer"] = revised_gold

    canonical_calls = clone(row["canonical_tool_calls"])
    new_call = {
        "call_id": next_call_id(canonical_calls),
        "tool_name": "resolve_point",
        "arguments": {
            "site_id": site_id,
            "point_class": point_class,
            "equipment_label": alt.get("equipment_label"),
            "location_label": alt.get("location_label"),
        },
    }
    canonical_calls.append(new_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        revised_resolve = clone(new_call)
        revised_resolve["call_id"] = next_call_id(variant_copy)
        variant_copy.append(revised_resolve)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    row["interaction_verifier"] = clone(row.get("interaction_verifier", {}))
    row["interaction_verifier"]["require_goal_revision_answer"] = True
    row["interaction_verifier"]["goal_revision_turn_count"] = 1
    row["interaction_verifier"]["max_user_turns"] = int(row["interaction_verifier"].get("max_user_turns", 0)) + 1

    milestones = clone(row.get("interaction_milestones", []))
    insert_at = next((idx for idx, milestone in enumerate(milestones) if milestone.get("name") == "provide_stream_evidence"), len(milestones))
    milestones[insert_at:insert_at] = [
        {"name": "receive_goal_revision", "type": "user_goal_revision", "required": True},
        {"name": "deliver_revised_operator_answer", "type": "agent_answer", "required": True},
    ]
    row["interaction_milestones"] = milestones

    row["interaction_mode"] = row["interaction_mode"].replace("_then_evidence", "_then_goal_revision_then_evidence")
    metadata = dict(row.get("metadata", {}))
    metadata["goal_revision_type"] = "point_target_revision"
    metadata["goal_revision_revised_target_descriptor"] = descriptor
    row["metadata"] = metadata
    difficulty = dict(row.get("difficulty_proxy", {}))
    difficulty["goal_revision_count"] = 1
    difficulty["state_carryover_required"] = True
    row["difficulty_proxy"] = difficulty
    sync_primary_phase_examples(row)
    return row


def append_goal_revision_milestone_pair(row: dict[str, Any]) -> None:
    milestones = clone(row.get("interaction_milestones", []))
    insert_at = next(
        (
            idx
            for idx, milestone in enumerate(milestones)
            if milestone.get("name") in {"justify_quality_decision", "provide_stream_evidence"}
        ),
        len(milestones),
    )
    milestone_names = {m.get("name") for m in milestones}
    if "receive_goal_revision" in milestone_names or "deliver_revised_operator_answer" in milestone_names:
        round_idx = len(row.get("goal_revision_turns", []))
        receive_name = f"receive_goal_revision_round_{round_idx}"
        answer_name = f"deliver_revised_operator_answer_round_{round_idx}"
    else:
        receive_name = "receive_goal_revision"
        answer_name = "deliver_revised_operator_answer"
    milestones[insert_at:insert_at] = [
        {
            "name": receive_name,
            "type": "user_goal_revision",
            "required": True,
        },
        {
            "name": answer_name,
            "type": "agent_answer",
            "required": True,
        },
    ]
    row["interaction_milestones"] = milestones


def refresh_goal_revision_verifier(row: dict[str, Any], increment_user_turns: int = 1) -> None:
    verifier = clone(row.get("interaction_verifier", {}))
    verifier["require_goal_revision_answer"] = True
    verifier["goal_revision_turn_count"] = len(row.get("goal_revision_turns", []))
    verifier["max_user_turns"] = int(verifier.get("max_user_turns", 0)) + increment_user_turns
    row["interaction_verifier"] = verifier
    difficulty = dict(row.get("difficulty_proxy", {}))
    difficulty["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    difficulty["state_carryover_required"] = True
    row["difficulty_proxy"] = difficulty


def original_return_prompt(static_row: dict[str, Any]) -> str:
    family = static_row["task_family"]
    period = static_row.get("metadata", {}).get("period")
    if family == "point_disambiguation":
        return "Actually keep the same site and measurement type, but go back to the original point."
    if family == "day_mean_lookup":
        return "Actually keep the same signal and site, but go back to the original day."
    if family == "relative_24h_mean_lookup":
        return "Actually keep the same signal and site, but go back to the original 24-hour window."
    if family == "window_mean_lookup" and period == "week":
        return "Actually keep the same signal and site, but go back to the original week."
    if family == "window_mean_lookup" and period == "month":
        return "Actually keep the same signal and site, but go back to the original month."
    if family == "window_pairwise_compare":
        return "Actually keep the same two signals and site, but go back to the original comparison window."
    if family == "window_rank" and period == "month":
        return "Actually keep the same candidate group and site, but go back to the original month."
    if family == "window_rank" and period == "week":
        return "Actually keep the same candidate group and site, but go back to the original week."
    if family == "window_rank":
        return "Actually keep the same candidate group and site, but go back to the original ranking window."
    if family == "timestamp_value_lookup":
        return "Actually keep the same signal and site, but go back to the original exact timestamp."
    return "Actually go back to the original request."


def relational_return_prompt(row: dict[str, Any], static_row: dict[str, Any]) -> str:
    family = static_row["task_family"]
    metadata = row.get("metadata", {})
    contract = row.get("goal_revision_contract", {})

    if family == "point_disambiguation":
        return "Now use the first point we talked about rather than the alternate one."

    if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup", "window_pairwise_compare", "window_rank"}:
        direction = metadata.get("goal_revision_direction")
        original_relation = "later" if direction == "previous" else "earlier"
        noun = "time window"
        if family == "day_mean_lookup":
            noun = "day"
        elif family == "relative_24h_mean_lookup":
            noun = "24-hour window"
        elif family == "window_pairwise_compare":
            noun = "comparison window"
        elif family == "window_rank":
            noun = "ranking window"
        return f"Now keep the same signal and site, but use the {original_relation} of the two {noun}s we just discussed."

    if family == "timestamp_value_lookup":
        phase_gold = row.get("phase_gold_final_answers", [])
        if len(phase_gold) >= 2:
            original_ts = parse_utc(str(phase_gold[0]["requested_timestamp"]))
            revised_ts = parse_utc(str(phase_gold[1]["requested_timestamp"]))
            original_relation = "earlier" if original_ts < revised_ts else "later"
        else:
            original_relation = "original"
        if original_relation == "original":
            return "Now keep the same signal and site, but use the original exact timestamp again."
        return f"Now keep the same signal and site, but use the {original_relation} of the two timestamps we just discussed."

    if family == "timestamp_nearest_lookup":
        phase_gold = row.get("phase_gold_final_answers", [])
        if len(phase_gold) >= 2:
            original_ts = parse_utc(str(phase_gold[0]["requested_timestamp"]))
            revised_ts = parse_utc(str(phase_gold[1]["requested_timestamp"]))
            original_relation = "earlier" if original_ts < revised_ts else "later"
        else:
            original_relation = "original"
        if original_relation == "original":
            return "Now keep the same signal and site, but use the original requested time again and return the nearest available reading."
        return f"Now keep the same signal and site, but use the {original_relation} of the two timestamps we just discussed and return the nearest available reading."

    if family == "quality_gate":
        direction = contract.get("direction")
        original_relation = "later" if direction == "previous" else "earlier"
        return f"Now keep the same signal and site, but make the answer-or-abstain decision for the {original_relation} of the two weeks we just discussed."

    return original_return_prompt(static_row)


def append_return_to_original_revision(
    row: dict[str, Any],
    static_row: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    if len(row.get("goal_revision_turns", [])) < 1 or len(row.get("goal_revision_turns", [])) >= 2:
        return row
    row["goal_revision_turns"].append(relational_return_prompt(row, static_row))
    original_gold = clone(row["phase_gold_final_answers"][0])
    row["phase_gold_final_answers"].append(clone(original_gold))
    row["gold_final_answer"] = clone(original_gold)

    canonical_calls = clone(row.get("canonical_tool_calls", []))
    original_call = next((clone(call) for call in canonical_calls if call.get("tool_name") == tool_name), None)
    if original_call is None:
        return row
    original_call["call_id"] = next_call_id(canonical_calls)
    canonical_calls.append(original_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_original = next((clone(call) for call in variant_copy if call.get("tool_name") == tool_name), None)
        if variant_original is None:
            continue
        variant_original["call_id"] = next_call_id(variant_copy)
        variant_copy.append(variant_original)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row["goal_revision_turns"])
    contract["controller_hardening_type"] = "return_to_original_revision"
    row["goal_revision_contract"] = contract
    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    metadata = dict(row.get("metadata", {}))
    metadata["controller_hardening_type"] = "return_to_original_revision"
    row["metadata"] = metadata
    sync_primary_phase_examples(row)
    return row


def append_second_point_target_revision(
    row: dict[str, Any],
    static_row: dict[str, Any],
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    if len(row.get("goal_revision_turns", [])) < 1 or len(row.get("goal_revision_turns", [])) >= 2:
        return row
    row["goal_revision_turns"].append(relational_return_prompt(row, static_row))
    original_gold = clone(row["phase_gold_final_answers"][0])
    row["phase_gold_final_answers"].append(clone(original_gold))
    row["gold_final_answer"] = clone(original_gold)

    canonical_calls = clone(row.get("canonical_tool_calls", []))
    first_resolve = next((clone(call) for call in canonical_calls if call.get("tool_name") == "resolve_point"), None)
    if first_resolve is None:
        return row
    first_resolve["call_id"] = next_call_id(canonical_calls)
    canonical_calls.append(first_resolve)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_original = next((clone(call) for call in variant_copy if call.get("tool_name") == "resolve_point"), None)
        if variant_original is None:
            continue
        variant_original["call_id"] = next_call_id(variant_copy)
        variant_copy.append(variant_original)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row["goal_revision_turns"])
    contract["controller_hardening_type"] = "second_point_target_revision"
    row["goal_revision_contract"] = contract
    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    metadata = dict(row.get("metadata", {}))
    metadata["controller_hardening_type"] = "second_point_target_revision"
    row["metadata"] = metadata
    sync_primary_phase_examples(row)
    return row


def append_second_timestamp_value_revision(row: dict[str, Any], static_row: dict[str, Any]) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    if len(row.get("goal_revision_turns", [])) < 1 or len(row.get("goal_revision_turns", [])) >= 2:
        return row
    original_gold = clone(row["phase_gold_final_answers"][0])
    row["goal_revision_turns"].append(relational_return_prompt(row, static_row))
    row["phase_gold_final_answers"].append(clone(original_gold))
    row["gold_final_answer"] = clone(original_gold)

    canonical_calls = clone(row.get("canonical_tool_calls", []))
    resolve_call = next((call for call in canonical_calls if call.get("tool_name") == "resolve_point"), None)
    if resolve_call is None:
        return row
    exact_call = {
        "call_id": next_call_id(canonical_calls),
        "tool_name": "lookup_observation",
        "arguments": {
            "stream_id": f"${resolve_call['call_id']}.stream_id",
            "timestamp": original_gold["requested_timestamp"],
            "mode": "exact",
        },
    }
    canonical_calls.append(exact_call)
    if not bool(original_gold.get("exact_match_found", True)):
        nearest_call = {
            "call_id": next_call_id(canonical_calls),
            "tool_name": "lookup_observation",
            "arguments": {
                "stream_id": f"${resolve_call['call_id']}.stream_id",
                "timestamp": original_gold["requested_timestamp"],
                "mode": "nearest",
            },
        }
        canonical_calls.append(nearest_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_resolve = next((call for call in variant_copy if call.get("tool_name") == "resolve_point"), None)
        if variant_resolve is None:
            continue
        variant_exact = clone(exact_call)
        variant_exact["call_id"] = next_call_id(variant_copy)
        variant_exact["arguments"]["stream_id"] = f"${variant_resolve['call_id']}.stream_id"
        variant_copy.append(variant_exact)
        if not bool(original_gold.get("exact_match_found", True)):
            variant_nearest = {
                "call_id": next_call_id(variant_copy),
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": f"${variant_resolve['call_id']}.stream_id",
                    "timestamp": original_gold["requested_timestamp"],
                    "mode": "nearest",
                },
            }
            variant_copy.append(variant_nearest)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row["goal_revision_turns"])
    contract["controller_hardening_type"] = "return_to_original_exact_timestamp"
    row["goal_revision_contract"] = contract
    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    metadata = dict(row.get("metadata", {}))
    metadata["controller_hardening_type"] = "return_to_original_exact_timestamp"
    row["metadata"] = metadata
    row = add_timestamp_cached_reuse_variants(
        row,
        original_timestamp=str(original_gold["requested_timestamp"]),
        revised_timestamp=str(row["phase_gold_final_answers"][1]["requested_timestamp"]),
    )
    sync_primary_phase_examples(row)
    return row


def second_timestamp_nearest_candidate(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    gold = row.get("gold_final_answer", {})
    stream_id = gold.get("stream_id")
    requested_timestamp = gold.get("requested_timestamp")
    if not isinstance(stream_id, str) or not isinstance(requested_timestamp, str):
        return None
    base_minute = parse_utc(requested_timestamp).floor("min")
    exact_miss_candidates: list[dict[str, Any]] = []
    fallback_candidates: list[dict[str, Any]] = []
    for delta_minutes in [-2, -1, 1, 2, -3, 3, -4, 4]:
        candidate = base_minute + pd.Timedelta(minutes=delta_minutes)
        if candidate.date() != base_minute.date() or candidate.isoformat() == requested_timestamp:
            continue
        exact = runtime.lookup_observation({"stream_id": stream_id, "timestamp": candidate.isoformat(), "mode": "exact"})
        nearest = runtime.lookup_observation({"stream_id": stream_id, "timestamp": candidate.isoformat(), "mode": "nearest"})
        if nearest.get("observed_timestamp") is None:
            continue
        payload = {
            "stream_id": stream_id,
            "requested_timestamp": candidate.isoformat(),
            "observed_timestamp": exact.get("observed_timestamp") if exact.get("exact_match_found") else nearest.get("observed_timestamp"),
            "value": exact.get("value") if exact.get("exact_match_found") else nearest.get("value"),
            "exact_match_found": bool(exact.get("exact_match_found", False)),
            "fallback_reason": None if exact.get("exact_match_found") else nearest.get("fallback_reason", "nearest_available_observation"),
            "offset_seconds": 0.0 if exact.get("exact_match_found") else nearest.get("offset_seconds"),
        }
        fallback_candidates.append(payload)
        if not payload["exact_match_found"]:
            exact_miss_candidates.append(payload)
    return exact_miss_candidates[0] if exact_miss_candidates else (fallback_candidates[0] if fallback_candidates else None)


def append_timestamp_nearest_controller_hardening(
    row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    if row.get("goal_revision_turns"):
        return row
    revision = second_timestamp_nearest_candidate(row, runtime)
    if revision is None:
        return row
    original_gold = clone(row["gold_final_answer"])
    row["phase_gold_final_answers"] = [clone(original_gold), clone(revision)]
    row["goal_revision_turns"] = [
        f"Now keep the same signal and site, but if I only know it was around {pd.Timestamp(revision['requested_timestamp']).strftime('%H:%M UTC on %B %-d, %Y')} instead, give me the nearest available reading."
    ]
    row["gold_final_answer"] = clone(revision)
    row["goal_revision_contract"] = {
        "type": "timestamp_policy_revision",
        "reuse_stream_context": True,
        "goal_revision_count": 1,
        "terminal_phase_index": 1,
        "public_requested_timestamp": revision["requested_timestamp"],
        "controller_hardening_type": "second_timestamp_policy_query",
    }

    canonical_calls = clone(row.get("canonical_tool_calls", []))
    resolve_call = next((call for call in canonical_calls if call.get("tool_name") == "resolve_point"), None)
    if resolve_call is None:
        return row
    exact_call = {
        "call_id": next_call_id(canonical_calls),
        "tool_name": "lookup_observation",
        "arguments": {
            "stream_id": f"${resolve_call['call_id']}.stream_id",
            "timestamp": revision["requested_timestamp"],
            "mode": "exact",
        },
    }
    canonical_calls.append(exact_call)
    if not bool(revision.get("exact_match_found", False)):
        nearest_call = {
            "call_id": next_call_id(canonical_calls),
            "tool_name": "lookup_observation",
            "arguments": {
                "stream_id": f"${resolve_call['call_id']}.stream_id",
                "timestamp": revision["requested_timestamp"],
                "mode": "nearest",
            },
        }
        canonical_calls.append(nearest_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_resolve = next((call for call in variant_copy if call.get("tool_name") == "resolve_point"), None)
        if variant_resolve is None:
            continue
        variant_exact = clone(exact_call)
        variant_exact["call_id"] = next_call_id(variant_copy)
        variant_exact["arguments"]["stream_id"] = f"${variant_resolve['call_id']}.stream_id"
        variant_copy.append(variant_exact)
        if not bool(revision.get("exact_match_found", False)):
            variant_nearest = {
                "call_id": next_call_id(variant_copy),
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": f"${variant_resolve['call_id']}.stream_id",
                    "timestamp": revision["requested_timestamp"],
                    "mode": "nearest",
                },
            }
            variant_copy.append(variant_nearest)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    metadata = dict(row.get("metadata", {}))
    metadata["controller_hardening_type"] = "second_timestamp_policy_query"
    row["metadata"] = metadata
    row = add_timestamp_cached_reuse_variants(
        row,
        original_timestamp=str(original_gold["requested_timestamp"]),
        revised_timestamp=str(revision["requested_timestamp"]),
    )
    sync_primary_phase_examples(row)
    return row


def quality_decision_from_metrics(
    metrics: dict[str, Any],
    runtime: ToolStoreRuntime,
    period: str,
    stream_id: str,
) -> dict[str, Any]:
    ref = metrics.get("quality_reference") or runtime.window_quality_reference(period)
    observed_fraction = metrics.get("observed_fraction")
    gap_ratio = metrics.get("gap_ratio")
    abstain_coverage_below = ref.get("abstain_observed_fraction_below")
    abstain_gap_above = ref.get("abstain_gap_ratio_above")
    answer_coverage_at_least = ref.get("answer_observed_fraction_at_least")
    answer_gap_at_most = ref.get("answer_gap_ratio_at_most")

    low_coverage = (
        observed_fraction is not None
        and abstain_coverage_below is not None
        and float(observed_fraction) < float(abstain_coverage_below)
    )
    high_gap = (
        gap_ratio is not None
        and abstain_gap_above is not None
        and float(gap_ratio) > float(abstain_gap_above)
    )
    answer_coverage_ok = (
        observed_fraction is not None
        and answer_coverage_at_least is not None
        and float(observed_fraction) >= float(answer_coverage_at_least)
    )
    answer_gap_ok = (
        answer_gap_at_most is None
        or gap_ratio is None
        or float(gap_ratio) <= float(answer_gap_at_most)
    )

    if low_coverage:
        decision = "abstain"
        reason = "low_coverage"
    elif high_gap:
        decision = "abstain"
        reason = "long_gap"
    elif answer_coverage_ok and answer_gap_ok:
        decision = "answer"
        reason = "healthy"
    else:
        decision = "abstain"
        reason = "marginal_quality"
    return {
        "stream_id": stream_id,
        "decision": decision,
        "reason": reason,
        "observed_fraction": observed_fraction,
        "gap_ratio": gap_ratio,
    }


def adjacent_quality_window_with_data(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    quality_call = next((call for call in row.get("canonical_tool_calls", []) if call.get("tool_name") == "inspect_quality_window"), None)
    if quality_call is None:
        return None
    args = quality_call.get("arguments", {})
    stream_id = str(row.get("gold_final_answer", {}).get("stream_id") or args.get("stream_id") or "")
    window_start = args.get("window_start")
    window_end = args.get("window_end")
    period = args.get("period", "week")
    if not stream_id or not isinstance(window_start, str) or not isinstance(window_end, str):
        return None
    candidates: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    prev_start, prev_end = shift_window(window_start, window_end, -1)
    candidates.append(("previous", prev_start, prev_end))
    next_start, next_end = shift_window(window_start, window_end, 1)
    candidates.append(("next", next_start, next_end))
    for direction, start_ts, end_ts in candidates:
        metrics = runtime.inspect_quality_window(
            {
                "stream_id": stream_id,
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
            }
        )
        if metrics.get("observed_fraction") is None and metrics.get("gap_ratio") is None:
            continue
        out = quality_decision_from_metrics(metrics, runtime, period, stream_id)
        out.update(
            {
                "window_start": start_ts.isoformat(),
                "window_end": end_ts.isoformat(),
                "period": period,
                "direction": direction,
            }
        )
        return out
    return None


def append_quality_gate_controller_hardening(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    if row.get("goal_revision_turns"):
        return row
    revision = adjacent_quality_window_with_data(row, runtime)
    if revision is None:
        return row
    row["phase_gold_final_answers"] = [clone(row["gold_final_answer"]), clone({k: revision[k] for k in ['stream_id','decision','reason','observed_fraction','gap_ratio']})]
    row["goal_revision_turns"] = [f"Now keep the same signal and site, but make the same answer-or-abstain decision for the {revision['direction']} week."]
    row["gold_final_answer"] = clone({k: revision[k] for k in ["stream_id", "decision", "reason", "observed_fraction", "gap_ratio"]})
    row["goal_revision_contract"] = {
        "type": "quality_gate_revision",
        "reuse_stream_context": True,
        "goal_revision_count": 1,
        "terminal_phase_index": 1,
        "direction": revision["direction"],
        "window_start": revision["window_start"],
        "window_end": revision["window_end"],
        "controller_hardening_type": "quality_revision",
    }

    canonical_calls = clone(row.get("canonical_tool_calls", []))
    resolve_call = next((call for call in canonical_calls if call.get("tool_name") == "resolve_point"), None)
    if resolve_call is None:
        return row
    inspect_call = {
        "call_id": next_call_id(canonical_calls),
        "tool_name": "inspect_quality_window",
        "arguments": {
            "stream_id": f"${resolve_call['call_id']}.stream_id",
            "window_start": revision["window_start"],
            "window_end": revision["window_end"],
            "period": revision["period"],
        },
    }
    canonical_calls.append(inspect_call)
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_resolve = next((call for call in variant_copy if call.get("tool_name") == "resolve_point"), None)
        if variant_resolve is None:
            continue
        variant_inspect = clone(inspect_call)
        variant_inspect["call_id"] = next_call_id(variant_copy)
        variant_inspect["arguments"]["stream_id"] = f"${variant_resolve['call_id']}.stream_id"
        variant_copy.append(variant_inspect)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    metadata = dict(row.get("metadata", {}))
    metadata["controller_hardening_type"] = "quality_revision"
    row["metadata"] = metadata
    sync_primary_phase_examples(row)
    return row


def latest_primary_gold(row: dict[str, Any]) -> dict[str, Any]:
    family = str(row.get("task_family", ""))
    phase_examples = row_phase_examples(row)
    phase_golds = row.get("phase_gold_final_answers") or [clone(row.get("gold_final_answer", {}))]
    for phase_example, gold in reversed(list(zip(phase_examples, phase_golds))):
        if str(phase_example.get("task_family", "")) == family:
            return clone(gold)
    return clone(row.get("gold_final_answer", {}))


def canonical_time_anchor(row: dict[str, Any], stream_id: str, runtime: ToolStoreRuntime) -> pd.Timestamp | None:
    gold = latest_primary_gold(row)
    for key in ("observed_timestamp", "requested_timestamp", "window_start"):
        value = gold.get(key)
        if isinstance(value, str):
            return parse_utc(value)
    for tool_name in ("aggregate_window", "compare_window", "rank_window", "inspect_quality_window"):
        call = next((c for c in row.get("canonical_tool_calls", []) if c.get("tool_name") == tool_name), None)
        if call is not None and isinstance(call.get("arguments", {}).get("window_start"), str):
            return parse_utc(call["arguments"]["window_start"])
    if stream_id not in STREAM_FIRST_TIMESTAMP_CACHE:
        history = runtime._load_history_for_stream(stream_id)
        STREAM_FIRST_TIMESTAMP_CACHE[stream_id] = None if history.empty else pd.to_datetime(history["timestamp"], utc=True).iloc[0]
    return STREAM_FIRST_TIMESTAMP_CACHE[stream_id]


def target_stream_id_for_primary_family(row: dict[str, Any]) -> str | None:
    family = str(row.get("task_family", ""))
    gold = latest_primary_gold(row)
    if family == "window_pairwise_compare":
        value = gold.get("winning_stream_id")
    else:
        value = gold.get("stream_id")
    return str(value) if isinstance(value, str) else None


def quality_phase_candidate_for_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    stream_id = target_stream_id_for_primary_family(row)
    if not stream_id:
        return None
    anchor = canonical_time_anchor(row, stream_id, runtime)
    if anchor is None:
        return None
    cache_key = (str(row.get("task_family", "")), stream_id, anchor.isoformat())
    if cache_key in QUALITY_PHASE_CANDIDATE_CACHE:
        return clone(QUALITY_PHASE_CANDIDATE_CACHE[cache_key])
    week_start = anchor.normalize() - pd.Timedelta(days=int(anchor.weekday()))
    for shift_weeks in [0, -1, 1]:
        start = week_start + pd.Timedelta(weeks=shift_weeks)
        end = start + pd.Timedelta(days=7)
        metrics = runtime.inspect_quality_window(
            {
                "stream_id": stream_id,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "period": "week",
            }
        )
        if metrics.get("observed_fraction") is None and metrics.get("gap_ratio") is None:
            continue
        out = quality_decision_from_metrics(metrics, runtime, "week", stream_id)
        out.update(
            {
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "period": "week",
            }
        )
        QUALITY_PHASE_CANDIDATE_CACHE[cache_key] = clone(out)
        return out
    QUALITY_PHASE_CANDIDATE_CACHE[cache_key] = None
    return None


def supported_quality_period(period: str | None) -> str:
    period_text = str(period or "week")
    if period_text in {"day", "week", "month"}:
        return period_text
    return "week"


def phase_indices_for_family(row: dict[str, Any], family: str) -> list[int]:
    return [
        idx
        for idx, phase in enumerate(row_phase_examples(row))
        if str(phase.get("task_family", "")) == family
    ]


def nth_tool_call(row: dict[str, Any], tool_name: str, occurrence: int) -> dict[str, Any] | None:
    matches = [call for call in row.get("canonical_tool_calls", []) if call.get("tool_name") == tool_name]
    if 0 <= occurrence < len(matches):
        return clone(matches[occurrence])
    return None


def point_quality_window(row: dict[str, Any], runtime: ToolStoreRuntime) -> tuple[str, str, str] | None:
    primary_gold = row.get("phase_gold_final_answers", [])
    if not primary_gold:
        return None
    stream_id = str(primary_gold[0].get("stream_id") or "")
    if not stream_id:
        return None
    anchor = canonical_time_anchor(row, stream_id, runtime)
    if anchor is None:
        return None
    week_start = anchor.normalize() - pd.Timedelta(days=int(anchor.weekday()))
    week_end = week_start + pd.Timedelta(days=7)
    return week_start.isoformat(), week_end.isoformat(), "week"


def primary_phase_contexts(row: dict[str, Any], runtime: ToolStoreRuntime) -> list[dict[str, Any]]:
    family = str(row.get("task_family", ""))
    phase_golds = list(row.get("phase_gold_final_answers", []))
    phase_indices = phase_indices_for_family(row, family)
    contexts: list[dict[str, Any]] = []
    point_window = point_quality_window(row, runtime) if family == "point_disambiguation" else None
    normalized_period = supported_quality_period(row.get("metadata", {}).get("period"))

    for occurrence, phase_idx in enumerate(phase_indices):
        if phase_idx >= len(phase_golds):
            continue
        gold = clone(phase_golds[phase_idx])
        if family == "point_disambiguation":
            if point_window is None:
                continue
            stream_id = str(gold.get("stream_id") or "")
            if not stream_id:
                continue
            window_start, window_end, period = point_window
            contexts.append(
                {
                    "stream_id": stream_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "period": period,
                    "phase_index": phase_idx,
                }
            )
            continue
        if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
            stream_id = str(gold.get("stream_id") or "")
            window_start = gold.get("window_start")
            window_end = gold.get("window_end")
            if not stream_id or not isinstance(window_start, str) or not isinstance(window_end, str):
                continue
            contexts.append(
                {
                    "stream_id": stream_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "period": "day" if family in {"day_mean_lookup", "relative_24h_mean_lookup"} else normalized_period,
                    "phase_index": phase_idx,
                }
            )
            continue
        if family == "window_pairwise_compare":
            compare_call = nth_tool_call(row, "compare_window", occurrence)
            stream_id = str(gold.get("winning_stream_id") or "")
            if compare_call is None or not stream_id:
                continue
            args = compare_call.get("arguments", {})
            window_start = args.get("window_start")
            window_end = args.get("window_end")
            if not isinstance(window_start, str) or not isinstance(window_end, str):
                continue
            contexts.append(
                {
                    "stream_id": stream_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "period": supported_quality_period(args.get("period")),
                    "phase_index": phase_idx,
                }
            )
            continue
        if family == "window_rank":
            rank_call = nth_tool_call(row, "rank_window", occurrence)
            stream_id = str(gold.get("stream_id") or "")
            if rank_call is None or not stream_id:
                continue
            args = rank_call.get("arguments", {})
            window_start = args.get("window_start")
            window_end = args.get("window_end")
            if not isinstance(window_start, str) or not isinstance(window_end, str):
                continue
            contexts.append(
                {
                    "stream_id": stream_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "period": supported_quality_period(args.get("period")),
                    "phase_index": phase_idx,
                }
            )
            continue
        if family == "quality_gate":
            inspect_call = nth_tool_call(row, "inspect_quality_window", occurrence)
            stream_id = str(gold.get("stream_id") or "")
            if not stream_id:
                continue
            if inspect_call is not None:
                args = inspect_call.get("arguments", {})
                gold["window_start"] = gold.get("window_start") or args.get("window_start")
                gold["window_end"] = gold.get("window_end") or args.get("window_end")
                gold["period"] = gold.get("period") or args.get("period")
            gold["phase_index"] = phase_idx
            contexts.append(gold)
            continue
    return contexts


def quality_gold_for_context(context: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    stream_id = context.get("stream_id")
    window_start = context.get("window_start")
    window_end = context.get("window_end")
    period = supported_quality_period(context.get("period"))
    if not isinstance(stream_id, str) or not isinstance(window_start, str) or not isinstance(window_end, str):
        return None
    metrics = runtime.inspect_quality_window(
        {
            "stream_id": stream_id,
            "window_start": window_start,
            "window_end": window_end,
            "period": period,
        }
    )
    out = quality_decision_from_metrics(metrics, runtime, period, stream_id)
    out.update(
        {
            "window_start": window_start,
            "window_end": window_end,
            "period": period,
        }
    )
    return out


def quality_preference_rank(candidate: dict[str, Any]) -> tuple[int, int, float, float]:
    decision = str(candidate.get("decision") or "")
    reason = str(candidate.get("reason") or "")
    observed = float(candidate.get("observed_fraction") or -1.0)
    gap = float(candidate.get("gap_ratio")) if candidate.get("gap_ratio") is not None else float("inf")
    # For human-facing reporting, tiny coverage differences should not dominate
    # clearly better gap regularity. Use coarse coverage buckets first, then gap.
    coverage_bucket = 1 if observed >= 0.95 else 0
    rounded_coverage = round(observed, 2)
    return (
        1 if decision == "answer" else 0,
        coverage_bucket,
        rounded_coverage,
        -gap,
        observed,
        1 if reason == "healthy" else 0,
    )


def quality_preference_candidate_for_row(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any] | None:
    contexts = primary_phase_contexts(row, runtime)
    if len(contexts) < 2:
        return None
    first_context, second_context = contexts[-2], contexts[-1]
    family = str(row.get("task_family", ""))
    if family == "quality_gate":
        first = clone(first_context)
        second = clone(second_context)
    else:
        first = quality_gold_for_context(first_context, runtime)
        second = quality_gold_for_context(second_context, runtime)
    if first is None or second is None:
        return None
    first_rank = quality_preference_rank(first)
    second_rank = quality_preference_rank(second)
    preferred_reference = "first" if first_rank >= second_rank else "second"
    chosen = clone(first if preferred_reference == "first" else second)
    chosen["preferred_reference"] = preferred_reference
    return chosen


def context_window_reference_text(context: dict[str, Any], label: str) -> str:
    window_start = context.get("window_start")
    period = supported_quality_period(context.get("period"))
    if not isinstance(window_start, str):
        return f"the {label} result's original time range"
    start = parse_utc(window_start)
    prefix = "the shared" if label == "shared" else f"the {label} result's"
    if period == "day":
        return f"{prefix} day of {start.strftime('%B %-d, %Y')}"
    if period == "month":
        return f"{prefix} month of {start.strftime('%B %Y')}"
    return f"{prefix} week beginning {start.strftime('%B %-d, %Y')}"


def quality_preference_prompt(
    row: dict[str, Any],
    quality_gold: dict[str, Any],
    contexts: list[dict[str, Any]],
) -> str:
    family = str(row.get("task_family", ""))
    first_context, second_context = contexts[-2], contexts[-1]
    first_ref = context_window_reference_text(first_context, "first")
    second_ref = context_window_reference_text(second_context, "second")
    if family == "quality_gate":
        return (
            f"Of the first and second answer-or-abstain judgments we just discussed, using {first_ref} and {second_ref}, "
            "which one would you trust more for reporting, and would you answer or abstain for that one based on data quality?"
        )
    if family == "point_disambiguation":
        shared_ref = context_window_reference_text(first_context, "shared")
        return (
            f"Of the first and second results we just discussed, if you judge both using {shared_ref}, "
            "which one would you trust more for reporting, and would you answer or abstain for that chosen one based on data quality?"
        )
    return (
        f"Of the first and second results we just discussed, using {first_ref} and {second_ref}, "
        "which one would you trust more for reporting, and would you answer or abstain for that chosen one based on data quality?"
    )


def append_quality_preference_phase(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    contexts = primary_phase_contexts(row, runtime)
    if len(contexts) < 2:
        return row
    quality_gold = quality_preference_candidate_for_row(row, runtime)
    if quality_gold is None:
        return row

    row["goal_revision_turns"] = list(row.get("goal_revision_turns", [])) + [quality_preference_prompt(row, quality_gold, contexts)]
    row["phase_gold_final_answers"] = list(row.get("phase_gold_final_answers", [])) + [clone(quality_gold)]
    append_phase_example(row, "quality_preference", quality_gold)
    row["gold_final_answer"] = clone(quality_gold)

    acceptable_sets = [clone(variant) for variant in row.get("acceptable_tool_call_sets", [])]
    if str(row.get("task_family", "")) != "quality_gate" and len(contexts) >= 2:
        context_pair = contexts[-2:]
        canonical_calls = clone(row.get("canonical_tool_calls", []))
        new_calls: list[dict[str, Any]] = []
        for context in context_pair:
            new_calls.append(
                {
                    "call_id": next_call_id(canonical_calls + new_calls),
                    "tool_name": "inspect_quality_window",
                    "arguments": {
                        "stream_id": context["stream_id"],
                        "window_start": context["window_start"],
                        "window_end": context["window_end"],
                        "period": supported_quality_period(context.get("period")),
                    },
                }
            )
        row["canonical_tool_calls"] = canonical_calls + new_calls

        expanded: list[list[dict[str, Any]]] = []
        for variant in acceptable_sets:
            variant_copy = clone(variant)
            for call in new_calls:
                appended = clone(call)
                appended["call_id"] = next_call_id(variant_copy)
                variant_copy.append(appended)
            expanded.append(variant_copy)
        acceptable_sets = expanded

    row["acceptable_tool_call_sets"] = acceptable_sets

    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    contract["terminal_phase_index"] = len(row.get("phase_gold_final_answers", [])) - 1
    contract["cross_axis_phase_family"] = "quality_preference"
    row["goal_revision_contract"] = contract
    metadata = dict(row.get("metadata", {}))
    metadata["cross_axis_phase_type"] = "quality_preference"
    metadata["phase_topology"] = "revision_then_quality_preference"
    row["metadata"] = metadata
    return row


def timestamp_preference_candidate_for_row(row: dict[str, Any]) -> dict[str, Any] | None:
    phase_examples = row_phase_examples(row)
    phase_golds = list(row.get("phase_gold_final_answers", []))
    timestamp_indices = [
        idx
        for idx, phase in enumerate(phase_examples)
        if str(phase.get("task_family", "")) in {"timestamp_value_lookup", "timestamp_nearest_lookup"}
    ]
    if len(timestamp_indices) < 2:
        return None
    first = clone(phase_golds[timestamp_indices[-2]])
    second = clone(phase_golds[timestamp_indices[-1]])

    def rank(candidate: dict[str, Any]) -> tuple[int, float]:
        exact = 1 if bool(candidate.get("exact_match_found", False)) else 0
        offset = float(candidate.get("offset_seconds")) if candidate.get("offset_seconds") is not None else 0.0
        return (exact, -offset)

    preferred_reference = "first" if rank(first) >= rank(second) else "second"
    chosen = clone(first if preferred_reference == "first" else second)
    chosen["preferred_reference"] = preferred_reference
    return chosen


def timestamp_preference_prompt(row: dict[str, Any], timestamp_gold: dict[str, Any]) -> str:
    return (
        "Of the first and second timestamp readings we just discussed, which one would you trust more for reporting: "
        "the first or the second?"
    )


def append_timestamp_preference_phase(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    timestamp_gold = timestamp_preference_candidate_for_row(row)
    if timestamp_gold is None:
        return row
    row["goal_revision_turns"] = list(row.get("goal_revision_turns", [])) + [timestamp_preference_prompt(row, timestamp_gold)]
    row["phase_gold_final_answers"] = list(row.get("phase_gold_final_answers", [])) + [clone(timestamp_gold)]
    append_phase_example(row, "timestamp_preference", timestamp_gold)
    row["gold_final_answer"] = clone(timestamp_gold)
    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    contract["terminal_phase_index"] = len(row.get("phase_gold_final_answers", [])) - 1
    contract["cross_axis_phase_family"] = "timestamp_preference"
    row["goal_revision_contract"] = contract
    metadata = dict(row.get("metadata", {}))
    metadata["cross_axis_phase_type"] = "timestamp_preference"
    metadata["phase_topology"] = "revision_then_timestamp_preference"
    row["metadata"] = metadata
    return row


def quality_composition_prompt(row: dict[str, Any], quality_gold: dict[str, Any]) -> str:
    family = str(row.get("task_family", ""))
    start = parse_utc(str(quality_gold["window_start"]))
    window_text = start.strftime("%B %-d, %Y")
    if family in {"window_pairwise_compare", "window_rank"}:
        return (
            f"Now keep the same site, but for the winning signal from the last answer, "
            f"would you answer or abstain for the week beginning {window_text} based on data quality?"
        )
    if family == "point_disambiguation":
        return (
            f"Now keep the same site, but for that signal, would you answer or abstain "
            f"for the week beginning {window_text} based on data quality?"
        )
    return (
        f"Now keep the same signal and site, but would you answer or abstain "
        f"for the week beginning {window_text} based on data quality?"
    )


def timestamp_phase_family(timestamp_gold: dict[str, Any]) -> str:
    return "timestamp_value_lookup" if bool(timestamp_gold.get("exact_match_found", False)) else "timestamp_nearest_lookup"


def timestamp_composition_prompt_for_row(row: dict[str, Any], timestamp_gold: dict[str, Any]) -> str:
    family = str(row.get("task_family", ""))
    public_ts = pd.Timestamp(timestamp_gold["requested_timestamp"])
    exact = bool(timestamp_gold.get("exact_match_found", False))
    if family in {"window_pairwise_compare", "window_rank"}:
        subject = "the winning signal from the last answer"
    elif family == "point_disambiguation":
        subject = "that signal"
    else:
        subject = "the same signal"
    if exact:
        return (
            f"Now keep the same site and {subject}, and give me the exact logged reading at "
            f"{public_ts.strftime('%H:%M UTC on %B %-d, %Y')}."
        )
    return (
        f"Now keep the same site and {subject}, and if I only know it was around "
        f"{public_ts.strftime('%H:%M UTC on %B %-d, %Y')}, give me the nearest available reading."
    )


def attach_cross_axis_timestamp_policy_contract(row: dict[str, Any], timestamp_gold: dict[str, Any]) -> None:
    gold_policy = "exact_observation" if bool(timestamp_gold.get("exact_match_found", False)) else "nearest_after_exact_miss"
    row["policy_choice_contract"] = {
        "type": "timestamp_observation_resolution",
        "phase_index": len(row.get("phase_gold_final_answers", [])) - 1,
        "declared_actions": ["clarify", "exact_probe", "nearest_fallback"],
        "reuse_requested_timestamp_after_clarification": True,
        "gold_policy": gold_policy,
        "gold_exact_match_found": bool(timestamp_gold.get("exact_match_found", False)),
        "fallback_reason": timestamp_gold.get("fallback_reason"),
        "cross_axis_phase": True,
    }
    metadata = dict(row.get("metadata", {}))
    metadata["policy_choice_type"] = "timestamp_observation_resolution"
    metadata["policy_choice_gold_policy"] = gold_policy
    metadata["cross_axis_timestamp_family"] = timestamp_phase_family(timestamp_gold)
    row["metadata"] = metadata


def append_timestamp_composition_phase(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    stream_id = target_stream_id_for_primary_family(row)
    if not stream_id:
        return row
    anchor = canonical_time_anchor(row, stream_id, runtime)
    timestamp_gold = timestamp_policy_candidate_for_stream(stream_id, runtime, anchor=anchor)
    if timestamp_gold is None:
        return row

    phase_family = timestamp_phase_family(timestamp_gold)
    row["goal_revision_turns"] = list(row.get("goal_revision_turns", [])) + [timestamp_composition_prompt_for_row(row, timestamp_gold)]
    row["phase_gold_final_answers"] = list(row.get("phase_gold_final_answers", [])) + [clone(timestamp_gold)]
    append_phase_example(row, phase_family, timestamp_gold)
    row["gold_final_answer"] = clone(timestamp_gold)

    exact_call = {
        "call_id": next_call_id(row.get("canonical_tool_calls", [])),
        "tool_name": "lookup_observation",
        "arguments": {
            "stream_id": stream_id,
            "timestamp": timestamp_gold["requested_timestamp"],
            "mode": "exact",
        },
    }
    canonical_calls = clone(row.get("canonical_tool_calls", []))
    canonical_calls.append(exact_call)
    if not bool(timestamp_gold.get("exact_match_found", False)):
        canonical_calls.append(
            {
                "call_id": next_call_id(canonical_calls),
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": stream_id,
                    "timestamp": timestamp_gold["requested_timestamp"],
                    "mode": "nearest",
                },
            }
        )
    row["canonical_tool_calls"] = canonical_calls

    acceptable_sets: list[list[dict[str, Any]]] = []
    seen: set[str] = set()

    def add_variant(variant: list[dict[str, Any]]) -> None:
        key = json.dumps(variant, ensure_ascii=False, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        acceptable_sets.append(variant)

    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_exact = clone(exact_call)
        variant_exact["call_id"] = next_call_id(variant_copy)
        variant_copy.append(variant_exact)
        add_variant(variant_copy)
        if bool(timestamp_gold.get("exact_match_found", False)):
            variant_plus_nearest = clone(variant_copy)
            variant_plus_nearest.append(
                {
                    "call_id": next_call_id(variant_plus_nearest),
                    "tool_name": "lookup_observation",
                    "arguments": {
                        "stream_id": stream_id,
                        "timestamp": timestamp_gold["requested_timestamp"],
                        "mode": "nearest",
                    },
                }
            )
            add_variant(variant_plus_nearest)
        else:
            variant_nearest = clone(variant_copy)
            variant_nearest.append(
                {
                    "call_id": next_call_id(variant_nearest),
                    "tool_name": "lookup_observation",
                    "arguments": {
                        "stream_id": stream_id,
                        "timestamp": timestamp_gold["requested_timestamp"],
                        "mode": "nearest",
                    },
                }
            )
            add_variant(variant_nearest)
            nearest_only = clone(variant)
            nearest_only.append(
                {
                    "call_id": next_call_id(nearest_only),
                    "tool_name": "lookup_observation",
                    "arguments": {
                        "stream_id": stream_id,
                        "timestamp": timestamp_gold["requested_timestamp"],
                        "mode": "nearest",
                    },
                }
            )
            add_variant(nearest_only)

    row["acceptable_tool_call_sets"] = acceptable_sets

    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    contract["terminal_phase_index"] = len(row.get("phase_gold_final_answers", [])) - 1
    contract["cross_axis_phase_family"] = phase_family
    row["goal_revision_contract"] = contract
    metadata = dict(row.get("metadata", {}))
    metadata["cross_axis_phase_type"] = phase_family
    metadata["phase_topology"] = "retrieval_revision_temporal_probe"
    row["metadata"] = metadata
    attach_cross_axis_timestamp_policy_contract(row, timestamp_gold)
    return row


def reporting_commitment_candidate(previous_phase_family: str, previous_gold: dict[str, Any]) -> dict[str, Any] | None:
    family = str(previous_phase_family or "")
    gold = clone(previous_gold)
    if family in {"quality_gate", "quality_preference"}:
        decision = str(gold.get("decision") or "")
        reason = str(gold.get("reason") or "")
        if decision == "answer" and reason == "healthy":
            return {"commitment_action": "answer", "reason": "healthy_quality"}
        if decision == "abstain" and reason:
            return {"commitment_action": "abstain", "reason": reason}
        return {
            "commitment_action": "re_clarify",
            "reason": "marginal_quality",
            "clarification_request": "narrower_time_range",
        }
    if family in {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"}:
        exact = bool(gold.get("exact_match_found", False))
        offset = gold.get("offset_seconds")
        if exact:
            return {"commitment_action": "answer", "reason": "exact_timestamp"}
        if offset is not None and float(offset) <= 120.0:
            return {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
        return {
            "commitment_action": "re_clarify",
            "reason": "timestamp_too_imprecise",
            "clarification_request": "more_precise_timestamp",
        }
    if family == "timestamp_resolution_context":
        quality_decision = str(gold.get("quality_decision") or "")
        quality_reason = str(gold.get("quality_reason") or "")
        if quality_decision == "abstain" and quality_reason in {"low_coverage", "long_gap"}:
            return {"commitment_action": "abstain", "reason": quality_reason}
        if quality_decision and (quality_decision != "answer" or quality_reason != "healthy"):
            return {
                "commitment_action": "re_clarify",
                "reason": "marginal_quality",
                "clarification_request": "narrower_time_range",
            }
        status = str(gold.get("resolution_status") or "")
        if status == "exact":
            return {"commitment_action": "answer", "reason": "exact_timestamp"}
        if status == "unique_nearest":
            return {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
        return {
            "commitment_action": "re_clarify",
            "reason": "timestamp_too_imprecise",
            "clarification_request": "more_precise_timestamp",
        }
    return None


def combined_reporting_commitment_candidate_for_row(row: dict[str, Any]) -> dict[str, Any]:
    quality_match: tuple[str, dict[str, Any]] | None = None
    timestamp_match: tuple[str, dict[str, Any]] | None = None
    for example, gold in reversed(list(zip(row_phase_examples(row), row.get("phase_gold_final_answers", [])))):
        family = str(example.get("task_family", ""))
        if quality_match is None and family in {"quality_gate", "quality_preference"}:
            quality_match = (family, clone(gold))
        if timestamp_match is None and family in {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"}:
            timestamp_match = (family, clone(gold))
        if quality_match is not None and timestamp_match is not None:
            break

    if quality_match is not None:
        _quality_family, quality_gold = quality_match
        decision = str(quality_gold.get("decision") or "")
        reason = str(quality_gold.get("reason") or "")
        if decision == "abstain" and reason:
            return {"commitment_action": "abstain", "reason": reason}

    if timestamp_match is not None:
        _timestamp_family, timestamp_gold = timestamp_match
        if bool(timestamp_gold.get("exact_match_found", False)):
            return {"commitment_action": "answer", "reason": "exact_timestamp"}
        offset = timestamp_gold.get("offset_seconds")
        if offset is not None and float(offset) <= 120.0:
            return {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
        return {
            "commitment_action": "re_clarify",
            "reason": "timestamp_too_imprecise",
            "clarification_request": "more_precise_timestamp",
        }

    if quality_match is not None:
        _quality_family, quality_gold = quality_match
        decision = str(quality_gold.get("decision") or "")
        reason = str(quality_gold.get("reason") or "")
        if decision == "answer" and reason == "healthy":
            return {"commitment_action": "answer", "reason": "healthy_quality"}
        if decision == "abstain" and reason:
            return {"commitment_action": "abstain", "reason": reason}
        return {
            "commitment_action": "re_clarify",
            "reason": "marginal_quality",
            "clarification_request": "narrower_time_range",
        }

    return {
        "commitment_action": "re_clarify",
        "reason": "marginal_quality",
        "clarification_request": "narrower_time_range",
    }


def strict_timestamp_quality_reporting_commitment_candidate_for_row(
    row: dict[str, Any],
    *,
    nearest_offset_threshold_seconds: float = 30.0,
) -> dict[str, Any]:
    quality_match: tuple[str, dict[str, Any]] | None = None
    timestamp_match: tuple[str, dict[str, Any]] | None = None
    for example, gold in reversed(list(zip(row_phase_examples(row), row.get("phase_gold_final_answers", [])))):
        family = str(example.get("task_family", ""))
        if quality_match is None and family in {"quality_gate", "quality_preference"}:
            quality_match = (family, clone(gold))
        if timestamp_match is None and family in {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"}:
            timestamp_match = (family, clone(gold))
        if quality_match is not None and timestamp_match is not None:
            break

    if quality_match is not None:
        _quality_family, quality_gold = quality_match
        decision = str(quality_gold.get("decision") or "")
        reason = str(quality_gold.get("reason") or "")
        if decision == "abstain" and reason:
            return {"commitment_action": "abstain", "reason": reason}
        if decision != "answer" or reason != "healthy":
            return {
                "commitment_action": "re_clarify",
                "reason": "marginal_quality",
                "clarification_request": "narrower_time_range",
            }

    if timestamp_match is not None:
        _timestamp_family, timestamp_gold = timestamp_match
        if bool(timestamp_gold.get("exact_match_found", False)):
            return {"commitment_action": "answer", "reason": "exact_timestamp"}
        offset = timestamp_gold.get("offset_seconds")
        if offset is not None and float(offset) <= nearest_offset_threshold_seconds:
            return {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
        return {
            "commitment_action": "re_clarify",
            "reason": "timestamp_too_imprecise",
            "clarification_request": "more_precise_timestamp",
        }

    return {
        "commitment_action": "re_clarify",
        "reason": "marginal_quality",
        "clarification_request": "narrower_time_range",
    }


def combined_reporting_commitment_prompt_for_row(row: dict[str, Any], commitment_gold: dict[str, Any]) -> str:
    families = [str(example.get("task_family", "")) for example in row_phase_examples(row)]
    has_quality = any(family in {"quality_gate", "quality_preference"} for family in families)
    has_timestamp = any(family in {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"} for family in families)
    action = str(commitment_gold.get("commitment_action") or "")
    clarification_request = str(commitment_gold.get("clarification_request") or "")

    if has_quality and has_timestamp:
        if action == "re_clarify" and clarification_request == "more_precise_timestamp":
            return (
                "Considering both the timestamped reading and the data-quality check we just discussed, "
                "should I report it, abstain, or ask you for a more precise timestamp before reporting it?"
            )
        if action == "re_clarify" and clarification_request == "narrower_time_range":
            return (
                "Considering both the timestamped reading and the data-quality check we just discussed, "
                "should I report it, abstain, or ask you for a narrower time range before reporting it?"
            )
        return (
            "Considering both the timestamped reading and the data-quality check we just discussed, "
            "should I report it as-is or abstain?"
        )

    if has_timestamp:
        if action == "re_clarify":
            return (
                "Based on the timestamped reading we just discussed, should I report it, abstain, "
                "or ask you for a more precise timestamp before reporting it?"
            )
        return "Based on the timestamped reading we just discussed, should I report it as-is or abstain?"

    if action == "re_clarify":
        return (
            "Based on the data-quality result we just discussed, should I report it, abstain, "
            "or ask you for a narrower time range before reporting it?"
        )
    return "Based on the data-quality result we just discussed, should I report it as-is or abstain?"


def reporting_commitment_prompt(previous_phase_family: str, commitment_gold: dict[str, Any]) -> str:
    family = str(previous_phase_family or "")
    if family in {"timestamp_value_lookup", "timestamp_nearest_lookup", "timestamp_preference"}:
        return (
            "Based on the last reading we just discussed, should I report it as-is, abstain, "
            "or ask you for a more precise timestamp before reporting it?"
        )
    return (
        "Based on the last result we just discussed, should I report it as-is, abstain, "
        "or ask you for a narrower time range before reporting it?"
    )


def append_reporting_commitment_phase(row: dict[str, Any]) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    phase_examples = row_phase_examples(row)
    if not phase_examples or not row.get("phase_gold_final_answers"):
        return row
    previous_phase_family = str(phase_examples[-1].get("task_family", ""))
    previous_gold = clone(row["phase_gold_final_answers"][-1])
    commitment_gold = reporting_commitment_candidate(previous_phase_family, previous_gold)
    if commitment_gold is None:
        return row

    row["goal_revision_turns"] = list(row.get("goal_revision_turns", [])) + [reporting_commitment_prompt(previous_phase_family, commitment_gold)]
    row["phase_gold_final_answers"] = list(row.get("phase_gold_final_answers", [])) + [clone(commitment_gold)]
    append_phase_example(row, "reporting_commitment", commitment_gold)
    row["gold_final_answer"] = clone(commitment_gold)
    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    contract["terminal_phase_index"] = len(row.get("phase_gold_final_answers", [])) - 1
    contract["terminal_commitment_phase"] = True
    row["goal_revision_contract"] = contract
    metadata = dict(row.get("metadata", {}))
    metadata["has_reporting_commitment_phase"] = True
    metadata["phase_topology"] = f"{metadata.get('phase_topology', 'unknown')}_then_reporting_commitment"
    row["metadata"] = metadata
    return row


def append_quality_composition_phase(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    if str(row.get("task_family", "")) == "quality_gate":
        return row
    quality_gold = quality_phase_candidate_for_row(row, runtime)
    if quality_gold is None:
        return row

    row["goal_revision_turns"] = list(row.get("goal_revision_turns", [])) + [quality_composition_prompt(row, quality_gold)]
    row["phase_gold_final_answers"] = list(row.get("phase_gold_final_answers", [])) + [clone(quality_gold)]
    append_phase_example(row, "quality_gate", quality_gold)
    row["gold_final_answer"] = clone(quality_gold)

    inspect_call = {
        "call_id": next_call_id(row.get("canonical_tool_calls", [])),
        "tool_name": "inspect_quality_window",
        "arguments": {
            "stream_id": quality_gold["stream_id"],
            "window_start": quality_gold["window_start"],
            "window_end": quality_gold["window_end"],
            "period": quality_gold["period"],
        },
    }
    row["canonical_tool_calls"] = clone(row.get("canonical_tool_calls", [])) + [inspect_call]

    acceptable_sets: list[list[dict[str, Any]]] = []
    for variant in row.get("acceptable_tool_call_sets", []):
        variant_copy = clone(variant)
        variant_inspect = clone(inspect_call)
        variant_inspect["call_id"] = next_call_id(variant_copy)
        variant_copy.append(variant_inspect)
        acceptable_sets.append(variant_copy)
    row["acceptable_tool_call_sets"] = acceptable_sets

    append_goal_revision_milestone_pair(row)
    refresh_goal_revision_verifier(row)
    contract = dict(row.get("goal_revision_contract", {}))
    contract["goal_revision_count"] = len(row.get("goal_revision_turns", []))
    contract["terminal_phase_index"] = len(row.get("phase_gold_final_answers", [])) - 1
    contract["cross_axis_phase_family"] = "quality_gate"
    row["goal_revision_contract"] = contract
    metadata = dict(row.get("metadata", {}))
    metadata["cross_axis_phase_type"] = "quality_gate"
    metadata["phase_topology"] = "retrieval_revision_commitment"
    row["metadata"] = metadata
    return row


def timestamp_policy_candidate_for_stream(
    stream_id: str,
    runtime: ToolStoreRuntime,
    anchor: pd.Timestamp | None = None,
) -> dict[str, Any] | None:
    cache_key = (stream_id, anchor.isoformat() if anchor is not None else None)
    if cache_key in TIMESTAMP_POLICY_CANDIDATE_CACHE:
        return clone(TIMESTAMP_POLICY_CANDIDATE_CACHE[cache_key])
    if anchor is None:
        if stream_id not in STREAM_FIRST_TIMESTAMP_CACHE:
            history = stream_history(runtime, stream_id)
            STREAM_FIRST_TIMESTAMP_CACHE[stream_id] = None if history.empty else history["timestamp"].iloc[0]
        anchor = STREAM_FIRST_TIMESTAMP_CACHE[stream_id]
        if anchor is None:
            TIMESTAMP_POLICY_CANDIDATE_CACHE[cache_key] = None
            return None
    history = stream_history(runtime, stream_id)
    if history.empty:
        TIMESTAMP_POLICY_CANDIDATE_CACHE[cache_key] = None
        return None
    timestamp_series = history["timestamp"]
    base_minute = anchor.floor("min")
    fallback_candidates: list[dict[str, Any]] = []
    miss_candidates: list[dict[str, Any]] = []
    for delta_minutes in [0, -1, 1, -2, 2, -3, 3, -4, 4]:
        candidate = base_minute + pd.Timedelta(minutes=delta_minutes)
        exact_matches = history.loc[timestamp_series == candidate]
        if exact_matches.empty:
            exact = {
                "stream_id": stream_id,
                "requested_timestamp": candidate.isoformat(),
                "exact_match_found": False,
            }
        else:
            exact_row = exact_matches.iloc[0]
            exact = {
                "stream_id": stream_id,
                "requested_timestamp": candidate.isoformat(),
                "observed_timestamp": pd.Timestamp(exact_row["timestamp"]).isoformat(),
                "value": round(float(exact_row["value"]), 4),
                "exact_match_found": True,
            }
        deltas = (timestamp_series - candidate).abs().dt.total_seconds()
        if deltas.empty:
            continue
        nearest_idx = pd.DataFrame({"delta_seconds": deltas, "timestamp": timestamp_series}).sort_values(["delta_seconds", "timestamp"]).index[0]
        best = history.loc[nearest_idx]
        nearest = {
            "stream_id": stream_id,
            "requested_timestamp": candidate.isoformat(),
            "observed_timestamp": pd.Timestamp(best["timestamp"]).isoformat(),
            "value": round(float(best["value"]), 4),
            "exact_match_found": bool(not exact_matches.empty),
            "fallback_reason": "nearest_available_observation",
            "offset_seconds": round(float(deltas.loc[nearest_idx]), 3),
        }
        payload = {
            "stream_id": stream_id,
            "requested_timestamp": candidate.isoformat(),
            "observed_timestamp": exact.get("observed_timestamp") if exact.get("exact_match_found") else nearest.get("observed_timestamp"),
            "value": exact.get("value") if exact.get("exact_match_found") else nearest.get("value"),
            "exact_match_found": bool(exact.get("exact_match_found", False)),
            "fallback_reason": None if exact.get("exact_match_found") else nearest.get("fallback_reason", "nearest_available_observation"),
            "offset_seconds": 0.0 if exact.get("exact_match_found") else nearest.get("offset_seconds"),
        }
        fallback_candidates.append(payload)
        if not payload["exact_match_found"]:
            miss_candidates.append(payload)
    exact_at_anchor = next(
        (
            candidate
            for candidate in fallback_candidates
            if bool(candidate.get("exact_match_found"))
            and str(candidate.get("requested_timestamp")) == base_minute.isoformat()
        ),
        None,
    )
    nearest_miss = min(miss_candidates, key=lambda item: (float(item.get("offset_seconds", 1e9)), str(item.get("requested_timestamp", "")))) if miss_candidates else None
    nearest_exact = min(
        [candidate for candidate in fallback_candidates if bool(candidate.get("exact_match_found"))],
        key=lambda item: (float(item.get("offset_seconds", 0.0)), str(item.get("requested_timestamp", ""))),
    ) if any(bool(candidate.get("exact_match_found")) for candidate in fallback_candidates) else None
    result = exact_at_anchor or nearest_miss or nearest_exact or (fallback_candidates[0] if fallback_candidates else None)
    TIMESTAMP_POLICY_CANDIDATE_CACHE[cache_key] = clone(result) if result is not None else None
    return clone(result)


def timestamp_composition_prompt(timestamp_gold: dict[str, Any]) -> str:
    public_ts = pd.Timestamp(timestamp_gold["requested_timestamp"])
    return (
        f"Now keep the same signal and site, but if I only know it was around "
        f"{public_ts.strftime('%H:%M UTC on %B %-d, %Y')}, give me the nearest available reading."
    )


def append_quality_to_timestamp_composition(
    row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    row = clone(row)
    row = ensure_phase_gold(row)
    if str(row.get("task_family", "")) != "quality_gate":
        return row
    row = append_timestamp_composition_phase(row, runtime)
    metadata = dict(row.get("metadata", {}))
    metadata["phase_topology"] = "quality_then_temporal_probe"
    row["metadata"] = metadata
    return row


def append_cross_axis_composition_phase(
    row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    family = str(static_row.get("task_family") or row.get("task_family") or "")
    updated = clone(row)

    if family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
        updated = append_timestamp_preference_phase(updated, runtime)
        return updated

    if family == "quality_gate":
        updated = append_timestamp_composition_phase(updated, runtime)
        return updated

    stream_id = target_stream_id_for_primary_family(updated)
    timestamp_candidate = None
    if stream_id:
        anchor = canonical_time_anchor(updated, stream_id, runtime)
        timestamp_candidate = timestamp_policy_candidate_for_stream(stream_id, runtime, anchor=anchor)
    quality_candidate = quality_phase_candidate_for_row(updated, runtime)

    if timestamp_candidate is not None:
        updated = append_timestamp_composition_phase(updated, runtime)

    should_append_quality = False
    if quality_candidate is not None:
        quality_reason = str(quality_candidate.get("reason") or "")
        if timestamp_candidate is None:
            should_append_quality = True
        elif quality_reason != "healthy":
            should_append_quality = True
        elif family in {"window_pairwise_compare", "window_rank"}:
            should_append_quality = True

    if should_append_quality:
        updated = append_quality_preference_phase(updated, runtime)

    return updated


def explicit_controller_composite_repair(
    row: dict[str, Any],
    static_row: dict[str, Any],
    runtime: ToolStoreRuntime,
    before_controller: dict[str, Any],
) -> dict[str, Any]:
    return row


def apply_timestamp_policy_surface(static_row: dict[str, Any], agentic_row: dict[str, Any]) -> dict[str, Any]:
    family = static_row.get("task_family")
    if not eligible_for_timestamp_policy_choice(static_row, agentic_row):
        return agentic_row

    row = clone(agentic_row)
    required_slots = list(row.get("required_clarification_slots", []))
    prompt_source = static_row.get("query", "")
    if family == "timestamp_nearest_lookup":
        prompt_source = make_implicit_nearest_prompt(static_row)
    if "site_id" in required_slots:
        prompt_source = remove_site_mentions(prompt_source, static_row["site_id"])
    if "time_reference" in required_slots:
        prompt_source = remove_time_mentions(prompt_source, family)
    visible = humanize_visible_prompt(prompt_source, family, "direct_then_evidence")
    visible = clean_surface_prompt(visible, family)
    row["initial_user_message"] = wrap_policy_prompt(visible, required_slots)
    row["query"] = row["initial_user_message"]
    row["hidden_user_instruction"] = visible

    if set(required_slots) == {"time_reference"}:
        row["interaction_mode"] = "clarify_time_then_policy_timestamp_then_evidence"
    elif set(required_slots) == {"site_id", "time_reference"}:
        row["interaction_mode"] = "clarify_site_time_then_policy_timestamp_then_evidence"
    elif set(required_slots) == {"site_id"}:
        row["interaction_mode"] = "clarify_site_then_policy_timestamp_then_evidence"
    else:
        row["interaction_mode"] = "policy_timestamp_then_evidence"

    gold = row.get("gold_final_answer", {})
    gold_policy = "exact_observation" if bool(gold.get("exact_match_found", True)) else "nearest_after_exact_miss"
    row["policy_choice_contract"] = {
        "type": "timestamp_observation_resolution",
        "declared_actions": ["clarify", "exact_probe", "nearest_fallback"],
        "reuse_requested_timestamp_after_clarification": True,
        "gold_policy": gold_policy,
        "gold_exact_match_found": bool(gold.get("exact_match_found", True)),
        "fallback_reason": gold.get("fallback_reason"),
    }

    metadata = dict(row.get("metadata", {}))
    metadata["policy_choice_type"] = "timestamp_observation_resolution"
    metadata["policy_choice_gold_policy"] = gold_policy
    row["metadata"] = metadata
    return row


def humanize_timestamp_nearest_contract(agentic_row: dict[str, Any]) -> dict[str, Any]:
    if agentic_row.get("task_family") != "timestamp_nearest_lookup":
        return agentic_row

    row = clone(agentic_row)
    gold = dict(row.get("gold_final_answer", {}))
    requested_timestamp = gold.get("requested_timestamp")
    observed_timestamp = gold.get("observed_timestamp")
    if not isinstance(requested_timestamp, str) or not isinstance(observed_timestamp, str):
        return row

    public_timestamp = floor_to_public_minute(requested_timestamp)
    observed_ts = parse_utc(observed_timestamp)
    public_timestamp_iso = public_timestamp.isoformat()

    gold["requested_timestamp"] = public_timestamp_iso
    gold["offset_seconds"] = round(abs((observed_ts - public_timestamp).total_seconds()), 3)
    row["gold_final_answer"] = gold

    def patch_lookup_timestamp(call: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
        patched = clone(call)
        args = dict(patched.get("arguments", {}))
        args["timestamp"] = public_timestamp_iso
        if mode is not None:
            args["mode"] = mode
        patched["arguments"] = args
        return patched

    canonical_calls = [clone(call) for call in row.get("canonical_tool_calls", [])]
    resolve_call = None
    nearest_call = None
    for call in canonical_calls:
        if call.get("tool_name") == "resolve_point" and resolve_call is None:
            resolve_call = clone(call)
        if call.get("tool_name") == "lookup_observation":
            nearest_call = patch_lookup_timestamp(call, mode="nearest")
    if resolve_call is not None and nearest_call is not None:
        exact_call = patch_lookup_timestamp(nearest_call, mode="exact")
        exact_call["call_id"] = "c2"
        nearest_call["call_id"] = "c3"
        row["canonical_tool_calls"] = [resolve_call, exact_call, nearest_call]

        acceptable_variants: list[list[dict[str, Any]]] = []
        seen_variants: set[str] = set()

        def add_variant(variant: list[dict[str, Any]]) -> None:
            key = json.dumps(variant, ensure_ascii=False, sort_keys=True)
            if key in seen_variants:
                return
            seen_variants.add(key)
            acceptable_variants.append(variant)

        add_variant([clone(resolve_call), clone(exact_call), clone(nearest_call)])

        nearest_only_call = clone(nearest_call)
        nearest_only_call["call_id"] = "c2"
        add_variant([clone(resolve_call), nearest_only_call])

        for variant in row.get("acceptable_tool_call_sets", []):
            patched_variant: list[dict[str, Any]] = []
            for call in variant:
                patched_call = clone(call)
                if patched_call.get("tool_name") == "lookup_observation":
                    patched_call = patch_lookup_timestamp(patched_call)
                patched_variant.append(patched_call)
            add_variant(patched_variant)

        row["acceptable_tool_call_sets"] = acceptable_variants

    contract = dict(row.get("policy_choice_contract", {}))
    if contract:
        contract["public_requested_timestamp"] = public_timestamp_iso
        contract["public_timestamp_granularity"] = "minute"
        row["policy_choice_contract"] = contract

    metadata = dict(row.get("metadata", {}))
    metadata["public_requested_timestamp"] = public_timestamp_iso
    metadata["public_timestamp_granularity"] = "minute"
    row["metadata"] = metadata
    return row


def prepare_agentic_row(
    static_row: dict[str, Any],
    source_row: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    family = str(static_row.get("task_family", source_row.get("task_family", "")))
    builder_name = "build_canonical_agentic_final"
    history = clone(source_row.get("generation_history", []))
    row = normalize_surface_fields(source_row, family)
    append_history_entry(
        history,
        stage="canonical_seed_entry",
        stage_type="audit",
        status="observed",
        builder=builder_name,
        details={
            "source_agentic_scenario_id": source_row.get("scenario_id"),
            "source_static_scenario_id": static_row.get("scenario_id"),
            "source_contract": row_contract_summary(row),
            "source_dsl_audit": row_dsl_audit_snapshot(row),
        },
    )
    row = apply_repair_step(
        row,
        history,
        "timestamp_policy_surface_repair",
        "repair",
        builder_name,
        lambda current: apply_timestamp_policy_surface(static_row, current),
    )
    row = apply_repair_step(
        row,
        history,
        "timestamp_nearest_contract_humanization",
        "repair",
        builder_name,
        lambda current: humanize_timestamp_nearest_contract(current),
    )
    row = apply_repair_step(
        row,
        history,
        "timestamp_value_revision_contract_repair",
        "repair",
        builder_name,
        lambda current: append_timestamp_value_revision_contract(current, static_row, runtime),
    )
    row = apply_repair_step(
        row,
        history,
        "goal_revision_contract_repair",
        "repair",
        builder_name,
        lambda current: append_goal_revision_contract(current, static_row, runtime),
    )
    row = apply_repair_step(
        row,
        history,
        "point_target_revision_contract_repair",
        "repair",
        builder_name,
        lambda current: append_point_target_revision_contract(current, static_row, runtime),
    )
    row = apply_repair_step(
        row,
        history,
        "goal_revision_clarification_repair",
        "repair",
        builder_name,
        lambda current: update_goal_revision_clarification_contract(static_row, current),
    )
    row = apply_repair_step(
        row,
        history,
        "canonical_surface_normalization",
        "surface",
        builder_name,
        lambda current: normalize_surface_fields(current, family),
    )
    append_history_entry(
        history,
        stage="controller_proxy_audit_round_1",
        stage_type="audit",
        status="observed",
        builder=builder_name,
        details={
            "contract_before_controller_repair": row_contract_summary(row),
            "dsl_audit_before_controller_repair": row_dsl_audit_snapshot(row),
            "controller_proxy_audit": controller_proxy_audit_snapshot(row, runtime),
        },
    )
    row = apply_controller_repair_step(
        row,
        static_row,
        runtime,
        history,
        "controller_proxy_repair_round_1",
        "repair",
        builder_name,
        explicit_controller_composite_repair,
    )
    row = apply_repair_step(
        row,
        history,
        "multi_axis_composition_repair",
        "repair",
        builder_name,
        lambda current: append_cross_axis_composition_phase(current, static_row, runtime),
    )
    row = apply_repair_step(
        row,
        history,
        "reporting_commitment_composition_repair",
        "repair",
        builder_name,
        append_reporting_commitment_phase,
    )
    row = apply_repair_step(
        row,
        history,
        "canonical_surface_normalization_after_controller_repair",
        "surface",
        builder_name,
        lambda current: normalize_surface_fields(current, family),
    )
    append_history_entry(
        history,
        stage="canonical_pre_hardness_contract_ready",
        stage_type="audit",
        status="generated",
        builder=builder_name,
        details={
            "final_contract_before_global_audit": row_contract_summary(row),
            "dsl_audit_before_global_hardness": row_dsl_audit_snapshot(row),
        },
    )
    return row, history


def interaction_primitives(row: dict[str, Any]) -> list[str]:
    primitives: list[str] = ["operator_handoff_surface"]
    slots = set(row.get("required_clarification_slots", []))
    if "site_id" in slots:
        primitives.append("clarify_site")
    if "time_reference" in slots:
        primitives.append("clarify_time")
    if any(family in {"timestamp_value_lookup", "timestamp_nearest_lookup"} for family in phase_task_families(row)):
        primitives.append("timestamp_policy_choice")
    if has_quality_decision_semantics(row):
        primitives.append("quality_decision_branch")
    if row.get("goal_revision_turns"):
        primitives.append("goal_revision")
        primitives.append("state_carryover")
    post_turns = row.get("post_answer_user_turns", [])
    if post_turns:
        primitives.append("evidence_followup")
    if has_rationale_followup(row):
        primitives.append("rationale_followup")
    return primitives


def normalized_interaction_mode(row: dict[str, Any]) -> str:
    slots = set(row.get("required_clarification_slots", []))
    has_policy = any(family in {"timestamp_value_lookup", "timestamp_nearest_lookup"} for family in phase_task_families(row))
    has_quality = has_quality_decision_semantics(row)
    has_rationale = has_rationale_followup(row)
    has_goal_revision = bool(row.get("goal_revision_turns"))
    phase_sequence = [str(phase.get("task_family", "")) for phase in row_phase_examples(row)]
    final_phase_family = phase_sequence[-1] if phase_sequence else ""

    if slots == {"site_id", "time_reference"}:
        prefix = "clarify_site_time"
    elif slots == {"site_id"}:
        prefix = "clarify_site"
    elif slots == {"time_reference"}:
        prefix = "clarify_time"
    else:
        prefix = "direct"

    parts = [prefix]
    if has_policy and has_quality:
        if final_phase_family == "quality_gate":
            parts.extend(["policy_timestamp", "quality_decision"])
        elif final_phase_family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
            parts.extend(["quality_decision", "policy_timestamp"])
        else:
            parts.extend(["quality_decision", "policy_timestamp"])
    elif has_policy:
        parts.append("policy_timestamp")
    elif has_quality:
        parts.append("quality_decision")
    if has_goal_revision:
        parts.append("goal_revision")
    if has_rationale:
        parts.append("rationale")
    parts.append("evidence")
    return "_then_".join(parts)


def interaction_necessity(row: dict[str, Any]) -> dict[str, Any]:
    post_turns = row.get("post_answer_user_turns", [])
    rationale_followup = has_rationale_followup(row)
    evidence_followup = bool(post_turns)
    goal_revision = bool(row.get("goal_revision_turns"))
    aggregate_reasoning = has_aggregate_window_semantics(row)
    group_reasoning = has_compare_window_semantics(row) or has_rank_window_semantics(row)
    target_resolution = row.get("task_family") in {"point_disambiguation", "timestamp_value_lookup"}
    quality_decision = has_quality_decision_semantics(row)
    timestamp_policy = any(family in {"timestamp_value_lookup", "timestamp_nearest_lookup"} for family in phase_task_families(row))
    needs = {
        "clarification_necessity": bool(row.get("required_clarification_slots")),
        "policy_branch_necessity": bool(row.get("policy_choice_contract")) or has_quality_decision_semantics(row),
        "aggregate_reasoning_necessity": aggregate_reasoning,
        "group_reasoning_necessity": group_reasoning,
        "target_resolution_necessity": target_resolution,
        "quality_decision_necessity": quality_decision,
        "timestamp_policy_necessity": timestamp_policy,
        "state_carryover_necessity": goal_revision,
        "goal_revision_necessity": goal_revision,
        "evidence_gated_commitment": evidence_followup,
        "rationale_gated_commitment": rationale_followup,
    }
    semantic_axes = {
        "policy_branch_necessity": needs["policy_branch_necessity"],
        "aggregate_reasoning_necessity": needs["aggregate_reasoning_necessity"],
        "group_reasoning_necessity": needs["group_reasoning_necessity"],
        "target_resolution_necessity": needs["target_resolution_necessity"],
        "quality_decision_necessity": needs["quality_decision_necessity"],
        "timestamp_policy_necessity": needs["timestamp_policy_necessity"],
    }
    interaction_axes = {
        "state_carryover_necessity": needs["state_carryover_necessity"],
        "goal_revision_necessity": needs["goal_revision_necessity"],
        "rationale_gated_commitment": needs["rationale_gated_commitment"],
    }
    needs["accepted_as_agentic"] = any(semantic_axes.values()) and any(interaction_axes.values())
    needs["accepted_as_agentic_basis"] = [
        axis for axis, active in {**semantic_axes, **interaction_axes}.items() if active
    ]
    return needs


def generic_audit_predicates(static_row: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lookup_observation_semantics": has_lookup_observation_semantics(row),
        "aggregate_window_semantics": has_aggregate_window_semantics(static_row),
        "compare_window_semantics": has_compare_window_semantics(static_row),
        "rank_window_semantics": has_rank_window_semantics(static_row),
        "quality_decision_semantics": has_quality_decision_semantics(row),
        "timestamp_policy_choice_eligible": eligible_for_timestamp_policy_choice(static_row, row),
        "goal_revision_eligible": eligible_for_goal_revision(static_row),
        "has_required_clarification_slots": bool(row.get("required_clarification_slots")),
        "has_post_answer_followup": bool(row.get("post_answer_user_turns")),
    }


def gold_signature(row: dict[str, Any]) -> str:
    return json.dumps(row.get("gold_final_answer", {}), ensure_ascii=False, sort_keys=True)


def public_view_signature(row: dict[str, Any]) -> str:
    payload = {
        "task_family": row.get("task_family"),
        "initial_user_message": row.get("initial_user_message"),
        "required_slots": sorted(row.get("required_clarification_slots", [])),
        "goal_revision_turns": row.get("goal_revision_turns", []),
        "post_answer_user_turns": row.get("post_answer_user_turns", []),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def collision_group_id(signature: str) -> str:
    return f"pv_{sha1(signature.encode('utf-8')).hexdigest()[:12]}"


def build_collision_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[public_view_signature(row)].append(row)
    collisions: dict[str, dict[str, Any]] = {}
    for signature, group in groups.items():
        distinct_gold = {gold_signature(row) for row in group}
        if len(group) > 1 and len(distinct_gold) > 1:
            collisions[signature] = {
                "group_id": collision_group_id(signature),
                "group_size": len(group),
                "distinct_gold_count": len(distinct_gold),
                "scenario_ids": [row["scenario_id"] for row in group],
            }
    return collisions


def dsl_required_atoms(row: dict[str, Any]) -> list[str]:
    atoms: list[str] = []
    tool_names = set(canonical_tool_names(row))
    slots = set(row.get("required_clarification_slots", []))
    phase_families = phase_task_families(row)
    phase_examples = row_phase_examples(row)
    if "site_id" in slots:
        atoms.append("ASK_SITE")
    if "time_reference" in slots:
        atoms.append("ASK_TIME")
    if "resolve_point" in tool_names and not has_compare_window_semantics(row) and not has_rank_window_semantics(row):
        atoms.append("RESOLVE_SINGLE_STREAM")
    if has_compare_window_semantics(row):
        atoms.append("RESOLVE_STREAM_PAIR")
    if has_rank_window_semantics(row):
        atoms.append("RESOLVE_RANK_SCOPE")
    if has_aggregate_window_semantics(row):
        atoms.append("AGGREGATE_WINDOW")
    if has_compare_window_semantics(row):
        atoms.append("COMPARE_WINDOW")
    if has_rank_window_semantics(row):
        atoms.append("RANK_WINDOW")
    if any(family in {"timestamp_value_lookup", "timestamp_nearest_lookup"} for family in phase_families):
        atoms.append("PROBE_EXACT")
        if any(
            phase.get("task_family") in {"timestamp_value_lookup", "timestamp_nearest_lookup"}
            and not bool(phase.get("gold_final_answer", {}).get("exact_match_found", False))
            for phase in phase_examples
        ):
            atoms.append("PROBE_NEAREST")
    if has_quality_decision_semantics(row):
        atoms.append("DECIDE_QUALITY")
    if row.get("goal_revision_turns"):
        atoms.extend(["ANSWER_INITIAL", "REVISE_SAME_CONTEXT", "ANSWER_FINAL"])
        if len(row.get("goal_revision_turns", [])) > 1:
            atoms.append("REVISE_SAME_CONTEXT_MULTI")
    else:
        if row.get("gold_final_answer", {}).get("decision") == "abstain":
            atoms.append("ABSTAIN_FINAL")
        else:
            atoms.append("ANSWER_FINAL")
    if has_rationale_followup(row):
        atoms.append("ANSWER_RATIONALE")
    if row.get("post_answer_user_turns"):
        atoms.append("ANSWER_EVIDENCE")
    return atoms


def requirement_signature(row: dict[str, Any]) -> str:
    payload = {
        "required_atoms": sorted(dsl_required_atoms(row)),
        "goal_revision": bool(row.get("goal_revision_turns")),
        "abstain_gold": row.get("gold_final_answer", {}).get("decision") == "abstain",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def enumerate_dsl_programs(max_atoms: int, forbidden_atoms: set[str]) -> list[frozenset[str]]:
    from itertools import combinations

    allowed = [atom for atom in DSL_ATOMS if atom not in forbidden_atoms]
    programs: list[frozenset[str]] = []
    for size in range(max_atoms + 1):
        for combo in combinations(allowed, size):
            programs.append(frozenset(combo))
    return programs


def dsl_program_solves_row(program: frozenset[str], row: dict[str, Any]) -> bool:
    required = set(dsl_required_atoms(row))
    if not required.issubset(program):
        return False
    if row.get("goal_revision_turns"):
        ordered = ["ANSWER_INITIAL", "REVISE_SAME_CONTEXT", "ANSWER_FINAL"]
        if not all(atom in program for atom in ordered):
            return False
    if row.get("gold_final_answer", {}).get("decision") == "abstain" and "ABSTAIN_FINAL" not in program:
        return False
    if not row.get("goal_revision_turns") and row.get("gold_final_answer", {}).get("decision") != "abstain":
        if "ANSWER_FINAL" not in program:
            return False
    return True


def bounded_dsl_search(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from math import comb

    requirement_groups: dict[str, dict[str, Any]] = {}
    signature_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        sig = requirement_signature(row)
        signature_rows.setdefault(sig, row)
        group = requirement_groups.setdefault(
            sig,
            {
                "requirement_signature": sig,
                "required_atoms": sorted(dsl_required_atoms(row)),
                "row_count": 0,
                "scenario_ids": [],
            },
        )
        group["row_count"] += 1
        if len(group["scenario_ids"]) < 8:
            group["scenario_ids"].append(row["scenario_id"])

    row_results: dict[str, dict[str, bool]] = {}
    class_reports: dict[str, Any] = {}
    for solver_class, spec in DSL_SOLVER_CLASSES.items():
        max_atoms = int(spec["max_atoms"])
        forbidden_atoms = set(spec["forbidden_atoms"])
        allowed_atoms = [atom for atom in DSL_ATOMS if atom not in forbidden_atoms]
        satisfiable_signatures: set[str] = set()
        satisfiable_row_count = 0
        for sig, group in requirement_groups.items():
            first_row = signature_rows[sig]
            if dsl_solver_class_satisfies_row(first_row, spec):
                satisfiable_signatures.add(sig)
                satisfiable_row_count += group["row_count"]
        candidate_program_count = sum(comb(len(allowed_atoms), size) for size in range(max_atoms + 1))
        class_reports[solver_class] = {
            "search_mode": "closed_form_monotone_exact",
            "max_atoms": max_atoms,
            "forbidden_atoms": sorted(spec["forbidden_atoms"]),
            "candidate_program_count": candidate_program_count,
            "satisfiable_requirement_signature_count": len(satisfiable_signatures),
            "best_cover_row_count": satisfiable_row_count,
            "best_program_atoms": [],
        }
        for row in rows:
            row_results.setdefault(row["scenario_id"], {})[solver_class] = requirement_signature(row) in satisfiable_signatures

    return {
        "dsl_atoms": DSL_ATOMS,
        "solver_classes": class_reports,
        "requirement_groups": sorted(requirement_groups.values(), key=lambda item: (-item["row_count"], item["requirement_signature"])),
        "row_results": row_results,
    }


def solver_hardness(
    row: dict[str, Any],
    collision_index: dict[str, dict[str, Any]],
    dsl_search_result: dict[str, Any],
) -> dict[str, Any]:
    blocked: list[str] = []
    certificates: list[dict[str, Any]] = []

    row_dsl = dsl_search_result["row_results"].get(row["scenario_id"], {})
    for solver_class, satisfiable in row_dsl.items():
        if not satisfiable:
            blocked.append(solver_class)
            certificates.append(
                {
                    "type": "bounded_dsl_search",
                    "solver_class": solver_class,
                    "required_atoms": dsl_required_atoms(row),
                    "reason": "no capability-subset program in the declared bounded DSL can satisfy the row's deterministic interaction requirements",
                }
            )

    signature = public_view_signature(row)
    collision = collision_index.get(signature)
    if collision is not None:
        blocked.append("RS1_public_slot_template")
        certificates.append(
            {
                "type": "observational_collision",
                "solver_class": "RS1_public_slot_template",
                "group_id": collision["group_id"],
                "group_size": collision["group_size"],
                "distinct_gold_count": collision["distinct_gold_count"],
                "witness_scenario_ids": collision["scenario_ids"][:6],
                "reason": "multiple rows share the same public solver view but require different gold answers",
            }
        )

    blocked = sorted(set(blocked))
    return {
        "declared_solver_classes_blocked": blocked,
        "certificate_count": len(certificates),
        "certificates": certificates,
        "public_view_signature_hash": collision_group_id(signature),
    }


def core_release_filter(row: dict[str, Any]) -> dict[str, Any]:
    blocked = set(row["agentic_lifting"]["declared_solver_hardness"]["declared_solver_classes_blocked"])
    reasons: list[str] = []
    if row.get("goal_revision_turns") and row.get("task_family") == "point_disambiguation" and "RS14_point_target_revision_template" in blocked:
        reasons.append("point_target_revision_template_hardness")
    if row.get("goal_revision_turns") and has_aggregate_window_semantics(row) and "RS8_stateful_single_stream_revision" in blocked:
        reasons.append("single_stream_revision_template_hardness")
    if row.get("goal_revision_turns") and has_compare_window_semantics(row) and "RS9_stateful_pairwise_revision" in blocked:
        reasons.append("pairwise_revision_template_hardness")
    if row.get("goal_revision_turns") and has_rank_window_semantics(row) and "RS10_rank_template_solver" in blocked:
        reasons.append("ranking_revision_template_hardness")
    contract = row.get("policy_choice_contract")
    if contract and contract.get("gold_policy") == "nearest_after_exact_miss" and "RS12_timestamp_policy_template" in blocked:
        reasons.append("timestamp_policy_template_hardness")
    if has_quality_decision_semantics(row) and "RS11_quality_gate_template_solver" in blocked:
        reasons.append("quality_gate_template_hardness")
    return {
        "main_release_eligible": bool(reasons),
        "main_release_reasons": reasons,
    }


def conversion_trace(
    static_row: dict[str, Any],
    agentic_row: dict[str, Any],
    audit_predicates: dict[str, Any],
    needs: dict[str, Any],
    primitives: list[str],
    hardness: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "stage": "seed_static_executable_task",
            "status": "applied",
            "details": {
                "source_static_scenario_id": static_row["scenario_id"],
                "source_query_surface_version": static_row.get("query_surface_version"),
                "source_required_tool_count": len(static_row.get("canonical_tool_calls", [])),
            },
        },
        {
            "stage": "operator_surface_wrap",
            "status": "applied",
            "details": {
                "source_static_query": static_row.get("query"),
                "lifted_initial_user_message": agentic_row.get("initial_user_message"),
            },
        },
        {
            "stage": "interaction_necessity_audit",
            "status": "applied",
            "details": {
                "generic_audit_predicates": audit_predicates,
                "interaction_necessity": needs,
            },
        },
        {
            "stage": "typed_interaction_lift",
            "status": "applied",
            "details": {
                "interaction_mode": agentic_row.get("interaction_mode"),
                "primitives": primitives,
            },
        },
        {
            "stage": "deterministic_contract_binding",
            "status": "applied",
            "details": {
                "deterministic_user_simulator": True,
                "deterministic_programmatic_evaluator": True,
                "acceptable_tool_call_set_count": len(agentic_row.get("acceptable_tool_call_sets", [])),
                "goal_revision_turn_count": len(agentic_row.get("goal_revision_turns", [])),
                "post_answer_user_turn_count": len(agentic_row.get("post_answer_user_turns", [])),
            },
        },
        {
            "stage": "declared_solver_hardness_audit",
            "status": "applied",
            "details": {
                "blocked_solver_classes": hardness["declared_solver_classes_blocked"],
                "certificate_count": hardness["certificate_count"],
            },
        },
    ]


def augment_rows(
    static_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    runtime: ToolStoreRuntime,
    collision_index: dict[str, dict[str, Any]],
    dsl_search_result: dict[str, Any],
) -> list[dict[str, Any]]:
    static_by_id = {row["scenario_id"]: row for row in static_rows}
    augmented: list[dict[str, Any]] = []
    for source_row in source_rows:
        static_id = source_row.get("backing_static_scenario_id") or source_row["scenario_id"]
        static_row = static_by_id[static_id]
        row, history = prepare_agentic_row(static_row, source_row, runtime)
        row["interaction_mode"] = normalized_interaction_mode(row)
        audit_predicates = generic_audit_predicates(static_row, row)
        needs = interaction_necessity(row)
        primitives = interaction_primitives(row)
        hardness = solver_hardness(row, collision_index, dsl_search_result)
        lift = {
            "lifting_version": LIFT_VERSION,
            "source_static_scenario_id": static_row["scenario_id"],
            "source_static_query": static_row.get("query"),
            "source_static_query_surface_version": static_row.get("query_surface_version"),
            "generic_audit_predicates": audit_predicates,
            "interaction_primitives": primitives,
            "interaction_necessity": needs,
            "deterministic_contract": {
                "deterministic_user_simulator": True,
                "deterministic_programmatic_evaluator": True,
                "goal_revision_turn_count": len(row.get("goal_revision_turns", [])),
                "followup_prompt_count": len(row.get("post_answer_user_turns", [])),
                "acceptable_tool_call_set_count": len(row.get("acceptable_tool_call_sets", [])),
            },
            "declared_solver_hardness": hardness,
            "conversion_trace": conversion_trace(static_row, row, audit_predicates, needs, primitives, hardness),
        }
        row["agentic_lifting"] = lift
        row["release_filter"] = core_release_filter(row)
        append_history_entry(
            history,
            stage="declared_solver_hardness_audit",
            stage_type="audit",
            status="generated",
            builder="build_canonical_agentic_final",
            details={
                "required_atoms": sorted(dsl_required_atoms(row)),
                "blocked_solver_classes": hardness["declared_solver_classes_blocked"],
                "certificate_count": hardness["certificate_count"],
                "public_view_signature_hash": hardness["public_view_signature_hash"],
                "release_filter": row["release_filter"],
            },
        )
        append_history_entry(
            history,
            stage="canonical_row_acceptance",
            stage_type="acceptance",
            status="accepted" if needs["accepted_as_agentic"] else "rejected",
            builder="build_canonical_agentic_final",
            details={
                "interaction_primitives": primitives,
                "interaction_necessity": needs,
                "main_release_eligible": row["release_filter"]["main_release_eligible"],
                "main_release_reasons": row["release_filter"]["main_release_reasons"],
            },
        )
        row["generation_history"] = history
        metadata = dict(row.get("metadata", {}))
        metadata["agentic_lifting_version"] = LIFT_VERSION
        metadata["agentic_lifting_interaction_primitives"] = primitives
        metadata["agentic_lifting_blocked_solver_classes"] = hardness["declared_solver_classes_blocked"]
        metadata["agentic_lifting_public_view_signature_hash"] = hardness["public_view_signature_hash"]
        metadata["main_release_eligible"] = row["release_filter"]["main_release_eligible"]
        metadata["main_release_reasons"] = row["release_filter"]["main_release_reasons"]
        metadata["generation_history_length"] = len(history)
        row["metadata"] = metadata
        augmented.append(row)
    return augmented


def lifting_summary(rows: list[dict[str, Any]], collision_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    primitive_counts: Counter[str] = Counter()
    blocked_counts: Counter[str] = Counter()
    necessity_counts: Counter[str] = Counter()
    family_primitives: dict[str, Counter[str]] = defaultdict(Counter)
    accepted = 0
    for row in rows:
        lift = row["agentic_lifting"]
        if lift["interaction_necessity"]["accepted_as_agentic"]:
            accepted += 1
        for primitive in lift["interaction_primitives"]:
            primitive_counts[primitive] += 1
            family_primitives[row["task_family"]][primitive] += 1
        for solver_class in lift["declared_solver_hardness"]["declared_solver_classes_blocked"]:
            blocked_counts[solver_class] += 1
        for key, value in lift["interaction_necessity"].items():
            if key in {"accepted_as_agentic", "accepted_as_agentic_basis"}:
                continue
            if value:
                necessity_counts[key] += 1
    return {
        "lifting_version": LIFT_VERSION,
        "row_count": len(rows),
        "accepted_as_agentic_count": accepted,
        "interaction_primitive_counts": dict(primitive_counts),
        "interaction_necessity_counts": dict(necessity_counts),
        "blocked_solver_class_counts": dict(blocked_counts),
        "public_view_collision_group_count": len(collision_index),
        "family_interaction_primitives": {
            family: dict(counter) for family, counter in sorted(family_primitives.items())
        },
    }


def core_release_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "task_families": dict(Counter(str(row.get("task_family", "")) for row in rows)),
        "interaction_modes": dict(Counter(str(row.get("interaction_mode", "")) for row in rows)),
        "release_reasons": dict(
            Counter(reason for row in rows for reason in row.get("release_filter", {}).get("main_release_reasons", []))
        ),
    }


def generation_history_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stage_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    repair_counts: Counter[str] = Counter()
    stage_status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    family_modified_repairs: dict[str, Counter[str]] = defaultdict(Counter)
    max_history_length = 0
    min_history_length = 10**9 if rows else 0
    for row in rows:
        history = row.get("generation_history", [])
        max_history_length = max(max_history_length, len(history))
        min_history_length = min(min_history_length, len(history))
        for entry in history:
            stage = str(entry.get("stage", ""))
            status = str(entry.get("status", ""))
            stage_counts[stage] += 1
            status_counts[status] += 1
            stage_status_counts[stage][status] += 1
            if str(entry.get("stage_type", "")) == "repair":
                repair_counts[stage] += 1
                if status == "modified":
                    family_modified_repairs[str(row.get("task_family", ""))][stage] += 1
    return {
        "row_count": len(rows),
        "min_history_length": min_history_length if rows else 0,
        "max_history_length": max_history_length,
        "stage_counts": dict(stage_counts),
        "status_counts": dict(status_counts),
        "repair_stage_counts": dict(repair_counts),
        "stage_status_counts": {stage: dict(counter) for stage, counter in sorted(stage_status_counts.items())},
        "family_modified_repairs": {family: dict(counter) for family, counter in sorted(family_modified_repairs.items())},
    }


def build_latent_risk_report(
    rows: list[dict[str, Any]],
    contract_preflight_report: dict[str, Any],
    explicit_controller_report: dict[str, Any] | None,
) -> dict[str, Any]:
    phase_count_histogram = Counter(len(row_phase_examples(row)) for row in rows)
    unique_patterns = {
        (str(row.get("task_family", "")), str(row.get("interaction_mode", "")), tuple(phase.get("task_family", "") for phase in row_phase_examples(row)))
        for row in rows
    }
    empty_phase_verifier_count = sum(
        1
        for row in rows
        for phase in row_phase_examples(row)
        if not phase.get("task_accomplish_verifier", {}).get("final_answer_checks", {}).get("required_fields", [])
    )
    acceptable_set_histogram = Counter(len(row.get("acceptable_tool_call_sets", [])) for row in rows)
    clarification_rows = sum(1 for row in rows if len(row.get("required_clarification_slots", [])) == 1)
    no_clarification_rows = sum(1 for row in rows if not row.get("required_clarification_slots"))

    risks: list[dict[str, Any]] = []
    if empty_phase_verifier_count:
        risks.append(
            {
                "id": "phase_verifier_under_specification",
                "severity": "high",
                "summary": "Some phase examples still have empty phase-level final_answer_checks.",
                "evidence": {"empty_phase_verifier_signature_count": empty_phase_verifier_count},
                "why_it_matters": "Phase-aware scoring can remain under-constrained if a phase does not declare answer-text facts to verify.",
            }
        )
    if len(phase_count_histogram) == 1 and list(phase_count_histogram.keys()) == [4]:
        risks.append(
            {
                "id": "uniform_four_phase_template",
                "severity": "medium",
                "summary": "Every row still uses the same four-phase topology.",
                "evidence": {
                    "phase_count_histogram": {str(key): value for key, value in phase_count_histogram.items()},
                    "unique_family_mode_phase_patterns": len(unique_patterns),
                },
                "why_it_matters": "A fully uniform topology can look like mechanical hardening rather than audit-driven composition.",
            }
        )
    if explicit_controller_report is not None:
        accomplished_count = int(explicit_controller_report.get("totals", {}).get("accomplished_count", 0))
        top_issues = [issue for issue, _count in explicit_controller_report.get("global_top_issues", [])[:10]]
        protocol_dominant_markers = (
            "missing_phase_answer:",
            "missing_goal_revision_answer",
            "call_sequence_mismatch",
            "missing_evidence_followup_answer",
        )
        if any(
            issue.startswith("missing_phase_answer:") or issue in protocol_dominant_markers
            for issue in top_issues
        ):
            risks.append(
                {
                    "id": "controller_protocol_dominant_failure_profile",
                    "severity": "medium",
                    "summary": "Explicit-controller error patterns are still heavily protocol- and trace-dominant.",
                    "evidence": {"accomplished_count": accomplished_count, "top_issues": top_issues},
                    "why_it_matters": "Even when controllers reach many rows, the remaining failures can still concentrate on trace choreography rather than purely semantic contradiction.",
                }
            )
    if clarification_rows > no_clarification_rows * 2:
        risks.append(
            {
                "id": "clarification_heavy_distribution",
                "severity": "medium",
                "summary": "Clarification-required rows still dominate the corpus.",
                "evidence": {
                    "rows_with_one_required_clarification_slot": clarification_rows,
                    "rows_with_zero_required_clarification_slots": no_clarification_rows,
                },
                "why_it_matters": "The benchmark remains focused on clarification-heavy agentic behavior rather than broad free-form exploration.",
            }
        )
    if sum(count for set_count, count in acceptable_set_histogram.items() if set_count <= 2) > 0:
        risks.append(
            {
                "id": "single_or_few_acceptable_paths_for_subset",
                "severity": "medium",
                "summary": "A subset of rows still has one or two acceptable tool-call variants only.",
                "evidence": {
                    "acceptable_tool_call_set_count_histogram": {str(key): value for key, value in acceptable_set_histogram.items()}
                },
                "why_it_matters": "Process scoring can still be brittle for semantically harmless tool-order deviations on those rows.",
            }
        )
    return {
        "report_version": "latent-risk-v3",
        "artifact_version": CANONICAL_VERSION,
        "lifting_version": LIFT_VERSION,
        "critical_contract_issues": {
            "count": int(contract_preflight_report.get("issue_count", 0)),
            "source_report": "contract_preflight_report.json",
        },
        "latent_risks": risks,
    }


def write_core_artifact(
    source_out_dir: Path,
    core_out_dir: Path,
    augmented_splits: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    corpus_name: str,
) -> None:
    if core_out_dir.exists():
        shutil.rmtree(core_out_dir)
    shutil.copytree(source_out_dir, core_out_dir)

    core_splits = {
        split: [row for row in rows if row.get("release_filter", {}).get("main_release_eligible")]
        for split, rows in augmented_splits.items()
    }
    for split, rows in core_splits.items():
        write_jsonl(core_out_dir / f"{split}.jsonl", rows)

    core_manifest = clone(manifest)
    core_manifest["track"] = f"{corpus_name}_e2e_agentic_canonical_core"
    core_manifest["artifact_version"] = f"{CANONICAL_VERSION}-core"
    core_manifest["canonical_core_release"] = True
    core_manifest["source_extended_release_dir"] = str(source_out_dir)
    core_manifest["splits"] = {split: len(rows) for split, rows in core_splits.items()}
    core_manifest["split_summaries"] = {split: summarize(rows) for split, rows in core_splits.items()}
    core_manifest["task_families"] = dict(Counter(str(row.get("task_family", "")) for rows in core_splits.values() for row in rows))
    interaction_summary_payload = split_interaction_summary([row for rows in core_splits.values() for row in rows])
    core_manifest["interaction_modes"] = interaction_summary_payload["interaction_modes"]
    core_manifest["family_interaction_modes"] = interaction_summary_payload["family_interaction_modes"]
    core_manifest["core_release_policy"] = {
        "enabled": True,
        "criterion": "rows must be blocked by at least one stronger bounded template solver over single-stream revision, pairwise revision, ranking revision, timestamp fallback policy, or quality-gate decision",
    }
    core_manifest["e2e_audit"] = str(core_out_dir / "e2e_audit.json")
    core_manifest["core_release_report"] = str(core_out_dir / "core_release_report.json")
    core_family_counts = Counter(str(row.get("task_family", "")) for rows in core_splits.values() for row in rows)
    core_reason_counts = Counter(
        reason
        for rows in core_splits.values()
        for row in rows
        for reason in row.get("release_filter", {}).get("main_release_reasons", [])
    )
    core_report = {
        "source_extended_release_dir": str(source_out_dir),
        "input_counts": {split: len(rows) for split, rows in augmented_splits.items()},
        "output_counts": {split: len(rows) for split, rows in core_splits.items()},
        "reason_counts": dict(core_reason_counts),
        "family_counts": dict(core_family_counts),
        "core_splits": {split: core_release_summary(rows) for split, rows in core_splits.items()},
    }
    write_json(core_out_dir / "manifest.json", core_manifest)
    write_json(core_out_dir / "core_release_report.json", core_report)
    write_json(core_out_dir / "e2e_audit.json", audit_bts_e2e(core_out_dir))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-dir", type=Path, default=STATIC_DEFAULT)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--tool-store-db", type=Path, default=TOOL_STORE_DB)
    parser.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--core-out-dir", type=Path, default=CORE_OUT_DEFAULT)
    parser.add_argument("--uniform-reference", type=Path, default=None)
    parser.add_argument("--corpus-name", type=str, default="bts")
    return parser.parse_args()


def build_canonical_agentic_final(
    *,
    static_dir: Path,
    source_dir: Path,
    tool_store_db: Path,
    out_dir: Path,
    core_out_dir: Path,
    corpus_name: str = "bts",
    uniform_reference: Path | None = None,
    use_default_uniform_reference: bool = True,
) -> dict[str, Any]:
    STREAM_HISTORY_CACHE.clear()
    STREAM_FIRST_TIMESTAMP_CACHE.clear()
    TIMESTAMP_POLICY_CANDIDATE_CACHE.clear()
    QUALITY_PHASE_CANDIDATE_CACHE.clear()
    if uniform_reference is None and use_default_uniform_reference and corpus_name == "bts" and UNIFORM_REFERENCE.exists():
        uniform_reference = UNIFORM_REFERENCE
    if uniform_reference is not None and not uniform_reference.exists():
        uniform_reference = None

    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(source_dir, out_dir)

    static_splits = {split: load_jsonl(static_dir / f"{split}.jsonl") for split in ("train", "dev", "test")}
    source_splits = {split: load_jsonl(source_dir / f"{split}.jsonl") for split in ("train", "dev", "test")}
    runtime = ToolStoreRuntime(tool_store_db)
    try:
        prepared_rows: list[dict[str, Any]] = []
        for split in ("train", "dev", "test"):
            static_by_id = {row["scenario_id"]: row for row in static_splits[split]}
            prepared_rows.extend(
                prepare_agentic_row(static_by_id[row.get("backing_static_scenario_id") or row["scenario_id"]], row, runtime)[0]
                for row in source_splits[split]
            )
        collision_index = build_collision_index(prepared_rows)
        dsl_search_result = bounded_dsl_search(prepared_rows)

        augmented_splits: dict[str, list[dict[str, Any]]] = {}
        for split in ("train", "dev", "test"):
            augmented = augment_rows(static_splits[split], source_splits[split], runtime, collision_index, dsl_search_result)
            augmented_splits[split] = augmented
            write_jsonl(out_dir / f"{split}.jsonl", augmented)
    finally:
        runtime.close()

    all_rows = [row for rows in augmented_splits.values() for row in rows]
    canonical_report = {
        "canonical_source_dir": str(source_dir),
        "static_source_dir": str(static_dir),
        "solver_audit_reference_dir": str(uniform_reference) if uniform_reference is not None else None,
        "selection_rationale": [
            "preserve natural operator-facing prompt surfaces",
            "preserve deterministic user simulator and deterministic evaluator",
            "preserve heldout test structure",
            "turn timestamp resolution into an actual policy-choice surface rather than an explicit family declaration",
            "attach short deterministic goal revision to executable mean families to force state carryover",
            "use predicate-driven variable phase topology instead of a fixed four-phase suffix template",
            "attach row-level deterministic agentic lifting certificates instead of relying on informal construction notes",
            "apply stronger bounded template-solver search and use bounded-template hardness as a core-release filter",
        ],
        "split_counts": {split: len(rows) for split, rows in augmented_splits.items()},
        "task_family_counts": dict(Counter(str(row.get("task_family", "")) for row in all_rows)),
        "policy_choice_contract_rows": sum(1 for row in all_rows if row.get("policy_choice_contract")),
        "goal_revision_row_count": sum(1 for row in all_rows if row.get("goal_revision_turns")),
        "clarification_required_row_count": sum(1 for row in all_rows if row.get("required_clarification_slots")),
        "splits": {split: summarize(rows) for split, rows in augmented_splits.items()},
        "quality_gate_test": quality_gate_summary(augmented_splits["test"]),
    }
    lifting_report = lifting_summary(all_rows, collision_index)
    history_report = generation_history_summary(all_rows)
    collision_report = {
        "collision_group_count": len(collision_index),
        "groups": collision_index,
    }
    dsl_search_report = {
        "dsl_version": "capability-subset-dsl-v2",
        "interpreter_order": DSL_ATOMS,
        "search_bound": {name: spec["max_atoms"] for name, spec in DSL_SOLVER_CLASSES.items()},
        "solver_classes": dsl_search_result["solver_classes"],
        "requirement_groups": dsl_search_result["requirement_groups"],
    }

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["splits"] = {split: len(rows) for split, rows in augmented_splits.items()}
    manifest["split_summaries"] = {split: summarize(rows) for split, rows in augmented_splits.items()}
    manifest["task_families"] = dict(Counter(str(row.get("task_family", "")) for row in all_rows))
    interaction_summary_payload = split_interaction_summary(all_rows)
    manifest["interaction_modes"] = interaction_summary_payload["interaction_modes"]
    manifest["family_interaction_modes"] = interaction_summary_payload["family_interaction_modes"]
    manifest["artifact_version"] = CANONICAL_VERSION
    manifest["track"] = f"{corpus_name}_e2e_agentic_canonical"
    manifest["canonical_final"] = True
    manifest["canonical_final_version"] = CANONICAL_VERSION
    manifest["lifting_version"] = LIFT_VERSION
    manifest["derived_from_agentic_source"] = str(source_dir)
    manifest["source_static_dir"] = str(static_dir)
    manifest["solver_audit_reference_dir"] = str(uniform_reference) if uniform_reference is not None else None
    manifest["artifact_lineage"] = {
        "static_seed_dir": str(static_dir),
        "agentic_source_dir": str(source_dir),
        "solver_audit_uniform_reference_dir": str(uniform_reference) if uniform_reference is not None else None,
        "canonical_builder": str(Path(__file__).resolve()),
        "canonical_version": CANONICAL_VERSION,
        "lifting_version": LIFT_VERSION,
        "corpus_name": corpus_name,
    }
    manifest["deterministic_agentic_lifting"] = {
        "enabled": True,
        "version": LIFT_VERSION,
        "declared_solver_classes": DECLARED_SOLVER_CLASSES,
        "guarantee_scope": "difficulty or impossibility is only claimed for the declared rule-solver classes above",
        "row_level_generation_history": True,
        "interaction_necessity_axes": [
            "clarification_necessity",
            "policy_branch_necessity",
            "state_carryover_necessity",
            "goal_revision_necessity",
            "evidence_gated_commitment",
            "rationale_gated_commitment",
        ],
    }
    manifest["canonical_release_policy"] = {
        "natural_prompt_surfaces": True,
        "deterministic_user_simulator": True,
        "deterministic_programmatic_evaluator": True,
        "heldout_test_policy": "preserve_existing_heldout_policy",
        "coded_target_references_in_released_product": False,
        "indexed_candidate_references_in_released_product": False,
        "solver_audit_uniform_contract_used_as_main_release": False if uniform_reference is not None else None,
        "row_level_agentic_lifting_certificates": True,
        "timestamp_policy_choice_enabled": True,
        "predicate_driven_variable_phase_topology": True,
        "goal_revision_enabled_for_mean_families": True,
        "core_release_filter_enabled": True,
    }
    manifest["canonical_report"] = str(out_dir / "canonical_report.json")
    manifest["agentic_lifting_report"] = str(out_dir / "agentic_lifting_report.json")
    manifest["generation_history_report"] = str(out_dir / "generation_history_report.json")
    manifest["collision_report"] = str(out_dir / "public_view_collision_report.json")
    manifest["bounded_dsl_search_report"] = str(out_dir / "bounded_dsl_search_report.json")
    manifest["e2e_audit"] = str(out_dir / "e2e_audit.json")
    manifest["deterministic_agentic_lifting"]["bounded_dsl_search"] = {
        "enabled": True,
        "dsl_version": "capability-subset-dsl-v2",
        "program_form": "capability subset interpreted by a fixed deterministic execution order",
        "solver_class_bounds": {name: spec["max_atoms"] for name, spec in DSL_SOLVER_CLASSES.items()},
    }

    write_json(manifest_path, manifest)
    write_json(out_dir / "canonical_report.json", canonical_report)
    write_json(out_dir / "agentic_lifting_report.json", lifting_report)
    write_json(out_dir / "generation_history_report.json", history_report)
    write_json(out_dir / "public_view_collision_report.json", collision_report)
    write_json(out_dir / "bounded_dsl_search_report.json", dsl_search_report)
    write_json(out_dir / "e2e_audit.json", audit_bts_e2e(out_dir))
    write_core_artifact(out_dir, core_out_dir, augmented_splits, manifest, corpus_name)
    return manifest


def main() -> None:
    args = parse_args()
    manifest = build_canonical_agentic_final(
        static_dir=args.static_dir,
        source_dir=args.source_dir,
        tool_store_db=args.tool_store_db,
        out_dir=args.out_dir,
        core_out_dir=args.core_out_dir,
        corpus_name=args.corpus_name,
        uniform_reference=args.uniform_reference,
        use_default_uniform_reference=True,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
