#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
STREAM_ID = re.compile(
    r"^[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}$"
)


def load_scenario(directory: Path, scenario_id: str) -> dict[str, Any]:
    for split in SPLITS:
        path = directory / f"{split}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("scenario_id") == scenario_id:
                    return row
    raise KeyError(f"scenario not found: {scenario_id}")


def load_submitted_scenario(bundle: Path, scenario_id: str) -> dict[str, Any]:
    with zipfile.ZipFile(bundle) as archive:
        for split in SPLITS:
            member = f"dataset_bundle/bts_agentbench_532/{split}.jsonl"
            for line in archive.read(member).splitlines():
                row = json.loads(line)
                if row.get("scenario_id") == scenario_id:
                    return row
    raise KeyError(f"scenario not found in submitted bundle: {scenario_id}")


def canonical_digest(row: dict[str, Any]) -> str:
    payload = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def raw_lineage(path: Path, stream_ids: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row.get("stream_id") in stream_ids
        ]


def collect_stream_ids(*values: Any) -> set[str]:
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if "stream_id" in key:
                    if isinstance(child, str) and STREAM_ID.fullmatch(child):
                        found.add(child)
                    elif isinstance(child, list):
                        found.update(
                            item
                            for item in child
                            if isinstance(item, str) and STREAM_ID.fullmatch(item)
                        )
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for value in values:
        visit(value)
    return found


def inline_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def display_count(value: str | None) -> str:
    if value in (None, ""):
        return "unknown"
    return f"{int(float(value)):,}"


def build_trace(
    *,
    scenario_id: str,
    static: dict[str, Any],
    final: dict[str, Any],
    lineage: list[dict[str, str]],
    replay: dict[str, Any] | None,
) -> dict[str, Any]:
    replay_check = None
    if replay is not None:
        replay_check = {
            "submitted_digest": canonical_digest(final),
            "replay_digest": canonical_digest(replay),
            "exact_json_object_match": replay == final,
        }
    return {
        "trace_version": "paper-submission-scenario-trace-v2",
        "scenario_id": scenario_id,
        "raw_sources": lineage,
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
        "agentic_episode": {
            key: final.get(key)
            for key in (
                "interaction_mode",
                "initial_user_message",
                "required_clarification_slots",
                "clarification_answers",
                "goal_revision_turns",
                "post_answer_user_turns",
                "canonical_tool_calls",
                "phase_gold_final_answers",
                "gold_final_answer",
                "evidence",
            )
        },
        "generation_history": final.get("generation_history", []),
        "replay_check": replay_check,
    }


def render_text(trace: dict[str, Any]) -> str:
    static = trace["static_task"]
    episode = trace["agentic_episode"]
    lines = [
        f"SCENARIO: {trace['scenario_id']}",
        f"FAMILY:   {static['task_family']}",
        f"SPLIT:    {static['split']}",
        "",
        "1. RAW TELEMETRY LINEAGE",
    ]
    for source in trace["raw_sources"]:
        lines.extend(
            [
                f"   archive:       {source.get('raw_archive')}",
                f"   member:        {source.get('raw_member_name')}",
                f"   stream_id:     {source.get('stream_id')}",
                f"   point_class:   {source.get('point_class')}",
                f"   equipment:     {source.get('equipment_label')}",
                f"   observations:  {display_count(source.get('raw_n_points'))}",
                f"   time range:    {source.get('raw_first_timestamp')} -> {source.get('raw_last_timestamp')}",
            ]
        )
    if not trace["raw_sources"]:
        lines.append("   no retained lineage row found")

    lines.extend(
        [
            "",
            "2. STATIC EXECUTABLE TASK",
            f"   query: {static['query']}",
            f"   gold:  {inline_json(static['gold_final_answer'])}",
            f"   evidence streams: {inline_json(static['evidence'].get('stream_ids', []))}",
            "",
            "3. GOLD TOOL TRACE",
        ]
    )
    for index, call in enumerate(episode["canonical_tool_calls"], start=1):
        lines.append(
            f"   C{index}. {call['tool_name']}({inline_json(call.get('arguments', {}))})"
        )

    lines.extend(
        [
            "",
            "4. AGENTIC INTERACTION CONTRACT",
            f"   USER: {episode['initial_user_message']}",
        ]
    )
    for slot in episode.get("required_clarification_slots") or []:
        answer = (episode.get("clarification_answers") or {}).get(slot)
        lines.append(f"   CLARIFY [{slot}]: {answer}")
    for index, turn in enumerate(episode.get("goal_revision_turns") or [], start=1):
        lines.append(f"   REVISION {index}: {turn}")
    for turn in episode.get("post_answer_user_turns") or []:
        lines.append(f"   EVIDENCE FOLLOW-UP: {turn}")

    lines.extend(["", "5. PHASE GOLD TRACE"])
    for index, gold in enumerate(episode["phase_gold_final_answers"], start=1):
        lines.append(f"   P{index}: {inline_json(gold)}")
    lines.extend(
        [
            "",
            "6. FINAL GOLD",
            f"   action:   {inline_json(episode['gold_final_answer'])}",
            f"   evidence: {inline_json(episode['evidence'])}",
            "",
            "7. DETERMINISTIC CONSTRUCTION STAGES",
        ]
    )
    for index, stage in enumerate(trace.get("generation_history") or [], start=1):
        lines.append(
            f"   {index:02d}. {stage.get('stage')} [{stage.get('status')}]"
        )

    replay = trace.get("replay_check")
    if replay is not None:
        lines.extend(
            [
                "",
                "8. REPLAY CHECK",
                f"   submitted row: {replay['submitted_digest']}",
                f"   replay row:    {replay['replay_digest']}",
                "   exact match:   "
                + ("YES" if replay["exact_json_object_match"] else "NO"),
            ]
        )
    return "\n".join(lines) + "\n"


