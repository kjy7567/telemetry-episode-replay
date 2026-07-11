#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd
from openai import OpenAI

from bts_agentbench.bts_e2e import (
    FOLLOWUP_PROMPT,
    RATIONALE_FOLLOWUP_PROMPT,
    STOP_ACK,
    DeterministicBtsUserSimulator,
    evidence_followup_prompt,
    load_jsonl,
)
from bts_agentbench.evaluator import verify_prediction
from bts_agentbench.operator_answer import check_evidence_followup, check_rationale_followup
from bts_agentbench.runtime import ExecutedCall, ToolStoreRuntime


SYSTEM_PROMPT = """You are a building-operations agent in a multi-turn conversation with an operator.

You may do exactly one of the following at each assistant turn:
1. Ask the operator a short clarification question if a required site, date, week, month, or timestamp is missing.
2. Call exactly one tool when you need grounded data.
3. Answer the operator in concise natural language once you have enough information.

Additional rules:
- Tool outputs are returned in tool messages after a function call.
- Do not wrap the final operator answer in JSON.
- When a prior tool output already contains a `call_id`, `stream_id`, or `stream_ids`, you may refer to it in later tool arguments as `$e1.stream_id` or `$e1.stream_ids` instead of copying long values.
- Use the exact tool schemas below. Do not invent alias argument names such as `date_range_start` or `date_range_end`.
- If the operator has already supplied a required site, date, week, month, or timestamp in the conversation, do not ask for that same clarification again.
- For any tool argument named `timestamp`, `window_start`, or `window_end`, include an explicit UTC offset such as `Z` or `+00:00`.
- After `aggregate_window`, answer with the returned `mean_value`.
- Calendar windows are half-open. For a month, use the first day of that month as `window_start` and the first day of the next month as `window_end`; do not use the last day of the month as `window_end`.
- For ranking questions, first call `list_points`, then call `rank_window` with the returned `stream_ids`; never answer from `list_points` alone.
- After `rank_window`, answer with `ranked_streams[0]` and include the exact winning `stream_id` and its `mean_value` when the operator asks which stream is top.
- For `list_points`, `location_type` is a category such as `Location`, `Room`, or `Conference_Room`; never use a site id such as `BTS_A`, `BTS_B`, or `BTS_C` as `location_type`.
- If the task already identifies a single point target by site, point class, and equipment or zone label, prefer `resolve_point` directly. Reserve `list_points` for ranking or search tasks that truly require multiple candidates.
- For timestamp questions, first try `lookup_observation` with `mode: "exact"` unless the operator explicitly asks for a nearby reading.
- After `lookup_observation` with `mode: "exact"`, report the exact returned value. Preserve fractional seconds in timestamps. If the tool result says `exact_match_found` is false and the task still asks for the requested reading, you may call `lookup_observation` again with `mode: "nearest"` and report the nearest logged reading together with the fallback reason.
- For nearby-reading tasks, report the returned nearby timestamp and value and make it explicit that there was no exact logged reading at the requested time.
- `inspect_quality` is a stream-level quality summary. `inspect_quality_window` is the time-bounded quality tool for a specific day, week, or month.
- For any day-, week-, or month-bounded quality comparison, use `inspect_quality_window`; do not substitute `inspect_quality`.
- For quality-gate questions, call `inspect_quality_window` for the requested week after resolving the point. Use its `quality_reference`: abstain if `observed_fraction` is below `abstain_observed_fraction_below` or `gap_ratio` is above `abstain_gap_ratio_above`; answer only if the metrics satisfy the answer thresholds. Explicitly say either "I would answer" or "I would abstain" and cite observed coverage and gap ratio.
- If the operator asks which stream or point you used, answer directly in natural language and include the exact stream_id you actually used.
- If the operator asks why you would answer or abstain, explain the quality-based reason directly in natural language.
- If a time reference is missing, ask for the specific missing unit the task needs, such as a date, week, month, time window, or exact timestamp. For timestamp tasks, ask for the exact timestamp including fractional seconds if available.
- After a tool call, if the current operator question is now answerable, answer it immediately before doing anything else.
- Do not invent stream_ids, timestamps, or values.
"""

