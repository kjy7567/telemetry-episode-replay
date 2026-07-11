#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("test", "dev", "train")


def load_rows(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in SPLITS:
        with (directory / f"{split}.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                row["_source_split"] = split
                rows.append(row)
    return rows


def select_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[str(row["task_family"])].append(row)

    selected: list[dict[str, Any]] = []
    for family in sorted(by_family):
        candidates = sorted(
            by_family[family],
            key=lambda row: (SPLITS.index(str(row["_source_split"])), str(row["scenario_id"])),
        )
        first = candidates[0]
        second = next(
            (
                row
                for row in candidates[1:]
                if row["_source_split"] != first["_source_split"]
                and row.get("site_id") != first.get("site_id")
            ),
            candidates[1],
        )
        selected.extend((first, second))
    return selected


def tool_context(row: dict[str, Any]) -> dict[str, Any]:
    calls = row.get("canonical_tool_calls", [])
    points: list[dict[str, Any]] = []
    operation: dict[str, Any] = {}
    for call in calls:
        arguments = call.get("arguments", {})
        if call.get("tool_name") == "resolve_point":
            points.append(
                {
                    key: arguments.get(key)
                    for key in ("point_class", "equipment_label", "location_label")
                    if arguments.get(key) is not None
                }
            )
        elif call.get("tool_name") == "list_points":
            points.append(
                {
                    key: arguments.get(key)
                    for key in ("point_class", "location_type")
                    if arguments.get(key) is not None
                }
            )
        else:
            operation = {
                "operation": call.get("tool_name"),
                **{
                    key: value
                    for key, value in arguments.items()
                    if key not in {"stream_id", "stream_ids", "left_stream_id", "right_stream_id"}
                },
            }
    return {
        "site_id": row.get("site_id"),
        "task_family": row.get("task_family"),
        "points": points,
        "operation": operation,
    }


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def humanize(value: str) -> str:
    return value.replace("_", " ")


def render_blind_form(rows: list[dict[str, Any]], group: str) -> str:
    lines = [
        f"# Blind Authoring Form: Group {group}",
        "",
        "Complete this form before opening the canonical review form. Write NONE for a turn that would not occur.",
        "",
    ]
    for row in rows:
        if row["assignment_group"] != group:
            continue
        context = json.loads(row["structured_context_json"])
        operation = context.get("operation", {})
        lines.extend(
            [
                f"## {row['card_id']}: {humanize(row['task_family']).title()}",
                "",
                f"- Site: `{context.get('site_id')}`",
                f"- Point context: `{json.dumps(context.get('points', []), ensure_ascii=False)}`",
                f"- Requested operation: `{json.dumps(operation, ensure_ascii=False)}`",
                "",
                "Initial request:",
                "",
                "Clarification reply, if any:",
                "",
                "Realistic goal revision, if any:",
                "",
                "Quality-decision request, if any:",
                "",
                "Evidence request, if any:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_review_form(rows: list[dict[str, Any]], group_by_card: dict[str, str], group: str) -> str:
    lines = [
        f"# Canonical Review Form: Group {group}",
        "",
        "Open this form only after completing the blind authoring form.",
        "",
    ]
    for row in rows:
        if group_by_card[row["card_id"]] != group:
            continue
        lines.extend(
            [
                f"## {row['card_id']}: {humanize(row['task_family']).title()}",
                "",
                f"**Canonical initial request:** {row['canonical_initial_request']}",
                "",
                f"**Required clarifications:** `{row['canonical_required_clarifications_json']}`",
                "",
                f"**Clarification answers:** `{row['canonical_clarification_answers_json']}`",
                "",
                f"**Goal revisions:** `{row['canonical_goal_revisions_json']}`",
                "",
                f"**Evidence follow-up:** {row['canonical_evidence_followup']}",
                "",
                "Enter ratings and comments for this card in `03_responses.csv`.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a blind, family-balanced domain-practitioner validation packet."
    )
    parser.add_argument(
        "--static-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-static-seed"
    )
    parser.add_argument(
        "--final-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-canonical-final"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "human_validation" / "packet"
    )
    args = parser.parse_args()

    static_rows = load_rows(args.static_dir)
    final_by_id = {row["scenario_id"]: row for row in load_rows(args.final_dir)}
    selected = select_rows(static_rows)

    blind_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    response_rows: list[dict[str, Any]] = []
    family_slots: dict[str, int] = defaultdict(int)
    for index, static in enumerate(selected, start=1):
        family = str(static["task_family"])
        family_slots[family] += 1
        slot = family_slots[family]
        card_id = f"HV-{index:02d}"
        final = final_by_id[str(static["scenario_id"])]
        blind_rows.append(
            {
                "card_id": card_id,
                "assignment_group": "A" if slot == 1 else "B",
                "task_family": family,
                "structured_context_json": json.dumps(tool_context(static), ensure_ascii=False),
                "authoring_instruction": (
                    "Write the initial request and follow-ups as you would communicate them during "
                    "real building-operations work. Use NONE for a turn that would not occur."
                ),
            }
        )
        review_rows.append(
            {
                "card_id": card_id,
                "scenario_id": static["scenario_id"],
                "split": static["_source_split"],
                "task_family": family,
                "canonical_initial_request": final.get("initial_user_message", ""),
                "canonical_required_clarifications_json": json.dumps(
                    final.get("required_clarification_slots", []), ensure_ascii=False
                ),
                "canonical_clarification_answers_json": json.dumps(
                    final.get("clarification_answers", {}), ensure_ascii=False
                ),
                "canonical_goal_revisions_json": json.dumps(
                    final.get("goal_revision_turns", []), ensure_ascii=False
                ),
                "canonical_evidence_followup": final.get("followup_prompt", ""),
                "canonical_final_action_json": json.dumps(
                    final.get("gold_final_answer", {}), ensure_ascii=False
                ),
            }
        )
        response_rows.append(
            {
                "participant_id": "",
                "card_id": card_id,
                "domain_role": "",
                "years_experience": "",
                "human_initial_request": "",
                "human_clarification_reply_or_none": "",
                "human_goal_revision_or_none": "",
                "human_quality_decision_request_or_none": "",
                "human_evidence_request_or_none": "",
                "benchmark_naturalness_1_5": "",
                "benchmark_obligation_match": "",
                "clarification_realistic": "",
                "goal_revision_realistic": "",
                "quality_decision_realistic": "",
                "evidence_followup_realistic": "",
                "meaning_preserved": "",
                "would_use_in_real_workflow": "",
                "major_issue_code": "",
                "notes": "",
                "consent_to_participate": "",
                "consent_to_quote": "",
            }
        )

    write_csv(
        args.output_dir / "01_blind_authoring_cards.csv",
        list(blind_rows[0]),
        blind_rows,
    )
    write_csv(
        args.output_dir / "02_canonical_review_cards.csv",
        list(review_rows[0]),
        review_rows,
    )
    write_csv(
        args.output_dir / "03_responses.csv",
        list(response_rows[0]),
        response_rows,
    )
    group_by_card = {row["card_id"]: row["assignment_group"] for row in blind_rows}
    for group in ("A", "B"):
        (args.output_dir / f"01_group_{group}_blind_form.md").write_text(
            render_blind_form(blind_rows, group), encoding="utf-8"
        )
        (args.output_dir / f"02_group_{group}_canonical_review.md").write_text(
            render_review_form(review_rows, group_by_card, group), encoding="utf-8"
        )
    manifest = {
        "packet_version": "domain-validation-v2",
        "selection": "two deterministic cards per family: test first, then a different split/site",
        "card_count": len(selected),
        "family_counts": dict(sorted(family_slots.items())),
        "blind_before_review": True,
        "affirmative_participation_consent_required": True,
        "response_rows_are_blank": True,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
