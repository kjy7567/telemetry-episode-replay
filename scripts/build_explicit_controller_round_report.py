#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(witness_dir: Path) -> dict[str, Any]:
    split_reports: dict[str, Any] = {}
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    global_issues: Counter[str] = Counter()
    total = 0
    total_accomplished = 0

    for split in ("train", "dev", "test"):
        report_path = witness_dir / split / "explicit_controller_report.json"
        payload = load_json(report_path)
        report = payload["reports"]["phase_complete_stronger_controller"]
        split_reports[split] = report
        total += int(report["scenario_count"])
        total_accomplished += int(report["label_counts"].get("accomplished", 0))
        for family, counts in report["by_family"].items():
            for label, count in counts.items():
                family_counts[family][label] += int(count)
        for issue, count in report["top_issues"]:
            global_issues[str(issue)] += int(count)

    return {
        "report_version": "explicit-controller-round-v4",
        "audit_round": "construction_exclusion_verification",
        "controller": "phase_complete_stronger_controller",
        "witness_dir": str(witness_dir),
        "rationale": [
            "re-run the frozen deterministic controller after construction to verify the predefined exclusion criterion",
            "retain per-row witnesses and summarize which task, temporal, grounding, and protocol checks the controller misses",
            "interpret zero accomplished rows only as confirmation that the construction-time exclusion rule was applied",
        ],
        "dataset_repair_policy": {
            "row_level_repair_trigger": "controller_accomplished_under_frozen_audit",
            "row_level_repairs_applied_in_this_round": 0,
            "reason": "this is a post-build rerun of the construction filter; any accomplished row would fail the declared exclusion criterion",
        },
        "totals": {
            "scenario_count": total,
            "accomplished_count": total_accomplished,
            "accomplished_rate": round(total_accomplished / total, 4) if total else 0.0,
        },
        "by_split": {
            split: {
                "scenario_count": int(report["scenario_count"]),
                "label_counts": report["label_counts"],
                "contradiction_count": int(report["contradiction_count"]),
                "top_issues": report["top_issues"][:10],
            }
            for split, report in split_reports.items()
        },
        "by_family": {family: dict(sorted(counter.items())) for family, counter in sorted(family_counts.items())},
        "global_top_issues": global_issues.most_common(20),
        "conclusion": {
            "controller_witness_rows_remaining": total_accomplished,
            "needs_additional_dataset_repair": total_accomplished > 0,
            "summary": (
                "The construction exclusion criterion is satisfied only when the frozen controller accomplishes "
                "zero rows. This result is not an independent estimate of benchmark difficulty."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_report(args.witness_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