GEMINI_SYSTEM_APPEND = """

Gemini-specific execution rules:
- Read the entire visible conversation before asking for clarification. If a site like BTS_A, BTS_B, or BTS_C is already present anywhere in the conversation, do not ask for site_id again.
- If a specific date, week beginning, month, or timestamp is already present anywhere in the conversation, do not ask for that same time reference again.
- Never emit more than one function call in a single assistant message. If you think you need multiple tools, call only the first tool and wait for its result.
- Never batch two resolve_point calls in one message.
- Never batch two inspect_quality_window calls in one message.
- For phrases like "around 23:56 UTC" or "around 00:21 UTC", first call lookup_observation with mode "exact". Only call mode "nearest" after the exact call returns exact_match_found = false.
- For quality decisions, always report the raw decimal observed_fraction and the raw decimal gap_ratio from the tool output. Do not convert observed_fraction into percentages.
- For answer-versus-abstain decisions, explicitly include either "I would answer" or "I would abstain".
- Keep each natural-language answer to at most two short sentences unless the user explicitly asks for a reason.
- Do not continue explaining after you have already answered the current user turn.
- When the operator asks the evidence follow-up, answer only that evidence question, include the exact stream_id you used, and then stop after the operator's acknowledgement.
- If the operator asks the rationale follow-up, answer only that rationale question in one concise sentence and then wait for the next user turn.
- For pairwise comparison tasks, use exactly this order: resolve left stream, resolve right stream, compare the current window, compare the revised window, inspect the relevant quality window(s), then answer.
- For ranking tasks, use exactly this order: list_points, rank the current month, rank the revised month, inspect the revised winner's quality window, then answer.
- For quality-gate tasks, if the first user turn says the site is missing, ask exactly one direct site question such as "Which building or site is this for?" and then wait for the reply.
- For quality-gate tasks, do not use `list_points`. Use `resolve_point` directly after the site is known, then call `inspect_quality_window` for the requested week. If a second week is later requested, call `inspect_quality_window` for that week and compare the quality result in natural language.
"""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "resolve_point",
            "description": "Resolve a single telemetry stream from site, Brick point class, equipment label, and optional location label.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "site_id": {"type": "string", "description": "Building site identifier."},
                    "point_class": {
                        "type": "string",
                        "description": "Exact Brick point class, for example Air_Differential_Pressure_Sensor or Zone_Air_Temperature_Sensor.",
                    },
                    "equipment_label": {
                        "type": "string",
                        "description": "Exact equipment alias from the task, for example BTS_C Zone 005.",
                    },
                    "location_label": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional location alias. Use null when not needed.",
                    },
                },
                "required": ["site_id", "point_class", "equipment_label", "location_label"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_points",
            "description": "List candidate telemetry streams under site and schema constraints.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "site_id": {"type": "string", "description": "Building site identifier."},
                    "point_class": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional Brick point class. Use null when not needed.",
                    },
                    "equipment_type": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional Brick equipment type. Use null when not needed.",
                    },
                    "location_type": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional Brick location type. Use null when not needed.",
                    },
                },
                "required": ["site_id", "point_class", "equipment_type", "location_type"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_window",
            "description": "Aggregate a single stream over a specific time window.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_id": {"type": "string", "description": "Stream identifier or prior-call reference."},
                    "metric": {"type": "string", "description": "Aggregation metric such as mean_value or max_value."},
                    "window_start": {"type": "string", "description": "Inclusive ISO timestamp."},
                    "window_end": {"type": "string", "description": "Exclusive ISO timestamp."},
                    "period": {"type": "string", "description": "Aggregation period such as day, week, or month."},
                },
                "required": ["stream_id", "metric", "window_start", "window_end", "period"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_window",
            "description": "Compare two streams over the same time window using a selected metric.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "left_stream_id": {"type": "string", "description": "Left stream identifier or prior-call reference."},
                    "right_stream_id": {"type": "string", "description": "Right stream identifier or prior-call reference."},
                    "metric": {"type": "string", "description": "Comparison metric such as mean_value."},
                    "window_start": {"type": "string", "description": "Inclusive ISO timestamp."},
                    "window_end": {"type": "string", "description": "Exclusive ISO timestamp."},
                    "period": {"type": "string", "description": "Comparison period such as day, week, or month."},
                },
                "required": ["left_stream_id", "right_stream_id", "metric", "window_start", "window_end", "period"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_window",
            "description": "Rank a set of candidate streams over a selected time window.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_ids": {
                        "anyOf": [
                            {"type": "string", "description": "Prior-call reference such as $e1.stream_ids."},
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Explicit list of stream identifiers.",
                            },
                        ]
                    },
                    "metric": {"type": "string", "description": "Ranking metric such as mean_value or std_value."},
                    "window_start": {"type": "string", "description": "Inclusive ISO timestamp."},
                    "window_end": {"type": "string", "description": "Exclusive ISO timestamp."},
                    "period": {"type": "string", "description": "Ranking period such as day, week, or month."},
                    "order": {"type": "string", "description": "Ranking order, typically asc or desc."},
                    "topk": {"type": "integer", "description": "Number of returned ranked items."},
                },
                "required": ["stream_ids", "metric", "window_start", "window_end", "period", "order", "topk"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_observation",
            "description": "Retrieve an observation at an exact timestamp or nearest fallback.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_id": {"type": "string", "description": "Stream identifier or prior-call reference."},
                    "timestamp": {"type": "string", "description": "Requested ISO timestamp."},
                    "mode": {"type": "string", "enum": ["exact", "nearest"], "description": "Lookup mode."},
                },
                "required": ["stream_id", "timestamp", "mode"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_quality",
            "description": "Inspect coverage, missingness, and gap statistics for a stream.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_id": {"type": "string", "description": "Stream identifier or prior-call reference."},
                },
                "required": ["stream_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_quality_window",
            "description": "Inspect coverage and gap statistics for a stream over a specific time window.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_id": {"type": "string", "description": "Stream identifier or prior-call reference."},
                    "window_start": {"type": "string", "description": "Inclusive ISO timestamp."},
                    "window_end": {"type": "string", "description": "Exclusive ISO timestamp."},
                    "period": {"type": "string", "description": "Window period such as day, week, or month."},
                },
                "required": ["stream_id", "window_start", "window_end", "period"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_BY_NAME: dict[str, dict[str, Any]] = {tool["function"]["name"]: tool for tool in TOOLS}

FAMILY_TOOL_NAMES: dict[str, list[str]] = {
    "point_disambiguation": ["resolve_point", "lookup_observation", "inspect_quality_window"],
    "day_mean_lookup": ["resolve_point", "aggregate_window", "lookup_observation", "inspect_quality_window"],
    "relative_24h_mean_lookup": ["resolve_point", "aggregate_window", "lookup_observation", "inspect_quality_window"],
    "window_mean_lookup": ["resolve_point", "aggregate_window", "lookup_observation", "inspect_quality_window"],
    "timestamp_value_lookup": ["resolve_point", "lookup_observation", "inspect_quality_window"],
    "timestamp_nearest_lookup": ["resolve_point", "lookup_observation", "inspect_quality_window"],
    "quality_gate": ["resolve_point", "inspect_quality_window"],
    "window_pairwise_compare": ["resolve_point", "compare_window", "lookup_observation", "inspect_quality_window"],
    "window_rank": ["list_points", "rank_window", "inspect_quality_window"],
}


def tools_for_example(example: dict[str, Any]) -> list[dict[str, Any]]:
    names = FAMILY_TOOL_NAMES.get(example.get("task_family"))
    if not names:
        return TOOLS
    return [TOOL_BY_NAME[name] for name in names]


def family_system_append(example: dict[str, Any]) -> str:
    family = example.get("task_family")
    if family == "quality_gate":
        return """

Family-specific execution rules for this scenario:
- This is a single-point quality-gate task. Do not call list_points.
- After site clarification, call resolve_point first, then inspect_quality_window for the requested week.
- If a second week is later requested, call inspect_quality_window for that second week and compare the two weekly quality outcomes in natural language.
- If resolve_point returns no matches, do not emit an empty assistant turn. Ask one short clarification question or retry resolve_point with a Brick-style point class.
"""
    if family == "point_disambiguation":
        return """

Family-specific execution rules for this scenario:
- This is a single-point revision task. Do not call list_points.
- If the first user turn only lacks the site, ask only for the site. Do not ask for any extra time clarification in the same turn.
- Resolve the initially requested point first.
- After the initial resolve_point call, answer the current user turn with the exact stream_id to use.
- After the operator revises the target point, resolve the revised point next.
- After the revised resolve_point call, answer that revised user turn with the exact revised stream_id to use.
- After resolving the revised point, do the requested timestamp lookup, then inspect the revised point's quality window, then answer the current user turn.
- Do not emit an empty assistant turn after resolve_point. Either call the next tool or answer the current user turn.
"""
    if family == "window_pairwise_compare":
        return """

Family-specific execution rules for this scenario:
- Resolve the left stream, resolve the right stream, compare the current window, compare the revised window, perform the required timestamp lookup for the revised comparison context, inspect the relevant quality window, then answer.
- You do have access to `lookup_observation` in this family; use it when the scenario asks for the timestamped reading after the revised comparison.
- After the revised-window comparison, do not stop. Continue into the timestamp lookup and then the quality tool before making the final answer-versus-abstain decision.
- If you have already answered the current-window comparison, continue with the revised comparison flow instead of repeating the earlier result.
- If the operator says `compare the previous week`, call compare_window for that revised week immediately without asking for another clarification.
- If the operator says `around ... UTC`, use the winning stream from the revised comparison and do the timestamp lookup flow immediately.
- Do not emit an empty assistant turn after a tool result. Either call the next tool or answer the current user turn.
"""
    if family == "window_rank":
        return """

Family-specific execution rules for this scenario:
- Use the exact Brick point class string `Position_Sensor` when calling list_points for this family.
- When the operator asks the evidence follow-up, cite the exact winning stream_id from the revised month that you used for the final reportability decision.
- The evidence follow-up answer for this family must contain only that exact revised-month winner stream_id and no other competing stream_id.
"""
    return ""


def blank_retry_instruction(
    example: dict[str, Any],
    current_user_prompt: str,
    executed_calls: list[ExecutedCall],
    phase_answer_texts: list[str],
) -> str:
    family = example.get("task_family")
    if family == "point_disambiguation":
        if len(executed_calls) == 1 and len(phase_answer_texts) == 0:
            return (
                "Continue the current task. You already resolved the initial point. "
                "Answer the operator's current question now by naming the exact stream_id to use."
            )
        if current_user_prompt in set(example.get("goal_revision_turns", [])):
            return (
                "Continue the goal-revision flow. Do not stop. "
                "Use the existing site and prior state, perform the next required tool step for this revision, or answer it directly if the required tool result is already available."
            )
    if family == "window_pairwise_compare":
        if current_user_prompt in set(example.get("goal_revision_turns", [])):
            return (
                "Continue the goal-revision flow for the pairwise task. "
                "Do not stop after the current-window comparison. "
                "Use the previously resolved streams and perform the next required revised-step tool call or answer."
            )
    if family == "window_rank" and current_user_prompt == evidence_followup_prompt(example):
        return (
            "Answer the evidence follow-up directly. "
            "Cite only the exact winning stream_id from the revised month used for the final reportability decision. "
            "Do not mention any other stream_id."
        )
    return (
        "Continue the current task. "
        "Do not emit an empty assistant message. "
        "Return exactly one of: one short clarification question, one tool call, or one short answer to the current user turn."
    )


def select_subset(rows: list[dict[str, Any]], max_per_mode: int, max_scenarios: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    selected_ids: set[str] = set()
    for row in rows:
        mode = row["interaction_mode"]
        if counts.get(mode, 0) >= max_per_mode:
            continue
        selected.append(row)
        selected_ids.add(row["scenario_id"])
        counts[mode] = counts.get(mode, 0) + 1
        if len(selected) >= max_scenarios:
            break
    if len(selected) < max_scenarios:
        for row in rows:
            if row["scenario_id"] in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row["scenario_id"])
            if len(selected) >= max_scenarios:
                break
    return selected
def evidence_followup_ok(
    example: dict[str, Any],
    answer_text: str,
    executed_calls: list[ExecutedCall],
    runtime: ToolStoreRuntime | None = None,
) -> bool:
    return check_evidence_followup(example, answer_text, executed_calls, runtime)


def minimum_turn_budget(example: dict[str, Any]) -> int:
    phase_count = len(example.get("phase_examples") or [])
    if phase_count == 0:
        phase_count = 1 + len(example.get("goal_revision_turns", []))
    canonical_tool_calls = len(example.get("canonical_tool_calls", []))
    clarification_turns = len(example.get("required_clarification_slots", []))
    evidence_turns = 1 if example.get("post_answer_user_turns") else 0
    rationale_turns = 1 if example.get("interaction_verifier", {}).get("require_rationale_followup", False) else 0
    return canonical_tool_calls + phase_count + clarification_turns + evidence_turns + rationale_turns + 1


def _normalize_utc_argument(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return value
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def normalize_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    for key in ("timestamp", "window_start", "window_end"):
        if key in normalized:
            normalized[key] = _normalize_utc_argument(normalized[key])
    return normalized


def rationale_followup_ok(
    example: dict[str, Any],
    answer_text: str,
    runtime: ToolStoreRuntime | None = None,
) -> bool:
    return check_rationale_followup(example, answer_text, runtime)


class OpenAIChatToolModel:
    def __init__(
        self,
        model_id: str,
        api_key_env: str,
        base_url: str | None,
        provider: str,
        max_completion_tokens: int,
        reasoning_effort: str | None,
        verbosity: str | None,
        temperature: float | None,
        seed: int | None,
        service_tier: str | None,
        timeout_seconds: float,
    ):
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"missing_api_key_env:{api_key_env}")
        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_seconds}
        if base_url not in {None, "", "none", "null"}:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.provider = provider
        self.model_id = model_id
        self.max_completion_tokens = max_completion_tokens
        self.base_url = None if base_url in {None, "", "none", "null"} else base_url
        self.reasoning_effort = None if reasoning_effort in {None, "", "none", "null"} else reasoning_effort
        self.verbosity = None if verbosity in {None, "", "none", "null"} else verbosity
        self.temperature = None if self.model_id.startswith("gpt-5.5") and temperature == 0.0 else temperature
        self.seed = seed
        self.service_tier = service_tier
        self.timeout_seconds = timeout_seconds

    def _effective_max_completion_tokens(self) -> int:
        if (
            self.base_url is not None
            and "openrouter.ai" in self.base_url
            and self.model_id == "google/gemini-3.1-pro-preview"
            and self.max_completion_tokens < 1536
        ):
            return 1536
        return self.max_completion_tokens

    @staticmethod
    def _retry_delay_seconds(exc: Exception) -> float | None:
        text = str(exc)
        match = re.search(r"retry in ([0-9]+(?:\\.[0-9]+)?)s", text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1)) + 1.0
        if "rate limit" in text.lower() or "resource_exhausted" in text.lower():
            return 5.0
        return None

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "tools": TOOLS if tools is None else tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_completion_tokens": self._effective_max_completion_tokens(),
        }
        if self.provider != "gemini":
            kwargs["store"] = False
        if self.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.verbosity is not None and self.provider != "gemini":
            kwargs["verbosity"] = self.verbosity
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.seed is not None and self.provider != "gemini":
            kwargs["seed"] = self.seed
        if self.service_tier is not None and self.provider != "gemini":
            kwargs["service_tier"] = self.service_tier
        attempts = 0
        while True:
            attempts += 1
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                delay = self._retry_delay_seconds(exc)
                if delay is None or attempts >= 4:
                    raise
                time.sleep(delay)

    def system_prompt(self) -> str:
        if self.provider == "gemini":
            return SYSTEM_PROMPT + GEMINI_SYSTEM_APPEND
        return SYSTEM_PROMPT


