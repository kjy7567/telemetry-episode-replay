#!/usr/bin/env python
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIRS = (
    "gpt-5.5",
    "gemini-3.1-pro-openrouter",
    "claude-opus-4.7-openrouter",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    root = REPO_ROOT / "reports" / "model-runs"
    report: dict[str, Any] = {"report_version": "fixed-trace-audit-v1", "models": {}}
    reference_ids: set[str] | None = None
    for model_dir in MODEL_DIRS:
        rows = load_jsonl(root / model_dir / "test.jsonl")
        ids = {str(row["scenario_id"]) for row in rows}
        if len(ids) != len(rows):
            raise RuntimeError(f"duplicate scenario ID in {model_dir}")
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise RuntimeError(f"trace scenario sets differ for {model_dir}")

        invalid_labels = [
            row["scenario_id"]
            for row in rows
            if (row["label"] == "accomplished")
            != bool(row["static_verification"]["task_ok"] and row["protocol_ok"])
        ]
        if invalid_labels:
            raise RuntimeError(f"accomplished-label mismatch in {model_dir}: {invalid_labels}")

        by_family: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            by_family[str(row["task_family"])][str(row["label"])] += 1
        labels = Counter(str(row["label"]) for row in rows)
        report["models"][model_dir] = {
            "rows": len(rows),
            "labels": dict(sorted(labels.items())),
            "protocol_success": sum(bool(row["protocol_ok"]) for row in rows),
            "task_success": sum(bool(row["static_verification"]["task_ok"]) for row in rows),
            "accomplished_definition_verified": True,
            "by_family": {family: dict(sorted(counts.items())) for family, counts in sorted(by_family.items())},
        }

    output = root / "trace_audit.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
