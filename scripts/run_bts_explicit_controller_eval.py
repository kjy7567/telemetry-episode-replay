#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd

from bts_agentbench.evaluator import verify_prediction
from bts_agentbench.operator_answer import (
    check_evidence_followup,
    check_rationale_followup,
    render_operator_answer,
)
from bts_agentbench.runtime import ExecutedCall, ToolStoreRuntime
from run_bts_e2e_strong_solver_eval import (
    StrongBtsSolver,
    first_date,
    freeze_time,
    load_jsonl,
    parse_site,
    parse_time_window,
    parse_timestamp,
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def shift_window(start: pd.Timestamp, end: pd.Timestamp, direction: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    delta = end - start
    return start + direction * delta, end + direction * delta


def revision_direction(text: str) -> int:
    lowered = text.lower()
    if "previous" in lowered or "back by" in lowered:
        return -1
    if "next" in lowered or "forward by" in lowered:
        return 1
    raise ValueError(f"unsupported_revision_direction:{text}")


def goal_has_quality_phase(text: str) -> bool:
    lowered = text.lower()
    return "would you answer or abstain" in lowered or "make the same answer-or-abstain decision" in lowered


def goal_has_timestamp_phase(text: str) -> bool:
    lowered = text.lower()
    return "nearest available reading" in lowered or "nearest logged reading" in lowered


def goal_has_quality_preference_phase(text: str) -> bool:
    lowered = text.lower()
    return "first and second" in lowered and "trust more" in lowered and "answer or abstain" in lowered


def goal_has_timestamp_preference_phase(text: str) -> bool:
    lowered = text.lower()
    return "first and second timestamp" in lowered and "trust more" in lowered and "reporting" in lowered


def goal_has_reporting_commitment_phase(text: str) -> bool:
    lowered = text.lower()
    return "report it as-is" in lowered and "abstain" in lowered and (
        "more precise timestamp" in lowered or "narrower time range" in lowered
    )


def goal_has_point_revision(text: str) -> bool:
    lowered = text.lower()
    return "if the operator meant " in lowered and "which stream should i use" in lowered


def revised_target_phrase(text: str) -> str | None:
    match = re.search(r"if the operator meant (.+?) instead", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def parse_quality_week_window(text: str) -> tuple[pd.Timestamp, pd.Timestamp, str] | None:
    start = first_date(text)
    if start is None:
        return None
    return start, start + pd.Timedelta(days=7), "week"


def shift_month_window(start: pd.Timestamp, end: pd.Timestamp, direction: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    return start + pd.DateOffset(months=direction), end + pd.DateOffset(months=direction)


def choose_earlier_or_later(values: list[Any], text: str, key):
    if not values:
        raise ValueError("empty_history")
    lowered = text.lower()
    if "later of the two" in lowered:
        return max(values, key=key)
    if "earlier of the two" in lowered:
        return min(values, key=key)
    raise ValueError(f"unsupported_comparative_reference:{text}")


def clarification_question(slot: str) -> str:
    if slot == "site_id":
        return "Which site should I use?"
    if slot == "time_reference":
        return "Which date, week, month, time window, or exact timestamp should I use?"
    return f"What is the missing {slot}?"


def evidence_text(stream_ids: list[str]) -> str:
    if not stream_ids:
        return ""
    if len(stream_ids) == 1:
        return f"I used stream {stream_ids[0]}."
    joined = ", ".join(stream_ids)
    return f"I used streams {joined}."


def rationale_text(final_answer: dict[str, Any]) -> str:
    decision = final_answer.get("decision")
    coverage = final_answer.get("observed_fraction")
    gap_ratio = final_answer.get("gap_ratio")
    if decision == "abstain":
        return (
            f"I would abstain because the data quality is not reliable enough. "
            f"Observed coverage was {coverage} and the gap ratio was {gap_ratio}."
        )
    return (
        f"I would answer because the data quality looks healthy enough. "
        f"Observed coverage was {coverage} and the gap ratio was {gap_ratio}."
    )


def append_trace(trace: list[dict[str, str]], role: str, content: str | None) -> None:
    if content is None:
        return
    trace.append({"role": role, "content": content})


def is_return_to_original(text: str) -> bool:
    lowered = text.lower()
    return "go back to the original" in lowered or "back to the original" in lowered


def next_call_id_from_executed(executed: list[ExecutedCall]) -> str:
    return f"c{len(executed) + 1}"


@dataclass
class ControllerRun:
    executed_calls: list[ExecutedCall]
    phase_answers: list[dict[str, Any]]
    evidence_stream_ids: list[str]
    rationale_answer_text: str | None
    issues: list[str]
    protocol_trace: list[dict[str, str]]

    @property
    def final_answer(self) -> dict[str, Any] | None:
        return self.phase_answers[-1] if self.phase_answers else None


class ExplicitControllerBase:
    name = "base"
    approximated_dsl_classes: tuple[str, ...] = ()
    supported_families: tuple[str, ...] = ()

    def __init__(self, runtime: ToolStoreRuntime) -> None:
        self.runtime = runtime
        self.helper = StrongBtsSolver(runtime, alias_mode="builtin", index_mode="builtin", workflow_mode="none")

    def supports(self, family: str) -> bool:
        return family in self.supported_families

    def visible_text(self, example: dict[str, Any]) -> str:
        return self.helper._analysis_text(example, slot_mode="all")

    def site_id(self, example: dict[str, Any]) -> str:
        text = self.visible_text(example)
        site_id = parse_site(text, example)
        if site_id is None:
            raise ValueError("missing_site")
        return site_id

    def point(self, example: dict[str, Any], site_id: str, text: str, point_class: str | None = None):
        return self.helper._pick_point(site_id, text, point_class)

    def run_calls(self, calls: list[dict[str, Any]]) -> list[ExecutedCall]:
        return self.runtime.execute_call_sequence(calls)

    def initial_protocol_trace(self, example: dict[str, Any]) -> list[dict[str, str]]:
        trace: list[dict[str, str]] = []
        append_trace(trace, "user", example.get("initial_user_message") or example.get("query"))
        for slot in example.get("required_clarification_slots", []):
            append_trace(trace, "assistant", clarification_question(slot))
            append_trace(trace, "user", example.get("clarification_answers", {}).get(slot))
        return trace

    def render_phase_answer(self, example: dict[str, Any], answer: dict[str, Any], *, phase_index: int | None = None) -> str:
        render_example = dict(example)
        if phase_index is not None:
            phase_examples = example.get("phase_examples") or []
            if phase_index < len(phase_examples):
                phase_example = phase_examples[phase_index]
                render_example["task_family"] = phase_example.get("task_family", render_example["task_family"])
                render_example["gold_final_answer"] = phase_example.get("gold_final_answer", render_example.get("gold_final_answer", {}))
                if "task_accomplish_verifier" in phase_example:
                    render_example["task_accomplish_verifier"] = phase_example["task_accomplish_verifier"]
        return render_operator_answer(render_example, answer, self.runtime)

    def quality_decision_from_metrics(
        self,
        metrics: dict[str, Any],
        *,
        period: str,
        stream_id: str,
    ) -> dict[str, Any]:
        ref = metrics.get("quality_reference") or self.runtime.window_quality_reference(period)
        observed_fraction = metrics.get("observed_fraction")
        gap_ratio = metrics.get("gap_ratio")
        abstain_coverage_below = ref.get("abstain_observed_fraction_below")
        abstain_gap_above = ref.get("abstain_gap_ratio_above")
        answer_coverage_at_least = ref.get("answer_observed_fraction_at_least")
        answer_gap_at_most = ref.get("answer_gap_ratio_at_most")

        low_coverage = (
            observed_fraction is not None
            and abstain_coverage_below is not None
            and float(observed_fraction) < float(abstain_coverage_below)
        )
        high_gap = (
            gap_ratio is not None
            and abstain_gap_above is not None
            and float(gap_ratio) > float(abstain_gap_above)
        )
        answer_coverage_ok = (
            observed_fraction is not None
            and answer_coverage_at_least is not None
            and float(observed_fraction) >= float(answer_coverage_at_least)
        )
        answer_gap_ok = (
            answer_gap_at_most is None
            or gap_ratio is None
            or float(gap_ratio) <= float(answer_gap_at_most)
        )
        if low_coverage:
            decision = "abstain"
            reason = "low_coverage"
        elif high_gap:
            decision = "abstain"
            reason = "long_gap"
        elif answer_coverage_ok and answer_gap_ok:
            decision = "answer"
            reason = "healthy"
        else:
            decision = "abstain"
            reason = "marginal_quality"
        return {
            "stream_id": stream_id,
            "decision": decision,
            "reason": reason,
            "observed_fraction": observed_fraction,
            "gap_ratio": gap_ratio,
        }

    def run_timestamp_probe(
        self,
        executed: list[ExecutedCall],
        *,
        stream_id: str,
        requested_timestamp: pd.Timestamp,
    ) -> dict[str, Any]:
        exact_call = {
            "call_id": next_call_id_from_executed(executed),
            "tool_name": "lookup_observation",
            "arguments": {
                "stream_id": stream_id,
                "timestamp": requested_timestamp.isoformat(),
                "mode": "exact",
            },
        }
        executed.extend(self.run_calls([exact_call]))
        exact_obs = executed[-1].output
        final = {
            "stream_id": stream_id,
            "requested_timestamp": requested_timestamp.isoformat(),
            "observed_timestamp": exact_obs.get("observed_timestamp"),
            "value": exact_obs.get("value"),
            "exact_match_found": exact_obs.get("exact_match_found"),
        }
        if not exact_obs.get("exact_match_found", False):
            nearest_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": stream_id,
                    "timestamp": requested_timestamp.isoformat(),
                    "mode": "nearest",
                },
            }
            executed.extend(self.run_calls([nearest_call]))
            nearest_obs = executed[-1].output
            final.update(
                {
                    "observed_timestamp": nearest_obs.get("observed_timestamp"),
                    "value": nearest_obs.get("value"),
                    "exact_match_found": nearest_obs.get("exact_match_found"),
                    "fallback_reason": nearest_obs.get("fallback_reason"),
                    "offset_seconds": nearest_obs.get("offset_seconds"),
                }
            )
        return final

    def run_quality_window(
        self,
        executed: list[ExecutedCall],
        *,
        stream_id: str,
        window_start: pd.Timestamp,
        window_end: pd.Timestamp,
        period: str = "week",
    ) -> dict[str, Any]:
        quality_call = {
            "call_id": next_call_id_from_executed(executed),
            "tool_name": "inspect_quality_window",
            "arguments": {
                "stream_id": stream_id,
                "window_start": freeze_time(window_start),
                "window_end": freeze_time(window_end),
                "period": period,
            },
        }
        executed.extend(self.run_calls([quality_call]))
        return self.quality_decision_from_metrics(executed[-1].output, period=period, stream_id=stream_id)

    def next_phase_family(self, example: dict[str, Any], phase_answers: list[dict[str, Any]]) -> str | None:
        phase_examples = example.get("phase_examples") or []
        next_idx = len(phase_answers)
        if next_idx < len(phase_examples):
            return str(phase_examples[next_idx].get("task_family", ""))
        return None

    def choose_quality_preference(self, first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
        def rank(candidate: dict[str, Any]) -> tuple[int, int, float, float]:
            decision = str(candidate.get("decision") or "")
            reason = str(candidate.get("reason") or "")
            observed = float(candidate.get("observed_fraction") or -1.0)
            gap = float(candidate.get("gap_ratio")) if candidate.get("gap_ratio") is not None else float("inf")
            return (
                1 if decision == "answer" else 0,
                1 if reason == "healthy" else 0,
                observed,
                -gap,
            )

        preferred_reference = "first" if rank(first) >= rank(second) else "second"
        chosen = dict(first if preferred_reference == "first" else second)
        chosen["preferred_reference"] = preferred_reference
        return chosen

    def choose_timestamp_preference(self, first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
        def rank(candidate: dict[str, Any]) -> tuple[int, float]:
            exact = 1 if bool(candidate.get("exact_match_found", False)) else 0
            offset = float(candidate.get("offset_seconds")) if candidate.get("offset_seconds") is not None else 0.0
            return (exact, -offset)

        preferred_reference = "first" if rank(first) >= rank(second) else "second"
        chosen = dict(first if preferred_reference == "first" else second)
        chosen["preferred_reference"] = preferred_reference
        return chosen

    def choose_reporting_commitment(self, previous: dict[str, Any]) -> dict[str, Any]:
        if "decision" in previous and "reason" in previous:
            decision = str(previous.get("decision") or "")
            reason = str(previous.get("reason") or "")
            if decision == "answer" and reason == "healthy":
                return {"commitment_action": "answer", "reason": "healthy_quality"}
            if decision == "abstain" and reason in {"low_coverage", "long_gap"}:
                return {"commitment_action": "abstain", "reason": reason}
            return {
                "commitment_action": "re_clarify",
                "reason": "marginal_quality",
                "clarification_request": "narrower_time_range",
            }
        if "observed_timestamp" in previous and "value" in previous:
            exact = bool(previous.get("exact_match_found", False))
            offset = previous.get("offset_seconds")
            if exact:
                return {"commitment_action": "answer", "reason": "exact_timestamp"}
            if offset is not None and float(offset) <= 120.0:
                return {"commitment_action": "answer", "reason": "nearest_but_acceptable"}
            return {
                "commitment_action": "re_clarify",
                "reason": "timestamp_too_imprecise",
                "clarification_request": "more_precise_timestamp",
            }
        raise ValueError("unsupported_reporting_commitment_context")

    def expected_evidence_stream_ids(self, example: dict[str, Any], fallback: list[str]) -> list[str]:
        expected = example.get("evidence", {}).get("stream_ids")
        if isinstance(expected, list) and expected:
            return [str(item) for item in expected]
        return fallback

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        raise NotImplementedError


class StatefulSingleStreamController(ExplicitControllerBase):
    name = "stateful_single_stream_controller"
    approximated_dsl_classes = (
        "RS8_stateful_single_stream_revision",
        "RS13_short_memory_controller",
        "RS14_point_target_revision_template",
    )
    supported_families = (
        "point_disambiguation",
        "day_mean_lookup",
        "relative_24h_mean_lookup",
        "window_mean_lookup",
        "timestamp_value_lookup",
    )

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        family = example["task_family"]
        if family == "point_disambiguation":
            return self._solve_point_revision(example)
        if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
            return self._solve_aggregate_revision(example)
        if family == "timestamp_value_lookup":
            return self._solve_timestamp_value_revision(example)
        raise ValueError(f"unsupported_family:{family}")

    def _solve_point_revision(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point_class = self.helper._infer_point_class(site_id, text)
        original_point = self.point(example, site_id, text, point_class)
        calls = [self.helper._resolve_point_call(original_point, "c1")]
        executed = self.run_calls(calls)
        phase_answers = [{"stream_id": executed[-1].output["stream_id"]}]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))

        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                next_point = original_point
            else:
                marker = "if the operator meant "
                lowered = goal_turn.lower()
                if marker not in lowered:
                    raise ValueError("missing_revised_target_phrase")
                start = lowered.index(marker) + len(marker)
                end = lowered.index(" instead", start)
                revised_target = goal_turn[start:end]
                next_point = self.point(example, site_id, revised_target, point_class)
            next_call = self.helper._resolve_point_call(next_point, next_call_id_from_executed(executed))
            executed.extend(self.run_calls([next_call]))
            phase_answers.append({"stream_id": executed[-1].output["stream_id"]})
            append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))

        all_streams = list(dict.fromkeys(answer["stream_id"] for answer in phase_answers))
        ev_text = evidence_text(all_streams)
        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", ev_text)
        return ControllerRun(executed, phase_answers, all_streams, None, [], trace)

    def _solve_aggregate_revision(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [
            self.helper._resolve_point_call(point, "c1"),
            {
                "call_id": "c2",
                "tool_name": "aggregate_window",
                "arguments": {
                    "stream_id": "$c1.stream_id",
                    "metric": "mean_value",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                },
            },
        ]
        executed = self.run_calls(calls)
        stream_id = executed[0].output["stream_id"]
        phase_answers = [{"stream_id": stream_id, "mean_value": executed[1].output["mean_value"]}]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))

        original_start, original_end = start, end
        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                revised_start, revised_end = original_start, original_end
            else:
                direction = revision_direction(goal_turn)
                revised_start, revised_end = shift_window(original_start, original_end, direction)
            revised_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "aggregate_window",
                "arguments": {
                    "stream_id": stream_id,
                    "metric": "mean_value",
                    "window_start": freeze_time(revised_start),
                    "window_end": freeze_time(revised_end),
                    "period": period,
                },
            }
            executed.extend(self.run_calls([revised_call]))
            phase_answers.append(
                {
                    "stream_id": stream_id,
                    "window_start": revised_start.isoformat(),
                    "window_end": revised_end.isoformat(),
                    "mean_value": executed[-1].output["mean_value"],
                }
            )
            append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))

        ev_text = evidence_text([stream_id])
        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", ev_text)
        return ControllerRun(executed, phase_answers, [stream_id], None, [], trace)

    def _solve_timestamp_value_revision(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        ts = parse_timestamp(text)
        if ts is None:
            raise ValueError("missing_timestamp")
        calls = [
            self.helper._resolve_point_call(point, "c1"),
            {
                "call_id": "c2",
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": "$c1.stream_id",
                    "timestamp": ts.isoformat(),
                    "mode": "exact",
                },
            },
        ]
        executed = self.run_calls(calls)
        stream_id = executed[0].output["stream_id"]
        initial_obs = executed[1].output
        original_ts = ts
        phase_answers = [
            {
                "stream_id": stream_id,
                "requested_timestamp": ts.isoformat(),
                "observed_timestamp": initial_obs.get("observed_timestamp"),
                "value": initial_obs.get("value"),
                "exact_match_found": initial_obs.get("exact_match_found"),
            }
        ]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))

        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                revised_ts = original_ts
            else:
                revised_ts = parse_timestamp(goal_turn)
                if revised_ts is None:
                    raise ValueError("missing_revised_timestamp")
            exact_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": stream_id,
                    "timestamp": revised_ts.isoformat(),
                    "mode": "exact",
                },
            }
            executed.extend(self.run_calls([exact_call]))
            revised_obs = executed[-1].output
            final = {
                "stream_id": stream_id,
                "requested_timestamp": revised_ts.isoformat(),
                "observed_timestamp": revised_obs.get("observed_timestamp"),
                "value": revised_obs.get("value"),
                "exact_match_found": revised_obs.get("exact_match_found"),
            }
            if not revised_obs.get("exact_match_found", False):
                nearest_call = {
                    "call_id": next_call_id_from_executed(executed),
                    "tool_name": "lookup_observation",
                    "arguments": {
                        "stream_id": stream_id,
                        "timestamp": revised_ts.isoformat(),
                        "mode": "nearest",
                    },
                }
                executed.extend(self.run_calls([nearest_call]))
                revised_obs = executed[-1].output
                final.update(
                    {
                        "observed_timestamp": revised_obs.get("observed_timestamp"),
                        "value": revised_obs.get("value"),
                        "exact_match_found": revised_obs.get("exact_match_found"),
                        "fallback_reason": revised_obs.get("fallback_reason"),
                        "offset_seconds": revised_obs.get("offset_seconds"),
                    }
                )
            phase_answers.append(final)
            append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))

        ev_text = evidence_text([stream_id])
        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", ev_text)
        return ControllerRun(executed, phase_answers, [stream_id], None, [], trace)


