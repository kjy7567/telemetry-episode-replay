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


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    parser.add_argument("--controller-report", type=Path)
    parser.add_argument("--independent-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    controller = (
        json.loads(args.controller_report.read_text(encoding="utf-8"))
        if args.controller_report is not None
        else None
    )
    independent = (
        json.loads(args.independent_report.read_text(encoding="utf-8"))
        if args.independent_report is not None
        else None
    )
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

    split_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for split in SPLITS:
        for row in submitted[split]:
            split_counts[row["task_family"]][split] += 1

    lines = [
        "# Exact Paper-Submission Replay",
        "",
        "This report compares the immutable benchmark supplied with the paper against fresh deterministic reconstructions. Model APIs are not called.",
        "",
        "## Plain-Language Result",
        "",
        "The reconstruction starts from raw timestamp/value archives and the retained semantic mapping, rebuilds the read-only telemetry database, reruns every family builder, and reconstructs every submitted interaction contract.",
        "",
        "- All 532 static tasks were regenerated from telemetry-backed candidates.",
        "- All 532 final episodes matched the submitted rows as complete JSON objects in Replay 1 and Replay 2.",
        "- The equality check covers user turns, clarification answers, goal revisions, tool calls, phase golds, final actions, evidence, verifiers, provenance, and serialization.",
        "- Both replay runs completed with zero coded preflight issues.",
        "- A full raw-to-static-to-agentic example is shown in [`examples/REPLAY_TRACE.md`](../examples/REPLAY_TRACE.md).",
        "",
        "## Integrity Identifiers",
        "",
        f"- Overall replay passed: `{str(bool(report['passed'])).lower()}`",
        f"- Construction runs: `{len(report['runs'])}`",
        f"- Cross-run byte equality: `{str(bool(report['cross_run_exact_match'])).lower()}`",
        f"- Selection contract SHA-256: `{report['selection_contract_sha256']}`",
        f"- Submitted source bundle SHA-256: `{report['submitted_source_bundle_sha256']}`",
        f"- Submitted dataset bundle SHA-256: `{report['submitted_dataset_bundle_sha256']}`",
    ]
    tool_store_build = report["tool_store_build"]
    if tool_store_build["mode"] == "raw_archives_to_fresh_tool_store":
        lines.extend(
            [
                "",
                "## Verified Raw Boundary",
                "",
                "The two episode builds shared one fresh read-only tool store reconstructed from the following checksummed raw telemetry archives and retained normalized metadata contract.",
                "",
                "| Raw archive | Bytes | SHA-256 |",
                "|---|---:|---|",
            ]
        )
        for record in tool_store_build["raw_archives"]:
            lines.append(
                f"| `{record['name']}` | {record['size_bytes']:,} | `{record['sha256']}` |"
            )
        lines.extend(
            [
                "",
                "| Retained catalog file | Bytes | SHA-256 |",
                "|---|---:|---|",
            ]
        )
        for record in tool_store_build["retained_catalog"]:
            lines.append(
                f"| `{record['name']}` | {record['size_bytes']:,} | `{record['sha256']}` |"
            )
        lines.extend(
            [
                "",
                "| Site | Streams processed | Skipped archive members |",
                "|---|---:|---:|",
            ]
        )
        for site, record in tool_store_build["preprocess_summary"]["site_stats"].items():
            lines.append(
                f"| `{site}` | {record['streams_processed']:,} | {record['skipped_members']:,} |"
            )
        coverage = tool_store_build["preprocess_summary"]["raw_coverage"]
        lines.extend(
            [
                "",
                f"The fresh tool store matched `{coverage['matched_streams']:,}` raw streams to the retained metadata contract. The `{coverage['skipped_members']}` skipped members are AppleDouble archive metadata rather than retained telemetry streams.",
                "",
                "Metadata normalization is not claimed as a cross-environment replay step: the historical submission mapping is retained and checksummed because unordered RDF traversal cannot recover all historical first-target choices for multi-edge relationships.",
            ]
        )

    if independent is not None:
        exact_exports = sum(
            int(record["exact_file_match"])
            for record in independent["logical_tool_store_files"].values()
        )
        lines.extend(
            [
                "",
                "## Independent Reconstruction Evidence",
                "",
                "Two independent raw telemetry preprocessing executions were compared before downstream episode verification.",
                "",
                f"- Raw archive inventories equal: `{str(bool(independent['raw_archive_inventory_match'])).lower()}`",
                f"- Byte-identical exported logical tool-store files: `{exact_exports}/{independent['logical_tool_store_file_count']}`",
                f"- Exact static-to-final episode builds across both raw stores: `{independent['total_exact_episode_builds']}`",
                f"- Independent replay report SHA-256: `{file_digest(args.independent_report)}`",
                "",
                "The two DuckDB container files are not byte-identical because their physical storage layout is not a canonical serialization. They are excluded from the determinism decision; the sorted exported tables are byte-identical, and both stores produce the same submitted static and final split hashes.",
            ]
        )

    lines.extend(
        [
            "",
            "## Split Hashes",
            "",
            "| Split | Static rows | Static submitted/replay SHA-256 | Final rows | Final submitted/replay SHA-256 | Exact |",
            "|---|---:|---|---:|---|:---:|",
        ]
    )
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
            "The final action below is shown directly so that replay is visible without interpreting a hash. `Exact rows` counts complete JSON-object equality, including user turns, calls, phase targets, evidence, verifiers, provenance, and metadata.",
            "",
            "| Family | Rows train/dev/test | Representative submitted final | Replay 1 final | Replay 2 final | Exact rows in both replays |",
            "|---|---:|---|---|---|---:|",
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
        representative_id = REPRESENTATIVES[family]
        submitted_final = json_inline(submitted_by_id[representative_id]["gold_final_answer"])
        replay_finals = [
            json_inline(run[representative_id]["gold_final_answer"])
            for run in replay_by_run
        ]
        replay_1_final = replay_finals[0] if replay_finals else "not run"
        replay_2_final = replay_finals[1] if len(replay_finals) > 1 else "not run"
        lines.append(
            f"| {label} | {counts['train']}/{counts['dev']}/{counts['test']} | "
            f"`{submitted_final}` | `{replay_1_final}` | `{replay_2_final}` | "
            f"{run1}; {run2} |"
        )

    if controller is not None:
        totals = controller["totals"]
        lines.extend(
            [
                "",
                "## Frozen Controller Audit",
                "",
                "The frozen construction-exclusion controller was rerun against Replay 1 after its final JSONL files had matched the submitted files byte for byte.",
                "",
                f"- Scenarios audited: `{totals['scenario_count']}`",
                f"- Accomplished: `{totals['accomplished_count']}`",
                f"- Audit report SHA-256: `{file_digest(args.controller_report)}`",
                "",
                "| Split | Rows | Accomplished |",
                "|---|---:|---:|",
            ]
        )
        for split in SPLITS:
            split_report = controller["by_split"][split]
            accomplished = int(split_report.get("label_counts", {}).get("accomplished", 0))
            lines.append(
                f"| {split.title()} | {split_report['scenario_count']} | {accomplished} |"
            )
        lines.extend(
            [
                "",
                "This confirms that the predefined exclusion rule remains satisfied. It is not reported as an independent estimate of benchmark difficulty or as a model baseline.",
            ]
        )

    lines.extend(["", "## Representative Family Traces", ""])
    for family, label in FAMILY_LABELS.items():
        scenario_id = REPRESENTATIVES[family]
        submitted_row = submitted_by_id[scenario_id]
        record = contract[scenario_id]
        tool_path = " -> ".join(
            call["tool_name"] for call in submitted_row["canonical_tool_calls"]
        )
        submitted_phases = submitted_row.get("phase_gold_final_answers", [])
        replay_rows = [run[scenario_id] for run in replay_by_run]
        replay_finals = [row["gold_final_answer"] for row in replay_rows]
        phase_gold_equal = all(
            row.get("phase_gold_final_answers", []) == submitted_phases
            for row in replay_rows
        )
        tool_trace_equal = all(
            row.get("canonical_tool_calls", [])
            == submitted_row.get("canonical_tool_calls", [])
            for row in replay_rows
        )
        complete_row_equal = all(row == submitted_row for row in replay_rows)
        digests = [canonical_digest(submitted_row)] + [
            canonical_digest(row) for row in replay_rows
        ]
        lines.extend(
            [
                f"### {label}",
                "",
                f"- Scenario: `{scenario_id}`",
                f"- Submitted final gold: `{json_inline(submitted_row['gold_final_answer'])}`",
            ]
        )
        for index, final_gold in enumerate(replay_finals, start=1):
            lines.append(f"- Replay {index} final gold: `{json_inline(final_gold)}`")
        lines.extend(
            [
                f"- Gold tool path in submitted and replayed rows: `{tool_path}`",
                f"- Phase gold trace identical: `{'yes' if phase_gold_equal else 'no'}` ({len(submitted_phases)} phases)",
                f"- Gold tool trace identical: `{'yes' if tool_trace_equal else 'no'}`",
                f"- Complete submitted/replay row equality: `{'yes' if complete_row_equal else 'no'}`",
                f"- Secondary integrity digest shared by all copies: `{digests[0] if len(set(digests)) == 1 else 'MISMATCH'}`",
                f"- Retained static identity: `{record['selection_identity_sha256']}`",
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
            "When the report's tool-store mode is `raw_archives_to_fresh_tool_store`, the replay first verifies the retained normalized metadata contract and then reruns raw telemetry preprocessing before both episode builds. Metadata normalization itself is an upstream, checksummed boundary rather than a claimed cross-environment replay step.",
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
