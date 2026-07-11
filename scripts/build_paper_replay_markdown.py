#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SPLITS = ("train", "dev", "test")
FAMILY_LABELS = {
    "point_disambiguation": "Point disambiguation",
    "day_mean_lookup": "Day mean lookup",
    "relative_24h_mean_lookup": "Relative 24h mean lookup",
    "window_mean_lookup": "Window mean lookup",
    "window_pairwise_compare": "Window pairwise compare",
    "window_rank": "Window rank",
    "timestamp_value_lookup": "Timestamp value lookup",
    "timestamp_nearest_lookup": "Timestamp nearest lookup",
    "quality_gate": "Quality gate",
}
REPRESENTATIVES = {
    "point_disambiguation": "test_point_disambiguation_00003",
    "day_mean_lookup": "test_day_mean_lookup_00003",
    "relative_24h_mean_lookup": "test_relative_24h_mean_lookup_00003",
    "window_mean_lookup": "test_window_mean_lookup_00003",
    "window_pairwise_compare": "test_window_pairwise_compare_00003",
    "window_rank": "test_window_rank_00003",
    "timestamp_value_lookup": "test_timestamp_value_lookup_00051",
    "timestamp_nearest_lookup": "test_timestamp_nearest_lookup_00051",
    "quality_gate": "test_quality_gate_00051",
}


def canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def submitted_rows(bundle: Path) -> dict[str, list[dict[str, Any]]]:
    with zipfile.ZipFile(bundle) as archive:
        return {
            split: [
                json.loads(line)
                for line in archive.read(
                    f"dataset_bundle/bts_agentbench_532/{split}.jsonl"
                ).splitlines()
                if line
            ]
            for split in SPLITS
        }


def json_inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset-bundle", type=Path, required=True)
    parser.add_argument("--selection-contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    work_dir = args.report.parent
    submitted = submitted_rows(args.dataset_bundle)
    submitted_by_id = {
        row["scenario_id"]: row for split in SPLITS for row in submitted[split]
    }
    contract = {
        row["scenario_id"]: row
        for row in load_jsonl(args.selection_contract)
    }
    replay_by_run: list[dict[str, dict[str, Any]]] = []
    for run in report["runs"]:
        run_index = int(run["run"])
        replay_by_run.append(
            {
                row["scenario_id"]: row
                for split in SPLITS
                for row in load_jsonl(
                    work_dir / f"run_{run_index}" / "final" / f"{split}.jsonl"
                )
            }
        )

    family_counts = Counter(row["task_family"] for row in submitted_by_id.values())
    split_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for split in SPLITS:
        for row in submitted[split]:
            split_counts[row["task_family"]][split] += 1

    lines = [
        "# Exact Paper-Submission Replay",
        "",
        "This report compares the immutable benchmark supplied with the paper against fresh deterministic reconstructions. Model APIs are not called.",
        "",
        "## Result",
        "",
        f"- Overall replay passed: `{str(bool(report['passed'])).lower()}`",
        f"- Construction runs: `{len(report['runs'])}`",
        f"- Cross-run byte equality: `{str(bool(report['cross_run_exact_match'])).lower()}`",
        f"- Selection contract SHA-256: `{report['selection_contract_sha256']}`",
        f"- Submitted source bundle SHA-256: `{report['submitted_source_bundle_sha256']}`",
        f"- Submitted dataset bundle SHA-256: `{report['submitted_dataset_bundle_sha256']}`",
        "",
        "## Split Hashes",
        "",
        "| Split | Static rows | Static submitted/replay SHA-256 | Final rows | Final submitted/replay SHA-256 | Exact |",
        "|---|---:|---|---:|---|:---:|",
    ]
    first = report["runs"][0]
    for split in SPLITS:
        static = first["static"][split]
        final = first["final"][split]
        lines.append(
            f"| {split.title()} | {static['row_count']} | `{static['sha256']}` | "
            f"{final['row_count']} | `{final['sha256']}` | "
            f"{'yes' if static['exact_file_match'] and final['exact_file_match'] else 'no'} |"
        )

    lines.extend(
        [
            "",
            "## Family-Wise Submitted vs Replay",
            "",
            "`Exact rows` counts complete JSON-object equality, including user turns, calls, phase targets, evidence, verifiers, provenance, and metadata.",
            "",
            "| Family | Submitted train/dev/test | Submitted rows | Replay 1 exact | Replay 2 exact |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for family, label in FAMILY_LABELS.items():
        family_ids = [
            scenario_id
            for scenario_id, row in submitted_by_id.items()
            if row["task_family"] == family
        ]
        exact_counts = [
            sum(run[scenario_id] == submitted_by_id[scenario_id] for scenario_id in family_ids)
            for run in replay_by_run
        ]
        run1 = f"{exact_counts[0]}/{len(family_ids)}" if exact_counts else "not run"
        run2 = f"{exact_counts[1]}/{len(family_ids)}" if len(exact_counts) > 1 else "not run"
        counts = split_counts[family]
        lines.append(
            f"| {label} | {counts['train']}/{counts['dev']}/{counts['test']} | "
            f"{family_counts[family]} | {run1} | {run2} |"
        )

    lines.extend(["", "## Representative Family Traces", ""])
    for family, label in FAMILY_LABELS.items():
        scenario_id = REPRESENTATIVES[family]
        submitted_row = submitted_by_id[scenario_id]
        record = contract[scenario_id]
        tool_path = " -> ".join(
            call["tool_name"] for call in submitted_row["canonical_tool_calls"]
        )
        digests = [canonical_digest(submitted_row)] + [
            canonical_digest(run[scenario_id]) for run in replay_by_run
        ]
        lines.extend(
            [
                f"### {label}",
                "",
                f"- Scenario: `{scenario_id}`",
                f"- Retained static identity: `{record['selection_identity_sha256']}`",
                f"- Tool path: `{tool_path}`",
                f"- Phase count: `{len(submitted_row.get('phase_gold_final_answers', []))}`",
                f"- Final gold: `{json_inline(submitted_row['gold_final_answer'])}`",
                f"- Submitted row digest: `{digests[0]}`",
            ]
        )
        for index, digest in enumerate(digests[1:], start=1):
            lines.append(f"- Replay {index} row digest: `{digest}`")
        lines.extend(
            [
                f"- All available digests equal: `{'yes' if len(set(digests)) == 1 else 'no'}`",
                "",
                "Initial request:",
                "",
                f"> {submitted_row['initial_user_message']}",
                "",
            ]
        )

    lines.extend(
        [
            "## What Was Recomputed",
            "",
            "Each run re-enumerated family candidates from the supplied tool store, matched all 532 frozen static identities exactly once, rebuilt E2E and operator surfaces, executed telemetry tools for phase targets, applied typed family repairs, and ran contract preflight. The submitted dataset bundle was read only after output generation for comparison.",
            "",
            "When the report's tool-store mode is `raw_archives_to_fresh_tool_store`, metadata normalization and raw telemetry preprocessing were also rerun before both episode builds.",
            "",
            "## Compatibility Boundary",
            "",
            "The exact replay preserves the paper snapshot, including two January training rank rows whose visible month direction was corrected only in the later maintenance path. Dev and test are unaffected by that maintenance issue. Exact replay and maintenance output are deliberately separate.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "python scripts/replay_paper_submission.py \\",
            "  --raw-dir /absolute/path/to/bts/raw \\",
            "  --work-dir ./data/local-build/paper-submission-replay \\",
            "  --runs 2",
            "```",
            "",
            "See `CONSTRUCTION_WALKTHROUGH.md` for the raw-member-to-static-to-agentic field-level derivation.",
        ]
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
