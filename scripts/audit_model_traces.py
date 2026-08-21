#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for path in (SRC_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_bts_e2e_openai_eval import (  # noqa: E402
    GEMINI_SYSTEM_APPEND,
    GPT55_BTS_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    prompt_append,
)

MODEL_DIRS = (
    "gpt-5.5",
    "gemini-3.1-pro-openrouter",
    "claude-opus-4.7-openrouter",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the computed audit with the retained report without rewriting it.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = REPO_ROOT / "reports" / "model-runs"
    submitted_bundle = REPO_ROOT / "release" / "submitted-dataset-bundle.zip"
    with zipfile.ZipFile(submitted_bundle) as archive:
        submitted_test_payload = archive.read(
            "dataset_bundle/bts_agentbench_532/test.jsonl"
        )
        xai4heat_test_payload = archive.read(
            "dataset_bundle/xai4heat_agentbench_204/test.jsonl"
        )
    submitted_test_rows = [
        json.loads(line) for line in submitted_test_payload.splitlines() if line
    ]
    submitted_test_ids = {
        str(row["scenario_id"]) for row in submitted_test_rows
    }
    submitted_by_id = {
        str(row["scenario_id"]): row for row in submitted_test_rows
    }
    report: dict[str, Any] = {
        "report_version": "fixed-trace-audit-v3",
        "submitted_test_rows": len(submitted_test_rows),
        "submitted_test_sha256": hashlib.sha256(submitted_test_payload).hexdigest(),
        "models": {},
    }
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
        if ids != submitted_test_ids:
            raise RuntimeError(f"trace scenario set differs from submitted test split: {model_dir}")

        invalid_contract_bindings: list[str] = []
        for row in rows:
            submitted_row = submitted_by_id[str(row["scenario_id"])]
            user_messages = [
                message.get("content")
                for message in row.get("messages", [])
                if message.get("role") == "user"
            ]
            if (
                row.get("task_family") != submitted_row.get("task_family")
                or row.get("interaction_mode") != submitted_row.get("interaction_mode")
                or not user_messages
                or user_messages[0] != submitted_row.get("initial_user_message")
            ):
                invalid_contract_bindings.append(str(row["scenario_id"]))
        if invalid_contract_bindings:
            raise RuntimeError(
                f"trace contract binding mismatch in {model_dir}: {invalid_contract_bindings}"
            )

        invalid_labels = [
            row["scenario_id"]
            for row in rows
            if (row["label"] == "accomplished")
            != bool(row["static_verification"]["task_ok"] and row["protocol_ok"])
        ]
        if invalid_labels:
            raise RuntimeError(f"accomplished-label mismatch in {model_dir}: {invalid_labels}")

        if model_dir == "gpt-5.5":
            profile = "gpt55-bts"
            expected_prompt = lambda source: GPT55_BTS_SYSTEM_PROMPT
        elif model_dir == "gemini-3.1-pro-openrouter":
            profile = "bts-guided"
            expected_prompt = lambda source: (
                SYSTEM_PROMPT + GEMINI_SYSTEM_APPEND + prompt_append(source, profile)
            )
        else:
            profile = "bts-guided"
            expected_prompt = lambda source: SYSTEM_PROMPT + prompt_append(source, profile)

        prompt_mismatches = [
            str(row["scenario_id"])
            for row in rows
            if row["messages"][0]["content"]
            != expected_prompt(submitted_by_id[str(row["scenario_id"])])
        ]
        if prompt_mismatches:
            raise RuntimeError(f"system-prompt mismatch in {model_dir}: {prompt_mismatches}")

        by_family: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            by_family[str(row["task_family"])][str(row["label"])] += 1
        labels = Counter(str(row["label"]) for row in rows)
        report["models"][model_dir] = {
            "rows": len(rows),
            "labels": dict(sorted(labels.items())),
            "protocol_success": sum(bool(row["protocol_ok"]) for row in rows),
            "task_success": sum(bool(row["static_verification"]["task_ok"]) for row in rows),
            "submitted_contract_binding_verified": True,
            "accomplished_definition_verified": True,
            "prompt_profile": profile,
            "system_prompts_verified": len(rows),
            "by_family": {family: dict(sorted(counts.items())) for family, counts in sorted(by_family.items())},
        }

    xai4heat_test_rows = [
        json.loads(line) for line in xai4heat_test_payload.splitlines() if line
    ]
    xai4heat_by_id = {
        str(row["scenario_id"]): row for row in xai4heat_test_rows
    }
    xai4heat_traces = load_jsonl(root / "gpt-5.5-xai4heat" / "test.jsonl")
    xai4heat_trace_ids = {str(row["scenario_id"]) for row in xai4heat_traces}
    if xai4heat_trace_ids != set(xai4heat_by_id):
        raise RuntimeError("XAI4HEAT trace scenario set differs from released test split")

    xai4heat_prompt_mismatches: list[str] = []
    xai4heat_binding_mismatches: list[str] = []
    xai4heat_label_mismatches: list[str] = []
    for row in xai4heat_traces:
        scenario_id = str(row["scenario_id"])
        source = xai4heat_by_id[scenario_id]
        expected = SYSTEM_PROMPT + prompt_append(source, "xai4heat")
        if row["messages"][0]["content"] != expected:
            xai4heat_prompt_mismatches.append(scenario_id)
        user_messages = [
            message.get("content")
            for message in row.get("messages", [])
            if message.get("role") == "user"
        ]
        if (
            row.get("task_family") != source.get("task_family")
            or row.get("interaction_mode") != source.get("interaction_mode")
            or not user_messages
            or user_messages[0] != source.get("initial_user_message")
        ):
            xai4heat_binding_mismatches.append(scenario_id)
        if (row.get("label") == "accomplished") != bool(
            row["static_verification"]["task_ok"] and row["protocol_ok"]
        ):
            xai4heat_label_mismatches.append(scenario_id)

    if xai4heat_prompt_mismatches:
        raise RuntimeError(f"XAI4HEAT system-prompt mismatch: {xai4heat_prompt_mismatches}")
    if xai4heat_binding_mismatches:
        raise RuntimeError(f"XAI4HEAT contract-binding mismatch: {xai4heat_binding_mismatches}")
    if xai4heat_label_mismatches:
        raise RuntimeError(f"XAI4HEAT accomplished-label mismatch: {xai4heat_label_mismatches}")

    report["xai4heat"] = {
        "submitted_test_rows": len(xai4heat_test_rows),
        "submitted_test_sha256": hashlib.sha256(xai4heat_test_payload).hexdigest(),
        "model": "gpt-5.5",
        "rows": len(xai4heat_traces),
        "accomplished": sum(row["label"] == "accomplished" for row in xai4heat_traces),
        "protocol_success": sum(bool(row["protocol_ok"]) for row in xai4heat_traces),
        "task_success": sum(bool(row["static_verification"]["task_ok"]) for row in xai4heat_traces),
        "submitted_contract_binding_verified": True,
        "accomplished_definition_verified": True,
        "prompt_profile": "xai4heat",
        "system_prompts_verified": len(xai4heat_traces),
    }

    output = root / "trace_audit.json"
    if args.check:
        retained = json.loads(output.read_text(encoding="utf-8"))
        if retained != report:
            raise RuntimeError("retained trace audit does not match recomputed report")
    else:
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
