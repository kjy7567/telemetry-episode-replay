from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from collections import defaultdict
import re

import duckdb
import pandas as pd

from .preprocess import compute_quality_metrics
from .raw import extract_stream_history

REPO_ROOT = Path(__file__).resolve().parents[2]


SCENARIO_TOOL_REGISTRY = {
    "version": "scenario-canonical-v2",
    "tools": [
        {
            "tool_name": "resolve_point",
            "description": "Resolve a single telemetry stream from site, point class, equipment, and optional location context.",
            "arguments": {
                "site_id": "building site identifier",
                "point_class": "Brick point class",
                "equipment_label": "equipment alias",
                "location_label": "location alias",
            },
        },
        {
            "tool_name": "list_points",
            "description": "List candidate stream identifiers under site and schema constraints.",
            "arguments": {
                "site_id": "building site identifier",
                "point_class": "Brick point class",
                "equipment_type": "Brick equipment type",
                "location_type": "Brick location type",
            },
        },
        {
            "tool_name": "aggregate_window",
            "description": "Aggregate a single stream over a specific time window using precomputed summaries.",
            "arguments": {
                "stream_id": "stream identifier or reference",
                "metric": "aggregation metric such as mean_value or max_value",
                "window_start": "inclusive ISO timestamp",
                "window_end": "exclusive ISO timestamp",
                "period": "day, week, or month",
            },
        },
        {
            "tool_name": "compare_window",
            "description": "Compare two streams over the same time window using a selected metric.",
            "arguments": {
                "left_stream_id": "left stream identifier or reference",
                "right_stream_id": "right stream identifier or reference",
                "metric": "aggregation metric such as mean_value",
                "window_start": "inclusive ISO timestamp",
                "window_end": "exclusive ISO timestamp",
                "period": "day, week, or month",
            },
        },
        {
            "tool_name": "rank_window",
            "description": "Rank a set of candidate streams over a selected time window.",
            "arguments": {
                "stream_ids": "candidate stream identifiers or reference",
                "metric": "aggregation metric such as mean_value or std_value",
                "window_start": "inclusive ISO timestamp",
                "window_end": "exclusive ISO timestamp",
                "period": "day, week, or month",
                "order": "asc or desc",
                "topk": "number of returned items",
            },
        },
        {
            "tool_name": "lookup_observation",
            "description": "Retrieve an observation from a stream at a requested timestamp using exact match or nearest-neighbor fallback.",
            "arguments": {
                "stream_id": "stream identifier or reference",
                "timestamp": "requested ISO timestamp",
                "mode": "exact or nearest",
            },
        },
        {
            "tool_name": "inspect_quality",
            "description": "Inspect coverage, missingness, duplicate timestamps, and constant-signal indicators for a stream.",
            "arguments": {
                "stream_id": "stream identifier or reference",
            },
        },
        {
            "tool_name": "inspect_quality_window",
            "description": "Inspect coverage and gap statistics for a stream over a specific time window.",
            "arguments": {
                "stream_id": "stream identifier or reference",
                "window_start": "inclusive ISO timestamp",
                "window_end": "exclusive ISO timestamp",
                "period": "day, week, or month",
            },
        },
    ],
}


AGGREGATION_COMPATIBILITY_REGEX = r"(_Sensor|_Setpoint|_Limit)$"
AGGREGATION_BLACKLIST_REGEX = r"(^Time_|^Duration_Sensor$|_Energy_Sensor$|^Chilled_Water_Supply_Flow_Sensor$|_Parameter$)"
TIMESTAMP_COMPATIBILITY_REGEX = r"(_Sensor|_Setpoint|_Limit)$"
TIMESTAMP_BLACKLIST_REGEX = r"(_Parameter$)"
QUALITY_COMPATIBILITY_REGEX = r"(_Sensor|_Setpoint|_Limit)$"
QUALITY_BLACKLIST_REGEX = r"(^Time_|^Duration_Sensor$|_Energy_Sensor$|_Parameter$|^Min_Limit$|^Max_Limit$)"
VALID_POINT_CLASS_REGEX = r"^[A-Z][A-Za-z0-9_]*$"
QUERY_SURFACE_VERSION = "operator-robust-v1"

EQUIPMENT_ALIAS_REWRITES = [
    ("Conference Room", "conference room"),
    ("Fcu", "FCU"),
    ("Ahu", "AHU"),
    ("Vav", "VAV"),
    ("Fan Coil Unit", "FCU"),
    ("Air Handler Unit", "AHU"),
    ("Variable Air Volume", "VAV"),
    ("Weather Station", "weather station"),
    ("Water Meter", "water meter"),
    ("Gas Meter", "gas meter"),
    ("Heat Pump", "heat pump"),
    ("Substation", "substation"),
    ("Building", "building"),
    ("Equipment", "equipment"),
    ("Room", "room"),
]


def is_missing(value: object | None) -> bool:
    return value is None or value != value


def clean_text(value: object | None) -> str | None:
    if is_missing(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def sanitize_json(value: object):
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if is_missing(value):
        return None
    return value


def iso(ts: object) -> str:
    return str(ts)


def round_float(value: object | None, digits: int = 4) -> float | None:
    if is_missing(value):
        return None
    return round(float(value), digits)


def maybe_location(location_label: object | None) -> str:
    text = clean_text(location_label)
    return f" in {text}" if text else ""


def canonical_call(call_id: str, tool_name: str, **arguments: object) -> dict:
    canonical_args = {}
    for key, value in arguments.items():
        sanitized = sanitize_json(value)
        if sanitized is None:
            continue
        canonical_args[key] = sanitized
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": canonical_args,
    }


def clone_calls(calls: list[dict]) -> list[dict]:
    return copy.deepcopy(calls)


def set_call_argument(call: dict, key: str, value: object | None) -> dict:
    updated = copy.deepcopy(call)
    if value is None:
        updated["arguments"].pop(key, None)
    else:
        updated["arguments"][key] = sanitize_json(value)
    return updated


def dedupe_call_sets(call_sets: list[list[dict]] | None) -> list[list[dict]] | None:
    if not call_sets:
        return None
    seen: set[str] = set()
    deduped: list[list[dict]] = []
    for call_set in call_sets:
        key = json.dumps(sanitize_json(call_set), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call_set)
    return deduped


def location_optional_for_resolve(
    con: duckdb.DuckDBPyConnection,
    site_id: str,
    point_class: str,
    equipment_label: str,
) -> bool:
    row = con.execute(
        """
        select count(distinct stream_id)
        from tool_ready_points
        where site_id = ? and point_class = ? and equipment_label = ?
        """,
        (site_id, point_class, equipment_label),
    ).fetchone()
    return bool(row and int(row[0]) == 1)


def build_task_accomplish_verifier(
    *,
    required_tools: list[str],
    required_answer_fields: list[str],
    numeric_tolerance: dict[str, float] | None = None,
    categorical_exact_match: list[str] | None = None,
    required_stream_ids: list[str] | None = None,
) -> dict:
    return {
        "verifier_name": "task_accomplish_v1",
        "label_space": ["accomplished", "partially_accomplished", "not_accomplished"],
        "final_answer_checks": {
            "required_fields": required_answer_fields,
            "numeric_tolerance": numeric_tolerance or {},
            "categorical_exact_match": categorical_exact_match or required_answer_fields,
        },
        "process_checks": {
            "required_tools": required_tools,
            "allow_additional_tools": True,
        },
        "evidence_checks": {
            "required_stream_ids": required_stream_ids or [],
        },
    }


def difficulty_proxy(
    *,
    required_tool_count: int,
    temporal_normalization_count: int,
    ambiguity_candidates: int,
    graph_hops: int,
    aggregation_operations: int,
) -> dict:
    return {
        "required_tool_count": required_tool_count,
        "temporal_normalization_count": temporal_normalization_count,
        "ambiguity_candidates": ambiguity_candidates,
        "graph_hops": graph_hops,
        "aggregation_operations": aggregation_operations,
    }


@dataclass
class ScenarioExample:
    split: str
    task_family: str
    site_id: str
    query: str
    canonical_tool_calls: list[dict]
    acceptable_tool_call_sets: list[list[dict]] | None
    gold_final_answer: dict
    evidence: dict
    task_accomplish_verifier: dict
    difficulty_proxy: dict
    metadata: dict

    def as_dict(self, scenario_id: str) -> dict:
        payload = {
            "scenario_id": scenario_id,
            "split": self.split,
            "task_family": self.task_family,
            "site_id": self.site_id,
            "query": self.query,
            "query_surface_version": QUERY_SURFACE_VERSION,
            "tool_registry_version": SCENARIO_TOOL_REGISTRY["version"],
            "canonical_tool_calls": self.canonical_tool_calls,
            "acceptable_tool_call_sets": self.acceptable_tool_call_sets or [self.canonical_tool_calls],
            "gold_final_answer": self.gold_final_answer,
            "evidence": self.evidence,
            "task_accomplish_verifier": self.task_accomplish_verifier,
            "difficulty_proxy": self.difficulty_proxy,
            "metadata": self.metadata,
        }
        return sanitize_json(payload)


