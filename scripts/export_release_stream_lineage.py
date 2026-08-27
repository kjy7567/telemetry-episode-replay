#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[1]
STREAM_ID = re.compile(r"^[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}$")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Export raw-archive lineage for streams referenced by the release.")
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=REPO_ROOT / "artifacts" / "bts-static-tasks",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "provenance" / "release_stream_lineage.csv",
    )
    args = parser.parse_args()

    scenarios_by_stream: dict[str, set[str]] = defaultdict(set)
    for split in ("train", "dev", "test"):
        for line in (args.static_dir / f"{split}.jsonl").open(encoding="utf-8"):
            row = json.loads(line)
            for stream_id in collect_stream_ids(row):
                scenarios_by_stream[stream_id].add(str(row["scenario_id"]))

    stream_ids = sorted(scenarios_by_stream)
    placeholders = ",".join("?" for _ in stream_ids)
    connection = duckdb.connect(str(args.tool_store_db), read_only=True)
    try:
        rows = connection.execute(
            f"""
            select site_id, stream_id, point_class, equipment_label, location_label,
                   raw_zip_path, raw_member_name, raw_n_points,
                   raw_first_timestamp, raw_last_timestamp
            from tool_ready_points
            where stream_id in ({placeholders})
            order by stream_id
            """,
            stream_ids,
        ).fetchdf().to_dict(orient="records")
    finally:
        connection.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "site_id",
        "stream_id",
        "point_class",
        "equipment_label",
        "location_label",
        "raw_archive",
        "raw_member_name",
        "raw_n_points",
        "raw_first_timestamp",
        "raw_last_timestamp",
        "scenario_ids",
    ]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "site_id": row["site_id"],
                    "stream_id": row["stream_id"],
                    "point_class": row["point_class"],
                    "equipment_label": row["equipment_label"],
                    "location_label": row["location_label"],
                    "raw_archive": Path(str(row["raw_zip_path"])).name,
                    "raw_member_name": row["raw_member_name"],
                    "raw_n_points": row["raw_n_points"],
                    "raw_first_timestamp": row["raw_first_timestamp"],
                    "raw_last_timestamp": row["raw_last_timestamp"],
                    "scenario_ids": ";".join(sorted(scenarios_by_stream[str(row["stream_id"])])),
                }
            )
    print(json.dumps({"output": str(args.output), "stream_count": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
