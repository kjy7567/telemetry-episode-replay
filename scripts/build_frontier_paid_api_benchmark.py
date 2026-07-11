from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_DEFAULT = REPO_ROOT / "artifacts" / "bts-agentic-source"
OUT_DEFAULT = REPO_ROOT / "artifacts" / "bts-frontier-paid-api-benchmark"
UNIFORM_REFERENCE = REPO_ROOT / "artifacts" / "bts-agentic-uniform-reference"

TARGET_SLOT = "target_reference"
COMPARISON_SLOT = "comparison_targets"
RANKING_SLOT = "ranking_scope"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_coded_answer(text: str) -> bool:
    lowered = text.lower()
    return "candidate #" in lowered or "point-class index #" in lowered


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    slot_cardinality = Counter(len(row.get("required_clarification_slots", [])) for row in rows)
    interaction_modes = Counter(str(row.get("interaction_mode", "")) for row in rows)
    task_families = Counter(str(row.get("task_family", "")) for row in rows)
    canonical_tool_lengths = Counter(len(row.get("canonical_tool_calls", [])) for row in rows)
    post_answer_lengths = Counter(len(row.get("post_answer_user_turns", [])) for row in rows)

    coded_rows = 0
    target_slot_rows = 0
    comparison_slot_rows = 0
    ranking_slot_rows = 0
    one_of_one_rows = 0

    for row in rows:
        slots = list(row.get("required_clarification_slots", []))
        if TARGET_SLOT in slots:
            target_slot_rows += 1
        if COMPARISON_SLOT in slots:
            comparison_slot_rows += 1
        if RANKING_SLOT in slots:
            ranking_slot_rows += 1
        answers = row.get("clarification_answers", {})
        if any(is_coded_answer(str(value)) for value in answers.values()):
            coded_rows += 1
        if any("candidate #1 of 1" in str(value).lower() or "point-class index #1 of 1" in str(value).lower() for value in answers.values()):
            one_of_one_rows += 1

    return {
        "count": len(rows),
        "interaction_modes": dict(interaction_modes),
        "task_families": dict(task_families),
        "required_clarification_slot_cardinality": {str(key): value for key, value in slot_cardinality.items()},
        "canonical_tool_call_lengths": {str(key): value for key, value in canonical_tool_lengths.items()},
        "post_answer_user_turn_lengths": {str(key): value for key, value in post_answer_lengths.items()},
        "coded_clarification_rows": coded_rows,
        "target_reference_slot_rows": target_slot_rows,
        "comparison_targets_slot_rows": comparison_slot_rows,
        "ranking_scope_slot_rows": ranking_slot_rows,
        "candidate_one_of_one_rows": one_of_one_rows,
    }


def quality_gate_test_summary(test_rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality_rows = [row for row in test_rows if row.get("task_family") == "quality_gate"]
    site_counts = Counter(str(row.get("site_id", "")) for row in quality_rows)
    decision_counts = Counter(str(row.get("gold_final_answer", {}).get("decision", "")) for row in quality_rows)
    return {
        "count": len(quality_rows),
        "site_counts": dict(site_counts),
        "decision_counts": dict(decision_counts),
    }


def main() -> None:
    source_dir = SOURCE_DEFAULT
    out_dir = OUT_DEFAULT

    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(source_dir, out_dir)

    split_rows = {
        split: load_jsonl(out_dir / f"{split}.jsonl")
        for split in ("train", "dev", "test")
    }

    report = {
        "source_dir": str(source_dir),
        "uniform_reference_dir": str(UNIFORM_REFERENCE),
        "benchmark_intent": "frontier_paid_api_zero_shot_or_light_prompting",
        "design_policy": {
            "visible_prompt_style": "natural_operator_ticket_or_handoff",
            "clarification_policy": "recoverable_site_or_time_slot_only",
            "coded_target_references_allowed": False,
            "indexed_candidate_references_allowed": False,
            "explicit_target_reference_slot_allowed": False,
            "explicit_comparison_targets_slot_allowed": False,
            "explicit_ranking_scope_slot_allowed": False,
            "heldout_site_policy": "preserve_BTS_C_test_holdout",
            "nearest_policy": "explicit_or_implicit_nearest surface semantics already encoded in source agentic benchmark",
        },
        "splits": {split: summarize(rows) for split, rows in split_rows.items()},
        "quality_gate_test": quality_gate_test_summary(split_rows["test"]),
    }

    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["track"] = "bts_e2e_agentic_frontier"
    manifest["frontier_paid_api_line"] = True
    manifest["frontier_line_version"] = "frontier-v1"
    manifest["derived_from_agentic_source"] = str(source_dir)
    manifest["contrast_with_solver_audited_uniform_artifact"] = str(UNIFORM_REFERENCE)
    manifest["recommended_use"] = (
        "Use this line for frontier paid API evaluations where the goal is strong zero-shot or light-prompting "
        "performance under deterministic evaluation without benchmark-native coded clarifications."
    )
    manifest["frontier_design_policy"] = report["design_policy"]
    manifest["frontier_report"] = str(out_dir / "frontier_report.json")
    manifest["frontier_risks_removed"] = [
        "coded target clarifications",
        "indexed candidate clarifications",
        "candidate #1 of 1 clarifications",
        "within-family uniform solver-hardening dependence",
    ]
    write_json(manifest_path, manifest)
    write_json(out_dir / "frontier_report.json", report)


if __name__ == "__main__":
    main()
