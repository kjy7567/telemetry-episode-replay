#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = (
    "participant_id",
    "card_id",
    "domain_role",
    "years_experience",
    "human_initial_request",
    "human_clarification_reply_or_none",
    "human_goal_revision_or_none",
    "human_quality_decision_request_or_none",
    "human_evidence_request_or_none",
    "benchmark_naturalness_1_5",
    "benchmark_obligation_match",
    "clarification_realistic",
    "goal_revision_realistic",
    "quality_decision_realistic",
    "evidence_followup_realistic",
    "meaning_preserved",
    "would_use_in_real_workflow",
    "major_issue_code",
    "consent_to_participate",
    "consent_to_quote",
)


def wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)]


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    naturalness = [int(row["benchmark_naturalness_1_5"]) for row in rows]
    match = sum(row["benchmark_obligation_match"] == "match" for row in rows)
    preserved = sum(row["meaning_preserved"] == "yes" for row in rows)
    usable = sum(row["would_use_in_real_workflow"] in {"yes", "with_edits"} for row in rows)
    return {
        "n": len(rows),
        "naturalness_mean": round(sum(naturalness) / len(naturalness), 3),
        "obligation_match": {"count": match, "rate": round(match / len(rows), 4), "ci95": wilson(match, len(rows))},
        "meaning_preserved": {"count": preserved, "rate": round(preserved / len(rows), 4), "ci95": wilson(preserved, len(rows))},
        "workflow_usable_with_or_without_edits": {
            "count": usable,
            "rate": round(usable / len(rows), 4),
            "ci95": wilson(usable, len(rows)),
        },
        "issue_codes": dict(sorted(Counter(row["major_issue_code"] for row in rows).items())),
    }


