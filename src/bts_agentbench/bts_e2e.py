from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .clarify_policy import clarify_policy_manifest_fields


E2E_TRACK_VERSION = "bts-interaction-contract"
FOLLOWUP_PROMPT = "Which stream or point did you base that on?"
FOLLOWUP_PROMPT_MULTI = "Which streams or points did you base that on?"
RATIONALE_FOLLOWUP_PROMPT = "Why do you think the signal is reliable enough, or not?"
STOP_ACK = "Thanks, that's what I needed. ###STOP###"
TIME_CLARIFICATION_SLOT = "time_reference"

SITE_CLARIFICATION_SLOT = "site_id"

SITE_CLARIFY_MODES = {
    "clarify_site_then_evidence",
    "clarify_site_time_then_evidence",
    "clarify_site_then_quality_decision_then_rationale_then_evidence",
}

TIME_CLARIFY_MODES = {
    "clarify_time_then_evidence",
    "clarify_site_time_then_evidence",
}

QUALITY_RATIONALE_MODES = {
    "quality_decision_then_rationale_then_evidence",
    "clarify_site_then_quality_decision_then_rationale_then_evidence",
}

TIME_CLARIFY_FAMILIES = {
    "day_mean_lookup",
    "relative_24h_mean_lookup",
    "window_mean_lookup",
    "window_pairwise_compare",
    "window_rank",
    "timestamp_value_lookup",
    "timestamp_nearest_lookup",
}

TEMPORAL_CLARIFY_EXCLUSION_FAMILIES = {"timestamp_nearest_lookup"}