def _usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
        }
    completion_details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = int(getattr(completion_details, "reasoning_tokens", 0) or 0)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "reasoning_tokens": reasoning_tokens,
    }


def run_e2e_example(
    model: OpenAIChatToolModel,
    runtime: ToolStoreRuntime,
    example: dict[str, Any],
    max_turns: int,
) -> dict[str, Any]:
    simulator = DeterministicBtsUserSimulator(example)
    active_tools = tools_for_example(example)
    system_prompt = model.system_prompt() + family_system_append(example)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    user_message = simulator.reset()
    messages.append({"role": "user", "content": user_message})

    executed_calls: list[ExecutedCall] = []
    raw_responses: list[str] = []
    final_answer_text: str | None = None
    phase_answer_texts: list[str] = []
    evidence_answer_text: str | None = None
    rationale_answer_text: str | None = None
    parse_error: str | None = None
    required_slots = set(example.get("required_clarification_slots", []))
    answered_slots: set[str] = set()
    pretermination_issues: list[str] = []
    turn_usage: list[dict[str, int]] = []
    current_user_prompt = user_message
    empty_retry_budget = 2

    def all_required_clarifications_answered() -> bool:
        return required_slots.issubset(answered_slots)

    effective_max_turns = max(max_turns, minimum_turn_budget(example))

    for _ in range(effective_max_turns):
        try:
            response = model.complete(messages, tools=active_tools)
        except Exception as exc:
            parse_error = f"api_error:{type(exc).__name__}:{exc}"
            break

        turn_usage.append(_usage_dict(response))
        choice = response.choices[0].message
        tool_calls = list(choice.tool_calls or [])

        if not tool_calls and not (choice.content or "").strip() and model.provider == "gemini" and empty_retry_budget > 0:
            empty_retry_budget -= 1
            retry_messages = list(messages)
            retry_messages.append(
                {
                    "role": "system",
                    "content": blank_retry_instruction(
                        example,
                        current_user_prompt,
                        executed_calls,
                        phase_answer_texts,
                    ),
                }
            )
            try:
                response = model.complete(retry_messages, tools=active_tools)
            except Exception as exc:
                parse_error = f"api_error:{type(exc).__name__}:{exc}"
                break
            turn_usage.append(_usage_dict(response))
            choice = response.choices[0].message
            tool_calls = list(choice.tool_calls or [])

        if tool_calls:
            if len(tool_calls) > 1:
                pretermination_issues.append("parallel_tool_calls_returned")
            if required_slots and not all_required_clarifications_answered():
                pretermination_issues.append("premature_tool_before_required_clarification")

            assistant_tool_calls: list[dict[str, Any]] = []
            for tool_call in tool_calls[:1]:
                tool_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    parse_error = f"tool_argument_json_error:{tool_name}:{type(exc).__name__}"
                    break
                if not isinstance(arguments, dict):
                    parse_error = "invalid_tool_call_payload"
                    break

                raw_responses.append(
                    json.dumps(
                        {"tool_name": tool_name, "arguments": arguments},
                        ensure_ascii=False,
                    )
                )
                try:
                    call_outputs = {call.call_id: call.output for call in executed_calls}
                    resolved_arguments = runtime._replace_refs(arguments, call_outputs)
                    resolved_arguments = normalize_tool_arguments(resolved_arguments)
                    output = runtime.execute_tool(tool_name, resolved_arguments)
                except Exception as exc:
                    parse_error = f"tool_execution_error:{tool_name}:{type(exc).__name__}"
                    break

                call = ExecutedCall(
                    call_id=f"e{len(executed_calls)+1}",
                    tool_name=tool_name,
                    arguments=resolved_arguments,
                    output=output,
                )
                executed_calls.append(call)
                assistant_tool_call = {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
                if getattr(tool_call, "model_extra", None):
                    extra_content = tool_call.model_extra.get("extra_content")
                    if extra_content is not None:
                        assistant_tool_call["extra_content"] = extra_content
                assistant_tool_calls.append(assistant_tool_call)
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": assistant_tool_calls,
                }
                if choice.content not in {None, ""}:
                    assistant_message["content"] = choice.content
                messages.append(assistant_message)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": ToolStoreRuntime.compact_observation(call),
                    }
                )
            if parse_error:
                break
            continue

        text = (choice.content or "").strip()
        raw_responses.append(text)
        if not text:
            parse_error = "empty_assistant_message"
            break

        expected_followup = example.get("followup_prompt", evidence_followup_prompt(example))
        if DeterministicBtsUserSimulator._looks_like_question(text):
            if not required_slots and not executed_calls and final_answer_text is None:
                pretermination_issues.append("unnecessary_clarification")
            elif required_slots and all_required_clarifications_answered() and not executed_calls and final_answer_text is None:
                pretermination_issues.append("redundant_clarification")

        if current_user_prompt == expected_followup:
            evidence_answer_text = text
        elif current_user_prompt == RATIONALE_FOLLOWUP_PROMPT:
            rationale_answer_text = text
        else:
            final_answer_text = text
            if not DeterministicBtsUserSimulator._looks_like_question(text):
                phase_answer_texts.append(text)

        messages.append({"role": "assistant", "content": text})
        reply = simulator.step(text)
        if reply is None:
            break
        messages.append({"role": "user", "content": reply})
        current_user_prompt = reply

        if reply == STOP_ACK:
            break
        for slot, answer in example.get("clarification_answers", {}).items():
            if reply == answer:
                answered_slots.add(slot)

    verification = verify_prediction(
        example,
        executed_calls,
        final_answer_text,
        None,
        runtime,
        phase_answers=phase_answer_texts,
    )

    interaction_issues: list[str] = []
    interaction_issues.extend(pretermination_issues)
    if required_slots and not all_required_clarifications_answered():
        for slot in sorted(required_slots - answered_slots):
            interaction_issues.append(f"missing_required_clarification:{slot}")
    if example.get("interaction_verifier", {}).get("require_rationale_followup", False):
        if rationale_answer_text is None:
            interaction_issues.append("missing_rationale_followup_answer")
        elif not rationale_followup_ok(example, rationale_answer_text, runtime):
            interaction_issues.append("invalid_rationale_followup_answer")
    if example.get("interaction_verifier", {}).get("require_goal_revision_answer", False):
        expected_phase_answers = 1 + len(example.get("goal_revision_turns", []))
        if len(phase_answer_texts) < expected_phase_answers:
            interaction_issues.append("missing_goal_revision_answer")
    if evidence_answer_text is None:
        interaction_issues.append("missing_evidence_followup_answer")
    elif not evidence_followup_ok(example, evidence_answer_text, executed_calls, runtime):
        interaction_issues.append("invalid_evidence_followup_answer")
    if current_user_prompt != STOP_ACK:
        interaction_issues.append("conversation_not_terminated")
    if parse_error:
        interaction_issues.append(parse_error)

    protocol_ok = not interaction_issues
    accomplished = verification.task_ok and protocol_ok
    strict_accomplished = verification.process_ok and verification.task_ok and protocol_ok
    if accomplished:
        e2e_label = "accomplished"
    elif verification.task_score > 0.0 or interaction_issues:
        e2e_label = "partially_accomplished"
    else:
        e2e_label = "not_accomplished"
    if strict_accomplished:
        strict_label = "accomplished"
    elif verification.strict_label != "not_accomplished" or interaction_issues:
        strict_label = "partially_accomplished"
    else:
        strict_label = "not_accomplished"

    usage_totals = {
        key: sum(item[key] for item in turn_usage)
        for key in ["prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"]
    }

    return {
        "scenario_id": example["scenario_id"],
        "task_family": example["task_family"],
        "interaction_mode": example["interaction_mode"],
        "messages": messages,
        "raw_responses": raw_responses,
        "executed_calls": [call.as_dict() for call in executed_calls],
        "final_answer_text": final_answer_text,
        "phase_answer_texts": phase_answer_texts,
        "rationale_answer_text": rationale_answer_text,
        "evidence_answer_text": evidence_answer_text,
        "static_verification": verification.as_dict(),
        "interaction_issues": interaction_issues,
        "protocol_ok": protocol_ok,
        "strict_label": strict_label,
        "label": e2e_label,
        "usage": usage_totals,
        "turn_usage": turn_usage,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="gpt-5.5")
    parser.add_argument("--benchmark-dir", type=Path, required=True)
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--split", type=str, choices=["dev", "test"], default="dev")
    parser.add_argument("--out-jsonl", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-completion-tokens", type=int, default=512)
    parser.add_argument("--max-per-mode", type=int, default=2)
    parser.add_argument("--max-scenarios", type=int, default=20)
    parser.add_argument("--reasoning-effort", type=str, default="medium")
    parser.add_argument("--verbosity", type=str, choices=["low", "medium", "high"], default="low")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--service-tier", type=str, choices=["auto", "default", "flex", "scale", "priority"], default=None)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--api-key-env", type=str, default="OPENAI_API_KEY")
    parser.add_argument("--base-url", type=str, default=None)
    parser.add_argument("--provider", type=str, choices=["openai", "gemini"], default="openai")
    parser.add_argument(
        "--family",
        action="append",
        default=[],
        help="Restrict evaluation to one or more task families.",
    )
    args = parser.parse_args()

    rows = load_jsonl(args.benchmark_dir / f"{args.split}.jsonl")
    if args.family:
        requested_families = set(args.family)
        rows = [row for row in rows if row.get("task_family") in requested_families]
    selected = select_subset(rows, max_per_mode=args.max_per_mode, max_scenarios=args.max_scenarios)

    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    model = OpenAIChatToolModel(
        model_id=args.model,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        provider=args.provider,
        max_completion_tokens=args.max_completion_tokens,
        reasoning_effort=args.reasoning_effort,
        verbosity=args.verbosity,
        temperature=args.temperature,
        seed=args.seed,
        service_tier=args.service_tier,
        timeout_seconds=args.timeout_seconds,
    )
    runtime = ToolStoreRuntime(args.tool_store_db)
    predictions: list[dict[str, Any]] = []
    try:
        with args.out_jsonl.open("w", encoding="utf-8") as handle:
            total = len(selected)
            for idx, row in enumerate(selected, start=1):
                prediction = run_e2e_example(model, runtime, row, args.max_turns)
                predictions.append(prediction)
                handle.write(json.dumps(prediction, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"[progress] {idx}/{total} {row['scenario_id']} {prediction['label']}", flush=True)
    finally:
        runtime.close()

    labels = Counter(row["label"] for row in predictions)
    runner_path = Path(__file__)
    benchmark_path = args.benchmark_dir / f"{args.split}.jsonl"
    prompt_hashes = {
        family: hashlib.sha256(
            (model.system_prompt() + family_system_append(row)).encode("utf-8")
        ).hexdigest()
        for family in sorted({str(row["task_family"]) for row in selected})
        for row in [next(item for item in selected if str(item["task_family"]) == family)]
    }
    summary = {
        "model": args.model,
        "split": args.split,
        "run_config": {
            "provider": args.provider,
            "base_url": args.base_url,
            "api_key_env": args.api_key_env,
            "families": sorted(args.family),
            "max_turns": args.max_turns,
            "row_minimum_turn_budget_enabled": True,
            "max_completion_tokens": args.max_completion_tokens,
            "effective_max_completion_tokens": model._effective_max_completion_tokens(),
            "max_per_mode": args.max_per_mode,
            "max_scenarios": args.max_scenarios,
            "reasoning_effort": args.reasoning_effort,
            "verbosity": args.verbosity,
            "temperature": args.temperature,
            "seed": args.seed,
            "service_tier": args.service_tier,
            "timeout_seconds": args.timeout_seconds,
            "parallel_tool_calls": False,
            "runner_sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
            "benchmark_split_sha256": hashlib.sha256(benchmark_path.read_bytes()).hexdigest(),
            "system_prompt_sha256_by_family": prompt_hashes,
        },
        "scenario_count": len(predictions),
        "counts": dict(labels),
        "strict_counts": dict(Counter(row["strict_label"] for row in predictions)),
        "mean_process_score": round(mean(row["static_verification"]["process_score"] for row in predictions), 4),
        "mean_final_score": round(mean(row["static_verification"]["final_score"] for row in predictions), 4),
        "mean_evidence_score": round(mean(row["static_verification"]["evidence_score"] for row in predictions), 4),
        "mean_core_score": round(mean(row["static_verification"]["core_score"] for row in predictions), 4),
        "mean_reporting_score": round(mean(row["static_verification"]["reporting_score"] for row in predictions), 4),
        "mean_grounding_score": round(mean(row["static_verification"]["grounding_score"] for row in predictions), 4),
        "mean_temporal_score": round(mean(row["static_verification"]["temporal_score"] for row in predictions), 4),
        "mean_phase_score": round(mean(row["static_verification"]["phase_score"] for row in predictions), 4),
        "mean_task_score": round(mean(row["static_verification"]["task_score"] for row in predictions), 4),
        "protocol_success_count": sum(1 for row in predictions if row["protocol_ok"]),
        "task_success_count": sum(1 for row in predictions if row["static_verification"]["task_ok"]),
        "static_strict_success_count": sum(1 for row in predictions if row["static_verification"]["strict_label"] == "accomplished"),
        "interaction_issue_counts": dict(Counter(issue for row in predictions for issue in row["interaction_issues"])),
        "usage": {
            "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in predictions),
            "completion_tokens": sum(row["usage"]["completion_tokens"] for row in predictions),
            "total_tokens": sum(row["usage"]["total_tokens"] for row in predictions),
            "reasoning_tokens": sum(row["usage"]["reasoning_tokens"] for row in predictions),
            "mean_total_tokens_per_scenario": round(mean(row["usage"]["total_tokens"] for row in predictions), 2),
        },
        "by_mode": {
            mode: {
                "count": sum(1 for row in predictions if row["interaction_mode"] == mode),
                "accomplished": sum(1 for row in predictions if row["interaction_mode"] == mode and row["label"] == "accomplished"),
                "strict_accomplished": sum(1 for row in predictions if row["interaction_mode"] == mode and row["strict_label"] == "accomplished"),
            }
            for mode in sorted({row["interaction_mode"] for row in predictions})
        },
    }
    args.out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
