#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SPLITS = ("train", "dev", "test")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-bundle", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    static_by_id = {
        row["scenario_id"]: row
        for split in SPLITS
        for row in load_jsonl(args.static_dir / f"{split}.jsonl")
    }
    with zipfile.ZipFile(args.dataset_bundle) as archive:
        final_rows = [
            json.loads(line)
            for split in SPLITS
            for line in archive.read(
                f"dataset_bundle/bts_agentbench_532/{split}.jsonl"
            ).splitlines()
            if line
        ]

    stage_status: defaultdict[str, Counter[str]] = defaultdict(Counter)
    family_modified: defaultdict[str, Counter[str]] = defaultdict(Counter)
    rows_with_modified_repair: set[str] = set()
    backing_id_matches = 0
    source_query_matches = 0
    source_family_matches = 0
    source_site_matches = 0
    for row in final_rows:
        scenario_id = str(row["scenario_id"])
        static = static_by_id[scenario_id]
        backing_id = str(row.get("backing_static_scenario_id") or "")
        if backing_id == scenario_id:
            backing_id_matches += 1
        lifting = row.get("agentic_lifting", {})
        if lifting.get("source_static_query") == static.get("query"):
            source_query_matches += 1
        if row.get("task_family") == static.get("task_family"):
            source_family_matches += 1
        if row.get("site_id") == static.get("site_id"):
            source_site_matches += 1

        for entry in row.get("generation_history", []):
            if entry.get("stage_type") != "repair":
                continue
            stage = str(entry.get("stage") or "")
            status = str(entry.get("status") or "")
            stage_status[stage][status] += 1
            if status == "modified":
                rows_with_modified_repair.add(scenario_id)
                family_modified[str(row["task_family"])][stage] += 1

    report = {
        "report_version": "paper-submission-repair-profile-v1",
        "row_count": len(final_rows),
        "lineage_invariants": {
            "backing_static_scenario_id_matches": backing_id_matches,
            "source_static_query_matches": source_query_matches,
            "task_family_matches": source_family_matches,
            "site_id_matches": source_site_matches,
        },
        "rows_with_at_least_one_modified_repair": len(rows_with_modified_repair),
        "repair_stage_status_counts": {
            stage: dict(sorted(counts.items()))
            for stage, counts in sorted(stage_status.items())
        },
        "family_modified_stage_counts": {
            family: dict(sorted(counts.items()))
            for family, counts in sorted(family_modified.items())
        },
        "interpretation": {
            "modified": "The typed stage changed one or more interaction-contract fields.",
            "noop": "The stage was checked but its predicate did not require a change.",
            "applied": "The final family semantics stamp was attached after typed checks.",
            "lineage_scope": "These are coded lineage invariants, not a human semantic-validity judgment.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