def build_scenario_id(split: str, task_family: str, index: int) -> str:
    return f"{split}_{task_family}_{index:05d}"


def select_split(site_id: str, non_test_seen: int, heldout_site_ids: frozenset[str]) -> str:
    if site_id in heldout_site_ids:
        return "test"
    return "dev" if non_test_seen % 5 == 4 else "train"


def quarter_bucket(value: object) -> str | None:
    if is_missing(value):
        return None
    text = str(value)
    month = int(text[5:7])
    quarter = ((month - 1) // 3) + 1
    return f"Q{quarter}"


@lru_cache(maxsize=4096)
def load_raw_history_cached(zip_path: str, member_name: str | None, stream_id: str) -> pd.DataFrame:
    member = member_name if member_name else None
    path = Path(zip_path)
    if not path.is_absolute():
        configured_raw_dir = os.environ.get("BTS_RAW_DIR")
        configured_path = Path(configured_raw_dir) / path.name if configured_raw_dir else None
        repository_path = REPO_ROOT / path
        if configured_path is not None and configured_path.exists():
            path = configured_path
        elif repository_path.exists():
            path = repository_path
        else:
            raise FileNotFoundError(
                f"Cannot resolve raw archive {zip_path!r}; set BTS_RAW_DIR to the directory "
                f"containing {path.name}."
            )
    return extract_stream_history(path, stream_id, member)


def valid_raw_history(zip_path: str, member_name: str | None, stream_id: str) -> pd.DataFrame:
    frame = load_raw_history_cached(zip_path, member_name, stream_id).copy()
    frame = frame.dropna(subset=["timestamp", "value"]).sort_values("timestamp").reset_index(drop=True)
    return frame


@dataclass
class QuerySurface:
    text: str
    template_id: str
    surface_transforms: list[str]


def dedupe_preserve(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def stable_choice(options: list[str], *parts: object) -> tuple[int, str]:
    if not options:
        raise ValueError("stable_choice requires a non-empty option list")
    seed = "||".join("" if part is None else str(part) for part in parts)
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(options)
    return index, options[index]


def normalize_for_overlap(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def display_identifier(text: object | None) -> str | None:
    clean = clean_text(text)
    if clean is None:
        return None
    return clean.replace("_", " ").strip()


def render_date_phrase(value: object) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return f"{ts.strftime('%B')} {ts.day}, {ts.year}"


def render_month_phrase(value: object) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return f"{ts.strftime('%B')} {ts.year}"


def render_timestamp_phrase(value: object) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return f"{ts.strftime('%H:%M')} UTC on {render_date_phrase(ts)}"


def rewrite_equipment_alias(text: str) -> str:
    rewritten = text
    for source, target in EQUIPMENT_ALIAS_REWRITES:
        rewritten = rewritten.replace(source, target)
    return rewritten


def equipment_display(site_id: str, equipment_label: object | None) -> str | None:
    clean = clean_text(equipment_label)
    if clean is None:
        return None
    if clean == site_id:
        return "the site"
    prefix = f"{site_id} "
    if clean.startswith(prefix):
        clean = clean[len(prefix):]
    return rewrite_equipment_alias(clean)


def equipment_phrase(equipment_ref: str | None, location_ref: str, *, relation: str) -> str:
    if not equipment_ref:
        return ""
    if equipment_ref == "the site":
        if location_ref:
            return f" for the site{location_ref}"
        return " for the site"
    if relation == "attached":
        return f" attached to {equipment_ref}{location_ref}"
    if relation == "at":
        return f" at {equipment_ref}{location_ref}"
    return f" on {equipment_ref}{location_ref}"


def location_display(site_id: str, location_label: object | None) -> str | None:
    clean = clean_text(location_label)
    if clean is None:
        return None
    prefix = f"{site_id} "
    if clean.startswith(prefix):
        clean = clean[len(prefix):]
    return rewrite_equipment_alias(clean)


def point_class_phrase(point_class: str, *seed_parts: object, mode: str = "default") -> str:
    words = point_class.split("_")
    if len(words) >= 2:
        stem = " ".join(word.lower() for word in words[:-1])
        suffix = words[-1]
    else:
        stem = point_class.replace("_", " ").lower()
        suffix = ""
    if suffix == "Sensor":
        if mode == "value":
            options = [f"{stem} reading", f"{stem} measurement"]
        else:
            options = [f"{stem} sensor", f"{stem} reading", f"{stem} measurement"]
    elif suffix == "Setpoint":
        options = [f"{stem} setpoint", f"{stem} setting"]
    elif suffix == "Limit":
        options = [f"{stem} limit", f"{stem} threshold"]
    elif suffix == "Status":
        options = [f"{stem} status", f"{stem} state"]
    else:
        options = [point_class.replace("_", " ").lower()]
    _, selected = stable_choice(options, "point_class", point_class, *seed_parts)
    return selected


def location_clause(site_id: str, equipment_label: object | None, location_label: object | None, *seed_parts: object) -> tuple[str, list[str]]:
    rendered_location = location_display(site_id, location_label)
    if rendered_location is None:
        return "", []
    if rendered_location == site_id:
        return "", []
    equipment_ref = equipment_display(site_id, equipment_label) or ""
    if equipment_ref == "the site":
        return "", []
    equipment_ref = equipment_display(site_id, equipment_label) or ""
    if normalize_for_overlap(rendered_location) and normalize_for_overlap(rendered_location) in normalize_for_overlap(equipment_ref):
        return "", []
    options = [f" in {rendered_location}", f" serving {rendered_location}"]
    _, selected = stable_choice(options, "location_clause", site_id, equipment_label, location_label, *seed_parts)
    transforms = ["reference_compression"] if rendered_location != clean_text(location_label) else []
    return selected, transforms


def group_scope_phrase(location_type: str | None) -> str:
    text = display_identifier(location_type)
    if text is None:
        return "matching locations"
    lowered = text.lower()
    if lowered == "location":
        return "locations"
    if lowered.endswith(" location"):
        return f"{lowered}s"
    if lowered.endswith(" locations"):
        return lowered
    return f"{lowered} locations"


def surface_metadata(metadata: dict, rendered: QuerySurface) -> dict:
    merged = dict(metadata)
    merged["query_surface_version"] = QUERY_SURFACE_VERSION
    merged["surface_template_id"] = rendered.template_id
    merged["surface_transforms"] = rendered.surface_transforms
    return merged


def render_point_disambiguation_query(site_id: str, point_class: str, equipment_label: str, location_label: str | None) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label)
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "point_disambiguation")
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    templates = [
        f"For {site_id}, which stream should I use for the {point_phrase}{asset_ref}?",
        f"In {site_id}, which stream matches the {point_phrase}{asset_ref}?",
        f"I need the stream behind the {point_phrase}{asset_ref} in {site_id}. Which one is it?",
    ]
    template_idx, text = stable_choice(templates, "point_disambiguation", site_id, point_class, equipment_label, location_label)
    transforms = dedupe_preserve(["operator_phrasing", "alias_substitution", "reference_compression", *location_transforms])
    return QuerySurface(text=text, template_id=f"point_disambiguation_t{template_idx + 1}", surface_transforms=transforms)


def render_window_mean_query(site_id: str, point_class: str, equipment_label: str, location_label: str | None, window_start: object) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label, window_start, mode="value")
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "window_mean", window_start)
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    week_phrase = render_date_phrase(window_start)
    templates = [
        f"For {site_id}, over the week beginning {week_phrase}, what was the average {point_phrase}{asset_ref}?",
        f"In {site_id}, what was the weekly average for the {point_phrase}{asset_ref} during the week of {week_phrase}?",
        f"Looking at {site_id}, what was the average {point_phrase}{asset_ref} across the week starting {week_phrase}?",
    ]
    template_idx, text = stable_choice(templates, "window_mean_lookup", site_id, point_class, equipment_label, location_label, window_start)
    transforms = dedupe_preserve(["operator_phrasing", "alias_substitution", "calendar_date_rendering", "reference_compression", *location_transforms])
    return QuerySurface(text=text, template_id=f"window_mean_lookup_t{template_idx + 1}", surface_transforms=transforms)


def render_day_mean_query(site_id: str, point_class: str, equipment_label: str, location_label: str | None, window_start: object) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label, window_start, mode="value")
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "day_mean", window_start)
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    date_phrase = render_date_phrase(window_start)
    templates = [
        f"In {site_id}, what was the average {point_phrase}{asset_ref} on {date_phrase}?",
        f"For {site_id}, what was the day-average {point_phrase}{asset_ref} on {date_phrase}?",
        f"On {date_phrase}, what did the {point_phrase}{asset_ref} average out to in {site_id}?",
    ]
    template_idx, text = stable_choice(templates, "day_mean_lookup", site_id, point_class, equipment_label, location_label, window_start)
    transforms = dedupe_preserve(["operator_phrasing", "alias_substitution", "calendar_date_rendering", "reference_compression", *location_transforms])
    return QuerySurface(text=text, template_id=f"day_mean_lookup_t{template_idx + 1}", surface_transforms=transforms)


