#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd

from bts_agentbench.evaluator import verify_prediction
from bts_agentbench.runtime import ExecutedCall, ToolStoreRuntime


MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_PATTERN = "|".join(MONTHS)

POINT_CLASS_ALIASES = {
    "Electrical_Energy_Sensor": "energy counter",
    "Electrical_Power_Sensor": "power draw",
    "Air_Differential_Pressure_Sensor": "air pressure delta",
    "Air_Differential_Pressure_Setpoint": "air pressure delta target",
    "Air_Flow_Sensor": "airflow",
    "Position_Sensor": "position signal",
    "Run_Time_Sensor": "runtime counter",
    "Frequency_Sensor": "frequency signal",
    "Motor_Speed_Sensor": "motor speed signal",
    "Heating_Thermal_Power_Sensor": "heating thermal output",
    "Temperature_Setpoint": "temperature target",
    "Air_Temperature_Setpoint": "air temperature target",
    "Chilled_Water_Flow_Sensor": "chilled-water flow",
}

ASSET_ALIAS_PATTERNS = [
    (r"^Electrical Generation Meter (\d+)$", "EGM-{}"),
    (r"^Electrical Storage Meter (\d+)$", "ESM-{}"),
    (r"^Electrical Meter (\d+)$", "EM-{}"),
    (r"^Supply Fan (\d+)$", "SF-{}"),
    (r"^Terminal Unit (\d+)$", "TU-{}"),
    (r"^VAV (\d+)$", "VAV-{}"),
    (r"^Zone (\d+)$", "zone tag Z-{}"),
    (r"^Floor (\d+)$", "floor tag F-{}"),
    (r"^Chilled Water Coil (\d+)$", "CHW coil {}"),
    (r"^Chilled Water Loop (\d+)$", "CHW loop {}"),
    (r"^Hot Water Coil (\d+)$", "HW coil {}"),
    (r"^Water Heater (\d+)$", "WH-{}"),
    (r"^Ventilation Air System (\d+)$", "VAS-{}"),
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def norm(text: object | None) -> str:
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def display_label(site_id: str, label: object | None) -> str:
    if label is None:
        return ""
    text = str(label)
    prefix = f"{site_id} "
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return text


def freeze_time(value: object) -> str:
    return str(pd.Timestamp(value))


def point_class_alias(point_class: str) -> str:
    if point_class in POINT_CLASS_ALIASES:
        return POINT_CLASS_ALIASES[point_class]
    return point_class.replace("_", " ").lower().replace(" sensor", " signal").replace(" setpoint", " target")


def asset_aliases(label: str) -> set[str]:
    aliases = {label}
    for pattern, template in ASSET_ALIAS_PATTERNS:
        match = re.match(pattern, label, flags=re.IGNORECASE)
        if match:
            aliases.add(template.format(match.group(1)))
    return aliases


def class_variants(point_class: str, *, include_aliases: bool = False) -> set[str]:
    base = point_class.replace("_", " ").lower()
    words = point_class.split("_")
    variants = {base}
    if include_aliases:
        variants.add(point_class_alias(point_class))
    if len(words) >= 2:
        stem = " ".join(word.lower() for word in words[:-1])
        suffix = words[-1]
        if suffix == "Sensor":
            variants.update({f"{stem} sensor", f"{stem} reading", f"{stem} measurement"})
        elif suffix == "Setpoint":
            variants.update({f"{stem} setpoint", f"{stem} setting"})
        elif suffix == "Limit":
            variants.update({f"{stem} limit", f"{stem} threshold"})
        elif suffix == "Status":
            variants.update({f"{stem} status", f"{stem} state"})
    return {norm(variant) for variant in variants if variant}


def parse_site(text: str, example: dict[str, Any]) -> str | None:
    match = re.search(r"\bBTS_[A-Z]\b", text)
    if match:
        return match.group(0)
    for answer in example.get("clarification_answers", {}).values():
        match = re.search(r"\bBTS_[A-Z]\b", str(answer))
        if match:
            return match.group(0)
    return None


def first_date(text: str) -> pd.Timestamp | None:
    patterns = [
        rf"\b(?:{MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return pd.Timestamp(match.group(0), tz="UTC")
    return None


def parse_timestamp(text: str) -> pd.Timestamp | None:
    pattern = rf"\b\d{{1,2}}:\d{{2}}(?::\d{{2}}(?:\.\d+)?)?\s+UTC\s+on\s+(?:{MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    raw = match.group(0)
    time_part, date_part = re.split(r"\s+UTC\s+on\s+", raw, maxsplit=1, flags=re.IGNORECASE)
    return pd.Timestamp(f"{date_part} {time_part} UTC")


def parse_month_start(text: str) -> pd.Timestamp | None:
    match = re.search(rf"\b(?:{MONTH_PATTERN})\s+\d{{4}}\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return pd.Timestamp(match.group(0), tz="UTC")


def parse_time_window(example: dict[str, Any], text: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None, str | None]:
    family = example["task_family"]
    if family == "day_mean_lookup":
        start = first_date(text)
        return (start, start + pd.Timedelta(days=1), "day") if start is not None else (None, None, None)
    if family == "relative_24h_mean_lookup":
        end = parse_timestamp(text) or first_date(text)
        return (end - pd.Timedelta(days=1), end, "custom") if end is not None else (None, None, None)
    if family in {"window_mean_lookup", "window_pairwise_compare", "quality_gate"}:
        start = first_date(text)
        return (start, start + pd.Timedelta(days=7), "week") if start is not None else (None, None, None)
    if family == "window_rank":
        start = parse_month_start(text)
        return (start, start + pd.DateOffset(months=1), "month") if start is not None else (None, None, None)
    return (None, None, None)


@dataclass(frozen=True)
class PointRow:
    site_id: str
    stream_id: str
    point_class: str
    equipment_label: str | None
    location_type: str | None
    location_label: str | None

    def equipment_display(self) -> str:
        return display_label(self.site_id, self.equipment_label)

    def location_display(self) -> str:
        return display_label(self.site_id, self.location_label)


@dataclass
class SolveResult:
    executed_calls: list[ExecutedCall]
    final_answer: dict[str, Any] | None
    evidence: dict[str, Any] | None
    parse_error: str | None = None


class StrongBtsSolver:
    def __init__(
        self,
        runtime: ToolStoreRuntime,
        *,
        alias_mode: str = "none",
        index_mode: str = "none",
        workflow_mode: str = "none",
    ):
        self.runtime = runtime
        self.alias_mode = alias_mode
        self.index_mode = index_mode
        self.workflow_mode = workflow_mode
        self._points_by_site: dict[str, list[PointRow]] = {}
        self._point_classes_by_site: dict[str, list[str]] = {}

    def _points(self, site_id: str) -> list[PointRow]:
        if site_id not in self._points_by_site:
            rows = self.runtime.con.execute(
                """
                select site_id, stream_id, point_class, equipment_label, location_type, location_label
                from tool_ready_points
                where site_id = ?
                order by stream_id
                """,
                (site_id,),
            ).fetchall()
            self._points_by_site[site_id] = [PointRow(*row) for row in rows]
            self._point_classes_by_site[site_id] = sorted({row[2] for row in rows})
        return self._points_by_site[site_id]

    def _analysis_text(self, example: dict[str, Any], slot_mode: str) -> str:
        parts = [example.get("initial_user_message") or example.get("query") or ""]
        answers = example.get("clarification_answers", {})
        for slot in ("site_id", "time_reference"):
            if slot in answers:
                parts.append(str(answers[slot]))
        if slot_mode == "all":
            for slot, answer in answers.items():
                if slot not in {"site_id", "time_reference"}:
                    parts.append(str(answer))
        return "\n".join(parts)

    def _infer_point_class(self, site_id: str, text: str) -> str | None:
        text_norm = norm(text)
        if self.index_mode == "builtin":
            indexed = self._infer_point_class_by_index(site_id, text)
            if indexed is not None:
                return indexed
        best: tuple[int, str] | None = None
        for point_class in self._point_classes_by_site.get(site_id) or [p.point_class for p in self._points(site_id)]:
            for variant in class_variants(point_class, include_aliases=self.alias_mode == "builtin"):
                if variant and re.search(rf"\b{re.escape(variant)}\b", text_norm):
                    score = len(variant)
                    if best is None or score > best[0]:
                        best = (score, point_class)
        return best[1] if best else None

    def _infer_point_class_by_index(self, site_id: str, text: str) -> str | None:
        match = re.search(r"\bpoint[- ]class\s+index\s*#?\s*(\d+)\s+of\s+\d+\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        classes = self._point_classes_by_site.get(site_id)
        if classes is None:
            self._points(site_id)
            classes = self._point_classes_by_site[site_id]
        index = int(match.group(1))
        if index < 1 or index > len(classes):
            raise ValueError(f"point_class_index_out_of_range:{index}:{len(classes)}")
        return classes[index - 1]

    def _candidate_indices(self, text: str) -> list[int]:
        if self.index_mode != "builtin":
            return []
        return [
            int(match.group(1))
            for match in re.finditer(r"\bcandidate\s*#\s*(\d+)\s+of\s+\d+\b", text, flags=re.IGNORECASE)
        ]

    def _pick_indexed_candidate(self, site_id: str, text: str, point_class: str | None = None, nth: int = 0) -> PointRow | None:
        indices = self._candidate_indices(text)
        if nth >= len(indices):
            return None
        point_class = point_class or self._infer_point_class(site_id, text)
        if point_class is None:
            return None
        rows = [row for row in self._points(site_id) if row.point_class == point_class]
        index = indices[nth]
        if index < 1 or index > len(rows):
            raise ValueError(f"candidate_index_out_of_range:{index}:{len(rows)}")
        return rows[index - 1]

    def _candidate_rows(self, site_id: str, text: str, point_class: str | None = None) -> list[PointRow]:
        text_norm = norm(text)
        rows = self._points(site_id)
        if point_class is not None:
            rows = [row for row in rows if row.point_class == point_class]
        exact: list[tuple[int, PointRow]] = []
        for row in rows:
            labels = [row.equipment_display(), row.location_display()]
            if self.alias_mode == "builtin":
                labels = sorted({alias for label in labels for alias in asset_aliases(label) if alias})
            for label in labels:
                label_norm = norm(label)
                if label_norm and re.search(rf"\b{re.escape(label_norm)}\b", text_norm):
                    exact.append((len(label_norm), row))
                    break
        exact.sort(key=lambda item: (-item[0], item[1].stream_id))
        return [row for _, row in exact]

    def _resolve_point_call(self, row: PointRow, call_id: str = "c1") -> dict[str, Any]:
        args: dict[str, Any] = {
            "site_id": row.site_id,
            "point_class": row.point_class,
            "equipment_label": row.equipment_label,
        }
        if row.location_label is not None:
            args["location_label"] = row.location_label
        return {"call_id": call_id, "tool_name": "resolve_point", "arguments": args}

    def _list_points_call(self, row: PointRow, call_id: str = "c0") -> dict[str, Any]:
        return {
            "call_id": call_id,
            "tool_name": "list_points",
            "arguments": {"site_id": row.site_id, "point_class": row.point_class},
        }

    def _resolve_point_workflow(self, row: PointRow, resolve_call_id: str = "c1") -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        if self.workflow_mode in {"enumerate", "full_protocol"}:
            calls.append(self._list_points_call(row, "c0"))
        calls.append(self._resolve_point_call(row, resolve_call_id))
        return calls

    def _raw_sample_timestamp(self, text: str) -> str | None:
        if self.workflow_mode != "full_protocol":
            return None
        match = re.search(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)\b", text)
        return match.group(0).replace("Z", "+00:00") if match else None

    def _full_protocol_calls(
        self,
        text: str,
        *,
        stream_ref: str = "$c1.stream_id",
        include_quality: bool = True,
    ) -> list[dict[str, Any]]:
        if self.workflow_mode != "full_protocol":
            return []
        calls: list[dict[str, Any]] = []
        if include_quality:
            calls.append({"call_id": "c_quality", "tool_name": "inspect_quality", "arguments": {"stream_id": stream_ref}})
        sample_ts = self._raw_sample_timestamp(text)
        if sample_ts is not None:
            calls.append(
                {
                    "call_id": "c_sample",
                    "tool_name": "lookup_observation",
                    "arguments": {"stream_id": stream_ref, "timestamp": sample_ts, "mode": "exact"},
                }
            )
        return calls

    def _pick_point(self, site_id: str, text: str, point_class: str | None = None) -> PointRow:
        point_class = point_class or self._infer_point_class(site_id, text)
        indexed = self._pick_indexed_candidate(site_id, text, point_class)
        if indexed is not None:
            return indexed
        candidates = self._candidate_rows(site_id, text, point_class)
        if not candidates:
            raise ValueError(f"no_point_candidate:{site_id}:{point_class}")
        unique_by_stream = {row.stream_id: row for row in candidates}
        if len(unique_by_stream) == 1:
            return next(iter(unique_by_stream.values()))
        if point_class is not None:
            same_class = [row for row in candidates if row.point_class == point_class]
            unique_same_class = {row.stream_id: row for row in same_class}
            if len(unique_same_class) == 1:
                return next(iter(unique_same_class.values()))
        return candidates[0]

    def _execute(self, calls: list[dict[str, Any]]) -> list[ExecutedCall]:
        return self.runtime.execute_call_sequence(calls)

    def solve(self, example: dict[str, Any], slot_mode: str) -> SolveResult:
        text = self._analysis_text(example, slot_mode)
        site_id = parse_site(text, example)
        if site_id is None:
            raise ValueError("missing_site")
        family = example["task_family"]
        if family == "point_disambiguation":
            return self._solve_point_disambiguation(example, text, site_id)
        if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
            return self._solve_aggregate(example, text, site_id)
        if family == "window_pairwise_compare":
            return self._solve_pairwise(example, text, site_id)
        if family == "window_rank":
            return self._solve_rank(example, text, site_id)
        if family == "timestamp_value_lookup":
            return self._solve_timestamp(example, text, site_id, mode="exact")
        if family == "timestamp_nearest_lookup":
            return self._solve_timestamp(example, text, site_id, mode="nearest")
        if family == "quality_gate":
            return self._solve_quality(example, text, site_id)
        raise ValueError(f"unsupported_family:{family}")

    def _solve_point_disambiguation(self, example: dict[str, Any], text: str, site_id: str) -> SolveResult:
        point = self._pick_point(site_id, text)
        calls = self._resolve_point_workflow(point, "c1")
        calls.extend(self._full_protocol_calls(text))
        executed = self._execute(calls)
        resolve_output = next(call.output for call in executed if call.tool_name == "resolve_point")
        stream_id = resolve_output.get("stream_id")
        return SolveResult(executed, {"stream_id": stream_id}, {"stream_ids": [stream_id]})

    def _solve_aggregate(self, example: dict[str, Any], text: str, site_id: str) -> SolveResult:
        point = self._pick_point(site_id, text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = self._resolve_point_workflow(point, "c1")
        calls.append(
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
        )
        calls.extend(self._full_protocol_calls(text))
        executed = self._execute(calls)
        resolve_output = next(call.output for call in executed if call.tool_name == "resolve_point")
        aggregate_output = next(call.output for call in executed if call.tool_name == "aggregate_window")
        stream_id = resolve_output["stream_id"]
        return SolveResult(
            executed,
            {"stream_id": stream_id, "mean_value": aggregate_output["mean_value"]},
            {"stream_ids": [stream_id]},
        )

    def _pair_segments(self, text: str) -> tuple[str, str]:
        core = text
        quote = re.search(r'"([^"]+)"', text)
        if quote:
            core = quote.group(1)
        if ":" in core:
            core = core.split(":", 1)[1]
        core = re.split(r"\?\s*", core, maxsplit=1)[0]
        left, right = re.split(r"\s+or\s+", core, maxsplit=1, flags=re.IGNORECASE)
        return left.strip(), right.strip()

    def _solve_pairwise(self, example: dict[str, Any], text: str, site_id: str) -> SolveResult:
        point_class = self._infer_point_class(site_id, text)
        left_text, right_text = self._pair_segments(text)
        if self.index_mode == "builtin" and len(self._candidate_indices(text)) >= 2:
            left = self._pick_indexed_candidate(site_id, text, point_class, nth=0)
            right = self._pick_indexed_candidate(site_id, text, point_class, nth=1)
            if left is None or right is None:
                raise ValueError("missing_pairwise_candidate_index")
        else:
            left = self._pick_point(site_id, left_text + "\n" + text, point_class)
            right = self._pick_point(site_id, right_text + "\n" + text, point_class)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None:
            raise ValueError("missing_time_window")
        calls: list[dict[str, Any]] = []
        if self.workflow_mode in {"enumerate", "full_protocol"}:
            calls.append(self._list_points_call(left, "c0"))
        calls.extend(
            [
                self._resolve_point_call(left, "c1"),
                self._resolve_point_call(right, "c2"),
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
        )
        if self.workflow_mode == "full_protocol":
            calls.append({"call_id": "c_left_quality", "tool_name": "inspect_quality", "arguments": {"stream_id": "$c1.stream_id"}})
            calls.append({"call_id": "c_right_quality", "tool_name": "inspect_quality", "arguments": {"stream_id": "$c2.stream_id"}})
        executed = self._execute(calls)
        obs = next(call.output for call in executed if call.tool_name == "compare_window")
        resolve_outputs = [call.output for call in executed if call.tool_name == "resolve_point"]
        return SolveResult(
            executed,
            {
                "winning_stream_id": obs["winning_stream_id"],
                "left_mean_value": obs["left_value"],
                "right_mean_value": obs["right_value"],
            },
            {"stream_ids": [resolve_outputs[0]["stream_id"], resolve_outputs[1]["stream_id"]]},
        )

    def _infer_location_type(self, text: str) -> str:
        lowered = text.lower()
        if "conference room" in lowered:
            return "Conference_Room"
        if "room" in lowered:
            return "Room"
        return "Location"

    def _solve_rank(self, example: dict[str, Any], text: str, site_id: str) -> SolveResult:
        point_class = self._infer_point_class(site_id, text)
        if point_class is None:
            raise ValueError("missing_point_class")
        start, end, period = parse_time_window(example, text)
        if start is None or end is None:
            raise ValueError("missing_time_window")
        location_type = self._infer_location_type(text)
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
        executed = self._execute(calls)
        ranked = executed[1].output.get("ranked_streams") or []
        if not ranked:
            raise ValueError("empty_rank")
        winner = ranked[0]
        stream_id = winner["stream_id"]
        extra_calls: list[dict[str, Any]] = []
        if self.workflow_mode in {"enumerate", "full_protocol"}:
            extra_calls.append(
                {
                    "call_id": "c3",
                    "tool_name": "inspect_quality",
                    "arguments": {"stream_id": stream_id},
                }
            )
        if self.workflow_mode == "full_protocol":
            sample_ts = self._raw_sample_timestamp(text)
            if sample_ts is not None:
                extra_calls.append(
                    {
                        "call_id": "c_sample",
                        "tool_name": "lookup_observation",
                        "arguments": {"stream_id": stream_id, "timestamp": sample_ts, "mode": "exact"},
                    }
                )
        if extra_calls:
            executed.extend(self._execute(extra_calls))
        return SolveResult(executed, {"stream_id": stream_id, "mean_value": winner["mean_value"]}, {"stream_ids": [stream_id]})

    def _solve_timestamp(self, example: dict[str, Any], text: str, site_id: str, mode: str) -> SolveResult:
        point = self._pick_point(site_id, text)
        ts = parse_timestamp(text)
        if ts is None:
            raise ValueError("missing_timestamp")
        calls = self._resolve_point_workflow(point, "c1")
        calls.append(
            {
                "call_id": "c2",
                "tool_name": "lookup_observation",
                "arguments": {"stream_id": "$c1.stream_id", "timestamp": ts.isoformat(), "mode": mode},
            },
        )
        calls.extend(self._full_protocol_calls(text))
        executed = self._execute(calls)
        obs = next(call.output for call in executed if call.call_id == "c2")
        final = {
            "stream_id": obs["stream_id"],
            "observed_timestamp": obs.get("observed_timestamp"),
            "value": obs.get("value"),
            "exact_match_found": obs["exact_match_found"],
        }
        if mode == "nearest":
            final["fallback_reason"] = obs.get("fallback_reason")
            final["offset_seconds"] = obs.get("offset_seconds")
        resolve_output = next(call.output for call in executed if call.tool_name == "resolve_point")
        return SolveResult(executed, final, {"stream_ids": [resolve_output["stream_id"]]})

    def _solve_quality(self, example: dict[str, Any], text: str, site_id: str) -> SolveResult:
        point = self._pick_point(site_id, text)
        start, end, period = parse_time_window(example, text)
        if start is None or end is None or period is None:
            raise ValueError("missing_time_window")
        calls = self._resolve_point_workflow(point, "c1")
        calls.append(
            {
                "call_id": "c2",
                "tool_name": "inspect_quality_window",
                "arguments": {
                    "stream_id": "$c1.stream_id",
                    "window_start": freeze_time(start),
                    "window_end": freeze_time(end),
                    "period": period,
                },
            }
        )
        calls.extend(self._full_protocol_calls(text, include_quality=False))
        executed = self._execute(calls)
        quality = next(call.output for call in executed if call.tool_name == "inspect_quality_window")
        ref = quality.get("quality_reference") or self.runtime.window_quality_reference("week")
        decision = "answer"
        reason = "healthy"
        if quality["observed_fraction"] is not None and ref.get("abstain_observed_fraction_below") is not None:
            if quality["observed_fraction"] < ref["abstain_observed_fraction_below"]:
                decision = "abstain"
                reason = "low_coverage"
        if decision != "abstain" and quality["gap_ratio"] is not None and ref.get("abstain_gap_ratio_above") is not None:
            if quality["gap_ratio"] > ref["abstain_gap_ratio_above"]:
                decision = "abstain"
                reason = "long_gap"
        return SolveResult(
            executed,
            {
                "decision": decision,
                "reason": reason,
                "observed_fraction": quality["observed_fraction"],
                "gap_ratio": quality["gap_ratio"],
            },
            {"stream_ids": [next(call.output for call in executed if call.tool_name == "resolve_point")["stream_id"]]},
        )


def run_solver(
    runtime: ToolStoreRuntime,
    examples: list[dict[str, Any]],
    slot_mode: str,
    alias_mode: str,
    index_mode: str,
    workflow_mode: str,
) -> list[dict[str, Any]]:
    solver = StrongBtsSolver(runtime, alias_mode=alias_mode, index_mode=index_mode, workflow_mode=workflow_mode)
    rows: list[dict[str, Any]] = []
    for example in examples:
        try:
            solved = solver.solve(example, slot_mode=slot_mode)
        except Exception as exc:
            solved = SolveResult([], None, None, f"solver_error:{type(exc).__name__}:{exc}")
        verification = verify_prediction(example, solved.executed_calls, solved.final_answer, solved.evidence, runtime)
        if solved.parse_error:
            verification.issues.append(solved.parse_error)
            if verification.strict_label == "accomplished":
                verification.strict_label = "partially_accomplished"
        rows.append(
            {
                "scenario_id": example["scenario_id"],
                "task_family": example["task_family"],
                "interaction_mode": example.get("interaction_mode"),
                "slot_mode": slot_mode,
                "alias_mode": alias_mode,
                "index_mode": index_mode,
                "workflow_mode": workflow_mode,
                "parse_error": solved.parse_error,
                "executed_calls": [call.as_dict() for call in solved.executed_calls],
                "final_answer": solved.final_answer,
                "evidence": solved.evidence,
                "verification": verification.as_dict(),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(row["verification"]["label"] for row in rows)
    by_family: dict[str, Counter[str]] = {}
    issues = Counter()
    for row in rows:
        by_family.setdefault(row["task_family"], Counter())[row["verification"]["label"]] += 1
        issues.update(row["verification"]["issues"])
    return {
        "scenario_count": len(rows),
        "label_counts": dict(labels),
        "strict_label_counts": dict(Counter(row["verification"].get("strict_label", row["verification"]["label"]) for row in rows)),
        "mean_process_score": round(mean(row["verification"]["process_score"] for row in rows), 4) if rows else 0.0,
        "mean_final_score": round(mean(row["verification"]["final_score"] for row in rows), 4) if rows else 0.0,
        "mean_evidence_score": round(mean(row["verification"]["evidence_score"] for row in rows), 4) if rows else 0.0,
        "mean_core_score": round(mean(row["verification"]["core_score"] for row in rows), 4) if rows else 0.0,
        "mean_reporting_score": round(mean(row["verification"]["reporting_score"] for row in rows), 4) if rows else 0.0,
        "mean_grounding_score": round(mean(row["verification"]["grounding_score"] for row in rows), 4) if rows else 0.0,
        "mean_temporal_score": round(mean(row["verification"]["temporal_score"] for row in rows), 4) if rows else 0.0,
        "mean_task_score": round(mean(row["verification"]["task_score"] for row in rows), 4) if rows else 0.0,
        "by_family": {family: dict(counter) for family, counter in sorted(by_family.items())},
        "top_issues": issues.most_common(30),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--benchmark-dir", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "dev", "test"], default="test")
    parser.add_argument("--slot-mode", choices=["standard", "all"], default="standard")
    parser.add_argument("--alias-mode", choices=["none", "builtin"], default="none")
    parser.add_argument("--index-mode", choices=["none", "builtin"], default="none")
    parser.add_argument("--workflow-mode", choices=["none", "enumerate", "full_protocol"], default="none")
    parser.add_argument("--out-jsonl", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    args = parser.parse_args()

    examples = load_jsonl(args.benchmark_dir / f"{args.split}.jsonl")
    runtime = ToolStoreRuntime(args.tool_store_db)
    try:
        rows = run_solver(
            runtime,
            examples,
            slot_mode=args.slot_mode,
            alias_mode=args.alias_mode,
            index_mode=args.index_mode,
            workflow_mode=args.workflow_mode,
        )
    finally:
        runtime.close()

    summary = summarize(rows)
    if args.workflow_mode == "full_protocol":
        summary["solver"] = "strong_symbolic_sql_solver_v5_full_protocol"
    elif args.workflow_mode == "enumerate":
        summary["solver"] = "strong_symbolic_sql_solver_v4_workflow_aware"
    elif args.index_mode == "builtin":
        summary["solver"] = "strong_symbolic_sql_solver_v3_index_aware"
    elif args.alias_mode == "none":
        summary["solver"] = "strong_symbolic_sql_solver_v1"
    else:
        summary["solver"] = "strong_symbolic_sql_solver_v2_alias_aware"
    summary["slot_mode"] = args.slot_mode
    summary["alias_mode"] = args.alias_mode
    summary["index_mode"] = args.index_mode
    summary["workflow_mode"] = args.workflow_mode
    summary["split"] = args.split

    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.out_jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    args.out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
