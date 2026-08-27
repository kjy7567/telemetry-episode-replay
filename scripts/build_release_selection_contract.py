#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bts_agentbench.scenario_benchmark import (  # noqa: E402
    TASK_FAMILY_BUILDERS,
    selection_identity_from_payload,
    selection_identity_sha256,
)


SPLITS = ("train", "dev", "test")
CANDIDATE_POPULATIONS = {
    "point_disambiguation": 4263,
    "day_mean_lookup": 2193431,
    "relative_24h_mean_lookup": 2193431,
    "window_mean_lookup": 315929,
    "window_pairwise_compare": 5989083,
    "window_rank": 1084,
    "timestamp_value_lookup": 2123,
    "timestamp_nearest_lookup": 2123,
    "quality_gate": 315,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def family_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stream_ids = {
        str(stream_id)
        for row in rows
        for stream_id in row.get("evidence", {}).get("stream_ids", [])
    }
    point_classes: set[str] = set()
    equipment_labels: set[str] = set()
    timestamps: list[str] = []
    for row in rows:
        metadata = row.get("metadata", {})
        if metadata.get("point_class"):
            point_classes.add(str(metadata["point_class"]))
        for call in row.get("canonical_tool_calls", []):
            arguments = call.get("arguments", {})
            if arguments.get("point_class"):
                point_classes.add(str(arguments["point_class"]))
            for key, value in arguments.items():
                if "equipment_label" in key and value:
                    equipment_labels.add(str(value))
                if key in {"timestamp", "window_start", "window_end"} and value:
                    timestamps.append(str(value))

    difficulty_values: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        for key, value in row.get("difficulty_proxy", {}).items():
            if isinstance(value, (int, float)):
                difficulty_values[key].append(float(value))

    decisions = Counter(
        str(row.get("gold_final_answer", {}).get("decision"))
        for row in rows
        if row.get("gold_final_answer", {}).get("decision") is not None
    )
    return {
        "retained_rows": len(rows),
        "candidate_population": CANDIDATE_POPULATIONS[rows[0]["task_family"]],
        "retained_fraction": round(
            len(rows) / CANDIDATE_POPULATIONS[rows[0]["task_family"]], 8
        ),
        "split_counts": dict(Counter(str(row["split"]) for row in rows)),
        "site_counts": dict(Counter(str(row["site_id"]) for row in rows)),
        "unique_evidence_stream_count": len(stream_ids),
        "unique_point_class_count": len(point_classes),
        "unique_equipment_label_count": len(equipment_labels),
        "temporal_argument_min": min(timestamps) if timestamps else None,
        "temporal_argument_max": max(timestamps) if timestamps else None,
        "decision_counts": dict(decisions),
        "difficulty_proxy": {
            key: {
                "min": min(values),
                "median": median(values),
                "max": max(values),
            }
            for key, values in sorted(difficulty_values.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a retained-row selection contract from a release static benchmark."
    )
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    source_rows_by_family: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    source_hashes: dict[str, str] = {}
    split_counts: dict[str, int] = {}
    for split in SPLITS:
        path = args.static_dir / f"{split}.jsonl"
        rows = load_jsonl(path)
        source_hashes[split] = sha256(path)
        split_counts[split] = len(rows)
        for row in rows:
            source_rows_by_family[str(row["task_family"])].append(row)
            scenario_id = str(row["scenario_id"])
            ordinal = int(scenario_id.rsplit("_", 1)[1])
            identity = selection_identity_from_payload(row)
            records.append(
                {
                    "scenario_id": scenario_id,
                    "split": split,
                    "task_family": row["task_family"],
                    "family_ordinal": ordinal,
                    "selection_identity_sha256": selection_identity_sha256(identity),
                    "selection_identity": identity,
                }
            )

    family_order = {name: index for index, name in enumerate(TASK_FAMILY_BUILDERS)}
    records.sort(
        key=lambda record: (
            family_order[str(record["task_family"])],
            int(record["family_ordinal"]),
        )
    )

    seen_ids: set[str] = set()
    seen_identities: set[str] = set()
    for record in records:
        scenario_id = str(record["scenario_id"])
        digest = str(record["selection_identity_sha256"])
        if scenario_id in seen_ids:
            raise ValueError(f"duplicate scenario ID: {scenario_id}")
        if digest in seen_identities:
            raise ValueError(f"duplicate selection identity: {digest}")
        seen_ids.add(scenario_id)
        seen_identities.add(digest)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "contract_type": "bts-release-static-selection",
        "purpose": "Freeze retained candidate identities while recomputing their static tasks from raw telemetry.",
        "row_count": len(records),
        "split_counts": split_counts,
        "family_counts": {
            family: sum(record["task_family"] == family for record in records)
            for family in TASK_FAMILY_BUILDERS
        },
        "source_static_split_sha256": source_hashes,
        "selection_contract_sha256": sha256(args.output),
        "family_profiles": {
            family: family_profile(source_rows_by_family[family])
            for family in TASK_FAMILY_BUILDERS
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