def render_relative_24h_query(site_id: str, point_class: str, equipment_label: str, location_label: str | None, window_end: object) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label, window_end, mode="value")
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "relative_24h", window_end)
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    anchor_phrase = render_timestamp_phrase(window_end)
    templates = [
        f"In the 24 hours leading up to {anchor_phrase}, what was the average {point_phrase}{asset_ref} in {site_id}?",
        f"For {site_id}, what did the {point_phrase}{asset_ref} average over the previous 24 hours as of {anchor_phrase}?",
        f"As of {anchor_phrase}, what was the prior-day average for the {point_phrase}{asset_ref} in {site_id}?",
    ]
    template_idx, text = stable_choice(templates, "relative_24h_mean_lookup", site_id, point_class, equipment_label, location_label, window_end)
    transforms = dedupe_preserve(
        ["operator_phrasing", "alias_substitution", "relative_time_rewriting", "clock_time_rendering", "reference_compression", *location_transforms]
    )
    return QuerySurface(text=text, template_id=f"relative_24h_mean_lookup_t{template_idx + 1}", surface_transforms=transforms)


def render_window_pairwise_query(
    site_id: str,
    point_class: str,
    left_equipment_label: str,
    left_location_label: str | None,
    right_equipment_label: str,
    right_location_label: str | None,
    window_start: object,
) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, left_equipment_label, right_equipment_label, window_start, mode="value")
    left_ref = equipment_display(site_id, left_equipment_label) or left_equipment_label
    right_ref = equipment_display(site_id, right_equipment_label) or right_equipment_label
    left_loc, left_transforms = location_clause(site_id, left_equipment_label, left_location_label, "window_pairwise", window_start, "left")
    right_loc, right_transforms = location_clause(site_id, right_equipment_label, right_location_label, "window_pairwise", window_start, "right")
    week_phrase = render_date_phrase(window_start)
    templates = [
        f"For {site_id}, during the week beginning {week_phrase}, which had the higher average {point_phrase}: {left_ref}{left_loc} or {right_ref}{right_loc}?",
        f"In {site_id}, which {point_phrase} averaged higher over the week of {week_phrase}: {left_ref}{left_loc} or {right_ref}{right_loc}?",
        f"Looking at the week starting {week_phrase} in {site_id}, which side came out higher on average for {point_phrase}: {left_ref}{left_loc} or {right_ref}{right_loc}?",
    ]
    template_idx, text = stable_choice(
        templates,
        "window_pairwise_compare",
        site_id,
        point_class,
        left_equipment_label,
        right_equipment_label,
        window_start,
    )
    transforms = dedupe_preserve(
        [
            "operator_phrasing",
            "alias_substitution",
            "calendar_date_rendering",
            "reference_compression",
            *left_transforms,
            *right_transforms,
        ]
    )
    return QuerySurface(text=text, template_id=f"window_pairwise_compare_t{template_idx + 1}", surface_transforms=transforms)


def render_window_rank_query(site_id: str, point_class: str, location_type: str, window_start: object) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, location_type, window_start, mode="value")
    scope = group_scope_phrase(location_type)
    month_phrase = render_month_phrase(window_start)
    templates = [
        f"Across {scope} in {site_id}, which {point_phrase} stream was highest on average in {month_phrase}?",
        f"For {site_id}, which {point_phrase} ranked highest on average across {scope} during {month_phrase}?",
        f"In {site_id}, looking across {scope}, which stream topped the average {point_phrase} readings in {month_phrase}?",
    ]
    template_idx, text = stable_choice(templates, "window_rank", site_id, point_class, location_type, window_start)
    transforms = dedupe_preserve(["operator_phrasing", "alias_substitution", "calendar_date_rendering", "group_scope_rendering"])
    return QuerySurface(text=text, template_id=f"window_rank_t{template_idx + 1}", surface_transforms=transforms)


def render_timestamp_exact_query(site_id: str, point_class: str, equipment_label: str, location_label: str | None, requested_timestamp: object) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label, requested_timestamp, mode="value")
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "timestamp_exact", requested_timestamp)
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    time_phrase = render_timestamp_phrase(requested_timestamp)
    templates = [
        f"At {time_phrase}, what reading did the {point_phrase}{asset_ref} report in {site_id}?",
        f"For {site_id}, what value was recorded for the {point_phrase}{asset_ref} at {time_phrase}?",
        f"In {site_id}, what did the {point_phrase}{asset_ref} read at {time_phrase}?",
    ]
    template_idx, text = stable_choice(templates, "timestamp_value_lookup", site_id, point_class, equipment_label, location_label, requested_timestamp)
    transforms = dedupe_preserve(["operator_phrasing", "alias_substitution", "clock_time_rendering", "reference_compression", *location_transforms])
    return QuerySurface(text=text, template_id=f"timestamp_value_lookup_t{template_idx + 1}", surface_transforms=transforms)


def render_timestamp_nearest_query(site_id: str, point_class: str, equipment_label: str, location_label: str | None, requested_timestamp: object) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label, requested_timestamp, mode="value")
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "timestamp_nearest", requested_timestamp)
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    time_phrase = render_timestamp_phrase(requested_timestamp)
    templates = [
        f"If there is no exact sample at {time_phrase}, what is the nearest available observation for the {point_phrase}{asset_ref} in {site_id}?",
        f"For {site_id}, if the {point_phrase}{asset_ref} has no exact reading at {time_phrase}, which nearest available observation should the agent return?",
        f"In {site_id}, when {time_phrase} has no exact value for the {point_phrase}{asset_ref}, what is the nearest available observation?",
    ]
    template_idx, text = stable_choice(templates, "timestamp_nearest_lookup", site_id, point_class, equipment_label, location_label, requested_timestamp)
    transforms = dedupe_preserve(
        ["operator_phrasing", "alias_substitution", "clock_time_rendering", "fallback_explicitness", "reference_compression", *location_transforms]
    )
    return QuerySurface(text=text, template_id=f"timestamp_nearest_lookup_t{template_idx + 1}", surface_transforms=transforms)


def render_quality_gate_query(
    site_id: str,
    point_class: str,
    equipment_label: str,
    location_label: str | None,
    window_start: object,
) -> QuerySurface:
    point_phrase = point_class_phrase(point_class, site_id, equipment_label, location_label, window_start, mode="value")
    equipment_ref = equipment_display(site_id, equipment_label) or equipment_label
    location_ref, location_transforms = location_clause(site_id, equipment_label, location_label, "quality_gate", window_start)
    asset_ref = equipment_phrase(equipment_ref, location_ref, relation="attached")
    week_phrase = render_date_phrase(window_start)
    templates = [
        f"In {site_id}, should an agent answer a weekly trend request for the {point_phrase}{asset_ref} for the week beginning {week_phrase}, or abstain because the data quality is not reliable enough?",
        f"For {site_id}, would you trust the {point_phrase}{asset_ref} enough to answer a weekly trend question for the week of {week_phrase}, or should the agent abstain?",
        f"Looking at {site_id}, should the agent answer or abstain on a weekly trend request for the {point_phrase}{asset_ref} for the week beginning {week_phrase} because of signal quality concerns?",
    ]
    template_idx, text = stable_choice(templates, "quality_gate", site_id, point_class, equipment_label, location_label, window_start)
    transforms = dedupe_preserve(["operator_phrasing", "alias_substitution", "calendar_grounding", "reference_compression", *location_transforms])
    return QuerySurface(text=text, template_id=f"quality_gate_t{template_idx + 1}", surface_transforms=transforms)


def valid_history_from_store(
    con: duckdb.DuckDBPyConnection,
    zip_path: str | None,
    member_name: str | None,
    stream_id: str,
) -> pd.DataFrame:
    if zip_path is not None:
        return valid_raw_history(zip_path, member_name, stream_id)
    frame = con.execute(
        """
        select timestamp, value
        from raw_observations
        where stream_id = ?
        order by timestamp
        """,
        (stream_id,),
    ).fetchdf()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.dropna(subset=["timestamp", "value"]).sort_values("timestamp").reset_index(drop=True)
    return frame


