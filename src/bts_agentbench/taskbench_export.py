from __future__ import annotations

import json
from pathlib import Path

from bts_agentbench.operator_answer import render_operator_answer
from bts_agentbench.scenario_benchmark import SCENARIO_TOOL_REGISTRY


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_decomposition(canonical_calls: list[dict]) -> list[dict]:
    steps = []
    for idx, call in enumerate(canonical_calls, start=1):
        steps.append(
            {
                "step_id": f"s{idx}",
                "tool_name": call["tool_name"],
                "goal": f"Use {call['tool_name']} to advance the building-operations scenario.",
                "arguments": call["arguments"],
            }
        )
    return steps


def system_prompt(mode: str) -> str:
    if mode == "decomposition":
        return (
            "You are a building-operations planning model. "
            "Decompose the scenario into an ordered JSON list of tool-oriented steps."
        )
    if mode == "tool_selection":
        return (
            "You are a building-operations planning model. "
            "Return only the ordered JSON list of tool names needed to solve the scenario."
        )
    if mode == "parameter_prediction":
        return (
            "You are a building-operations tool-calling model. "
            "Return only canonical JSON tool calls with arguments."
        )
    return (
        "You are a building-operations agent. "
        "Return canonical JSON tool calls and a concise natural-language final answer."
    )


def user_prompt(example: dict, mode: str) -> str:
    base = {
        "query": example["query"],
        "task_family": example["task_family"],
        "site_id": example["site_id"],
        "available_tools": SCENARIO_TOOL_REGISTRY["tools"],
    }
    if mode == "decomposition":
        base["instruction"] = "Return JSON with key 'steps'."
    elif mode == "tool_selection":
        base["instruction"] = "Return JSON with key 'selected_tools'."
    elif mode == "parameter_prediction":
        base["instruction"] = "Return JSON with key 'tool_calls'."
    else:
        base["instruction"] = "Return JSON with keys 'tool_calls' and 'final_answer'."
    return json.dumps(base, ensure_ascii=False)


def assistant_target(example: dict, mode: str) -> dict:
    canonical_calls = example["canonical_tool_calls"]
    if mode == "decomposition":
        return {"steps": build_decomposition(canonical_calls)}
    if mode == "tool_selection":
        return {"selected_tools": [call["tool_name"] for call in canonical_calls]}
    if mode == "parameter_prediction":
        return {"tool_calls": canonical_calls}
    return {
        "tool_calls": canonical_calls,
        "final_answer": render_operator_answer(example, example["gold_final_answer"]),
    }


def convert_example(example: dict, mode: str) -> dict:
    return {
        "scenario_id": example["scenario_id"],
        "split": example["split"],
        "task_family": example["task_family"],
        "site_id": example["site_id"],
        "messages": [
            {"role": "system", "content": system_prompt(mode)},
            {"role": "user", "content": user_prompt(example, mode)},
            {"role": "assistant", "content": json.dumps(assistant_target(example, mode), ensure_ascii=False)},
        ],
        "query": example["query"],
        "canonical_tool_calls": example["canonical_tool_calls"],
        "gold_final_answer": example["gold_final_answer"],
        "task_accomplish_verifier": example["task_accomplish_verifier"],
        "metadata": example["metadata"],
        "mode": mode,
    }


def export_taskbench_training(benchmark_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    modes = ["decomposition", "tool_selection", "parameter_prediction", "full_agent"]
    splits = ["train", "dev", "test"]
    summary: dict[str, dict[str, int]] = {mode: {} for mode in modes}

    for mode in modes:
        mode_dir = out_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        for split in splits:
            source = benchmark_dir / f"{split}.jsonl"
            rows = [convert_example(example, mode) for example in load_jsonl(source)]
            summary[mode][split] = len(rows)
            with (mode_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    (out_dir / "canonical_tool_registry.json").write_text(
        json.dumps(SCENARIO_TOOL_REGISTRY, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