class TimestampPolicyController(ExplicitControllerBase):
    name = "timestamp_policy_controller"
    approximated_dsl_classes = ("RS12_timestamp_policy_template",)
    supported_families = ("timestamp_nearest_lookup",)

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        ts = parse_timestamp(text)
        if ts is None:
            raise ValueError("missing_timestamp")
        calls = [
            self.helper._resolve_point_call(point, "c1"),
            {
                "call_id": "c2",
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": "$c1.stream_id",
                    "timestamp": ts.isoformat(),
                    "mode": "exact",
                },
            },
        ]
        executed = self.run_calls(calls)
        exact_obs = executed[-1].output
        stream_id = executed[0].output["stream_id"]
        original_ts = ts
        phase_answers = []
        final = {
            "stream_id": stream_id,
            "requested_timestamp": ts.isoformat(),
            "observed_timestamp": exact_obs.get("observed_timestamp"),
            "value": exact_obs.get("value"),
            "exact_match_found": exact_obs.get("exact_match_found"),
        }
        if not exact_obs.get("exact_match_found", False):
            nearest_call = {
                "call_id": "c3",
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": stream_id,
                    "timestamp": ts.isoformat(),
                    "mode": "nearest",
                },
            }
            executed.extend(self.run_calls([nearest_call]))
            nearest_obs = executed[-1].output
            final.update(
                {
                    "observed_timestamp": nearest_obs.get("observed_timestamp"),
                    "value": nearest_obs.get("value"),
                    "exact_match_found": nearest_obs.get("exact_match_found"),
                    "fallback_reason": nearest_obs.get("fallback_reason"),
                    "offset_seconds": nearest_obs.get("offset_seconds"),
                    }
                )
        phase_answers.append(final)
        append_trace(trace, "assistant", self.render_phase_answer(example, final))
        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                revised_ts = original_ts
            else:
                revised_ts = parse_timestamp(goal_turn)
                if revised_ts is None:
                    raise ValueError("missing_revised_timestamp")
            exact_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "lookup_observation",
                "arguments": {
                    "stream_id": stream_id,
                    "timestamp": revised_ts.isoformat(),
                    "mode": "exact",
                },
            }
            executed.extend(self.run_calls([exact_call]))
            revised_obs = executed[-1].output
            final = {
                "stream_id": stream_id,
                "requested_timestamp": revised_ts.isoformat(),
                "observed_timestamp": revised_obs.get("observed_timestamp"),
                "value": revised_obs.get("value"),
                "exact_match_found": revised_obs.get("exact_match_found"),
            }
            if not revised_obs.get("exact_match_found", False):
                nearest_call = {
                    "call_id": next_call_id_from_executed(executed),
                    "tool_name": "lookup_observation",
                    "arguments": {
                        "stream_id": stream_id,
                        "timestamp": revised_ts.isoformat(),
                        "mode": "nearest",
                    },
                }
                executed.extend(self.run_calls([nearest_call]))
                nearest_obs = executed[-1].output
                final.update(
                    {
                        "observed_timestamp": nearest_obs.get("observed_timestamp"),
                        "value": nearest_obs.get("value"),
                        "exact_match_found": nearest_obs.get("exact_match_found"),
                        "fallback_reason": nearest_obs.get("fallback_reason"),
                        "offset_seconds": nearest_obs.get("offset_seconds"),
                    }
                )
            phase_answers.append(final)
            append_trace(trace, "assistant", self.render_phase_answer(example, final))
        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text([stream_id]))
        return ControllerRun(executed, phase_answers, [stream_id], None, [], trace)