def render_markdown(trace: dict[str, Any]) -> str:
    static = trace["static_task"]
    episode = trace["agentic_episode"]
    lines = [
        f"# Replay Trace: `{trace['scenario_id']}`",
        "",
        f"- Family: `{static['task_family']}`",
        f"- Split: `{static['split']}`",
        "",
        "## 1. Raw Telemetry Lineage",
        "",
        "| Archive | Member | Stream | Point | Equipment | Observations | Range |",
        "|---|---|---|---|---|---:|---|",
    ]
    for source in trace["raw_sources"]:
        lines.append(
            f"| `{source.get('raw_archive')}` | `{source.get('raw_member_name')}` | "
            f"`{source.get('stream_id')}` | `{source.get('point_class')}` | "
            f"{source.get('equipment_label')} | {display_count(source.get('raw_n_points'))} | "
            f"`{source.get('raw_first_timestamp')}` to `{source.get('raw_last_timestamp')}` |"
        )
    if not trace["raw_sources"]:
        lines.append("| not found | | | | | | |")

    lines.extend(
        [
            "",
            "## 2. Static Executable Task",
            "",
            f"> {static['query']}",
            "",
            "```json",
            json.dumps(static["gold_final_answer"], indent=2, ensure_ascii=False),
            "```",
            "",
            "## 3. Gold Tool Trace",
            "",
        ]
    )
    for index, call in enumerate(episode["canonical_tool_calls"], start=1):
        lines.append(
            f"{index}. `{call['tool_name']}({inline_json(call.get('arguments', {}))})`"
        )

    lines.extend(
        [
            "",
            "## 4. Agentic Interaction Contract",
            "",
            f"**Initial user:** {episode['initial_user_message']}",
            "",
        ]
    )
    for slot in episode.get("required_clarification_slots") or []:
        answer = (episode.get("clarification_answers") or {}).get(slot)
        lines.append(f"**Clarification `{slot}`:** {answer}")
        lines.append("")
    for index, turn in enumerate(episode.get("goal_revision_turns") or [], start=1):
        lines.append(f"**Revision {index}:** {turn}")
        lines.append("")
    for turn in episode.get("post_answer_user_turns") or []:
        lines.append(f"**Evidence follow-up:** {turn}")
        lines.append("")

    lines.extend(["## 5. Phase Gold Trace", ""])
    for index, gold in enumerate(episode["phase_gold_final_answers"], start=1):
        lines.extend(
            [
                f"**P{index}**",
                "",
                "```json",
                json.dumps(gold, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## 6. Final Gold",
            "",
            f"- Action: `{inline_json(episode['gold_final_answer'])}`",
            f"- Evidence: `{inline_json(episode['evidence'])}`",
            "",
            "## 7. Deterministic Construction Stages",
            "",
            "| Step | Stage | Status |",
            "|---:|---|---|",
        ]
    )
    for index, stage in enumerate(trace.get("generation_history") or [], start=1):
        lines.append(
            f"| {index} | `{stage.get('stage')}` | `{stage.get('status')}` |"
        )
    replay = trace.get("replay_check")
    if replay is not None:
        lines.extend(
            [
                "",
                "## 8. Replay Check",
                "",
                f"- Submitted row digest: `{replay['submitted_digest']}`",
                f"- Replayed row digest: `{replay['replay_digest']}`",
                "- Complete JSON-object equality: "
                + ("**YES**" if replay["exact_json_object_match"] else "**NO**"),
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show one submitted raw-to-static-to-agentic scenario trace."
    )
    parser.add_argument("scenario_id")
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=REPO_ROOT / "release" / "submitted-static-reference",
        help="Static task directory. Defaults to the exact paper submission.",
    )
    parser.add_argument(
        "--submitted-bundle",
        type=Path,
        default=REPO_ROOT / "release" / "submitted-dataset-bundle.zip",
        help="Bundle containing the exact submitted final episodes.",
    )
    parser.add_argument(
        "--replay-dir",
        type=Path,
        help="Optional reconstructed final directory to compare with the submission.",
    )
    parser.add_argument(
        "--stream-lineage",
        type=Path,
        default=REPO_ROOT / "provenance" / "release_stream_lineage.csv",
    )
    parser.add_argument(
        "--format", choices=("text", "markdown", "json"), default="text"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    static = load_scenario(args.static_dir, args.scenario_id)
    submitted = load_submitted_scenario(args.submitted_bundle, args.scenario_id)
    replay = (
        load_scenario(args.replay_dir, args.scenario_id)
        if args.replay_dir is not None
        else None
    )
    stream_ids = collect_stream_ids(static, submitted)
    trace = build_trace(
        scenario_id=args.scenario_id,
        static=static,
        final=submitted,
        lineage=raw_lineage(args.stream_lineage, stream_ids),
        replay=replay,
    )
    if args.format == "json":
        rendered = json.dumps(trace, indent=2, ensure_ascii=False) + "\n"
    elif args.format == "markdown":
        rendered = render_markdown(trace)
    else:
        rendered = render_text(trace)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
