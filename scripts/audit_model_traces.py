#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT / "src", REPO_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bts_agentbench.evaluator import verify_prediction  # noqa: E402
from bts_agentbench.runtime import ExecutedCall, ToolStoreRuntime  # noqa: E402
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
    parser = argparse.ArgumentParser(
        description="Bind retained model traces to the clean release and deterministically rescore them."
    )
    parser.add_argument("--model-root", type=Path, default=REPO_ROOT / "reports" / "model-runs")
    parser.add_argument("--bts-benchmark-dir", type=Path, required=True)
    parser.add_argument("--bts-tool-store-db", type=Path, required=True)
    parser.add_argument("--xai4heat-benchmark-dir", type=Path, required=True)
    parser.add_argument("--xai4heat-tool-store-db", type=Path, required=True)
    parser.add_argument("--bts-raw-dir", type=Path)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "reports" / "model-runs" / "trace_audit.json")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def payload_and_rows(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    payload = path.read_bytes()
    return payload, [json.loads(line) for line in payload.splitlines() if line.strip()]


def executed_calls(row: dict[str, Any]) -> list[ExecutedCall]:
    return [
        ExecutedCall(
            call_id=str(call["call_id"]),
            tool_name=str(call["tool_name"]),
            arguments=dict(call.get("arguments") or {}),
            output=dict(call.get("output") or {}),
        )
        for call in row.get("executed_calls", [])
    ]


def rescore(
    source: dict[str, Any],
    trace: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    return verify_prediction(
        source,
        executed_calls(trace),
        trace.get("final_answer_text"),
        None,
        runtime,
        phase_answers=list(trace.get("phase_answer_texts") or []),
    ).as_dict()


def first_user_message(trace: dict[str, Any]) -> str | None:
    return next(
        (
            str(message.get("content"))
            for message in trace.get("messages", [])
            if message.get("role") == "user"
        ),
        None,
    )


def audit_bts(
    root: Path,
    sources: list[dict[str, Any]],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    source_by_id = {str(row["scenario_id"]): row for row in sources}
    source_ids = set(source_by_id)
    report: dict[str, Any] = {}
    reference_ids: set[str] | None = None

    for model_dir in MODEL_DIRS:
        traces = load_jsonl(root / model_dir / "test.jsonl")
        ids = {str(row["scenario_id"]) for row in traces}
        if len(ids) != len(traces) or ids != source_ids:
            raise RuntimeError(f"trace scenario set differs from clean BTS test split: {model_dir}")
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise RuntimeError(f"trace scenario sets differ: {model_dir}")

        if model_dir == "gpt-5.5":
            profile = "gpt55-bts"
            expected_prompt = lambda source: GPT55_BTS_SYSTEM_PROMPT
        elif model_dir == "gemini-3.1-pro-openrouter":
            profile = "bts-guided"
            expected_prompt = lambda source: SYSTEM_PROMPT + GEMINI_SYSTEM_APPEND + prompt_append(source, profile)
        else:
            profile = "bts-guided"
            expected_prompt = lambda source: SYSTEM_PROMPT + prompt_append(source, profile)

        binding_mismatches: list[str] = []
        prompt_mismatches: list[str] = []
        rescore_mismatches: list[str] = []
        by_family: dict[str, Counter[str]] = defaultdict(Counter)
        labels: Counter[str] = Counter()

        for trace in traces:
            scenario_id = str(trace["scenario_id"])
            source = source_by_id[scenario_id]
            if (
                trace.get("task_family") != source.get("task_family")
                or trace.get("interaction_mode") != source.get("interaction_mode")
                or first_user_message(trace) != source.get("initial_user_message")
            ):
                binding_mismatches.append(scenario_id)
            if trace["messages"][0]["content"] != expected_prompt(source):
                prompt_mismatches.append(scenario_id)
            if rescore(source, trace, runtime) != trace.get("static_verification"):
                rescore_mismatches.append(scenario_id)
            label = str(trace["label"])
            labels[label] += 1
            by_family[str(trace["task_family"])][label] += 1

        if binding_mismatches or prompt_mismatches or rescore_mismatches:
            raise RuntimeError(
                f"trace audit failed for {model_dir}: binding={binding_mismatches}, "
                f"prompt={prompt_mismatches}, rescore={rescore_mismatches}"
            )
        report[model_dir] = {
            "rows": len(traces),
            "labels": dict(sorted(labels.items())),
            "protocol_success": sum(bool(row["protocol_ok"]) for row in traces),
            "task_success": sum(bool(row["static_verification"]["task_ok"]) for row in traces),
            "clean_contract_bindings_verified": len(traces),
            "deterministic_rescores_matched": len(traces),
            "prompt_profile": profile,
            "system_prompts_verified": len(traces),
            "by_family": {
                family: dict(sorted(counts.items()))
                for family, counts in sorted(by_family.items())
            },
        }
    return report


def audit_xai4heat(
    root: Path,
    sources: list[dict[str, Any]],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    source_by_id = {str(row["scenario_id"]): row for row in sources}
    traces = load_jsonl(root / "gpt-5.5-xai4heat" / "test.jsonl")
    if {str(row["scenario_id"]) for row in traces} != set(source_by_id):
        raise RuntimeError("trace scenario set differs from clean XAI4HEAT test split")

    binding_mismatches: list[str] = []
    prompt_mismatches: list[str] = []
    rescore_mismatches: list[str] = []
    for trace in traces:
        scenario_id = str(trace["scenario_id"])
        source = source_by_id[scenario_id]
        if (
            trace.get("task_family") != source.get("task_family")
            or trace.get("interaction_mode") != source.get("interaction_mode")
            or first_user_message(trace) != source.get("initial_user_message")
        ):
            binding_mismatches.append(scenario_id)
        if trace["messages"][0]["content"] != SYSTEM_PROMPT + prompt_append(source, "xai4heat"):
            prompt_mismatches.append(scenario_id)
        if rescore(source, trace, runtime) != trace.get("static_verification"):
            rescore_mismatches.append(scenario_id)

    if binding_mismatches or prompt_mismatches or rescore_mismatches:
        raise RuntimeError(
            f"XAI4HEAT trace audit failed: binding={binding_mismatches}, "
            f"prompt={prompt_mismatches}, rescore={rescore_mismatches}"
        )
    return {
        "model": "gpt-5.5",
        "rows": len(traces),
        "accomplished": sum(row["label"] == "accomplished" for row in traces),
        "protocol_success": sum(bool(row["protocol_ok"]) for row in traces),
        "task_success": sum(bool(row["static_verification"]["task_ok"]) for row in traces),
        "clean_contract_bindings_verified": len(traces),
        "deterministic_rescores_matched": len(traces),
        "prompt_profile": "xai4heat",
    }


def main() -> None:
    args = parse_args()
    if args.bts_raw_dir is not None:
        os.environ["BTS_RAW_DIR"] = str(args.bts_raw_dir.resolve())

    bts_payload, bts_rows = payload_and_rows(args.bts_benchmark_dir / "test.jsonl")
    xai_payload, xai_rows = payload_and_rows(args.xai4heat_benchmark_dir / "test.jsonl")
    bts_runtime = ToolStoreRuntime(args.bts_tool_store_db)
    xai_runtime = ToolStoreRuntime(args.xai4heat_tool_store_db)
    try:
        report = {
            "report_type": "retained-trace-clean-release-audit",
            "bts_test_rows": len(bts_rows),
            "bts_test_sha256": hashlib.sha256(bts_payload).hexdigest(),
            "xai4heat_test_rows": len(xai_rows),
            "xai4heat_test_sha256": hashlib.sha256(xai_payload).hexdigest(),
            "models": audit_bts(args.model_root, bts_rows, bts_runtime),
            "xai4heat": audit_xai4heat(args.model_root, xai_rows, xai_runtime),
        }
    finally:
        bts_runtime.close()
        xai_runtime.close()

    if args.check:
        retained = json.loads(args.output.read_text(encoding="utf-8"))
        if retained != report:
            raise RuntimeError("retained trace audit does not match recomputed report")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