MONTH_NAME_RE = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_space(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    if text:
        text = text[0].upper() + text[1:]
    return text


def repair_surface_artifacts(text: str) -> str:
    cleaned = text
    cleaned = cleaned.replace(" reading readings", " readings")
    cleaned = cleaned.replace(" measurement measurements", " measurements")
    cleaned = cleaned.replace(" sensor sensors", " sensors")
    cleaned = cleaned.replace(" reading reading", " reading")
    cleaned = cleaned.replace(" measurement measurement", " measurement")
    cleaned = cleaned.replace(" sensor sensor", " sensor")
    return normalize_space(cleaned)


def humanize_visible_prompt(text: str, family: str, interaction_mode: str) -> str:
    cleaned = repair_surface_artifacts(text)
    replacements = [
        (" attached to ", " on "),
        (" serving ", " in "),
        ("day-average", "average"),
        ("which stream matches the", "which stream is the right one for the"),
        ("which stream matches", "which stream is the right one for"),
        ("which stream should I use for", "which stream should I use for"),
        ("what did the ", "what was the "),
        (" read at ", " at "),
        (" report in ", " in "),
        (" report at ", " at "),
        (" came out higher on average for ", " averaged higher for "),
        (" which nearest available observation should the agent return ", " which nearby reading should I use "),
        (" what is the nearest available observation ", " what nearby reading should I use "),
    ]
    for src, dst in replacements:
        cleaned = cleaned.replace(src, dst)

    if family == "relative_24h_mean_lookup":
        cleaned = cleaned.replace("prior-day average", "average over the previous 24 hours")
    if family == "timestamp_value_lookup":
        cleaned = re.sub(
            r"^(?P<prefix>(?:In|For) BTS_[A-Z],) what was the (?P<subject>.+?) read\?$",
            lambda m: f"{m.group('prefix')} what was the {m.group('subject')}?",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^What did the (?P<subject>.+?) read\?$",
            lambda m: f"What was the {m.group('subject')}?",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^What was the (?P<subject>.+?) read\?$",
            lambda m: f"What was the {m.group('subject')}?",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^At (?P<time>.+?), what reading did the (?P<subject>.+?) in (?P<site>BTS_[A-Z])\?$",
            lambda m: f"At {m.group('time')}, what was the {m.group('subject')} in {m.group('site')}?",
            cleaned,
            flags=re.IGNORECASE,
        )
    if family == "timestamp_nearest_lookup":
        cleaned = re.sub(
            r"which nearest available observation should the agent return",
            "which nearby reading should I use",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"what is the nearest available observation",
            "what nearby reading should I use",
            cleaned,
            flags=re.IGNORECASE,
        )
    if family == "quality_gate":
        cleaned = cleaned.replace("is this signal reliable enough for a typical weekly trend question about", "would you trust this signal enough for a typical weekly trend question about")
        cleaned = cleaned.replace(", or should the agent abstain?", ", or would you abstain?")
    if family == "window_rank":
        cleaned = cleaned.replace("was highest on average", "had the highest average")
    if family == "timestamp_nearest_lookup" and interaction_mode == "implicit_nearest_then_evidence":
        cleaned = cleaned.replace("what was the ", "what was the ")

    return normalize_space(cleaned)


def remove_site_mentions(text: str, site_id: str) -> str:
    cleaned = text
    patterns = [
        rf"^In {re.escape(site_id)},\s*",
        rf"^For {re.escape(site_id)},\s*",
        rf"^Looking at {re.escape(site_id)},\s*",
        rf"\bin {re.escape(site_id)}\b",
        rf"\bfor {re.escape(site_id)}\b",
        rf"\bwithin {re.escape(site_id)}\b",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(f"{site_id} ", "")
    cleaned = cleaned.replace(f" {site_id}", "")
    cleaned = cleaned.replace("  ", " ")
    cleaned = re.sub(r"^\s*,\s*", "", cleaned)
    return repair_surface_artifacts(cleaned)


def remove_time_mentions(text: str, family: str) -> str:
    cleaned = text
    if family == "window_pairwise_compare":
        rewrite_patterns = [
            (
                re.compile(
                    r"^For (?P<site>BTS_[A-Z]), during the week beginning [A-Z][a-z]+ \d{1,2}, \d{4}, (?P<rest>.+)$",
                    flags=re.IGNORECASE,
                ),
                "For {site}, {rest}",
            ),
            (
                re.compile(
                    r"^In (?P<site>BTS_[A-Z]), (?P<rest>.+?) over the week of [A-Z][a-z]+ \d{1,2}, \d{4}:(?P<suffix>.+)$",
                    flags=re.IGNORECASE,
                ),
                "In {site}, {rest}:{suffix}",
            ),
            (
                re.compile(
                    r"^Looking at the week starting [A-Z][a-z]+ \d{1,2}, \d{4} in (?P<site>BTS_[A-Z]), (?P<rest>.+)$",
                    flags=re.IGNORECASE,
                ),
                "In {site}, {rest}",
            ),
        ]
        for pattern, template in rewrite_patterns:
            match = pattern.match(cleaned)
            if match:
                return repair_surface_artifacts(template.format(**match.groupdict()))
    if family == "timestamp_nearest_lookup":
        rewrite_patterns = [
            (
                re.compile(
                    r"^(?P<prefix>(?:For|In) BTS_[A-Z]), if the (?P<subject>.+?) has no exact (?:reading|value) at .+?, which nearest available observation should the agent return\?$",
                    flags=re.IGNORECASE,
                ),
                "{prefix}, if there is no exact reading at the requested time, which nearest available observation should the agent return for the {subject}?",
            ),
            (
                re.compile(
                    r"^(?P<prefix>(?:For|In) BTS_[A-Z]), when .+? has no exact value for the (?P<subject>.+?), what is the nearest available observation\?$",
                    flags=re.IGNORECASE,
                ),
                "{prefix}, what is the nearest available observation for the {subject} at the requested time?",
            ),
            (
                re.compile(
                    r"^If there is no exact sample at .+?, what is the nearest available observation for the (?P<subject>.+?) in (?P<site>BTS_[A-Z])\?$",
                    flags=re.IGNORECASE,
                ),
                "In {site}, if there is no exact sample at the requested time, what is the nearest available observation for the {subject}?",
            ),
            (
                re.compile(
                    r"^If there is no exact sample at .+?, what is the nearest available observation for the (?P<subject>.+?)\?$",
                    flags=re.IGNORECASE,
                ),
                "If there is no exact sample at the requested time, what is the nearest available observation for the {subject}?",
            ),
        ]
        for pattern, template in rewrite_patterns:
            match = pattern.match(cleaned)
            if match:
                return repair_surface_artifacts(template.format(**match.groupdict()))
    family_patterns = {
        "day_mean_lookup": [
            r"\bon [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        ],
        "relative_24h_mean_lookup": [
            r"\bas of \d{2}:\d{2} UTC on [A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\bin the 24 hours leading up to \d{2}:\d{2} UTC on [A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\bover the previous 24 hours as of \d{2}:\d{2} UTC on [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        ],
        "window_mean_lookup": [
            r"\bover the week beginning [A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\bduring the week of [A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\bfor the week of [A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\bacross the week starting [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        ],
        "window_rank": [
            r"\bin [A-Z][a-z]+ \d{4}\b",
            r"\bduring [A-Z][a-z]+ \d{4}\b",
        ],
        "timestamp_value_lookup": [
            r"\bat \d{2}:\d{2} UTC on [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        ],
    }
    for pattern in family_patterns.get(family, []):
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    generic_patterns = [
        r"\bthe week starting [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bweek starting [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bduring the week beginning [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bover the week of [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bfor the week of [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bthe month starting [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bmonth starting [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bat \d{2}:\d{2}(?::\d{2})? UTC on [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bto \d{2}:\d{2}(?::\d{2})? UTC on [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\b\d{2}:\d{2}(?::\d{2})?\s*UTC\b",
        r"\bon [A-Z][a-z]+ \d{1,2}, \d{4}\b",
        r"\bin [A-Z][a-z]+ \d{4}\b",
        r"\bduring [A-Z][a-z]+ \d{4}\b",
    ]
    for pattern in generic_patterns:
        cleaned = re.sub(pattern, " the requested time", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bthe requested time\s+in\s+", "the requested time in ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bthe requested time\s*,", "the requested time,", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"^[\s,;:.-]+", "", cleaned)
    cleaned = re.sub(r"\s+\?", "?", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    return repair_surface_artifacts(cleaned)


def has_explicit_calendar_reference(text: str) -> bool:
    lowered = text.lower()
    if re.search(rf"\b{MONTH_NAME_RE}\s+\d{{1,2}},\s+\d{{4}}\b", lowered):
        return True
    if re.search(rf"\b(?:in|during)\s+{MONTH_NAME_RE}\s+\d{{4}}\b", lowered):
        return True
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", lowered):
        return True
    return False


def rewrite_quality_prompt(text: str) -> str:
    cleaned = repair_surface_artifacts(text)
    patterns = [
        (
            re.compile(
                r"^(?P<prefix>(?:In|For|Looking at) BTS_[A-Z]), should an agent answer a weekly trend request for (?P<subject>.+?) for the week beginning (?P<week>[A-Z][a-z]+ \d{1,2}, \d{4}), or abstain because the data quality is not reliable enough\?$",
                flags=re.IGNORECASE,
            ),
            "{prefix}, would you trust this signal enough for the weekly trend question about {subject} for the week beginning {week}, or would you abstain?",
        ),
        (
            re.compile(
                r"^(?P<prefix>(?:In|For|Looking at) BTS_[A-Z]), should the agent answer or abstain on a weekly trend request for (?P<subject>.+?) for the week beginning (?P<week>[A-Z][a-z]+ \d{1,2}, \d{4}) because of signal quality concerns\?$",
                flags=re.IGNORECASE,
            ),
            "{prefix}, would you trust this signal enough for the weekly trend question about {subject} for the week beginning {week}, or would you abstain?",
        ),
        (
            re.compile(
                r"^(?P<prefix>(?:In|For|Looking at) BTS_[A-Z]), would you trust (?P<subject>.+?) enough to answer a weekly trend question for the week of (?P<week>[A-Z][a-z]+ \d{1,2}, \d{4}), or should the agent abstain\?$",
                flags=re.IGNORECASE,
            ),
            "{prefix}, would you trust this signal enough for the weekly trend question about {subject} for the week beginning {week}, or would you abstain?",
        ),
        (
            re.compile(
                r"^should an agent answer a weekly trend request for (?P<subject>.+?) for the week beginning (?P<week>[A-Z][a-z]+ \d{1,2}, \d{4}), or abstain because the data quality is not reliable enough\?$",
                flags=re.IGNORECASE,
            ),
            "Would you trust this signal enough for the weekly trend question about {subject} for the week beginning {week}, or would you abstain?",
        ),
        (
            re.compile(
                r"^should the agent answer or abstain on a weekly trend request for (?P<subject>.+?) for the week beginning (?P<week>[A-Z][a-z]+ \d{1,2}, \d{4}) because of signal quality concerns\?$",
                flags=re.IGNORECASE,
            ),
            "Would you trust this signal enough for the weekly trend question about {subject} for the week beginning {week}, or would you abstain?",
        ),
        (
            re.compile(
                r"^would you trust (?P<subject>.+?) enough to answer a weekly trend question for the week of (?P<week>[A-Z][a-z]+ \d{1,2}, \d{4}), or should the agent abstain\?$",
                flags=re.IGNORECASE,
            ),
            "Would you trust this signal enough for the weekly trend question about {subject} for the week beginning {week}, or would you abstain?",
        ),
    ]
    for pattern, template in patterns:
        match = pattern.match(cleaned)
        if match:
            return normalize_space(template.format(**match.groupdict()))
    return cleaned


def month_name(month_number: int) -> str:
    return datetime(2000, month_number, 1).strftime("%B")


def parse_iso_like_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def infer_time_fields(row: dict[str, Any]) -> dict[str, str]:
    gold = row.get("gold_final_answer", {})
    inferred: dict[str, str] = {}
    for key in ("window_start", "window_end", "requested_timestamp", "observed_timestamp"):
        if key in gold:
            inferred[key] = gold[key]
    for call in row.get("canonical_tool_calls", []):
        args = call.get("arguments", {})
        for key in ("window_start", "window_end", "requested_timestamp"):
            if key not in inferred and key in args and isinstance(args[key], str):
                inferred[key] = args[key]
    return inferred


def infer_time_period(row: dict[str, Any]) -> str | None:
    for call in row.get("canonical_tool_calls", []):
        args = call.get("arguments", {})
        period = args.get("period")
        if isinstance(period, str) and period:
            return period
    return None


def time_reference_question(row: dict[str, Any]) -> str:
    family = row["task_family"]
    period = infer_time_period(row)
    if family == "day_mean_lookup":
        return "Which date should I use for that average?"
    if family == "relative_24h_mean_lookup":
        return "What exact date or timestamp should I use to anchor the trailing 24 hours?"
    if family in {"window_mean_lookup", "window_pairwise_compare"}:
        if period == "week":
            return "Which week should I use for that comparison?"
        if period == "month":
            return "Which month should I use for that comparison?"
        return "Which time window should I use for that comparison?"
    if family == "window_rank":
        if period == "month":
            return "Which month should I rank over?"
        return "Which time window should I rank over?"
    if family == "timestamp_value_lookup":
        return "What exact timestamp should I use, including fractional seconds if you have them?"
    if family == "timestamp_nearest_lookup":
        return "What exact requested timestamp should I use, including fractional seconds if you have them?"
    if period == "day":
        return "Which date should I use?"
    if period == "week":
        return "Which week should I use?"
    if period == "month":
        return "Which month should I use?"
    return "Which date or time window should I use?"


def render_time_reference(row: dict[str, Any]) -> str:
    family = row["task_family"]
    time_fields = infer_time_fields(row)
    if family == "day_mean_lookup":
        start = parse_iso_like_timestamp(time_fields["window_start"])
        return f"I mean {start.strftime('%B')} {start.day}, {start.year}."
    if family == "relative_24h_mean_lookup":
        end = parse_iso_like_timestamp(time_fields["window_end"])
        return f"Use the 24 hours leading up to {end.strftime('%H:%M')} UTC on {end.strftime('%B')} {end.day}, {end.year}."
    if family in {"window_mean_lookup", "window_pairwise_compare"}:
        start = parse_iso_like_timestamp(time_fields["window_start"])
        return f"I mean the week beginning {start.strftime('%B')} {start.day}, {start.year}."
    if family == "window_rank":
        start = parse_iso_like_timestamp(time_fields["window_start"])
        return f"I mean {month_name(start.month)} {start.year}."
    if family in {"timestamp_value_lookup", "timestamp_nearest_lookup"}:
        requested = parse_iso_like_timestamp(time_fields["requested_timestamp"])
        time_text = requested.strftime("%H:%M:%S")
        if requested.microsecond:
            time_text = f"{time_text}.{requested.microsecond:06d}".rstrip("0")
        return (
            f"I mean {time_text} UTC on "
            f"{requested.strftime('%B')} {requested.day}, {requested.year}."
        )
    return "Please use the exact time window from my original request."


def evidence_stream_ids(row: dict[str, Any]) -> list[str]:
    return list(row.get("evidence", {}).get("stream_ids", []))


def evidence_followup_prompt(row: dict[str, Any]) -> str:
    return FOLLOWUP_PROMPT_MULTI if len(evidence_stream_ids(row)) > 1 else FOLLOWUP_PROMPT


def make_implicit_nearest_prompt(row: dict[str, Any]) -> str:
    query = row["query"]
    patterns = [
        (
            re.compile(
                r"^(?P<prefix>(?:For|In) BTS_[A-Z]), if the (?P<subject>.+?) has no exact (?:reading|value) at (?P<time>.+?), which nearest available observation should the agent return\?$",
                flags=re.IGNORECASE,
            ),
            "{prefix}, what did the {subject} read at {time}?",
        ),
        (
            re.compile(
                r"^(?P<prefix>(?:For|In) BTS_[A-Z]), when (?P<time>.+?) has no exact (?:reading|value) for the (?P<subject>.+?), what is the nearest available observation\?$",
                flags=re.IGNORECASE,
            ),
            "{prefix}, what did the {subject} read at {time}?",
        ),
        (
            re.compile(
                r"^If there is no exact (?:sample|reading|value) at (?P<time>.+?), what is the nearest available observation for the (?P<subject>.+?)\?$",
                flags=re.IGNORECASE,
            ),
            "What did the {subject} read at {time}?",
        ),
    ]
    for pattern, template in patterns:
        match = pattern.match(query)
        if match:
            return repair_surface_artifacts(template.format(**match.groupdict()))
    return repair_surface_artifacts(
        query.replace("which nearest available observation should the agent return?", "what reading should I use?")
        .replace("what is the nearest available observation?", "what reading should I use?")
        .replace(" if ", " ")
        .replace(" has no exact reading", "")
        .replace(" has no exact value", "")
    )


def scenario_index(row: dict[str, Any]) -> int:
    try:
        return int(str(row["scenario_id"]).rsplit("_", 1)[-1])
    except Exception:
        return 0


def _primary_temporal_tool_call(row: dict[str, Any]) -> dict[str, Any] | None:
    for call in row.get("canonical_tool_calls", []):
        tool_name = str(call.get("tool_name", ""))
        if tool_name not in {"aggregate_window", "compare_window", "rank_window", "lookup_observation", "inspect_quality_window"}:
            continue
        call_id = str(call.get("call_id", ""))
        if "sample" in call_id:
            continue
        return call
    return None


def temporal_clarification_class(row: dict[str, Any]) -> str:
    family = str(row.get("task_family", ""))
    if family not in TIME_CLARIFY_FAMILIES:
        return "non_temporal"
    if family in TEMPORAL_CLARIFY_EXCLUSION_FAMILIES:
        return "nearest_fallback"
    primary_call = _primary_temporal_tool_call(row)
    if primary_call is None:
        return "non_temporal"
    if primary_call.get("tool_name") == "lookup_observation":
        mode = str(primary_call.get("arguments", {}).get("mode", ""))
        if mode == "nearest":
            return "nearest_fallback"
        if mode == "exact":
            return "anchor_missing_eligible"
    if primary_call.get("tool_name") in {"aggregate_window", "compare_window", "rank_window"}:
        return "anchor_missing_eligible"
    return "non_temporal"


def eligible_for_time_clarification(row: dict[str, Any]) -> bool:
    return temporal_clarification_class(row) == "anchor_missing_eligible"


def interaction_mode_for_row(row: dict[str, Any]) -> str:
    family = row["task_family"]
    if family == "quality_gate":
        return "quality_decision_then_rationale_then_evidence"
    if family == "point_disambiguation":
        return "direct_then_evidence"
    if temporal_clarification_class(row) == "nearest_fallback":
        return "implicit_nearest_then_evidence"
    # Only insert time clarification in the base E2E contract when the
    # executable seed does not already expose a concrete temporal anchor.
    if eligible_for_time_clarification(row) and not has_explicit_calendar_reference(str(row.get("query", ""))):
        return "clarify_time_then_evidence"
    return "direct_then_evidence"


def mode_requires_site_clarification(interaction_mode: str) -> bool:
    return interaction_mode in SITE_CLARIFY_MODES


def mode_requires_time_clarification(interaction_mode: str) -> bool:
    return interaction_mode in TIME_CLARIFY_MODES


def mode_requires_quality_rationale(interaction_mode: str) -> bool:
    return interaction_mode in QUALITY_RATIONALE_MODES


def initial_user_message(row: dict[str, Any], interaction_mode: str) -> str:
    query = rewrite_quality_prompt(row["query"]) if row["task_family"] == "quality_gate" else row["query"]
    if mode_requires_site_clarification(interaction_mode):
        query = remove_site_mentions(query, row["site_id"])
    if mode_requires_time_clarification(interaction_mode):
        query = remove_time_mentions(query, row["task_family"])
    if interaction_mode == "implicit_nearest_then_evidence":
        return humanize_visible_prompt(make_implicit_nearest_prompt(row), row["task_family"], interaction_mode)
    return humanize_visible_prompt(query, row["task_family"], interaction_mode)


def hidden_instruction(row: dict[str, Any]) -> str:
    if row["task_family"] == "quality_gate":
        return rewrite_quality_prompt(row["query"])
    return repair_surface_artifacts(row["query"])


def clarification_answers(row: dict[str, Any], interaction_mode: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    if mode_requires_site_clarification(interaction_mode):
        answers[SITE_CLARIFICATION_SLOT] = f"It's in {row['site_id']}."
    if mode_requires_time_clarification(interaction_mode):
        answers[TIME_CLARIFICATION_SLOT] = render_time_reference(row)
    return answers


def clarification_questions(row: dict[str, Any], interaction_mode: str) -> dict[str, str]:
    questions: dict[str, str] = {}
    if mode_requires_site_clarification(interaction_mode):
        questions[SITE_CLARIFICATION_SLOT] = "Which BTS site should I use?"
    if mode_requires_time_clarification(interaction_mode):
        temporal_class = temporal_clarification_class(row)
        if temporal_class == "nearest_fallback":
            questions[TIME_CLARIFICATION_SLOT] = "Which exact timestamp should I use before checking the nearest logged observation?"
        else:
            questions[TIME_CLARIFICATION_SLOT] = "Which date, week, month, time window, or exact timestamp should I use?"
    return questions


def required_clarification_slots(interaction_mode: str) -> list[str]:
    slots: list[str] = []
    if mode_requires_site_clarification(interaction_mode):
        slots.append(SITE_CLARIFICATION_SLOT)
    if mode_requires_time_clarification(interaction_mode):
        slots.append(TIME_CLARIFICATION_SLOT)
    return slots


def interaction_milestones(interaction_mode: str) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []
    if mode_requires_site_clarification(interaction_mode):
        milestones.append(
            {
                "name": "collect_site_id",
                "type": "user_clarification",
                "slot": SITE_CLARIFICATION_SLOT,
                "required": True,
            }
        )
    if mode_requires_time_clarification(interaction_mode):
        milestones.append(
            {
                "name": "collect_time_reference",
                "type": "user_clarification",
                "slot": TIME_CLARIFICATION_SLOT,
                "required": True,
            }
        )
    milestones.extend(
        [
            {
                "name": "deliver_operator_answer",
                "type": "agent_answer",
                "required": True,
            },
        ]
    )
    if mode_requires_quality_rationale(interaction_mode):
        milestones.append(
            {
                "name": "justify_quality_decision",
                "type": "agent_rationale_followup",
                "required": True,
            }
        )
    milestones.append(
        {
            "name": "provide_stream_evidence",
            "type": "agent_evidence_followup",
            "required": True,
        }
    )
    return milestones


def post_answer_user_turns(row: dict[str, Any], interaction_mode: str) -> list[str]:
    evidence_prompt = evidence_followup_prompt(row)
    if mode_requires_quality_rationale(interaction_mode):
        return [RATIONALE_FOLLOWUP_PROMPT, evidence_prompt]
    return [evidence_prompt]


@dataclass
class BtsE2EScenario:
    base: dict[str, Any]
    interaction_mode: str

    def as_dict(self) -> dict[str, Any]:
        base = dict(self.base)
        metadata = dict(base.get("metadata", {}))
        metadata.update(
            {
                "interaction_mode": self.interaction_mode,
                "backing_query_surface_version": base.get("query_surface_version"),
                "e2e_track_version": E2E_TRACK_VERSION,
                "evidence_stream_count": len(evidence_stream_ids(base)),
            }
        )
        required_slots = required_clarification_slots(self.interaction_mode)
        followup_prompt = evidence_followup_prompt(base)
        return {
            "scenario_id": base["scenario_id"],
            "split": base["split"],
            "site_id": base["site_id"],
            "task_family": base["task_family"],
            "track": "bts_e2e",
            "e2e_track_version": E2E_TRACK_VERSION,
            "backing_static_scenario_id": base["scenario_id"],
            "initial_user_message": initial_user_message(base, self.interaction_mode),
            "hidden_user_instruction": hidden_instruction(base),
            "interaction_mode": self.interaction_mode,
            "clarification_answers": clarification_answers(base, self.interaction_mode),
            "clarification_questions": clarification_questions(base, self.interaction_mode),
            "required_clarification_slots": required_slots,
            "followup_prompt": followup_prompt,
            "post_answer_user_turns": post_answer_user_turns(base, self.interaction_mode),
            "termination_reply": STOP_ACK,
            "interaction_verifier": {
                "required_clarification_slots": required_slots,
                "require_evidence_followup": True,
                "require_rationale_followup": mode_requires_quality_rationale(self.interaction_mode),
                "max_user_turns": 2 + len(required_slots) + len(post_answer_user_turns(base, self.interaction_mode)),
            },
            "interaction_milestones": interaction_milestones(self.interaction_mode),
            "canonical_tool_calls": base["canonical_tool_calls"],
            "acceptable_tool_call_sets": base["acceptable_tool_call_sets"],
            "gold_final_answer": base["gold_final_answer"],
            "evidence": base["evidence"],
            "task_accomplish_verifier": base["task_accomplish_verifier"],
            "difficulty_proxy": base["difficulty_proxy"],
            "metadata": metadata,
        }


class DeterministicBtsUserSimulator:
    def __init__(self, scenario: dict[str, Any]):
        self.scenario = scenario
        self.pending_slots = list(scenario.get("required_clarification_slots", []))
        self.answer_seen = False
        self.goal_revision_turns = list(scenario.get("goal_revision_turns", []))
        self.goal_revision_index = 0
        self.awaiting_goal_revision_answer = False
        self.post_answer_turns = list(scenario.get("post_answer_user_turns", [scenario.get("followup_prompt", FOLLOWUP_PROMPT)]))
        self.post_answer_index = 0
        self.done = False

    def reset(self) -> str:
        return self.scenario["initial_user_message"]

    @staticmethod
    def _is_tool_action(text: str) -> bool:
        stripped = text.strip()
        return stripped.startswith("{") and '"tool_name"' in stripped

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        lowered = text.strip().lower()
        return "?" in lowered or lowered.startswith(
            ("what ", "which ", "where ", "when ", "do you ", "can you ", "could you ", "is it ", "are you ")
        )

    @staticmethod
    def _asks_for_site(text: str) -> bool:
        lowered = text.lower()
        keywords = [
            "which site",
            "what site",
            "site id",
            "which building",
            "what building",
            "which building or site",
            "what building or site",
            "which site is this for",
            "what site is this for",
            "which building is this for",
            "what building is this for",
            "which building or site is this for",
            "what building or site is this for",
            "which bts",
            "what facility",
            "where is this",
        ]
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _asks_for_time_reference(text: str) -> bool:
        lowered = text.lower()
        if not DeterministicBtsUserSimulator._looks_like_question(text):
            return False
        keywords = [
            "date",
            "day",
            "week",
            "month",
            "timestamp",
            "time window",
            "date or timestamp",
            "requested time",
            "trailing 24 hours",
            "anchor",
        ]
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _asks_for_custom_slot(slot: str, text: str) -> bool:
        lowered = text.lower()
        if not DeterministicBtsUserSimulator._looks_like_question(text):
            return False
        slot_lowered = slot.lower()
        if any(token in slot_lowered for token in ["target", "asset", "point", "signal"]):
            keywords = [
                "which signal",
                "what signal",
                "which point",
                "what point",
                "which equipment",
                "what equipment",
                "which asset",
                "what asset",
                "target",
            ]
            return any(keyword in lowered for keyword in keywords)
        if any(token in slot_lowered for token in ["comparison", "candidate"]):
            keywords = ["which candidates", "what candidates", "which two", "what two", "compare", "comparison"]
            return any(keyword in lowered for keyword in keywords)
        if any(token in slot_lowered for token in ["scope", "group"]):
            keywords = ["which scope", "what scope", "across what", "which group", "what group", "locations"]
            return any(keyword in lowered for keyword in keywords)
        return False

    def step(self, agent_message: str) -> str | None:
        if self.done or self._is_tool_action(agent_message):
            return None

        if self.pending_slots:
            for slot in list(self.pending_slots):
                if slot == "site_id" and self._asks_for_site(agent_message):
                    self.pending_slots.remove(slot)
                    return self.scenario["clarification_answers"][slot]
                if slot == TIME_CLARIFICATION_SLOT and self._asks_for_time_reference(agent_message):
                    self.pending_slots.remove(slot)
                    return self.scenario["clarification_answers"][slot]
                if (
                    slot not in {"site_id", TIME_CLARIFICATION_SLOT}
                    and slot in self.scenario.get("clarification_answers", {})
                    and self._asks_for_custom_slot(slot, agent_message)
                ):
                    self.pending_slots.remove(slot)
                    return self.scenario["clarification_answers"][slot]
            if not self._looks_like_question(agent_message):
                return "I still need the missing detail before I can confirm that."
            if "site_id" in self.pending_slots:
                return "I mean the building or site. Please ask me directly for it."
            for slot in self.pending_slots:
                if slot not in {TIME_CLARIFICATION_SLOT, "site_id"}:
                    return "I mean the missing signal, equipment, candidate, or scope detail. Please ask me directly for it."
            return (
                "I mean the missing time reference. Please ask directly for the exact date, week, month, "
                "time window, or exact timestamp, including fractional seconds when needed."
            )

        if not self.answer_seen:
            if self._looks_like_question(agent_message):
                return "That's all the context I have. Please go ahead."
            self.answer_seen = True
            if self.goal_revision_turns:
                self.awaiting_goal_revision_answer = True
                return self.goal_revision_turns[self.goal_revision_index]
            return self.post_answer_turns[self.post_answer_index]

        if self.awaiting_goal_revision_answer:
            if self._looks_like_question(agent_message):
                return "Use the same stream and site as before. Only update the requested time window or comparison target I just asked for."
            self.goal_revision_index += 1
            if self.goal_revision_index < len(self.goal_revision_turns):
                return self.goal_revision_turns[self.goal_revision_index]
            self.awaiting_goal_revision_answer = False
            if self.post_answer_turns:
                return self.post_answer_turns[self.post_answer_index]
            self.done = True
            return STOP_ACK

        if self.post_answer_index < len(self.post_answer_turns):
            if self._looks_like_question(agent_message):
                if self.post_answer_turns[self.post_answer_index] == RATIONALE_FOLLOWUP_PROMPT:
                    return "Please tell me why you think I should answer or abstain."
                if self.scenario.get("followup_prompt") == FOLLOWUP_PROMPT_MULTI:
                    return "Just tell me the specific streams or points you used."
                return "Just tell me the specific stream or point you used."
            self.post_answer_index += 1
            if self.post_answer_index < len(self.post_answer_turns):
                return self.post_answer_turns[self.post_answer_index]
            self.done = True
            return STOP_ACK

        self.done = True
        return self.scenario["termination_reply"]


def build_bts_e2e(
    static_dir: Path,
    out_dir: Path,
    *,
    corpus_name: str = "bts",
    track_name: str | None = None,
    e2e_track_version: str | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_track_name = track_name or f"{corpus_name}_e2e"
    resolved_e2e_track_version = e2e_track_version or E2E_TRACK_VERSION
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "dev": [], "test": []}
    interaction_summary: dict[str, int] = {}
    family_summary: dict[str, int] = {}
    split_interaction_summary: dict[str, dict[str, int]] = {split: {} for split in splits}
    family_interaction_summary: dict[str, dict[str, int]] = {}

    for split in splits:
        rows = load_jsonl(static_dir / f"{split}.jsonl")
        for row in rows:
            mode = interaction_mode_for_row(row)
            interaction_summary[mode] = interaction_summary.get(mode, 0) + 1
            split_interaction_summary[split][mode] = split_interaction_summary[split].get(mode, 0) + 1
            family = row["task_family"]
            family_summary[family] = family_summary.get(family, 0) + 1
            family_interaction_summary.setdefault(family, {})
            family_interaction_summary[family][mode] = family_interaction_summary[family].get(mode, 0) + 1
            scenario = BtsE2EScenario(base=row, interaction_mode=mode).as_dict()
            scenario["track"] = resolved_track_name
            scenario["e2e_track_version"] = resolved_e2e_track_version
            scenario["metadata"] = dict(scenario.get("metadata", {}))
            scenario["metadata"]["e2e_track_version"] = resolved_e2e_track_version
            scenario["query"] = row.get("query")
            scenario["query_surface_version"] = row.get("query_surface_version")
            scenario["backing_static_scenario_id"] = row["scenario_id"]
            scenario["generation_history"] = [
                {
                    "stage": "seed_static_executable_task",
                    "stage_type": "seed",
                    "builder": "scenario_benchmark",
                    "status": "observed",
                    "details": {
                        "source_static_scenario_id": row["scenario_id"],
                        "task_family": row["task_family"],
                        "query": row.get("query"),
                        "query_surface_version": row.get("query_surface_version"),
                        "canonical_tool_names": [call.get("tool_name") for call in row.get("canonical_tool_calls", [])],
                        "canonical_tool_count": len(row.get("canonical_tool_calls", [])),
                    },
                },
                {
                    "stage": "deterministic_e2e_contract_generation",
                    "stage_type": "contract",
                    "builder": "build_bts_e2e",
                    "status": "generated",
                    "details": {
                        "e2e_track_version": resolved_e2e_track_version,
                        "interaction_mode": scenario.get("interaction_mode"),
                        "required_clarification_slots": scenario.get("required_clarification_slots", []),
                        "goal_revision_turn_count": len(scenario.get("goal_revision_turns", [])),
                        "post_answer_user_turn_count": len(scenario.get("post_answer_user_turns", [])),
                        "interaction_milestones": [milestone.get("name") for milestone in scenario.get("interaction_milestones", [])],
                        "followup_prompt": scenario.get("followup_prompt"),
                    },
                },
            ]
            splits[split].append(scenario)
        write_jsonl(out_dir / f"{split}.jsonl", splits[split])

    manifest = {
        "track": resolved_track_name,
        "e2e_track_version": resolved_e2e_track_version,
        "corpus_name": corpus_name,
        "source_static_dir": str(static_dir),
        "splits": {split: len(rows) for split, rows in splits.items()},
        "task_families": family_summary,
        "interaction_modes": interaction_summary,
        "split_interaction_modes": split_interaction_summary,
        "family_interaction_modes": family_interaction_summary,
        "deterministic_user_simulator": True,
        "followup_prompt_policy": {
            "single_stream": FOLLOWUP_PROMPT,
            "multi_stream": FOLLOWUP_PROMPT_MULTI,
        },
        **clarify_policy_manifest_fields(
            clarify_count=sum(1 for split_rows in splits.values() for row in split_rows if row["required_clarification_slots"]),
            clarify_scope="recoverable-slot masking with temporal fallback disentanglement",
        ),
        "temporal_clarify_policy": {
            "eligible_class": "anchor_missing_eligible",
            "excluded_class": "nearest_fallback",
            "excluded_families": sorted(TEMPORAL_CLARIFY_EXCLUSION_FAMILIES),
        },
        "clarification_capability_count": sum(1 for split_rows in splits.values() for row in split_rows if row["required_clarification_slots"]),
        "quality_abstention_capability_count": sum(
            1
            for split_rows in splits.values()
            for row in split_rows
            if row["task_family"] == "quality_gate" and row["gold_final_answer"].get("decision") == "abstain"
        ),
        "clarify_plus_quality_mode_count": sum(
            1
            for split_rows in splits.values()
            for row in split_rows
            if row["interaction_mode"] == "clarify_site_then_quality_decision_then_rationale_then_evidence"
        ),
        "row_level_generation_history": {
            "enabled": True,
            "history_version": "generation-history",
            "stages": [
                "seed_static_executable_task",
                "deterministic_e2e_contract_generation",
            ],
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tool_registry = static_dir / "scenario_tool_registry.json"
    if tool_registry.exists():
        (out_dir / "scenario_tool_registry.json").write_text(tool_registry.read_text(encoding="utf-8"), encoding="utf-8")
    return manifest


def audit_bts_e2e(benchmark_dir: Path) -> dict[str, Any]:
    counts: dict[str, int] = {"scenario_count": 0, "failed": 0}
    failures: list[dict[str, Any]] = []
    for split in ["train", "dev", "test"]:
        for row in load_jsonl(benchmark_dir / f"{split}.jsonl"):
            counts["scenario_count"] += 1
            issues: list[str] = []
            mode = row["interaction_mode"]
            initial = row["initial_user_message"]
            site_id = row["site_id"]
            if mode_requires_site_clarification(mode) and site_id.lower() in initial.lower():
                issues.append("site_leak_in_initial_message")
            expected_followup = evidence_followup_prompt(row)
            if row["followup_prompt"] != expected_followup:
                issues.append("unexpected_followup_prompt")
            if "provide_stream_evidence" not in {m["name"] for m in row["interaction_milestones"]}:
                issues.append("missing_evidence_milestone")
            if row.get("followup_prompt") not in row.get("post_answer_user_turns", []):
                issues.append("missing_evidence_followup_turn")
            if mode_requires_time_clarification(mode):
                if has_explicit_calendar_reference(initial):
                    issues.append("time_leak_in_initial_message")
                if re.search(r"\b\d{2}:\d{2}\s*utc\b", initial, flags=re.IGNORECASE):
                    issues.append("clock_time_leak_in_initial_message")
            if row.get("task_family") == "timestamp_nearest_lookup":
                lowered = initial.lower()
                policy_contract = row.get("policy_choice_contract")
                if not policy_contract and not any(
                    phrase in lowered
                    for phrase in [
                        "no exact sample",
                        "exact sample was unavailable",
                        "nearby reading",
                        "nearest logged reading",
                        "around a requested timestamp",
                    ]
                ):
                    issues.append("nearest_intent_missing_in_initial_message")
            if mode_requires_quality_rationale(mode):
                milestone_names = {m["name"] for m in row["interaction_milestones"]}
                if "justify_quality_decision" not in milestone_names:
                    issues.append("missing_rationale_milestone")
                if row.get("post_answer_user_turns", [None])[0] != RATIONALE_FOLLOWUP_PROMPT:
                    issues.append("missing_rationale_followup_prompt")
            goal_revision_turns = row.get("goal_revision_turns", [])
            if goal_revision_turns:
                milestone_names = {m["name"] for m in row["interaction_milestones"]}
                if "receive_goal_revision" not in milestone_names:
                    issues.append("missing_goal_revision_milestone")
                if "deliver_revised_operator_answer" not in milestone_names:
                    issues.append("missing_revised_answer_milestone")
                if not row.get("interaction_verifier", {}).get("require_goal_revision_answer", False):
                    issues.append("goal_revision_verifier_missing")
            if len(evidence_stream_ids(row)) > 1 and row.get("followup_prompt") != FOLLOWUP_PROMPT_MULTI:
                issues.append("ambiguous_single_stream_prompt_for_multi_stream_evidence")
            if issues:
                counts["failed"] += 1
                failures.append({"scenario_id": row["scenario_id"], "issues": issues})
    counts["passed"] = counts["scenario_count"] - counts["failed"]
    counts["failures"] = failures
    return counts
