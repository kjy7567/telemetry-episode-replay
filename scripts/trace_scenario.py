#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
STREAM_ID = re.compile(r"^[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}$")


def load_scenario(directory: Path, scenario_id: str) -> dict[str, Any]:
    for split in ("train", "dev", "test"):
        path = directory / f"{split}.jsonl"
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            if row.get("scenario_id") == scenario_id:
                return row
    raise KeyError(f"scenario not found: {scenario_id}")


def collect_stream_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if "stream_id" in key:
                if isinstance(child, str) and STREAM_ID.match(child):
                    found.add(child)
                elif isinstance(child, list):
                    found.update(item for item in child if isinstance(item, str) and STREAM_ID.match(item))
            found.update(collect_stream_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(collect_stream_ids(child))
    return found


def raw_lineage(path: Path, stream_ids: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("stream_id") in stream_ids]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a row-level static-to-final provenance trace.")
    parser.add_argument("scenario_id")
    parser.add_argument("--static-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-static-seed")
    parser.add_argument("--final-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-canonical-final")
    parser.add_argument(
        "--stream-lineage",
        type=Path,
        default=REPO_ROOT / "provenance" / "release_stream_lineage.csv",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    static = load_scenario(args.static_dir, args.scenario_id)
    final = load_scenario(args.final_dir, args.scenario_id)
    stream_ids = collect_stream_ids(static) | collect_stream_ids(final)
    trace = {
        "trace_version": "scenario-provenance-v1",
        "scenario_id": args.scenario_id,
        "raw_sources": raw_lineage(args.stream_lineage, stream_ids),
        "static_task": {
            key: static.get(key)
            for key in (
                "scenario_id",
                "split",
                "site_id",
                "task_family",
                "query",
                "canonical_tool_calls",
                "acceptable_tool_call_sets",
                "gold_final_answer",
                "evidence",
                "task_accomplish_verifier",
            )
        },
        "final_episode": {
            key: final.get(key)
            for key in (
                "scenario_id",
                "backing_static_scenario_id",
                "interaction_mode",
                "initial_user_message",
                "required_clarification_slots",
                "clarification_answers",
                "goal_revision_turns",
                "phase_examples",
                "phase_gold_final_answers",
                "gold_final_answer",
                "evidence",
                "release_filter",
            )
        },
        "generation_history": final.get("generation_history", []),
    }
    rendered = json.dumps(trace, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
