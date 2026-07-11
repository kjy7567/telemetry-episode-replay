from __future__ import annotations

from typing import Any


ITERATIVE_DECISION_CONTRACT_VERSION = "iterative-decision-v1"
ITERATIVE_DECISION_PROBLEM = "clarify_act_answer"
ITERATIVE_DECISION_TYPES = ("clarify", "act", "answer")

DECISION_TYPE_BY_STAGE = {
    "clarification_question": "clarify",
    "tool_call": "act",
    "final_answer": "answer",
    "evidence_followup_answer": "answer",
    "rationale_followup_answer": "answer",
    "followup_answer": "answer",
}

RESPONSE_FORMAT_BY_DECISION_TYPE = {
    "clarify": "natural_language_question",
    "act": "compact_json_tool_action",
    "answer": "natural_language_answer",
}


def decision_type_for_stage(stage: str) -> str:
    if stage not in DECISION_TYPE_BY_STAGE:
        raise KeyError(f"Unsupported iterative-contract stage: {stage}")
    return DECISION_TYPE_BY_STAGE[stage]


def response_format_for_stage(stage: str) -> str:
    return RESPONSE_FORMAT_BY_DECISION_TYPE[decision_type_for_stage(stage)]


def contract_fields(stage: str) -> dict[str, Any]:
    decision_type = decision_type_for_stage(stage)
    return {
        "contract_version": ITERATIVE_DECISION_CONTRACT_VERSION,
        "decision_problem": ITERATIVE_DECISION_PROBLEM,
        "decision_type": decision_type,
        "available_decision_types": list(ITERATIVE_DECISION_TYPES),
        "response_format": RESPONSE_FORMAT_BY_DECISION_TYPE[decision_type],
    }


def contract_manifest_fields(stage_counts: dict[str, int]) -> dict[str, Any]:
    decision_counts: dict[str, int] = {}
    response_counts: dict[str, int] = {}
    for stage, count in stage_counts.items():
        decision_type = decision_type_for_stage(stage)
        decision_counts[decision_type] = decision_counts.get(decision_type, 0) + count
        response_format = RESPONSE_FORMAT_BY_DECISION_TYPE[decision_type]
        response_counts[response_format] = response_counts.get(response_format, 0) + count
    return {
        "contract_version": ITERATIVE_DECISION_CONTRACT_VERSION,
        "decision_problem": ITERATIVE_DECISION_PROBLEM,
        "available_decision_types": list(ITERATIVE_DECISION_TYPES),
        "decision_type_counts_train": decision_counts,
        "response_format_counts_train": response_counts,
    }