def rate_cell(metric: dict[str, Any]) -> str:
    low, high = metric["ci95"]
    return f'{metric["count"]} ({100 * metric["rate"]:.1f}%; 95% CI {100 * low:.1f}-{100 * high:.1f})'


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Domain-Practitioner Validation Results",
        "",
        (
            f'Two-stage blind authoring and canonical review produced {report["response_count"]} responses '
            f'from {report["participant_count"]} eligible building-telemetry practitioners. '
            "Participants authored requests before seeing benchmark wording."
        ),
        "",
        "## Participants",
        "",
        "| Participant | Role category | Experience | Group | Responses |",
        "|---|---|---:|:---:|---:|",
    ]
    for participant in report["participants"]:
        lines.append(
            f'| {participant["participant_id"]} | {participant["domain_role"]} | '
            f'{participant["years_experience"]} years | {participant["assignment_group"]} | '
            f'{participant["response_count"]} |'
        )

    overall = report["overall"]
    lines.extend(
        [
            "",
            "## Overall",
            "",
            "| Measure | Result |",
            "|---|---:|",
            f'| Naturalness (1-5) | {overall["naturalness_mean"]:.3f} |',
            f'| Obligation match | {rate_cell(overall["obligation_match"])} |',
            f'| Meaning preserved | {rate_cell(overall["meaning_preserved"])} |',
            (
                "| Usable in workflow, with or without edits | "
                f'{rate_cell(overall["workflow_usable_with_or_without_edits"])} |'
            ),
            "",
            "## Family Results",
            "",
            "| Family | N | Naturalness | Obligation match | Meaning preserved | Workflow usable |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for family, result in report["by_family"].items():
        lines.append(
            f'| {family} | {result["n"]} | {result["naturalness_mean"]:.3f} | '
            f'{rate_cell(result["obligation_match"])} | {rate_cell(result["meaning_preserved"])} | '
            f'{rate_cell(result["workflow_usable_with_or_without_edits"])} |'
        )

    lines.extend(["", "## Consented Human-Authored Examples", ""])
    if not report["human_authored_examples"]:
        lines.append("No participant consented to quotation.")
    for example in report["human_authored_examples"]:
        lines.extend(
            [
                f'### {example["card_id"]}: {example["task_family"]}',
                "",
                f'- Human initial request: {example["human_initial_request"]}',
                f'- Human clarification reply: {example["human_clarification_reply_or_none"]}',
                f'- Human goal revision: {example["human_goal_revision_or_none"]}',
                f'- Human quality request: {example["human_quality_decision_request_or_none"]}',
                f'- Human evidence request: {example["human_evidence_request_or_none"]}',
                f'- Canonical initial request: {example["canonical_initial_request"]}',
                f'- Obligation match: {example["benchmark_obligation_match"]}',
                f'- Notes: {example["notes"] or "NONE"}',
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and summarize completed practitioner responses.")
    parser.add_argument(
        "--responses",
        type=Path,
        default=REPO_ROOT / "human_validation" / "packet" / "03_responses.csv",
    )
    parser.add_argument(
        "--review-cards",
        type=Path,
        default=REPO_ROOT / "human_validation" / "packet" / "02_canonical_review_cards.csv",
    )
    parser.add_argument(
        "--blind-cards",
        type=Path,
        default=REPO_ROOT / "human_validation" / "packet" / "01_blind_authoring_cards.csv",
    )
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "human_validation" / "results.json"
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPO_ROOT / "human_validation" / "results.md",
    )
    args = parser.parse_args()

    with args.review_cards.open(encoding="utf-8", newline="") as handle:
        cards = {row["card_id"]: row for row in csv.DictReader(handle)}
    with args.blind_cards.open(encoding="utf-8", newline="") as handle:
        groups = {row["card_id"]: row["assignment_group"] for row in csv.DictReader(handle)}
    with args.responses.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    errors: list[str] = []
    card_counts = Counter(row.get("card_id", "") for row in rows)
    for card_id, count in card_counts.items():
        if card_id and count != 1:
            errors.append(f"card {card_id}: expected one response, found {count}")
    for index, row in enumerate(rows, start=2):
        missing = [field for field in REQUIRED_FIELDS if not row.get(field, "").strip()]
        if missing:
            errors.append(f"response line {index}: missing {', '.join(missing)}")
            continue
        if row["card_id"] not in cards:
            errors.append(f"response line {index}: unknown card_id {row['card_id']}")
        if row["benchmark_naturalness_1_5"] not in {"1", "2", "3", "4", "5"}:
            errors.append(f"response line {index}: naturalness must be 1-5")
        try:
            years = float(row["years_experience"])
            if years < 2:
                errors.append(f"response line {index}: participant must have at least two years of experience")
        except ValueError:
            errors.append(f"response line {index}: years_experience must be numeric")
        allowed = {
            "benchmark_obligation_match": {"match", "partial", "mismatch"},
            "clarification_realistic": {"yes", "no", "not_applicable"},
            "goal_revision_realistic": {"yes", "no"},
            "quality_decision_realistic": {"yes", "no"},
            "evidence_followup_realistic": {"yes", "no"},
            "meaning_preserved": {"yes", "partial", "no"},
            "would_use_in_real_workflow": {"yes", "with_edits", "no"},
            "consent_to_participate": {"yes"},
            "consent_to_quote": {"yes", "no"},
            "major_issue_code": {
                "none",
                "wording",
                "missing_context",
                "unrealistic_revision",
                "unrealistic_quality_step",
                "unrealistic_evidence",
                "semantic_mismatch",
                "other",
            },
        }
        for field, values in allowed.items():
            if row[field] not in values:
                errors.append(f"response line {index}: invalid {field}={row[field]!r}")

    if errors:
        print(json.dumps({"status": "incomplete_or_invalid", "errors": errors}, indent=2))
        raise SystemExit(2)

    participant_ids = sorted({row["participant_id"] for row in rows})
    participant_groups: dict[str, set[str]] = defaultdict(set)
    participant_metadata: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        participant_groups[row["participant_id"]].add(groups[row["card_id"]])
        participant_metadata[row["participant_id"]].add((row["domain_role"], row["years_experience"]))
    mixed = {participant: sorted(values) for participant, values in participant_groups.items() if len(values) > 1}
    if mixed:
        raise SystemExit(f"Each practitioner must complete only one blind group; mixed assignments: {mixed}")
    inconsistent = {
        participant: sorted(values)
        for participant, values in participant_metadata.items()
        if len(values) > 1
    }
    if inconsistent:
        raise SystemExit(f"Participant role and experience must be consistent across responses: {inconsistent}")
    families: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        families[cards[row["card_id"]]["task_family"]].append(row)
    family_counts = {family: len(items) for family, items in sorted(families.items())}
    if len(participant_ids) < 2 or len(rows) < 18 or any(count < 2 for count in family_counts.values()):
        raise SystemExit(
            "Validation requires at least two practitioners, 18 completed responses, and two responses per family."
        )

    report = {
        "report_version": "domain-validation-results-v1",
        "participant_count": len(participant_ids),
        "response_count": len(rows),
        "participants": [
            {
                "participant_id": participant,
                "domain_role": next(iter(participant_metadata[participant]))[0],
                "years_experience": float(next(iter(participant_metadata[participant]))[1]),
                "assignment_group": next(iter(participant_groups[participant])),
                "response_count": sum(row["participant_id"] == participant for row in rows),
            }
            for participant in participant_ids
        ],
        "family_counts": family_counts,
        "overall": summarize(rows),
        "by_family": {family: summarize(items) for family, items in sorted(families.items())},
        "human_authored_examples": [
            {
                "participant_id": row["participant_id"],
                "card_id": row["card_id"],
                "task_family": cards[row["card_id"]]["task_family"],
                "human_initial_request": row["human_initial_request"],
                "human_clarification_reply_or_none": row["human_clarification_reply_or_none"],
                "human_goal_revision_or_none": row["human_goal_revision_or_none"],
                "human_quality_decision_request_or_none": row[
                    "human_quality_decision_request_or_none"
                ],
                "human_evidence_request_or_none": row["human_evidence_request_or_none"],
                "canonical_initial_request": cards[row["card_id"]]["canonical_initial_request"],
                "benchmark_obligation_match": row["benchmark_obligation_match"],
                "notes": row.get("notes", ""),
            }
            for row in rows
            if row["consent_to_quote"] == "yes"
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
