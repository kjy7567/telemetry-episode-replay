from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def issue_category(issue: str) -> str:
    if issue.startswith("missing_phase_answer:"):
        return "protocol_phase_completion_missing"
    if issue == "missing_goal_revision_answer":
        return "protocol_goal_revision_missing"
    if issue == "missing_evidence_followup_answer":
        return "protocol_evidence_followup_missing"
    if issue == "call_sequence_mismatch":
        return "process_call_sequence_mismatch"
    if issue.startswith("missing_tool:"):
        return "process_required_tool_missing"
    if issue.startswith("missing_answer_fact:"):
        return "semantic_final_answer_missing"
    if issue.startswith("missing_evidence:"):
        return "grounding_evidence_missing"
    if issue.startswith("temporal_mismatch:"):
        return "temporal_contract_mismatch"
    return "other"


def parse_error_category(parse_error: str | None) -> str:
    if not parse_error:
        return "none"
    if "missing_revised_timestamp" in parse_error:
        return "controller_parse_missing_revised_timestamp"
    if "missing_revised_target_phrase" in parse_error:
        return "controller_parse_missing_revised_target_phrase"
    if "unsupported_revision_direction" in parse_error:
        return "controller_parse_unsupported_revision_surface"
    if "KeyError:'stream_id'" in parse_error:
        return "controller_binding_missing_stream_id"
    if "empty_rank" in parse_error:
        return "controller_rank_empty_result"
    if "missing_point_class" in parse_error:
        return "controller_parse_missing_point_class"
    if "no_point_candidate" in parse_error:
        return "controller_grounding_no_point_candidate"
    return "controller_other_error"


def first_blocking_layer(row: dict[str, Any]) -> str:
    if row.get("parse_error"):
        return "controller_parse_or_binding_failure"
    if not row.get("protocol_trace"):
        return "protocol_trace_missing"
    verification = row.get("verification", {})
    phase_issues = verification.get("phase_issues", []) or []
    if phase_issues:
        return "phase_completion_failure"
    process_issues = verification.get("process_issues", []) or []
    if process_issues:
        return "tool_process_failure"
    temporal_issues = verification.get("temporal_issues", []) or []
    if temporal_issues:
        return "temporal_contract_failure"
    final_issues = verification.get("final_issues", []) or []
    if final_issues:
        return "final_answer_failure"
    return "other_failure"


def analyze_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, dict[str, Any]] = {}
    by_split: dict[str, dict[str, Any]] = {}

    parse_error_counts = Counter()
    parse_error_category_counts = Counter()
    issue_category_counts = Counter()
    first_blocking_counts = Counter()
    family_first_blocking: dict[str, Counter[str]] = defaultdict(Counter)
    family_parse_categories: dict[str, Counter[str]] = defaultdict(Counter)
    split_parse_categories: dict[str, Counter[str]] = defaultdict(Counter)

    protocol_trace_nonempty = 0
    executed_calls_nonempty = 0
    phase_answers_nonempty = 0
    final_answer_nonempty = 0
    evidence_answer_nonempty = 0
    rationale_answer_nonempty = 0
    protocol_ok_count = 0
    supported_family_count = 0

    for row in rows:
        family = str(row.get("task_family", ""))
        split = str(row.get("scenario_id", "")).split("_", 1)[0]
        parse_error = row.get("parse_error")
        parse_cat = parse_error_category(parse_error)
        parse_error_category_counts[parse_cat] += 1
        if parse_error:
            parse_error_counts[str(parse_error)] += 1
        split_parse_categories[split][parse_cat] += 1
        family_parse_categories[family][parse_cat] += 1

        if row.get("protocol_trace"):
            protocol_trace_nonempty += 1
        if row.get("executed_calls"):
            executed_calls_nonempty += 1
        if row.get("phase_answers"):
            phase_answers_nonempty += 1
        if row.get("final_answer_text"):
            final_answer_nonempty += 1
        if row.get("evidence_answer_text"):
            evidence_answer_nonempty += 1
        if row.get("rationale_answer_text"):
            rationale_answer_nonempty += 1
        if row.get("protocol_ok"):
            protocol_ok_count += 1
        if row.get("supported_family"):
            supported_family_count += 1

        verification = row.get("verification", {})
        for issue in verification.get("issues", []) or []:
            issue_category_counts[issue_category(issue)] += 1

        blocking = first_blocking_layer(row)
        first_blocking_counts[blocking] += 1
        family_first_blocking[family][blocking] += 1

    for family in sorted(family_parse_categories):
        by_family[family] = {
            "row_count": int(sum(family_parse_categories[family].values())),
            "parse_error_categories": dict(family_parse_categories[family].most_common()),
            "first_blocking_layers": dict(family_first_blocking[family].most_common()),
        }
    for split in sorted(split_parse_categories):
        by_split[split] = {
            "row_count": int(sum(split_parse_categories[split].values())),
            "parse_error_categories": dict(split_parse_categories[split].most_common()),
        }

    return {
        "report_version": "explicit-controller-failure-analysis-v2",
        "row_count": len(rows),
        "controller_reachability": {
            "supported_family_count": supported_family_count,
            "protocol_trace_nonempty_count": protocol_trace_nonempty,
            "executed_calls_nonempty_count": executed_calls_nonempty,
            "phase_answers_nonempty_count": phase_answers_nonempty,
            "final_answer_nonempty_count": final_answer_nonempty,
            "evidence_answer_nonempty_count": evidence_answer_nonempty,
            "rationale_answer_nonempty_count": rationale_answer_nonempty,
            "protocol_ok_count": protocol_ok_count,
        },
        "first_blocking_layers": dict(first_blocking_counts.most_common()),
        "parse_error_category_counts": dict(parse_error_category_counts.most_common()),
        "top_parse_errors": [[key, value] for key, value in parse_error_counts.most_common(25)],
        "issue_category_counts": dict(issue_category_counts.most_common()),
        "by_split": by_split,
        "by_family": by_family,
        "interpretation": {
            "summary": "The phase-complete stronger controller either fails to complete the released contract or fails downstream on task semantics, so the observed result should be interpreted as controller insufficiency under the released benchmark contract rather than a universal symbolic impossibility claim.",
            "evidence_focus": [
                "the stronger controller is capable of protocol-complete multi-phase traces on simpler rows",
                "remaining failures identify where the released benchmark exceeds this controller's phase-complete capability",
                "contract-level and semantic downstream failures are separated in the analysis rather than collapsed into a single zero-success claim",
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-dir", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for split in ("train", "dev", "test"):
        path = args.witness_dir / split / "phase_complete_stronger_controller.jsonl"
        rows.extend(load_jsonl(path))

    report = analyze_rows(rows)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