def quality_reference_for_period(
    con: duckdb.DuckDBPyConnection,
    period: str,
) -> dict[str, float | None]:
    if period == "week":
        sample_query = """
            with weekly_window_support as (
                select
                    t.stream_id,
                    t.raw_zip_path,
                    t.raw_member_name,
                    w.window_start,
                    w.window_end,
                    row_number() over (
                        partition by t.site_id, t.stream_id
                        order by w.window_start
                    ) as per_stream_rank,
                    row_number() over (
                        partition by t.site_id
                        order by t.point_class, t.stream_id, w.window_start
                    ) as site_rank
                from tool_ready_points t
                join weekly_aggregates w using (site_id, stream_id)
                join quality_metrics q using (site_id, stream_id)
                where
                    t.candidate_ambiguity = 1
                    and t.point_class is not null
                    and t.point_class <> 'Point'
                    and t.equipment_label is not null
                    and regexp_matches(t.point_class, '{valid}')
                    and regexp_matches(t.point_class, '{compat}')
                    and not regexp_matches(t.point_class, '{blacklist}')
                    and q.median_step_seconds is not null
                    and q.median_step_seconds > 0
            )
            select *
            from weekly_window_support
            where per_stream_rank <= 8 and site_rank <= 8000
            order by stream_id, window_start
        """.format(
            compat=QUALITY_COMPATIBILITY_REGEX,
            blacklist=QUALITY_BLACKLIST_REGEX,
            valid=VALID_POINT_CLASS_REGEX,
        )
        sample_rows = fetch_rows(con, sample_query)
        observed_values: list[float] = []
        gap_values: list[float] = []
        for row in sample_rows:
            frame = valid_history_from_store(con, row["raw_zip_path"], row["raw_member_name"], row["stream_id"])
            metrics = exact_window_quality(frame, row["window_start"], row["window_end"])
            observed = metrics.get("observed_fraction")
            gap_ratio = metrics.get("gap_ratio")
            if observed is not None:
                observed_values.append(float(observed))
            if gap_ratio is not None:
                gap_values.append(float(gap_ratio))
            if len(observed_values) >= 2000 and len(gap_values) >= 2000:
                break
        if observed_values:
            observed_series = pd.Series(observed_values)
            gap_series = pd.Series(gap_values) if gap_values else None
            return {
                "abstain_observed_fraction_below": round(float(observed_series.quantile(0.10)), 4),
                "answer_observed_fraction_at_least": round(float(observed_series.quantile(0.75)), 4),
                "answer_gap_ratio_at_most": round(float(gap_series.quantile(0.50)), 4) if gap_series is not None and not gap_series.empty else None,
                "abstain_gap_ratio_above": round(float(gap_series.quantile(0.85)), 4) if gap_series is not None and not gap_series.empty else None,
            }

    aggregate_table = {
        "day": "daily_aggregates",
        "week": "weekly_aggregates",
        "month": "monthly_aggregates",
    }[period]
    coverage_row = con.execute(
        f"""
        with window_coverage as (
            select
                least(
                    1.0,
                    a.count / greatest(
                        1.0,
                        floor((epoch(a.window_end) - epoch(a.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as observed_fraction
            from {aggregate_table} a
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        )
        select
            quantile_cont(observed_fraction, 0.10),
            quantile_cont(observed_fraction, 0.75)
        from window_coverage
        """
    ).fetchone()
    gap_row = con.execute(
        """
        select
            quantile_cont(longest_gap_seconds / nullif(median_step_seconds, 0), 0.50),
            quantile_cont(longest_gap_seconds / nullif(median_step_seconds, 0), 0.95)
        from quality_metrics
        where median_step_seconds is not null and median_step_seconds > 0
        """
    ).fetchone()
    return {
        "abstain_observed_fraction_below": round(float(coverage_row[0]), 4) if coverage_row and coverage_row[0] is not None else None,
        "answer_observed_fraction_at_least": round(float(coverage_row[1]), 4) if coverage_row and coverage_row[1] is not None else None,
        "answer_gap_ratio_at_most": round(float(gap_row[0]), 4) if gap_row and gap_row[0] is not None else None,
        "abstain_gap_ratio_above": round(float(gap_row[1]), 4) if gap_row and gap_row[1] is not None else None,
    }


def exact_window_quality(
    frame: pd.DataFrame,
    window_start: object,
    window_end: object,
) -> dict[str, float | int | None]:
    start = pd.Timestamp(window_start)
    end = pd.Timestamp(window_end)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")

    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    window = frame.loc[(timestamps >= start) & (timestamps < end)].copy()
    if window.empty:
        return {
            "n_points": 0,
            "observed_fraction": None,
            "gap_ratio": None,
            "duplicate_timestamp_fraction": None,
            "nan_fraction": None,
        }

    metrics = compute_quality_metrics(
        pd.DatetimeIndex(pd.to_datetime(window["timestamp"], utc=True)),
        window["value"].astype(float).to_numpy(),
    )
    median_step = metrics.get("median_step_seconds")
    longest_gap = metrics.get("longest_gap_seconds")
    gap_ratio = None
    if median_step is not None and longest_gap is not None and float(median_step) > 0:
        gap_ratio = float(longest_gap) / float(median_step)
    return {
        "n_points": int(metrics.get("n_points", 0) or 0),
        "observed_fraction": round(float(metrics["observed_fraction"]), 4) if metrics.get("observed_fraction") is not None else None,
        "gap_ratio": round(float(gap_ratio), 4) if gap_ratio is not None else None,
        "duplicate_timestamp_fraction": round(float(metrics["duplicate_timestamp_fraction"]), 4) if metrics.get("duplicate_timestamp_fraction") is not None else None,
        "nan_fraction": round(float(metrics["nan_fraction"]), 4) if metrics.get("nan_fraction") is not None else None,
    }


def take_by_split(
    rows: list[dict],
    builder,
    train_target: int,
    dev_target: int,
    test_target: int,
    heldout_site_ids: frozenset[str] | None = None,
    balance_key_fn=None,
    max_per_balance_key: dict[str, int] | None = None,
) -> list[ScenarioExample]:
    counts = {"train": 0, "dev": 0, "test": 0}
    non_test_seen = 0
    examples: list[ScenarioExample] = []
    balance_counts: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
    heldout = heldout_site_ids or frozenset({"BTS_C"})
    for row in rows:
        split = select_split(row["site_id"], non_test_seen, heldout)
        if row["site_id"] not in heldout:
            non_test_seen += 1
        target = {"train": train_target, "dev": dev_target, "test": test_target}[split]
        if counts[split] >= target:
            continue
        if balance_key_fn is not None:
            balance_key = balance_key_fn(row)
            if balance_key is not None:
                split_cap = (max_per_balance_key or {}).get(split)
                if split_cap is not None and balance_counts[split][balance_key] >= split_cap:
                    continue
        example = builder(row, split)
        if example is None:
            continue
        examples.append(example)
        counts[split] += 1
        if balance_key_fn is not None:
            balance_key = balance_key_fn(row)
            if balance_key is not None:
                balance_counts[split][balance_key] += 1
        if counts["train"] >= train_target and counts["dev"] >= dev_target and counts["test"] >= test_target:
            break
    return examples