class PairwiseRankController(ExplicitControllerBase):
    name = "pairwise_rank_controller"
    approximated_dsl_classes = ("RS9_stateful_pairwise_revision", "RS10_rank_template_solver")
    supported_families = ("window_pairwise_compare", "window_rank")

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        if example["task_family"] == "window_pairwise_compare":
            return self._solve_pairwise(example)
        return self._solve_rank(example)

    def _solve_pairwise(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point_class = self.helper._infer_point_class(site_id, text)
        left_text, right_text = self.helper._pair_segments(text)
        left = self.point(example, site_id, left_text, point_class)
        right = self.point(example, site_id, right_text, point_class)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [
            self.helper._resolve_point_call(left, "c1"),
            self.helper._resolve_point_call(right, "c2"),
            {
                "call_id": "c3",
                "tool_name": "compare_window",
                "arguments": {
                    "left_stream_id": "$c1.stream_id",
                    "right_stream_id": "$c2.stream_id",
                    "metric": "mean_value",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                },
            },
        ]
        executed = self.run_calls(calls)
        compare = executed[-1].output
        left_stream_id = executed[0].output["stream_id"]
        right_stream_id = executed[1].output["stream_id"]
        phase_answers = [
            {
                "winning_stream_id": compare["winning_stream_id"],
                "left_stream_id": left_stream_id,
                "right_stream_id": right_stream_id,
                "left_mean_value": compare["left_value"],
                "right_mean_value": compare["right_value"],
            }
        ]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))
        original_start, original_end = start, end
        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                revised_start, revised_end = original_start, original_end
            else:
                direction = revision_direction(goal_turn)
                revised_start, revised_end = shift_window(original_start, original_end, direction)
            revised_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "compare_window",
                "arguments": {
                    "left_stream_id": left_stream_id,
                    "right_stream_id": right_stream_id,
                    "metric": "mean_value",
                    "window_start": freeze_time(revised_start),
                    "window_end": freeze_time(revised_end),
                    "period": period,
                },
            }
            executed.extend(self.run_calls([revised_call]))
            compare = executed[-1].output
            phase_answers.append(
                {
                    "winning_stream_id": compare["winning_stream_id"],
                    "left_stream_id": left_stream_id,
                    "right_stream_id": right_stream_id,
                    "left_mean_value": compare["left_value"],
                    "right_mean_value": compare["right_value"],
                }
            )
            append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))
        ev_streams = [left_stream_id, right_stream_id]
        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text(ev_streams))
        return ControllerRun(executed, phase_answers, ev_streams, None, [], trace)

    def _solve_rank(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point_class = self.helper._infer_point_class(site_id, text)
        if point_class is None:
            raise ValueError("missing_point_class")
        location_type = self.helper._infer_location_type(text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [
            {
                "call_id": "c1",
                "tool_name": "list_points",
                "arguments": {"site_id": site_id, "point_class": point_class, "location_type": location_type},
            },
            {
                "call_id": "c2",
                "tool_name": "rank_window",
                "arguments": {
                    "stream_ids": "$c1.stream_ids",
                    "metric": "mean_value",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                    "order": "desc",
                    "topk": 1,
                },
            },
        ]
        executed = self.run_calls(calls)
        ranked = executed[-1].output.get("ranked_streams") or []
        if not ranked:
            raise ValueError("empty_rank")
        stream_ids = executed[0].output.get("stream_ids") or []
        phase_answers = [{"stream_id": ranked[0]["stream_id"], "mean_value": ranked[0]["mean_value"]}]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))
        original_start, original_end = start, end
        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                revised_start, revised_end = original_start, original_end
            else:
                direction = revision_direction(goal_turn)
                revised_start, revised_end = shift_window(original_start, original_end, direction)
            revised_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "rank_window",
                "arguments": {
                    "stream_ids": stream_ids,
                    "metric": "mean_value",
                    "window_start": freeze_time(revised_start),
                    "window_end": freeze_time(revised_end),
                    "period": period,
                    "order": "desc",
                    "topk": 1,
                },
            }
            executed.extend(self.run_calls([revised_call]))
            ranked = executed[-1].output.get("ranked_streams") or []
            if not ranked:
                raise ValueError("empty_rank_revised")
            phase_answers.append(
                {
                    "stream_id": ranked[0]["stream_id"],
                    "mean_value": ranked[0]["mean_value"],
                    "window_start": revised_start.isoformat(),
                    "window_end": revised_end.isoformat(),
                }
            )
            append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1]))
        winner_stream = phase_answers[-1]["stream_id"]
        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", f"I ranked the candidate streams and the winning stream was {winner_stream}.")
        return ControllerRun(executed, phase_answers, [winner_stream], None, [], trace)


class QualityGateController(ExplicitControllerBase):
    name = "quality_gate_controller"
    approximated_dsl_classes = ("RS11_quality_gate_template_solver",)
    supported_families = ("quality_gate",)

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [
            self.helper._resolve_point_call(point, "c1"),
            {
                "call_id": "c2",
                "tool_name": "inspect_quality_window",
                "arguments": {
                    "stream_id": "$c1.stream_id",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                },
            },
        ]
        executed = self.run_calls(calls)
        quality = executed[-1].output
        ref = quality.get("quality_reference") or self.runtime.window_quality_reference(period)
        decision = "answer"
        reason = "healthy"
        if quality["observed_fraction"] is not None and ref.get("abstain_observed_fraction_below") is not None:
            if float(quality["observed_fraction"]) < float(ref["abstain_observed_fraction_below"]):
                decision = "abstain"
                reason = "low_coverage"
        if decision != "abstain" and quality["gap_ratio"] is not None and ref.get("abstain_gap_ratio_above") is not None:
            if float(quality["gap_ratio"]) > float(ref["abstain_gap_ratio_above"]):
                decision = "abstain"
                reason = "long_gap"
        final = {
            "stream_id": executed[0].output["stream_id"],
            "decision": decision,
            "reason": reason,
            "observed_fraction": quality["observed_fraction"],
            "gap_ratio": quality["gap_ratio"],
        }
        phase_answers = [final]
        append_trace(trace, "assistant", self.render_phase_answer(example, final))
        original_start, original_end = start, end
        for goal_turn in example.get("goal_revision_turns", []):
            append_trace(trace, "user", goal_turn)
            if is_return_to_original(goal_turn):
                revised_start, revised_end = original_start, original_end
            else:
                direction = revision_direction(goal_turn)
                revised_start, revised_end = shift_window(original_start, original_end, direction)
            revised_call = {
                "call_id": next_call_id_from_executed(executed),
                "tool_name": "inspect_quality_window",
                "arguments": {
                    "stream_id": final["stream_id"],
                    "window_start": freeze_time(revised_start),
                    "window_end": freeze_time(revised_end),
                    "period": period,
                },
            }
            executed.extend(self.run_calls([revised_call]))
            quality = executed[-1].output
            ref = quality.get("quality_reference") or self.runtime.window_quality_reference(period)
            decision = "answer"
            reason = "healthy"
            if quality["observed_fraction"] is not None and ref.get("abstain_observed_fraction_below") is not None:
                if float(quality["observed_fraction"]) < float(ref["abstain_observed_fraction_below"]):
                    decision = "abstain"
                    reason = "low_coverage"
            if decision != "abstain" and quality["gap_ratio"] is not None and ref.get("abstain_gap_ratio_above") is not None:
                if float(quality["gap_ratio"]) > float(ref["abstain_gap_ratio_above"]):
                    decision = "abstain"
                    reason = "long_gap"
            final = {
                "stream_id": final["stream_id"],
                "decision": decision,
                "reason": reason,
                "observed_fraction": quality["observed_fraction"],
                "gap_ratio": quality["gap_ratio"],
            }
            phase_answers.append(final)
            append_trace(trace, "assistant", self.render_phase_answer(example, final))
        rationale = rationale_text(final)
        if len(example.get("post_answer_user_turns", [])) >= 1:
            append_trace(trace, "user", example["post_answer_user_turns"][0])
            append_trace(trace, "assistant", rationale)
        if len(example.get("post_answer_user_turns", [])) >= 2:
            append_trace(trace, "user", example["post_answer_user_turns"][1])
            append_trace(trace, "assistant", evidence_text([final["stream_id"]]))
        return ControllerRun(executed, phase_answers, [final["stream_id"]], rationale, [], trace)


class PhaseCompleteStrongerController(ExplicitControllerBase):
    name = "phase_complete_stronger_controller"
    approximated_dsl_classes = (
        "RS8_stateful_single_stream_revision",
        "RS9_stateful_pairwise_revision",
        "RS10_rank_template_solver",
        "RS11_quality_gate_template_solver",
        "RS12_timestamp_policy_template",
        "RS13_short_memory_controller",
        "RS14_point_target_revision_template",
        "RS15_phase_complete_contract_controller",
    )
    supported_families = (
        "point_disambiguation",
        "day_mean_lookup",
        "relative_24h_mean_lookup",
        "window_mean_lookup",
        "window_pairwise_compare",
        "window_rank",
        "timestamp_value_lookup",
        "timestamp_nearest_lookup",
        "quality_gate",
    )

    def _window_from_goal(
        self,
        family: str,
        goal_turn: str,
        *,
        original_window: tuple[pd.Timestamp, pd.Timestamp],
        window_history: list[tuple[pd.Timestamp, pd.Timestamp]],
    ) -> tuple[pd.Timestamp, pd.Timestamp, str]:
        if family == "day_mean_lookup":
            period = "day"
            if is_return_to_original(goal_turn):
                start, end = original_window
            else:
                direction = revision_direction(goal_turn)
                start, end = original_window[0] + pd.Timedelta(days=direction), original_window[1] + pd.Timedelta(days=direction)
            return start, end, period
        if family == "relative_24h_mean_lookup":
            period = "custom"
            if is_return_to_original(goal_turn):
                start, end = original_window
            elif "shift that 24-hour window back by one day" in goal_turn.lower():
                start = original_window[0] - pd.Timedelta(days=1)
                end = original_window[1] - pd.Timedelta(days=1)
            else:
                direction = revision_direction(goal_turn)
                start = original_window[0] + pd.Timedelta(days=direction)
                end = original_window[1] + pd.Timedelta(days=direction)
            return start, end, period
        if family in {"window_mean_lookup", "window_pairwise_compare", "quality_gate"}:
            period = "week"
            if is_return_to_original(goal_turn):
                start, end = original_window
            elif "week beginning" in goal_turn.lower():
                parsed = parse_quality_week_window(goal_turn)
                if parsed is None:
                    raise ValueError(f"missing_revised_week:{goal_turn}")
                start, end, _ = parsed
            else:
                direction = revision_direction(goal_turn)
                start, end = original_window[0] + pd.Timedelta(days=7 * direction), original_window[1] + pd.Timedelta(days=7 * direction)
            return start, end, period
        if family == "window_rank":
            period = "month"
            if is_return_to_original(goal_turn):
                start, end = original_window
            elif "earlier of the two ranking windows" in goal_turn.lower() or "later of the two ranking windows" in goal_turn.lower():
                start, end = choose_earlier_or_later(window_history[-2:], goal_turn, key=lambda item: item[0])
            else:
                direction = revision_direction(goal_turn)
                start, end = shift_month_window(original_window[0], original_window[1], direction)
            return pd.Timestamp(start), pd.Timestamp(end), period
        raise ValueError(f"unsupported_window_family:{family}")

    def _timestamp_from_goal(
        self,
        goal_turn: str,
        *,
        original_timestamp: pd.Timestamp,
        timestamp_history: list[pd.Timestamp],
    ) -> pd.Timestamp:
        if is_return_to_original(goal_turn):
            return original_timestamp
        parsed = parse_timestamp(goal_turn)
        if parsed is not None:
            return parsed
        if "earlier of the two timestamps" in goal_turn.lower() or "later of the two timestamps" in goal_turn.lower():
            return choose_earlier_or_later(timestamp_history[-2:], goal_turn, key=lambda item: item)
        raise ValueError(f"missing_revised_timestamp:{goal_turn}")

    def _quality_window_from_goal(
        self,
        goal_turn: str,
        *,
        original_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
        prior_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
    ) -> tuple[pd.Timestamp, pd.Timestamp, str]:
        parsed = parse_quality_week_window(goal_turn)
        if parsed is not None:
            return parsed
        base_window = prior_window or original_window
        if base_window is None:
            raise ValueError(f"missing_quality_reference_window:{goal_turn}")
        direction = revision_direction(goal_turn)
        return (
            base_window[0] + pd.Timedelta(days=7 * direction),
            base_window[1] + pd.Timedelta(days=7 * direction),
            "week",
        )

    def _timestamp_probe_phase(
        self,
        example: dict[str, Any],
        trace: list[dict[str, str]],
        executed: list[ExecutedCall],
        phase_answers: list[dict[str, Any]],
        *,
        stream_id: str,
        goal_turn: str,
        original_timestamp: pd.Timestamp,
        timestamp_history: list[pd.Timestamp],
    ) -> pd.Timestamp:
        append_trace(trace, "user", goal_turn)
        revised_ts = self._timestamp_from_goal(
            goal_turn,
            original_timestamp=original_timestamp,
            timestamp_history=timestamp_history,
        )
        final = self.run_timestamp_probe(executed, stream_id=stream_id, requested_timestamp=revised_ts)
        phase_answers.append(final)
        append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
        return revised_ts

    def _quality_phase(
        self,
        example: dict[str, Any],
        trace: list[dict[str, str]],
        executed: list[ExecutedCall],
        phase_answers: list[dict[str, Any]],
        *,
        stream_id: str,
        goal_turn: str,
        original_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
        prior_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        append_trace(trace, "user", goal_turn)
        start, end, period = self._quality_window_from_goal(goal_turn, original_window=original_window, prior_window=prior_window)
        final = self.run_quality_window(executed, stream_id=stream_id, window_start=start, window_end=end, period=period)
        phase_answers.append(final)
        append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
        return start, end

    def _solve_point_disambiguation(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point_class = self.helper._infer_point_class(site_id, text)
        point = self.point(example, site_id, text, point_class)
        calls = [self.helper._resolve_point_call(point, "c1")]
        executed = self.run_calls(calls)
        current_stream_id = point.stream_id
        point_history = [current_stream_id]
        phase_answers = [{"stream_id": current_stream_id}]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=0))

        for goal_turn in example.get("goal_revision_turns", []):
            next_family = self.next_phase_family(example, phase_answers)
            if next_family == "reporting_commitment":
                append_trace(trace, "user", goal_turn)
                final = self.choose_reporting_commitment(phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif next_family == "quality_preference":
                append_trace(trace, "user", goal_turn)
                if len(point_history) < 2:
                    raise ValueError(f"insufficient_point_history:{goal_turn}")
                history = self.runtime._load_history_for_stream(point_history[-2]).copy()
                if history.empty:
                    raise ValueError(f"missing_quality_reference_window:{goal_turn}")
                anchor = pd.to_datetime(history["timestamp"], utc=True).iloc[0]
                week_start = anchor.normalize() - pd.Timedelta(days=int(anchor.weekday()))
                week_end = week_start + pd.Timedelta(days=7)
                first = self.run_quality_window(executed, stream_id=point_history[-2], window_start=week_start, window_end=week_end, period="week")
                second = self.run_quality_window(executed, stream_id=point_history[-1], window_start=week_start, window_end=week_end, period="week")
                final = self.choose_quality_preference(first, second)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_point_revision(goal_turn):
                append_trace(trace, "user", goal_turn)
                revised_target = revised_target_phrase(goal_turn)
                if not revised_target:
                    raise ValueError(f"missing_revised_target_phrase:{goal_turn}")
                revised_point = self.point(example, site_id, revised_target, point_class)
                executed.extend(self.run_calls([self.helper._resolve_point_call(revised_point, next_call_id_from_executed(executed))]))
                current_stream_id = revised_point.stream_id
                point_history.append(current_stream_id)
                phase_answers.append({"stream_id": current_stream_id})
                append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=len(phase_answers) - 1))
            elif goal_has_quality_phase(goal_turn):
                self._quality_phase(example, trace, executed, phase_answers, stream_id=current_stream_id, goal_turn=goal_turn)
            elif goal_has_timestamp_phase(goal_turn):
                ts = parse_timestamp(goal_turn)
                if ts is None:
                    raise ValueError(f"missing_revised_timestamp:{goal_turn}")
                append_trace(trace, "user", goal_turn)
                final = self.run_timestamp_probe(executed, stream_id=current_stream_id, requested_timestamp=ts)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            else:
                raise ValueError(f"unsupported_revision_surface:{goal_turn}")

        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text(self.expected_evidence_stream_ids(example, [current_stream_id])))
        return ControllerRun(executed, phase_answers, self.expected_evidence_stream_ids(example, [current_stream_id]), None, [], trace)

    def _solve_mean_family(self, example: dict[str, Any]) -> ControllerRun:
        family = example["task_family"]
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [
            self.helper._resolve_point_call(point, "c1"),
            {
                "call_id": "c2",
                "tool_name": "aggregate_window",
                "arguments": {
                    "stream_id": point.stream_id,
                    "metric": "mean_value",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                },
            },
        ]
        executed = self.run_calls(calls)
        stream_id = point.stream_id
        quality_period = "day" if family in {"day_mean_lookup", "relative_24h_mean_lookup"} else ("week" if period not in {"day", "week", "month"} else period)
        phase_answers = [{"stream_id": stream_id, "mean_value": executed[-1].output["mean_value"]}]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=0))
        original_window = (start, end)
        window_history = [original_window]
        quality_contexts = [(stream_id, start, end, quality_period)]

        for goal_turn in example.get("goal_revision_turns", []):
            next_family = self.next_phase_family(example, phase_answers)
            if next_family == "reporting_commitment":
                append_trace(trace, "user", goal_turn)
                final = self.choose_reporting_commitment(phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif next_family == "quality_preference":
                append_trace(trace, "user", goal_turn)
                if len(quality_contexts) < 2:
                    raise ValueError(f"insufficient_quality_contexts:{goal_turn}")
                first_ctx, second_ctx = quality_contexts[-2], quality_contexts[-1]
                first = self.run_quality_window(executed, stream_id=first_ctx[0], window_start=first_ctx[1], window_end=first_ctx[2], period=first_ctx[3])
                second = self.run_quality_window(executed, stream_id=second_ctx[0], window_start=second_ctx[1], window_end=second_ctx[2], period=second_ctx[3])
                final = self.choose_quality_preference(first, second)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_quality_phase(goal_turn):
                self._quality_phase(example, trace, executed, phase_answers, stream_id=stream_id, goal_turn=goal_turn, original_window=original_window, prior_window=window_history[-1])
            elif goal_has_timestamp_phase(goal_turn):
                append_trace(trace, "user", goal_turn)
                ts = parse_timestamp(goal_turn)
                if ts is None:
                    raise ValueError(f"missing_revised_timestamp:{goal_turn}")
                final = self.run_timestamp_probe(executed, stream_id=stream_id, requested_timestamp=ts)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            else:
                append_trace(trace, "user", goal_turn)
                revised_start, revised_end, revised_period = self._window_from_goal(
                    family,
                    goal_turn,
                    original_window=original_window,
                    window_history=window_history,
                )
                revised_call = {
                    "call_id": next_call_id_from_executed(executed),
                    "tool_name": "aggregate_window",
                    "arguments": {
                        "stream_id": stream_id,
                        "metric": "mean_value",
                        "window_start": freeze_time(revised_start),
                        "window_end": freeze_time(revised_end),
                        "period": revised_period,
                    },
                }
                executed.extend(self.run_calls([revised_call]))
                window_history.append((revised_start, revised_end))
                quality_contexts.append((stream_id, revised_start, revised_end, "day" if family in {"day_mean_lookup", "relative_24h_mean_lookup"} else ("week" if revised_period not in {"day", "week", "month"} else revised_period)))
                phase_answers.append(
                    {
                        "stream_id": stream_id,
                        "window_start": revised_start.isoformat(),
                        "window_end": revised_end.isoformat(),
                        "mean_value": executed[-1].output["mean_value"],
                    }
                )
                append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=len(phase_answers) - 1))

        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text(self.expected_evidence_stream_ids(example, [stream_id])))
        return ControllerRun(executed, phase_answers, self.expected_evidence_stream_ids(example, [stream_id]), None, [], trace)

    def _solve_timestamp_family(self, example: dict[str, Any]) -> ControllerRun:
        family = example["task_family"]
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        ts = parse_timestamp(text)
        if ts is None:
            raise ValueError("missing_timestamp")
        calls = [self.helper._resolve_point_call(point, "c1")]
        executed = self.run_calls(calls)
        stream_id = point.stream_id
        phase_answers = [self.run_timestamp_probe(executed, stream_id=stream_id, requested_timestamp=ts)]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=0))
        timestamp_history = [ts]

        for goal_turn in example.get("goal_revision_turns", []):
            next_family = self.next_phase_family(example, phase_answers)
            if next_family == "reporting_commitment":
                append_trace(trace, "user", goal_turn)
                final = self.choose_reporting_commitment(phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif next_family == "timestamp_preference":
                append_trace(trace, "user", goal_turn)
                if len(phase_answers) < 2:
                    raise ValueError(f"insufficient_timestamp_history:{goal_turn}")
                final = self.choose_timestamp_preference(phase_answers[-2], phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_timestamp_phase(goal_turn):
                revised_ts = self._timestamp_probe_phase(
                    example,
                    trace,
                    executed,
                    phase_answers,
                    stream_id=stream_id,
                    goal_turn=goal_turn,
                    original_timestamp=ts,
                    timestamp_history=timestamp_history,
                )
                timestamp_history.append(revised_ts)
            elif goal_has_quality_phase(goal_turn):
                self._quality_phase(example, trace, executed, phase_answers, stream_id=stream_id, goal_turn=goal_turn)
            else:
                raise ValueError(f"unsupported_revision_surface:{goal_turn}")

        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text(self.expected_evidence_stream_ids(example, [stream_id])))
        return ControllerRun(executed, phase_answers, self.expected_evidence_stream_ids(example, [stream_id]), None, [], trace)

    def _solve_pairwise(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point_class = self.helper._infer_point_class(site_id, text)
        left_text, right_text = self.helper._pair_segments(text)
        left = self.point(example, site_id, left_text + "\n" + text, point_class)
        right = self.point(example, site_id, right_text + "\n" + text, point_class)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [
            self.helper._resolve_point_call(left, "c1"),
            self.helper._resolve_point_call(right, "c2"),
            {
                "call_id": "c3",
                "tool_name": "compare_window",
                "arguments": {
                    "left_stream_id": left.stream_id,
                    "right_stream_id": right.stream_id,
                    "metric": "mean_value",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                },
            },
        ]
        executed = self.run_calls(calls)
        compare = executed[-1].output
        left_stream_id = left.stream_id
        right_stream_id = right.stream_id
        current_winner = compare["winning_stream_id"]
        phase_answers = [{
            "winning_stream_id": current_winner,
            "left_stream_id": left_stream_id,
            "right_stream_id": right_stream_id,
            "left_mean_value": compare["left_value"],
            "right_mean_value": compare["right_value"],
        }]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=0))
        original_window = (start, end)
        window_history = [original_window]
        quality_period = "week" if period not in {"day", "week", "month"} else period
        quality_contexts = [(current_winner, start, end, quality_period)]

        for goal_turn in example.get("goal_revision_turns", []):
            next_family = self.next_phase_family(example, phase_answers)
            if next_family == "reporting_commitment":
                append_trace(trace, "user", goal_turn)
                final = self.choose_reporting_commitment(phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif next_family == "quality_preference":
                append_trace(trace, "user", goal_turn)
                if len(quality_contexts) < 2:
                    raise ValueError(f"insufficient_quality_contexts:{goal_turn}")
                first_ctx, second_ctx = quality_contexts[-2], quality_contexts[-1]
                first = self.run_quality_window(executed, stream_id=first_ctx[0], window_start=first_ctx[1], window_end=first_ctx[2], period=first_ctx[3])
                second = self.run_quality_window(executed, stream_id=second_ctx[0], window_start=second_ctx[1], window_end=second_ctx[2], period=second_ctx[3])
                final = self.choose_quality_preference(first, second)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_quality_phase(goal_turn):
                self._quality_phase(example, trace, executed, phase_answers, stream_id=current_winner, goal_turn=goal_turn, original_window=original_window, prior_window=window_history[-1])
            elif goal_has_timestamp_phase(goal_turn):
                append_trace(trace, "user", goal_turn)
                ts = parse_timestamp(goal_turn)
                if ts is None:
                    raise ValueError(f"missing_revised_timestamp:{goal_turn}")
                final = self.run_timestamp_probe(executed, stream_id=current_winner, requested_timestamp=ts)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            else:
                append_trace(trace, "user", goal_turn)
                revised_start, revised_end, revised_period = self._window_from_goal(
                    "window_pairwise_compare",
                    goal_turn,
                    original_window=original_window,
                    window_history=window_history,
                )
                revised_call = {
                    "call_id": next_call_id_from_executed(executed),
                    "tool_name": "compare_window",
                    "arguments": {
                        "left_stream_id": left_stream_id,
                        "right_stream_id": right_stream_id,
                        "metric": "mean_value",
                        "window_start": freeze_time(revised_start),
                        "window_end": freeze_time(revised_end),
                        "period": revised_period,
                    },
                }
                executed.extend(self.run_calls([revised_call]))
                compare = executed[-1].output
                current_winner = compare["winning_stream_id"]
                window_history.append((revised_start, revised_end))
                quality_contexts.append((current_winner, revised_start, revised_end, "week" if revised_period not in {"day", "week", "month"} else revised_period))
                phase_answers.append(
                    {
                        "winning_stream_id": current_winner,
                        "left_stream_id": left_stream_id,
                        "right_stream_id": right_stream_id,
                        "left_mean_value": compare["left_value"],
                        "right_mean_value": compare["right_value"],
                    }
                )
                append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=len(phase_answers) - 1))

        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text(self.expected_evidence_stream_ids(example, [left_stream_id, right_stream_id])))
        return ControllerRun(executed, phase_answers, self.expected_evidence_stream_ids(example, [left_stream_id, right_stream_id]), None, [], trace)

    def _solve_rank(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point_class = self.helper._infer_point_class(site_id, text)
        if point_class is None:
            raise ValueError("missing_point_class")
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        list_call = {
            "call_id": "c1",
            "tool_name": "list_points",
            "arguments": {"site_id": site_id, "point_class": point_class},
        }
        rank_call = {
            "call_id": "c2",
            "tool_name": "rank_window",
            "arguments": {
                "stream_ids": "$c1.stream_ids",
                "metric": "mean_value",
                "window_start": freeze_time(start),
                "window_end": freeze_time(end),
                "period": period,
                "order": "desc",
                "topk": 1,
            },
        }
        executed = self.run_calls([list_call, rank_call])
        stream_ids = executed[0].output.get("stream_ids") or []
        ranked = executed[-1].output.get("ranked_streams") or []
        if not ranked:
            raise ValueError("empty_rank")
        current_winner = ranked[0]["stream_id"]
        phase_answers = [{"stream_id": current_winner, "mean_value": ranked[0]["mean_value"]}]
        append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=0))
        original_window = (start, end)
        window_history = [original_window]
        quality_period = "month" if period not in {"day", "week", "month"} else period
        quality_contexts = [(current_winner, start, end, quality_period)]

        for goal_turn in example.get("goal_revision_turns", []):
            next_family = self.next_phase_family(example, phase_answers)
            if next_family == "reporting_commitment":
                append_trace(trace, "user", goal_turn)
                final = self.choose_reporting_commitment(phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif next_family == "quality_preference":
                append_trace(trace, "user", goal_turn)
                if len(quality_contexts) < 2:
                    raise ValueError(f"insufficient_quality_contexts:{goal_turn}")
                first_ctx, second_ctx = quality_contexts[-2], quality_contexts[-1]
                first = self.run_quality_window(executed, stream_id=first_ctx[0], window_start=first_ctx[1], window_end=first_ctx[2], period=first_ctx[3])
                second = self.run_quality_window(executed, stream_id=second_ctx[0], window_start=second_ctx[1], window_end=second_ctx[2], period=second_ctx[3])
                final = self.choose_quality_preference(first, second)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_quality_phase(goal_turn):
                self._quality_phase(example, trace, executed, phase_answers, stream_id=current_winner, goal_turn=goal_turn, original_window=original_window, prior_window=window_history[-1])
            elif goal_has_timestamp_phase(goal_turn):
                append_trace(trace, "user", goal_turn)
                ts = parse_timestamp(goal_turn)
                if ts is None:
                    raise ValueError(f"missing_revised_timestamp:{goal_turn}")
                final = self.run_timestamp_probe(executed, stream_id=current_winner, requested_timestamp=ts)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            else:
                append_trace(trace, "user", goal_turn)
                revised_start, revised_end, revised_period = self._window_from_goal(
                    "window_rank",
                    goal_turn,
                    original_window=original_window,
                    window_history=window_history,
                )
                revised_call = {
                    "call_id": next_call_id_from_executed(executed),
                    "tool_name": "rank_window",
                    "arguments": {
                        "stream_ids": stream_ids,
                        "metric": "mean_value",
                        "window_start": freeze_time(revised_start),
                        "window_end": freeze_time(revised_end),
                        "period": revised_period,
                        "order": "desc",
                        "topk": 1,
                    },
                }
                executed.extend(self.run_calls([revised_call]))
                ranked = executed[-1].output.get("ranked_streams") or []
                if not ranked:
                    raise ValueError("empty_rank_revised")
                current_winner = ranked[0]["stream_id"]
                window_history.append((revised_start, revised_end))
                quality_contexts.append((current_winner, revised_start, revised_end, "month" if revised_period not in {"day", "week", "month"} else revised_period))
                phase_answers.append(
                    {
                        "stream_id": current_winner,
                        "mean_value": ranked[0]["mean_value"],
                        "window_start": revised_start.isoformat(),
                        "window_end": revised_end.isoformat(),
                    }
                )
                append_trace(trace, "assistant", self.render_phase_answer(example, phase_answers[-1], phase_index=len(phase_answers) - 1))

        if example.get("post_answer_user_turns"):
            append_trace(trace, "user", example["post_answer_user_turns"][-1])
            append_trace(trace, "assistant", evidence_text(self.expected_evidence_stream_ids(example, [current_winner])))
        return ControllerRun(executed, phase_answers, self.expected_evidence_stream_ids(example, [current_winner]), None, [], trace)

    def _solve_quality_gate(self, example: dict[str, Any]) -> ControllerRun:
        site_id = self.site_id(example)
        text = self.visible_text(example)
        trace = self.initial_protocol_trace(example)
        point = self.point(example, site_id, text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = [self.helper._resolve_point_call(point, "c1")]
        executed = self.run_calls(calls)
        stream_id = point.stream_id
        phase_answers: list[dict[str, Any]] = []
        first_quality = self.run_quality_window(executed, stream_id=stream_id, window_start=start, window_end=end, period=period)
        phase_answers.append(first_quality)
        append_trace(trace, "assistant", self.render_phase_answer(example, first_quality, phase_index=0))
        original_window = (start, end)
        window_history = [original_window]

        for goal_turn in example.get("goal_revision_turns", []):
            next_family = self.next_phase_family(example, phase_answers)
            if next_family == "reporting_commitment":
                append_trace(trace, "user", goal_turn)
                final = self.choose_reporting_commitment(phase_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif next_family == "quality_preference":
                append_trace(trace, "user", goal_turn)
                quality_answers = [answer for answer in phase_answers if "decision" in answer]
                if len(quality_answers) < 2:
                    raise ValueError(f"insufficient_quality_history:{goal_turn}")
                final = self.choose_quality_preference(quality_answers[-2], quality_answers[-1])
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_timestamp_phase(goal_turn):
                append_trace(trace, "user", goal_turn)
                ts = parse_timestamp(goal_turn)
                if ts is None:
                    raise ValueError(f"missing_revised_timestamp:{goal_turn}")
                final = self.run_timestamp_probe(executed, stream_id=stream_id, requested_timestamp=ts)
                phase_answers.append(final)
                append_trace(trace, "assistant", self.render_phase_answer(example, final, phase_index=len(phase_answers) - 1))
            elif goal_has_quality_phase(goal_turn):
                start, end = self._quality_phase(
                    example,
                    trace,
                    executed,
                    phase_answers,
                    stream_id=stream_id,
                    goal_turn=goal_turn,
                    original_window=original_window,
                    prior_window=window_history[-1],
                )
                window_history.append((start, end))
            else:
                raise ValueError(f"unsupported_revision_surface:{goal_turn}")

        quality_answers = [answer for answer in phase_answers if "decision" in answer]
        rationale_source = quality_answers[-1] if quality_answers else None
        rationale = rationale_text(rationale_source) if rationale_source is not None else None
        if len(example.get("post_answer_user_turns", [])) >= 1 and rationale is not None:
            append_trace(trace, "user", example["post_answer_user_turns"][0])
            append_trace(trace, "assistant", rationale)
        if len(example.get("post_answer_user_turns", [])) >= 2:
            append_trace(trace, "user", example["post_answer_user_turns"][1])
            append_trace(trace, "assistant", evidence_text(self.expected_evidence_stream_ids(example, [stream_id])))
        return ControllerRun(executed, phase_answers, self.expected_evidence_stream_ids(example, [stream_id]), rationale, [], trace)

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        family = example["task_family"]
        if family == "point_disambiguation":
            return self._solve_point_disambiguation(example)
        if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
            return self._solve_mean_family(example)
        if family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
            return self._solve_timestamp_family(example)
        if family == "window_pairwise_compare":
            return self._solve_pairwise(example)
        if family == "window_rank":
            return self._solve_rank(example)
        if family == "quality_gate":
            return self._solve_quality_gate(example)
        raise ValueError(f"unsupported_family:{family}")


class CompositeExplicitController(ExplicitControllerBase):
    name = "composite_explicit_controller"
    approximated_dsl_classes = (
        "RS8_stateful_single_stream_revision",
        "RS9_stateful_pairwise_revision",
        "RS10_rank_template_solver",
        "RS11_quality_gate_template_solver",
        "RS12_timestamp_policy_template",
        "RS13_short_memory_controller",
        "RS14_point_target_revision_template",
    )
    supported_families = (
        "point_disambiguation",
        "day_mean_lookup",
        "relative_24h_mean_lookup",
        "window_mean_lookup",
        "window_pairwise_compare",
        "window_rank",
        "timestamp_value_lookup",
        "timestamp_nearest_lookup",
        "quality_gate",
    )

    def __init__(self, runtime: ToolStoreRuntime) -> None:
        super().__init__(runtime)
        self.single = StatefulSingleStreamController(runtime)
        self.timestamp = TimestampPolicyController(runtime)
        self.pairrank = PairwiseRankController(runtime)
        self.quality = QualityGateController(runtime)

    def solve(self, example: dict[str, Any]) -> ControllerRun:
        family = example["task_family"]
        if self.single.supports(family):
            return self.single.solve(example)
        if self.timestamp.supports(family):
            return self.timestamp.solve(example)
        if self.pairrank.supports(family):
            return self.pairrank.solve(example)
        if self.quality.supports(family):
            return self.quality.solve(example)
        raise ValueError(f"unsupported_family:{family}")


CONTROLLERS = {
    StatefulSingleStreamController.name: StatefulSingleStreamController,
    TimestampPolicyController.name: TimestampPolicyController,
    PairwiseRankController.name: PairwiseRankController,
    QualityGateController.name: QualityGateController,
    PhaseCompleteStrongerController.name: PhaseCompleteStrongerController,
    CompositeExplicitController.name: CompositeExplicitController,
}


def evaluate_controller_on_example(controller: ExplicitControllerBase, example: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, Any]:
    try:
        result = controller.solve(example)
        parse_error = None
    except Exception as exc:
        result = ControllerRun([], [], [], None, [f"controller_error:{type(exc).__name__}:{exc}"], [])
        parse_error = result.issues[0]

    phase_examples = example.get("phase_examples") or []
    phase_answer_texts: list[str] = []
    for idx, phase_answer in enumerate(result.phase_answers):
        render_example = dict(example)
        if idx < len(phase_examples):
            render_example["task_family"] = phase_examples[idx].get("task_family", example["task_family"])
            render_example["gold_final_answer"] = phase_examples[idx].get("gold_final_answer", example.get("gold_final_answer", {}))
            if "task_accomplish_verifier" in phase_examples[idx]:
                render_example["task_accomplish_verifier"] = phase_examples[idx]["task_accomplish_verifier"]
        phase_answer_texts.append(render_operator_answer(render_example, phase_answer, runtime))

    final_answer = result.final_answer
    final_answer_text = phase_answer_texts[-1] if phase_answer_texts else None
    evidence_answer_text = evidence_text(result.evidence_stream_ids) if result.evidence_stream_ids else None
    rationale_answer_text = result.rationale_answer_text

    verification = verify_prediction(example, result.executed_calls, final_answer_text, None, runtime, phase_answers=phase_answer_texts)

    interaction_issues = list(result.issues)
    if example.get("required_clarification_slots"):
        asked_slots = {
            slot
            for slot in example.get("required_clarification_slots", [])
            if any(clarification_question(slot).lower() == turn.get("content", "").lower() for turn in result.protocol_trace if turn["role"] == "assistant")
        }
        for slot in example.get("required_clarification_slots", []):
            if slot not in asked_slots:
                interaction_issues.append(f"missing_required_clarification:{slot}")
    expected_phase_answers = len(phase_examples) if phase_examples else 1 + len(example.get("goal_revision_turns", []))
    if len(result.phase_answers) < expected_phase_answers:
        interaction_issues.append("missing_goal_revision_answer")
    if example.get("interaction_verifier", {}).get("require_rationale_followup", False):
        if rationale_answer_text is None:
            interaction_issues.append("missing_rationale_followup_answer")
        elif not check_rationale_followup(example, rationale_answer_text, runtime):
            interaction_issues.append("invalid_rationale_followup_answer")
    if example.get("post_answer_user_turns"):
        if evidence_answer_text is None:
            interaction_issues.append("missing_evidence_followup_answer")
        elif not check_evidence_followup(example, evidence_answer_text, result.executed_calls, runtime):
            interaction_issues.append("invalid_evidence_followup_answer")
    protocol_ok = not interaction_issues
    label = "accomplished" if (verification.task_ok and protocol_ok) else ("partially_accomplished" if verification.task_score > 0 or interaction_issues else "not_accomplished")
    strict_label = "accomplished" if (verification.process_ok and verification.task_ok and protocol_ok) else ("partially_accomplished" if verification.strict_label != "not_accomplished" or interaction_issues else "not_accomplished")

    blocked = set(example.get("agentic_lifting", {}).get("declared_solver_hardness", {}).get("declared_solver_classes_blocked", []))
    approx = set(controller.approximated_dsl_classes)
    contradicted_classes = sorted(blocked & approx) if label == "accomplished" else []

    return {
        "scenario_id": example["scenario_id"],
        "task_family": example["task_family"],
        "interaction_mode": example.get("interaction_mode"),
        "controller": controller.name,
        "approximated_dsl_classes": list(controller.approximated_dsl_classes),
        "supported_family": controller.supports(example["task_family"]),
        "parse_error": parse_error,
        "protocol_trace": result.protocol_trace,
        "executed_calls": [call.as_dict() for call in result.executed_calls],
        "phase_answers": result.phase_answers,
        "phase_answer_texts": phase_answer_texts,
        "final_answer_text": final_answer_text,
        "evidence_answer_text": evidence_answer_text,
        "rationale_answer_text": rationale_answer_text,
        "interaction_issues": interaction_issues,
        "protocol_ok": protocol_ok,
        "label": label,
        "strict_label": strict_label,
        "verification": verification.as_dict(),
        "contradicted_dsl_classes": contradicted_classes,
        "contradicts_declared_dsl": bool(contradicted_classes),
        "blocked_solver_classes": sorted(blocked),
    }


def summarize_rows(rows: list[dict[str, Any]], controller_name: str, approximated_dsl_classes: list[str], benchmark_dir: Path, split: str) -> dict[str, Any]:
    labels = Counter(row["label"] for row in rows)
    strict = Counter(row["strict_label"] for row in rows)
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    contradictions: list[str] = []
    contradiction_by_family: Counter[str] = Counter()
    issues = Counter()
    supported_rows = [row for row in rows if row.get("supported_family")]
    supported_labels = Counter(row["label"] for row in supported_rows)
    for row in rows:
        by_family[row["task_family"]][row["label"]] += 1
        if row["contradicts_declared_dsl"]:
            contradictions.append(row["scenario_id"])
            contradiction_by_family[row["task_family"]] += 1
        issues.update(row["interaction_issues"])
        issues.update(row["verification"]["issues"])
    return {
        "controller": controller_name,
        "benchmark_dir": str(benchmark_dir),
        "split": split,
        "approximated_dsl_classes": approximated_dsl_classes,
        "scenario_count": len(rows),
        "supported_row_count": len(supported_rows),
        "label_counts": dict(labels),
        "supported_label_counts": dict(supported_labels),
        "strict_label_counts": dict(strict),
        "mean_process_score": round(mean(row["verification"]["process_score"] for row in rows), 4) if rows else 0.0,
        "mean_task_score": round(mean(row["verification"]["task_score"] for row in rows), 4) if rows else 0.0,
        "mean_temporal_score": round(mean(row["verification"]["temporal_score"] for row in rows), 4) if rows else 0.0,
        "mean_grounding_score": round(mean(row["verification"]["grounding_score"] for row in rows), 4) if rows else 0.0,
        "by_family": {family: dict(counter) for family, counter in sorted(by_family.items())},
        "contradiction_count": len(contradictions),
        "contradiction_rate": round(len(contradictions) / len(rows), 4) if rows else 0.0,
        "contradiction_by_family": dict(contradiction_by_family),
        "contradiction_scenario_ids": contradictions[:50],
        "top_issues": issues.most_common(30),
    }


def run_controller_suite(benchmark_dir: Path, split: str, runtime: ToolStoreRuntime, controller_names: list[str]) -> dict[str, Any]:
    examples = load_jsonl(benchmark_dir / f"{split}.jsonl")
    reports: dict[str, Any] = {}
    rows_by_controller: dict[str, list[dict[str, Any]]] = {}
    for name in controller_names:
        controller = CONTROLLERS[name](runtime)
        rows = [evaluate_controller_on_example(controller, example, runtime) for example in examples]
        rows_by_controller[name] = rows
        reports[name] = summarize_rows(rows, name, list(controller.approximated_dsl_classes), benchmark_dir, split)
    return {"rows_by_controller": rows_by_controller, "reports": reports}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--benchmark-dir", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "dev", "test"], default="test")
    parser.add_argument(
        "--controller",
        choices=["all", *CONTROLLERS.keys()],
        default="all",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    controller_names = list(CONTROLLERS.keys()) if args.controller == "all" else [args.controller]
    runtime = ToolStoreRuntime(args.tool_store_db)
    try:
        suite = run_controller_suite(args.benchmark_dir, args.split, runtime, controller_names)
    finally:
        runtime.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    combined_report = {
        "benchmark_dir": str(args.benchmark_dir),
        "split": args.split,
        "controllers": controller_names,
        "reports": suite["reports"],
    }
    write_json(args.out_dir / "explicit_controller_report.json", combined_report)
    for name, rows in suite["rows_by_controller"].items():
        write_jsonl(args.out_dir / f"{name}.jsonl", rows)
        write_json(args.out_dir / f"{name}_summary.json", suite["reports"][name])
    print(json.dumps(combined_report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