def fetch_rows(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    return con.execute(query).fetchdf().to_dict(orient="records")


def gen_point_disambiguation(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with class_counts as (
            select site_id, point_class, count(*) as class_size
            from tool_ready_points
            where point_class is not null and point_class <> 'Point' and regexp_matches(point_class, '{valid}') and equipment_label is not null and candidate_ambiguity = 1
            group by 1, 2
            having count(*) >= 2
        ),
        ranked as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                c.class_size,
                row_number() over (partition by t.site_id order by c.class_size desc, t.stream_id) as site_rank
            from tool_ready_points t
            join class_counts c using (site_id, point_class)
            where t.candidate_ambiguity = 1
        )
        select *
        from ranked
        where site_rank <= 2000
        order by site_rank, site_id
    """.format(valid=VALID_POINT_CLASS_REGEX)
    rows = fetch_rows(con, query)

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            )
        ]
        acceptable = [calls]
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            acceptable.append(
                [
                    canonical_call(
                        "c1",
                        "resolve_point",
                        site_id=row["site_id"],
                        point_class=row["point_class"],
                        equipment_label=row["equipment_label"],
                    )
                ]
            )
        rendered = render_point_disambiguation_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
        )
        return ScenarioExample(
            split=split,
            task_family="point_disambiguation",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={"stream_id": row["stream_id"]},
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point"],
                required_answer_fields=["stream_id"],
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=1,
                temporal_normalization_count=0,
                ambiguity_candidates=int(row["class_size"]),
                graph_hops=1,
                aggregation_operations=0,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"],),
        max_per_balance_key={"train": 12, "dev": 4, "test": 4},
    )


def gen_window_mean_lookup(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with weekly_window_quality as (
            select
                w.site_id,
                w.stream_id,
                w.window_start,
                w.window_end,
                w.count,
                least(
                    1.0,
                    w.count / greatest(
                        1.0,
                        floor((epoch(w.window_end) - epoch(w.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as window_coverage
            from weekly_aggregates w
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        ),
        support_cutoff as (
            select quantile_cont(window_coverage, 0.10) as min_window_coverage
            from weekly_window_quality
        ),
        class_caps as (
            select
                t.site_id,
                t.point_class,
                quantile_cont(abs(w.mean_value), 0.995) as max_abs_mean
            from tool_ready_points t
            join weekly_aggregates w using (site_id, stream_id)
            where
                t.point_class is not null
                and t.point_class <> 'Point'
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and w.mean_value is not null
            group by 1, 2
            having count(*) >= 100
        ),
        candidates as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                t.candidate_ambiguity,
                w.window_start,
                w.window_end,
                q.count as window_count,
                q.window_coverage,
                w.mean_value,
                c.max_abs_mean,
                row_number() over (partition by t.site_id, t.stream_id order by w.window_start) as per_stream_rank,
                row_number() over (partition by t.site_id order by t.stream_id, w.window_start) as site_rank
            from tool_ready_points t
            join weekly_aggregates w using (site_id, stream_id)
            join weekly_window_quality q using (site_id, stream_id, window_start, window_end)
            join support_cutoff s on true
            join class_caps c on t.site_id = c.site_id and t.point_class = c.point_class
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and q.window_coverage >= s.min_window_coverage
                and abs(w.mean_value) <= c.max_abs_mean
        )
        select *
        from candidates
        where per_stream_rank = 1 and site_rank <= 3000
        order by site_rank, site_id
    """.format(
        compat=AGGREGATION_COMPATIBILITY_REGEX,
        blacklist=AGGREGATION_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    rows = fetch_rows(con, query)

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "aggregate_window",
                stream_id="$c1.stream_id",
                metric="mean_value",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="week",
            ),
        ]
        acceptable = [calls]
        alt_custom = clone_calls(calls)
        alt_custom[1] = set_call_argument(alt_custom[1], "period", "custom")
        acceptable.append(alt_custom)
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            alt_noloc = clone_calls(calls)
            alt_noloc[0] = set_call_argument(alt_noloc[0], "location_label", None)
            acceptable.append(alt_noloc)
        rendered = render_window_mean_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
            row["window_start"],
        )
        return ScenarioExample(
            split=split,
            task_family="window_mean_lookup",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["stream_id"],
                "window_start": iso(row["window_start"]),
                "window_end": iso(row["window_end"]),
                "mean_value": round_float(row["mean_value"]),
            },
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "aggregate_window"],
                required_answer_fields=["stream_id", "mean_value"],
                numeric_tolerance={"mean_value": 0.001},
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=1,
                ambiguity_candidates=int(row["candidate_ambiguity"]),
                graph_hops=1,
                aggregation_operations=1,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "period": "week",
                "window_coverage": round_float(row["window_coverage"]),
                "class_abs_cap": round_float(row["max_abs_mean"]),
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], quarter_bucket(row["window_start"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )


def gen_day_mean_lookup(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with daily_window_quality as (
            select
                d.site_id,
                d.stream_id,
                d.window_start,
                d.window_end,
                d.count,
                least(
                    1.0,
                    d.count / greatest(
                        1.0,
                        floor((epoch(d.window_end) - epoch(d.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as window_coverage
            from daily_aggregates d
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        ),
        support_cutoff as (
            select quantile_cont(window_coverage, 0.10) as min_window_coverage
            from daily_window_quality
        ),
        class_caps as (
            select
                t.site_id,
                t.point_class,
                quantile_cont(abs(d.mean_value), 0.995) as max_abs_mean
            from tool_ready_points t
            join daily_aggregates d using (site_id, stream_id)
            where
                t.point_class is not null
                and t.point_class <> 'Point'
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and d.mean_value is not null
            group by 1, 2
            having count(*) >= 100
        ),
        candidates as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                t.candidate_ambiguity,
                d.window_start,
                d.window_end,
                q.count as window_count,
                q.window_coverage,
                d.mean_value,
                c.max_abs_mean,
                row_number() over (
                    partition by t.site_id, t.stream_id, quarter(d.window_start)
                    order by d.window_start
                ) as per_stream_quarter_rank,
                row_number() over (partition by t.site_id order by t.stream_id, d.window_start) as site_rank
            from tool_ready_points t
            join daily_aggregates d using (site_id, stream_id)
            join daily_window_quality q using (site_id, stream_id, window_start, window_end)
            join support_cutoff s on true
            join class_caps c on t.site_id = c.site_id and t.point_class = c.point_class
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and q.window_coverage >= s.min_window_coverage
                and abs(d.mean_value) <= c.max_abs_mean
        )
        select *
        from candidates
        where per_stream_quarter_rank = 1 and site_rank <= 6000
        order by site_rank, site_id
    """.format(
        compat=AGGREGATION_COMPATIBILITY_REGEX,
        blacklist=AGGREGATION_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    rows = fetch_rows(con, query)

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "aggregate_window",
                stream_id="$c1.stream_id",
                metric="mean_value",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="day",
            ),
        ]
        acceptable = [calls]
        alt_custom = clone_calls(calls)
        alt_custom[1] = set_call_argument(alt_custom[1], "period", "custom")
        acceptable.append(alt_custom)
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            alt_noloc = clone_calls(calls)
            alt_noloc[0] = set_call_argument(alt_noloc[0], "location_label", None)
            acceptable.append(alt_noloc)
        rendered = render_day_mean_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
            row["window_start"],
        )
        return ScenarioExample(
            split=split,
            task_family="day_mean_lookup",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["stream_id"],
                "window_start": iso(row["window_start"]),
                "window_end": iso(row["window_end"]),
                "mean_value": round_float(row["mean_value"]),
            },
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "aggregate_window"],
                required_answer_fields=["stream_id", "mean_value"],
                numeric_tolerance={"mean_value": 0.001},
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=1,
                ambiguity_candidates=int(row["candidate_ambiguity"]),
                graph_hops=1,
                aggregation_operations=1,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "period": "day",
                "window_coverage": round_float(row["window_coverage"]),
                "site_class_abs_cap": round_float(row["max_abs_mean"]),
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], quarter_bucket(row["window_start"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )


def gen_relative_24h_mean_lookup(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with daily_window_quality as (
            select
                d.site_id,
                d.stream_id,
                d.window_start,
                d.window_end,
                d.count,
                least(
                    1.0,
                    d.count / greatest(
                        1.0,
                        floor((epoch(d.window_end) - epoch(d.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as window_coverage
            from daily_aggregates d
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        ),
        support_cutoff as (
            select quantile_cont(window_coverage, 0.10) as min_window_coverage
            from daily_window_quality
        ),
        class_caps as (
            select
                t.site_id,
                t.point_class,
                quantile_cont(abs(d.mean_value), 0.995) as max_abs_mean
            from tool_ready_points t
            join daily_aggregates d using (site_id, stream_id)
            where
                t.point_class is not null
                and t.point_class <> 'Point'
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and d.mean_value is not null
            group by 1, 2
            having count(*) >= 100
        ),
        candidates as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                t.candidate_ambiguity,
                d.window_start,
                d.window_end,
                q.count as window_count,
                q.window_coverage,
                d.mean_value,
                c.max_abs_mean,
                row_number() over (
                    partition by t.site_id, t.stream_id, quarter(d.window_start)
                    order by d.window_start desc
                ) as per_stream_quarter_rank,
                row_number() over (partition by t.site_id order by t.stream_id, d.window_start desc) as site_rank
            from tool_ready_points t
            join daily_aggregates d using (site_id, stream_id)
            join daily_window_quality q using (site_id, stream_id, window_start, window_end)
            join support_cutoff s on true
            join class_caps c on t.site_id = c.site_id and t.point_class = c.point_class
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and q.window_coverage >= s.min_window_coverage
                and abs(d.mean_value) <= c.max_abs_mean
        )
        select *
        from candidates
        where per_stream_quarter_rank = 1 and site_rank <= 6000
        order by site_rank, site_id
    """.format(
        compat=AGGREGATION_COMPATIBILITY_REGEX,
        blacklist=AGGREGATION_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    rows = fetch_rows(con, query)

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "aggregate_window",
                stream_id="$c1.stream_id",
                metric="mean_value",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="custom",
            ),
        ]
        acceptable = [calls]
        alt_day = clone_calls(calls)
        alt_day[1] = set_call_argument(alt_day[1], "period", "day")
        acceptable.append(alt_day)
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            alt_noloc = clone_calls(calls)
            alt_noloc[0] = set_call_argument(alt_noloc[0], "location_label", None)
            acceptable.append(alt_noloc)
        rendered = render_relative_24h_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
            row["window_end"],
        )
        return ScenarioExample(
            split=split,
            task_family="relative_24h_mean_lookup",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["stream_id"],
                "window_start": iso(row["window_start"]),
                "window_end": iso(row["window_end"]),
                "mean_value": round_float(row["mean_value"]),
            },
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "aggregate_window"],
                required_answer_fields=["stream_id", "mean_value"],
                numeric_tolerance={"mean_value": 0.001},
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=2,
                ambiguity_candidates=int(row["candidate_ambiguity"]),
                graph_hops=1,
                aggregation_operations=1,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "period": "relative_24h",
                "relative_expression": "previous_24_hours",
                "window_coverage": round_float(row["window_coverage"]),
                "site_class_abs_cap": round_float(row["max_abs_mean"]),
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], quarter_bucket(row["window_start"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )


def gen_window_pairwise_compare(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with weekly_window_quality as (
            select
                w.site_id,
                w.stream_id,
                w.window_start,
                w.window_end,
                w.count,
                least(
                    1.0,
                    w.count / greatest(
                        1.0,
                        floor((epoch(w.window_end) - epoch(w.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as window_coverage
            from weekly_aggregates w
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        ),
        support_cutoff as (
            select quantile_cont(window_coverage, 0.10) as min_window_coverage
            from weekly_window_quality
        ),
        class_caps as (
            select
                t.site_id,
                t.point_class,
                quantile_cont(abs(w.mean_value), 0.995) as max_abs_mean
            from tool_ready_points t
            join weekly_aggregates w using (site_id, stream_id)
            where
                t.point_class is not null
                and t.point_class <> 'Point'
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and w.mean_value is not null
            group by 1, 2
            having count(*) >= 100
        ),
        candidates as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                w.window_start,
                w.window_end,
                w.mean_value,
                q.count,
                q.window_coverage,
                c.max_abs_mean
            from tool_ready_points t
            join weekly_aggregates w using (site_id, stream_id)
            join weekly_window_quality q using (site_id, stream_id, window_start, window_end)
            join support_cutoff s on true
            join class_caps c on t.site_id = c.site_id and t.point_class = c.point_class
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and q.window_coverage >= s.min_window_coverage
                and abs(w.mean_value) <= c.max_abs_mean
        ),
        pairs as (
            select
                a.site_id,
                a.point_class,
                a.window_start,
                a.window_end,
                a.window_coverage,
                a.max_abs_mean,
                a.stream_id as left_stream_id,
                a.equipment_label as left_equipment_label,
                a.location_label as left_location_label,
                a.mean_value as left_mean_value,
                b.stream_id as right_stream_id,
                b.equipment_label as right_equipment_label,
                b.location_label as right_location_label,
                b.mean_value as right_mean_value,
                abs(a.mean_value - b.mean_value) as diff_value
            from candidates a
            join candidates b
              on a.site_id = b.site_id
             and a.point_class = b.point_class
             and a.window_start = b.window_start
             and a.stream_id < b.stream_id
             and a.equipment_label <> b.equipment_label
        ),
        pair_cutoffs as (
            select
                site_id,
                point_class,
                quantile_cont(diff_value, 0.50) as min_gap
            from pairs
            group by 1, 2
        ),
        ranked as (
            select
                p.*,
                c.min_gap,
                row_number() over (
                    partition by p.site_id
                    order by p.diff_value desc, p.point_class, p.window_start,
                             p.left_stream_id, p.right_stream_id
                ) as site_rank
            from pairs p
            join pair_cutoffs c using (site_id, point_class)
            where p.diff_value > 0 and p.diff_value >= c.min_gap
        )
        select *
        from ranked
        where site_rank <= 2500
        order by site_rank, site_id
    """.format(
        compat=AGGREGATION_COMPATIBILITY_REGEX,
        blacklist=AGGREGATION_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    rows = fetch_rows(con, query)

    def build(row: dict, split: str) -> ScenarioExample:
        left_location = clean_text(row["left_location_label"])
        right_location = clean_text(row["right_location_label"])
        left_wins = float(row["left_mean_value"]) > float(row["right_mean_value"])
        winning_stream_id = row["left_stream_id"] if left_wins else row["right_stream_id"]
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["left_equipment_label"],
                location_label=left_location,
            ),
            canonical_call(
                "c2",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["right_equipment_label"],
                location_label=right_location,
            ),
            canonical_call(
                "c3",
                "compare_window",
                left_stream_id="$c1.stream_id",
                right_stream_id="$c2.stream_id",
                metric="mean_value",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="week",
            ),
        ]
        alt_calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["right_equipment_label"],
                location_label=right_location,
            ),
            canonical_call(
                "c2",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["left_equipment_label"],
                location_label=left_location,
            ),
            canonical_call(
                "c3",
                "compare_window",
                left_stream_id="$c2.stream_id",
                right_stream_id="$c1.stream_id",
                metric="mean_value",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="week",
            ),
        ]
        acceptable = [calls, alt_calls]
        alt_custom = clone_calls(calls)
        alt_custom[2] = set_call_argument(alt_custom[2], "period", "custom")
        acceptable.append(alt_custom)
        rendered = render_window_pairwise_query(
            row["site_id"],
            row["point_class"],
            row["left_equipment_label"],
            left_location,
            row["right_equipment_label"],
            right_location,
            row["window_start"],
        )
        return ScenarioExample(
            split=split,
            task_family="window_pairwise_compare",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "winning_stream_id": winning_stream_id,
                "left_stream_id": row["left_stream_id"],
                "right_stream_id": row["right_stream_id"],
                "left_mean_value": round_float(row["left_mean_value"]),
                "right_mean_value": round_float(row["right_mean_value"]),
            },
            evidence={"stream_ids": [row["left_stream_id"], row["right_stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "compare_window"],
                required_answer_fields=["winning_stream_id", "left_mean_value", "right_mean_value"],
                numeric_tolerance={"left_mean_value": 0.001, "right_mean_value": 0.001},
                required_stream_ids=[row["left_stream_id"], row["right_stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=3,
                temporal_normalization_count=1,
                ambiguity_candidates=2,
                graph_hops=1,
                aggregation_operations=1,
            ),
            metadata={
                "point_class": row["point_class"],
                "period": "week",
                "window_coverage": round_float(row["window_coverage"]),
                "pair_gap_value": round_float(row["diff_value"]),
                "pair_gap_cutoff": round_float(row["min_gap"]),
                "site_class_abs_cap": round_float(row["max_abs_mean"]),
                "acceptable_alternative_tool_calls": dedupe_call_sets(acceptable)[1:],
            }
            | surface_metadata({}, rendered),
        )

    examples = take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], quarter_bucket(row["window_start"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )
    for example in examples:
        alt = example.metadata.pop("acceptable_alternative_tool_calls", None)
        if alt:
            example.metadata["has_alternative_path"] = True
            example.metadata["alternative_path_count"] = len(alt)
    return examples


def gen_window_rank(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with monthly_window_quality as (
            select
                m.site_id,
                m.stream_id,
                m.window_start,
                m.window_end,
                m.count,
                least(
                    1.0,
                    m.count / greatest(
                        1.0,
                        floor((epoch(m.window_end) - epoch(m.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as window_coverage
            from monthly_aggregates m
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        ),
        support_cutoff as (
            select quantile_cont(window_coverage, 0.10) as min_window_coverage
            from monthly_window_quality
        ),
        class_caps as (
            select
                t.site_id,
                t.point_class,
                quantile_cont(abs(m.mean_value), 0.995) as max_abs_mean
            from tool_ready_points t
            join monthly_aggregates m using (site_id, stream_id)
            where
                t.point_class is not null
                and t.point_class <> 'Point'
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and m.mean_value is not null
            group by 1, 2
            having count(*) >= 100
        ),
        candidates as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.location_type,
                m.window_start,
                m.window_end,
                m.mean_value,
                q.count,
                q.window_coverage,
                c.max_abs_mean
            from tool_ready_points t
            join monthly_aggregates m using (site_id, stream_id)
            join monthly_window_quality q using (site_id, stream_id, window_start, window_end)
            join support_cutoff s on true
            join class_caps c on t.site_id = c.site_id and t.point_class = c.point_class
            where
                t.point_class is not null
                and t.point_class <> 'Point'
                and t.location_type is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and q.window_coverage >= s.min_window_coverage
                and abs(m.mean_value) <= c.max_abs_mean
        ),
        grouped as (
            select
                site_id,
                location_type,
                point_class,
                window_start,
                window_end,
                count(distinct stream_id) as n_streams
            from candidates
            group by 1, 2, 3, 4, 5
            having count(distinct stream_id) >= 3
        ),
        ranked as (
            select
                c.*,
                g.n_streams,
                row_number() over (
                    partition by c.site_id, c.location_type, c.point_class, c.window_start
                    order by c.mean_value desc, c.stream_id
                ) as pos
            from candidates c
            join grouped g using (site_id, location_type, point_class, window_start, window_end)
        ),
        final as (
            select
                a.site_id,
                a.location_type,
                a.point_class,
                a.window_start,
                a.window_end,
                a.window_coverage,
                a.max_abs_mean,
                a.stream_id as top_stream_id,
                a.mean_value as top_mean_value,
                a.n_streams,
                b.stream_id as runner_stream_id,
                b.mean_value as runner_mean_value,
                abs(a.mean_value - b.mean_value) as margin_value
            from ranked a
            join ranked b
              on a.site_id = b.site_id
             and a.location_type = b.location_type
             and a.point_class = b.point_class
             and a.window_start = b.window_start
             and a.pos = 1
             and b.pos = 2
        ),
        margin_cutoffs as (
            select
                site_id,
                location_type,
                point_class,
                quantile_cont(margin_value, 0.50) as min_margin
            from final
            group by 1, 2, 3
        ),
        site_ranked as (
            select
                f.*,
                c.min_margin,
                row_number() over (
                    partition by f.site_id
                    order by f.n_streams desc, f.margin_value desc,
                             f.location_type, f.point_class, f.window_start,
                             f.top_stream_id, f.runner_stream_id
                ) as site_rank
            from final f
            join margin_cutoffs c using (site_id, location_type, point_class)
            where f.margin_value > 0 and f.margin_value >= c.min_margin
        )
        select *
        from site_ranked
        where site_rank <= 2000
        order by site_rank, site_id
    """.format(
        compat=AGGREGATION_COMPATIBILITY_REGEX,
        blacklist=AGGREGATION_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    rows = fetch_rows(con, query)

    def build(row: dict, split: str) -> ScenarioExample:
        calls = [
            canonical_call(
                "c1",
                "list_points",
                site_id=row["site_id"],
                point_class=row["point_class"],
                location_type=row["location_type"],
            ),
            canonical_call(
                "c2",
                "rank_window",
                stream_ids="$c1.stream_ids",
                metric="mean_value",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="month",
                order="desc",
                topk=1,
            ),
        ]
        acceptable = [calls]
        alt_custom = clone_calls(calls)
        alt_custom[1] = set_call_argument(alt_custom[1], "period", "custom")
        acceptable.append(alt_custom)
        rendered = render_window_rank_query(
            row["site_id"],
            row["point_class"],
            row["location_type"],
            row["window_start"],
        )
        return ScenarioExample(
            split=split,
            task_family="window_rank",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["top_stream_id"],
                "mean_value": round_float(row["top_mean_value"]),
                "window_start": iso(row["window_start"]),
                "window_end": iso(row["window_end"]),
            },
            evidence={"stream_ids": [row["top_stream_id"], row["runner_stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["list_points", "rank_window"],
                required_answer_fields=["stream_id", "mean_value"],
                numeric_tolerance={"mean_value": 0.001},
                required_stream_ids=[row["top_stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=1,
                ambiguity_candidates=int(row["n_streams"]),
                graph_hops=1,
                aggregation_operations=1,
            ),
            metadata={
                "point_class": row["point_class"],
                "location_type": row["location_type"],
                "period": "month",
                "window_coverage": round_float(row["window_coverage"]),
                "rank_margin_value": round_float(row["margin_value"]),
                "rank_margin_cutoff": round_float(row["min_margin"]),
                "site_class_abs_cap": round_float(row["max_abs_mean"]),
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], row["location_type"], quarter_bucket(row["window_start"])),
        max_per_balance_key={"train": 3, "dev": 2, "test": 2},
    )


def gen_timestamp_value_lookup(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with ranked as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                t.candidate_ambiguity,
                t.raw_zip_path,
                t.raw_member_name,
                q.duplicate_timestamp_fraction,
                row_number() over (partition by t.site_id order by t.point_class, t.stream_id) as site_rank
            from tool_ready_points t
            join quality_metrics q using (site_id, stream_id)
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and coalesce(q.duplicate_timestamp_fraction, 0.0) = 0.0
        )
        select *
        from ranked
        where site_rank <= 1200
        order by site_id, point_class, stream_id
    """.format(
        compat=TIMESTAMP_COMPATIBILITY_REGEX,
        blacklist=TIMESTAMP_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    base_rows = fetch_rows(con, query)
    rows: list[dict] = []
    site_caps = {"BTS_A": 180, "BTS_B": 180, "BTS_C": 180}
    site_counts = defaultdict(int)
    for row in base_rows:
        if site_counts[row["site_id"]] >= site_caps.get(row["site_id"], 180):
            continue
        frame = valid_history_from_store(con, row["raw_zip_path"], row["raw_member_name"], row["stream_id"])
        if len(frame) < 3:
            continue
        idx = len(frame) // 2
        chosen = frame.iloc[idx]
        rows.append(
            {
                **row,
                "requested_timestamp": pd.Timestamp(chosen["timestamp"]).isoformat(),
                "observed_timestamp": pd.Timestamp(chosen["timestamp"]).isoformat(),
                "observed_value": float(chosen["value"]),
            }
        )
        site_counts[row["site_id"]] += 1
        if all(site_counts[site] >= site_caps[site] for site in site_caps):
            break

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "lookup_observation",
                stream_id="$c1.stream_id",
                timestamp=row["requested_timestamp"],
                mode="exact",
            ),
        ]
        acceptable = [calls]
        alt_nearest = clone_calls(calls)
        alt_nearest[1] = set_call_argument(alt_nearest[1], "mode", "nearest")
        acceptable.append(alt_nearest)
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            alt_noloc = clone_calls(calls)
            alt_noloc[0] = set_call_argument(alt_noloc[0], "location_label", None)
            acceptable.append(alt_noloc)
        rendered = render_timestamp_exact_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
            row["requested_timestamp"],
        )
        return ScenarioExample(
            split=split,
            task_family="timestamp_value_lookup",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["stream_id"],
                "requested_timestamp": row["requested_timestamp"],
                "observed_timestamp": row["observed_timestamp"],
                "value": round_float(row["observed_value"]),
                "exact_match_found": True,
            },
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "lookup_observation"],
                required_answer_fields=["stream_id", "observed_timestamp", "value", "exact_match_found"],
                numeric_tolerance={"value": 0.001},
                categorical_exact_match=["stream_id", "observed_timestamp", "exact_match_found"],
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=0,
                ambiguity_candidates=int(row["candidate_ambiguity"]),
                graph_hops=1,
                aggregation_operations=0,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "timestamp_mode": "exact",
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], quarter_bucket(row["requested_timestamp"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )


def gen_timestamp_nearest_lookup(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with ranked as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                t.candidate_ambiguity,
                t.raw_zip_path,
                t.raw_member_name,
                q.median_step_seconds,
                q.duplicate_timestamp_fraction,
                row_number() over (partition by t.site_id order by t.point_class, t.stream_id) as site_rank
            from tool_ready_points t
            join quality_metrics q using (site_id, stream_id)
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
                and q.median_step_seconds is not null
                and q.median_step_seconds > 0
                and coalesce(q.duplicate_timestamp_fraction, 0.0) = 0.0
        )
        select *
        from ranked
        where site_rank <= 1200
        order by site_id, point_class, stream_id
    """.format(
        compat=TIMESTAMP_COMPATIBILITY_REGEX,
        blacklist=TIMESTAMP_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    base_rows = fetch_rows(con, query)
    rows: list[dict] = []
    site_caps = {"BTS_A": 180, "BTS_B": 180, "BTS_C": 180}
    site_counts = defaultdict(int)
    for row in base_rows:
        if site_counts[row["site_id"]] >= site_caps.get(row["site_id"], 180):
            continue
        frame = valid_history_from_store(con, row["raw_zip_path"], row["raw_member_name"], row["stream_id"])
        if len(frame) < 3:
            continue
        timestamps = pd.to_datetime(frame["timestamp"], utc=True)
        values = frame["value"].astype(float).tolist()
        diffs = (timestamps.diff().dt.total_seconds()).tolist()
        median_step = float(row["median_step_seconds"])
        chosen = None
        for idx in range(1, len(frame)):
            gap = diffs[idx]
            if gap is None or gap <= 0:
                continue
            if gap < max(1.0, 0.5 * median_step) or gap > 1.5 * median_step:
                continue
            left_ts = timestamps.iloc[idx - 1]
            requested_ts = left_ts + pd.Timedelta(seconds=gap * 0.4)
            chosen = {
                **row,
                "requested_timestamp": requested_ts.isoformat(),
                "observed_timestamp": pd.Timestamp(left_ts).isoformat(),
                "observed_value": float(values[idx - 1]),
                "offset_seconds": round(float(gap * 0.4), 3),
            }
            break
        if chosen is not None:
            rows.append(chosen)
            site_counts[row["site_id"]] += 1
            if all(site_counts[site] >= site_caps[site] for site in site_caps):
                break

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "lookup_observation",
                stream_id="$c1.stream_id",
                timestamp=row["requested_timestamp"],
                mode="nearest",
            ),
        ]
        acceptable = [calls]
        alt_exact_then_nearest = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "lookup_observation",
                stream_id="$c1.stream_id",
                timestamp=row["requested_timestamp"],
                mode="exact",
            ),
            canonical_call(
                "c3",
                "lookup_observation",
                stream_id="$c1.stream_id",
                timestamp=row["requested_timestamp"],
                mode="nearest",
            ),
        ]
        acceptable.append(alt_exact_then_nearest)
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            alt_noloc = clone_calls(calls)
            alt_noloc[0] = set_call_argument(alt_noloc[0], "location_label", None)
            acceptable.append(alt_noloc)
        rendered = render_timestamp_nearest_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
            row["requested_timestamp"],
        )
        return ScenarioExample(
            split=split,
            task_family="timestamp_nearest_lookup",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["stream_id"],
                "requested_timestamp": row["requested_timestamp"],
                "observed_timestamp": row["observed_timestamp"],
                "value": round_float(row["observed_value"]),
                "exact_match_found": False,
                "fallback_reason": "nearest_available_observation",
                "offset_seconds": round_float(row["offset_seconds"]),
            },
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "lookup_observation"],
                required_answer_fields=[
                    "stream_id",
                    "observed_timestamp",
                    "value",
                    "exact_match_found",
                    "fallback_reason",
                    "offset_seconds",
                ],
                numeric_tolerance={"value": 0.001, "offset_seconds": 1.0},
                categorical_exact_match=["stream_id", "observed_timestamp", "exact_match_found", "fallback_reason"],
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=0,
                ambiguity_candidates=int(row["candidate_ambiguity"]),
                graph_hops=1,
                aggregation_operations=0,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "timestamp_mode": "nearest",
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=10,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["point_class"], quarter_bucket(row["requested_timestamp"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )


def gen_quality_gate(
    con: duckdb.DuckDBPyConnection,
    heldout_site_ids: frozenset[str] | None = None,
) -> list[ScenarioExample]:
    query = """
        with weekly_window_support as (
            select
                w.site_id,
                w.stream_id,
                w.window_start,
                w.window_end,
                w.count,
                least(
                    1.0,
                    w.count / greatest(
                        1.0,
                        floor((epoch(w.window_end) - epoch(w.window_start)) / q.median_step_seconds) + 1.0
                    )
                ) as window_coverage
            from weekly_aggregates w
            join quality_metrics q using (site_id, stream_id)
            where q.median_step_seconds is not null and q.median_step_seconds > 0
        ),
        ranked as (
            select
                t.site_id,
                t.stream_id,
                t.point_class,
                t.equipment_label,
                t.location_label,
                t.raw_zip_path,
                t.raw_member_name,
                s.window_start,
                s.window_end,
                s.window_coverage,
                row_number() over (
                    partition by t.site_id, t.stream_id
                    order by s.window_start
                ) as per_stream_rank,
                row_number() over (
                    partition by t.site_id
                    order by t.point_class, t.stream_id, s.window_start
                ) as site_rank
            from tool_ready_points t
            join weekly_window_support s using (site_id, stream_id)
            where
                t.candidate_ambiguity = 1
                and t.point_class is not null
                and t.point_class <> 'Point'
                and t.equipment_label is not null
                and regexp_matches(t.point_class, '{valid}')
                and regexp_matches(t.point_class, '{compat}')
                and not regexp_matches(t.point_class, '{blacklist}')
        )
        select *
        from ranked
        where
            (
                per_stream_rank <= 8
                or (
                    per_stream_rank between 17 and 97
                    and ((per_stream_rank - 17) % 8) = 0
                )
            )
            and site_rank <= 8000
        order by site_id, point_class, stream_id, window_start
    """.format(
        compat=QUALITY_COMPATIBILITY_REGEX,
        blacklist=QUALITY_BLACKLIST_REGEX,
        valid=VALID_POINT_CLASS_REGEX,
    )
    refs = quality_reference_for_period(con, "week")
    base_rows = fetch_rows(con, query)
    by_site_and_decision: defaultdict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"abstain": [], "answer": []})
    for row in base_rows:
        frame = valid_history_from_store(con, row["raw_zip_path"], row["raw_member_name"], row["stream_id"])
        metrics = exact_window_quality(frame, row["window_start"], row["window_end"])
        observed_fraction = metrics.get("observed_fraction")
        gap_ratio = metrics.get("gap_ratio")
        duplicate_fraction = metrics.get("duplicate_timestamp_fraction")
        nan_fraction = metrics.get("nan_fraction")
        if observed_fraction is None or metrics.get("n_points", 0) < 2:
            continue

        decision = None
        reason = None
        if (
            refs["abstain_observed_fraction_below"] is not None
            and observed_fraction < refs["abstain_observed_fraction_below"]
        ):
            decision = "abstain"
            reason = "low_coverage"
        elif (
            refs["abstain_gap_ratio_above"] is not None
            and gap_ratio is not None
            and gap_ratio > refs["abstain_gap_ratio_above"]
        ):
            decision = "abstain"
            reason = "long_gap"
        elif (
            refs["answer_observed_fraction_at_least"] is not None
            and observed_fraction >= refs["answer_observed_fraction_at_least"]
            and (refs["answer_gap_ratio_at_most"] is None or gap_ratio is None or gap_ratio <= refs["answer_gap_ratio_at_most"])
            and (duplicate_fraction or 0.0) == 0.0
            and (nan_fraction or 0.0) == 0.0
        ):
            decision = "answer"
            reason = "healthy"
        if decision is None or reason is None:
            continue

        candidate = {
            **row,
            "decision": decision,
            "reason": reason,
            "observed_fraction": observed_fraction,
            "gap_ratio": gap_ratio,
            "duplicate_timestamp_fraction": duplicate_fraction,
            "nan_fraction": nan_fraction,
        }
        by_site_and_decision[row["site_id"]][decision].append(candidate)

    rows: list[dict[str, Any]] = []
    for site_id in sorted(by_site_and_decision):
        site_abstain = by_site_and_decision[site_id]["abstain"]
        site_answer = by_site_and_decision[site_id]["answer"]
        max_len = max(len(site_abstain), len(site_answer))
        for idx in range(max_len):
            if idx < len(site_abstain):
                rows.append(dict(site_abstain[idx]))
            if idx < len(site_answer):
                rows.append(dict(site_answer[idx]))

    def build(row: dict, split: str) -> ScenarioExample:
        location_label = clean_text(row["location_label"])
        calls = [
            canonical_call(
                "c1",
                "resolve_point",
                site_id=row["site_id"],
                point_class=row["point_class"],
                equipment_label=row["equipment_label"],
                location_label=location_label,
            ),
            canonical_call(
                "c2",
                "inspect_quality_window",
                stream_id="$c1.stream_id",
                window_start=iso(row["window_start"]),
                window_end=iso(row["window_end"]),
                period="week",
            ),
        ]
        acceptable = [calls]
        if location_label and location_optional_for_resolve(con, row["site_id"], row["point_class"], row["equipment_label"]):
            alt_noloc = clone_calls(calls)
            alt_noloc[0] = set_call_argument(alt_noloc[0], "location_label", None)
            acceptable.append(alt_noloc)
        rendered = render_quality_gate_query(
            row["site_id"],
            row["point_class"],
            row["equipment_label"],
            location_label,
            row["window_start"],
        )
        return ScenarioExample(
            split=split,
            task_family="quality_gate",
            site_id=row["site_id"],
            query=rendered.text,
            canonical_tool_calls=calls,
            acceptable_tool_call_sets=dedupe_call_sets(acceptable),
            gold_final_answer={
                "stream_id": row["stream_id"],
                "decision": row["decision"],
                "reason": row["reason"],
                "observed_fraction": round_float(row["observed_fraction"]),
                "gap_ratio": round_float(row["gap_ratio"]),
            },
            evidence={"stream_ids": [row["stream_id"]]},
            task_accomplish_verifier=build_task_accomplish_verifier(
                required_tools=["resolve_point", "inspect_quality_window"],
                required_answer_fields=["decision", "reason"],
                numeric_tolerance={"observed_fraction": 0.001, "gap_ratio": 0.001},
                categorical_exact_match=["decision", "reason"],
                required_stream_ids=[row["stream_id"]],
            ),
            difficulty_proxy=difficulty_proxy(
                required_tool_count=2,
                temporal_normalization_count=1,
                ambiguity_candidates=1,
                graph_hops=1,
                aggregation_operations=0,
            ),
            metadata={
                "point_class": row["point_class"],
                "equipment_label": row["equipment_label"],
                "location_label": location_label,
                "quality_reason": row["reason"],
                "window_start": iso(row["window_start"]),
                "window_end": iso(row["window_end"]),
                "period": "week",
                "observed_fraction": round_float(row["observed_fraction"]),
                "gap_ratio": round_float(row["gap_ratio"]),
                "has_alternative_path": len(dedupe_call_sets(acceptable) or []) > 1,
                "alternative_path_count": max(0, len(dedupe_call_sets(acceptable) or []) - 1),
            }
            | surface_metadata({}, rendered),
        )

    return take_by_split(
        rows,
        build,
        train_target=40,
        dev_target=10,
        test_target=9,
        heldout_site_ids=heldout_site_ids,
        balance_key_fn=lambda row: (row["decision"], row["point_class"], quarter_bucket(row["window_start"])),
        max_per_balance_key={"train": 4, "dev": 2, "test": 2},
    )


TASK_FAMILY_BUILDERS = {
    "point_disambiguation": gen_point_disambiguation,
    "day_mean_lookup": gen_day_mean_lookup,
    "relative_24h_mean_lookup": gen_relative_24h_mean_lookup,
    "window_mean_lookup": gen_window_mean_lookup,
    "window_pairwise_compare": gen_window_pairwise_compare,
    "window_rank": gen_window_rank,
    "timestamp_value_lookup": gen_timestamp_value_lookup,
    "timestamp_nearest_lookup": gen_timestamp_nearest_lookup,
    "quality_gate": gen_quality_gate,
}


def generate_scenario_benchmark(
    tool_store_db: Path,
    out_dir: Path,
    heldout_site_ids: list[str] | tuple[str, ...] | None = None,
    include_families: list[str] | tuple[str, ...] | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(tool_store_db), read_only=True)
    try:
        heldout = frozenset(heldout_site_ids or ["BTS_C"])
        splits: dict[str, list[dict]] = {"train": [], "dev": [], "test": []}
        family_summary: dict[str, int] = {}
        family_items = TASK_FAMILY_BUILDERS.items()
        if include_families is not None:
            include = set(include_families)
            family_items = [(name, builder) for name, builder in family_items if name in include]
        for family_name, builder in family_items:
            examples = builder(con, heldout_site_ids=heldout)
            family_summary[family_name] = len(examples)
            for index, example in enumerate(examples, start=1):
                payload = example.as_dict(build_scenario_id(example.split, family_name, index))
                splits[example.split].append(payload)
    finally:
        con.close()

    for split_name, rows in splits.items():
        with (out_dir / f"{split_name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "splits": {split_name: len(rows) for split_name, rows in splits.items()},
        "task_families": family_summary,
        "heldout_site_ids": sorted(heldout),
        "query_surface_version": QUERY_SURFACE_VERSION,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "scenario_tool_registry.json").write_text(
        json.dumps(SCENARIO_TOOL_REGISTRY, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest
