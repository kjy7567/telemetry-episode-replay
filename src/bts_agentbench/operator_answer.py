from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .runtime import ExecutedCall, ToolStoreRuntime

REPORTING_FIELDS = {"sample_exact_match_found", "sample_checked_timestamp"}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _format_number(value: Any) -> list[str]:
    try:
        numeric = float(value)
    except Exception:
        return []
    variants = {
        f"{numeric:.4f}",
        f"{numeric:.3f}",
        f"{numeric:.2f}",
        f"{numeric:.1f}",
        str(round(numeric, 4)),
    }
    cleaned = set()
    for variant in variants:
        trimmed = variant.rstrip("0").rstrip(".")
        cleaned.add(variant)
        if trimmed:
            cleaned.add(trimmed)
    return sorted({normalize_text(item) for item in cleaned if item})


def _format_timestamp(value: Any) -> list[str]:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return []
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    variants = {
        ts.isoformat(),
        ts.strftime("%Y-%m-%d %H:%M:%S%z"),
        ts.strftime("%Y-%m-%d %H:%M:%S"),
        ts.strftime("%Y-%m-%d %H:%M"),
        ts.strftime("%Y-%m-%d"),
        ts.strftime("%H:%M:%S"),
        ts.strftime("%H:%M"),
        ts.strftime("%b %d, %Y %H:%M"),
        ts.strftime("%B %d, %Y %H:%M"),
    }
    return sorted({normalize_text(item.replace("+0000", "+00:00")) for item in variants if item})


def _preferred_reference_variants(gold: dict[str, Any]) -> list[str]:
    variants: set[str] = set()
    ref = str(gold.get("preferred_reference") or "")
    if ref == "first":
        variants.update(
            {
                "first",
                "the first",
                "trust the first",
                "trust first",
                "first slightly more",
                "the first slightly more",
                "first result",
                "the first result",
                "first timestamp result",
                "the first timestamp result",
                "first reading",
                "the first reading",
            }
        )
    elif ref == "second":
        variants.update(
            {
                "second",
                "the second",
                "trust the second",
                "trust second",
                "second slightly more",
                "the second slightly more",
                "second result",
                "the second result",
                "second timestamp result",
                "the second timestamp result",
                "second reading",
                "the second reading",
            }
        )
    elif ref:
        variants.add(ref)

    stream_id = gold.get("stream_id")
    if isinstance(stream_id, str) and stream_id.strip():
        variants.add(stream_id)

    period = str(gold.get("period") or "")
    for timestamp_key in ["window_start", "requested_timestamp", "observed_timestamp"]:
        value = gold.get(timestamp_key)
        if value is None:
            continue
        try:
            ts = pd.Timestamp(value)
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        base_date = {
            ts.strftime("%b %d"),
            ts.strftime("%b %d, %Y"),
            ts.strftime("%B %d"),
            ts.strftime("%B %d, %Y"),
            ts.strftime("%Y-%m-%d"),
            ts.strftime("%H:%M"),
            ts.strftime("%H:%M:%S"),
        }
        variants.update(base_date)
        if period == "week":
            variants.update(
                {
                    f"week beginning {ts.strftime('%b %d, %Y')}",
                    f"week beginning {ts.strftime('%B %d, %Y')}",
                    f"week beginning {ts.strftime('%b %d')}",
                    f"week beginning {ts.strftime('%B %d')}",
                    f"week of {ts.strftime('%b %d, %Y')}",
                    f"week of {ts.strftime('%B %d, %Y')}",
                }
            )
        elif period == "month":
            variants.update(
                {
                    ts.strftime("%b %Y"),
                    ts.strftime("%B %Y"),
                    f"month of {ts.strftime('%b %Y')}",
                    f"month of {ts.strftime('%B %Y')}",
                }
            )
        elif period == "day":
            variants.update(
                {
                    f"{ts.strftime('%b %d')} result",
                    f"{ts.strftime('%B %d')} result",
                    f"{ts.strftime('%b %d, %Y')} result",
                    f"{ts.strftime('%B %d, %Y')} result",
                }
            )
    return sorted({normalize_text(item) for item in variants if item})


def _compress_label(label: str) -> set[str]:
    variants = {label, label.lower()}
    lowered = label.lower()
    replacements = {
        "fan coil unit": "fcu",
        "air handler unit": "ahu",
        "building control unit": "bcu",
    }
    for source, target in replacements.items():
        if source in lowered:
            variants.add(lowered.replace(source, target))
    parts = label.split(" ", 1)
    if len(parts) == 2 and "_" in parts[0]:
        variants.add(parts[1])
        variants.add(parts[1].lower())
    return {normalize_text(item) for item in variants if item}


def descriptor_candidates(runtime: ToolStoreRuntime | None, stream_id: str, fallback_label: str | None = None) -> list[str]:
    candidates: set[str] = {normalize_text(stream_id)}
    if runtime is not None:
        description = runtime.describe_stream(stream_id)
        for key in ["point_label", "equipment_label", "location_label"]:
            value = description.get(key)
            if isinstance(value, str) and value.strip():
                candidates.update(_compress_label(value))
    if fallback_label:
        candidates.update(_compress_label(fallback_label))
    return sorted(candidates)


def _text_mentions_stream(text: str, runtime: ToolStoreRuntime | None, stream_id: str, fallback_label: str | None = None) -> bool:
    return any(candidate and candidate in text for candidate in descriptor_candidates(runtime, stream_id, fallback_label))


def _extract_stream_ids_from_value(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, dict):
        out: set[str] = set()
        for key, item in value.items():
            if key == "stream_id" and isinstance(item, str):
                out.add(item)
            else:
                out.update(_extract_stream_ids_from_value(item))
        return out
    if isinstance(value, list):
        out: set[str] = set()
        for item in value:
            out.update(_extract_stream_ids_from_value(item))
        return out
    return set()


def check_evidence_followup(
    example: dict[str, Any],
    answer_text: str,
    executed_calls: list[ExecutedCall],
    runtime: ToolStoreRuntime | None = None,
) -> bool:
    text = normalize_text(answer_text)
    evidence = example.get("evidence", {})
    required = evidence.get("stream_ids", [])
    if not text or not required:
        return False

    def window_basis_variants(window_start: Any, period: str) -> list[str]:
        try:
            ts = pd.Timestamp(window_start)
        except Exception:
            return []
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        variants: set[str] = set()
        if period == "week":
            variants.update(
                {
                    f"week beginning {ts.strftime('%b %d, %Y')}",
                    f"week beginning {ts.strftime('%B %d, %Y')}",
                    f"week beginning {ts.strftime('%b %d')}",
                    f"week beginning {ts.strftime('%B %d')}",
                    f"week of {ts.strftime('%b %d, %Y')}",
                    f"week of {ts.strftime('%B %d, %Y')}",
                }
            )
        elif period == "month":
            variants.update(
                {
                    ts.strftime("%b %Y"),
                    ts.strftime("%B %Y"),
                    f"month of {ts.strftime('%b %Y')}",
                    f"month of {ts.strftime('%B %Y')}",
                }
            )
        elif period == "day":
            variants.update(
                {
                    ts.strftime("%b %d"),
                    ts.strftime("%B %d"),
                    ts.strftime("%b %d, %Y"),
                    ts.strftime("%B %d, %Y"),
                    ts.strftime("%Y-%m-%d"),
                }
            )
        else:
            variants.update(_format_timestamp(window_start))
        return sorted({normalize_text(item) for item in variants if item})

    def basis_ok() -> bool:
        basis_hits: list[bool] = []
        if isinstance(evidence.get("basis_observed_timestamp"), str):
            basis_hits.append(any(variant and variant in text for variant in _format_timestamp(evidence["basis_observed_timestamp"])))
        window_start = evidence.get("basis_window_start")
        period = str(evidence.get("basis_period") or "")
        if window_start is not None and period:
            basis_hits.append(any(variant and variant in text for variant in window_basis_variants(window_start, period)))
        preferred_reference = evidence.get("basis_preferred_reference")
        if preferred_reference:
            basis_hits.append(
                any(
                    variant and variant in text
                    for variant in _preferred_reference_variants(
                        {
                            "preferred_reference": preferred_reference,
                            "window_start": evidence.get("basis_window_start"),
                            "requested_timestamp": evidence.get("basis_requested_timestamp"),
                            "observed_timestamp": evidence.get("basis_observed_timestamp"),
                            "period": evidence.get("basis_period"),
                        }
                    )
                )
            )
        for label_key in ["basis_equipment_label", "basis_point_label", "basis_location_label"]:
            label = evidence.get(label_key)
            if isinstance(label, str) and label.strip():
                basis_hits.append(any(candidate and candidate in text for candidate in _compress_label(label)))
        if not basis_hits:
            return True
        return any(basis_hits)

    if all(_text_mentions_stream(text, runtime, stream_id) for stream_id in required) and basis_ok():
        return True

    if example.get("task_family") == "window_rank":
        winner_stream_id = example.get("gold_final_answer", {}).get("stream_id")
        if winner_stream_id and _text_mentions_stream(text, runtime, winner_stream_id):
            return basis_ok()
        executed_stream_ids: set[str] = set()
        for call in executed_calls:
            executed_stream_ids.update(_extract_stream_ids_from_value(call.output))
        mentioned_stream_ids = {
            stream_id for stream_id in executed_stream_ids if _text_mentions_stream(text, runtime, stream_id)
        }
        if len(mentioned_stream_ids) >= 2:
            return basis_ok()
        if any(
            cue in text
            for cue in [
                "ranked",
                "ranking",
                "candidate streams",
                "candidate points",
                "across streams",
                "across points",
            ]
        ):
            return basis_ok()
    if example.get("task_family") == "window_pairwise_compare":
        winner_stream_id = None
        for gold in reversed(example.get("phase_gold_final_answers", [])):
            if isinstance(gold, dict) and gold.get("winning_stream_id"):
                winner_stream_id = str(gold["winning_stream_id"])
                break
        if winner_stream_id and _text_mentions_stream(text, runtime, winner_stream_id):
            return basis_ok()
        mentioned_required = sum(1 for stream_id in required if _text_mentions_stream(text, runtime, stream_id))
        if mentioned_required >= 1 and any(
            cue in text
            for cue in [
                "winning",
                "higher average",
                "higher mean",
                "higher value",
                "compared",
                "comparison",
                "versus",
                "vs",
            ]
        ):
            return basis_ok()
    return False


def check_rationale_followup(
    example: dict[str, Any],
    answer_text: str,
    runtime: ToolStoreRuntime | None = None,
) -> bool:
    text = normalize_text(answer_text)
    if not text:
        return False

    gold = example["gold_final_answer"]
    if example.get("task_family") == "quality_gate":
        for phase_example, phase_gold in reversed(
            list(zip(example.get("phase_examples", []), example.get("phase_gold_final_answers", [])))
        ):
            if str(phase_example.get("task_family", "")) == "quality_gate":
                gold = phase_gold
                break
    decision = gold.get("decision")
    if decision == "answer":
        decision_cues = [
            "i would answer",
            "would answer",
            "i would not abstain",
            "would not abstain",
            "not abstain",
            "can answer",
            "answer this",
            "healthy enough",
            "reliable enough",
            "looks healthy",
            "supports answering",
            "supports reporting",
            "reliable enough to answer",
            "reliable enough to report",
        ]
        conflicting_cues = ["i would abstain", "would abstain", "cannot answer", "can't answer", "insufficient"]
    else:
        decision_cues = [
            "i would abstain",
            "would abstain",
            "abstain",
            "cannot answer",
            "can't answer",
            "not reliable enough",
            "insufficient",
            "not enough data",
            "poor quality",
        ]
        conflicting_cues = ["i would answer", "would answer", "can answer", "looks healthy", "healthy enough"]

    has_decision = any(cue in text for cue in decision_cues)
    if not has_decision:
        return False
    if decision == "answer" and any(cue in text for cue in conflicting_cues):
        return False

    coverage_terms = [
        "coverage",
        "observed fraction",
        "observed coverage",
        "fraction observed",
        "enough data coverage",
        "insufficient coverage",
        "low coverage",
    ]
    gap_terms = [
        "gap ratio",
        "longest gap",
        "large gap",
        "big gap",
        "too many gaps",
        "missing data",
        "missingness",
    ]
    quality_terms = [
        "data quality",
        "signal quality",
        "quality metrics",
        "quality is poor",
        "quality looks healthy",
        "quality is healthy",
        "quality is reliable",
    ]

    observed_fraction_terms = _format_number(gold.get("observed_fraction"))
    gap_ratio_terms = _format_number(gold.get("gap_ratio"))

    has_coverage_reason = any(term in text for term in coverage_terms) or any(
        term and term in text for term in observed_fraction_terms
    )
    has_gap_reason = any(term in text for term in gap_terms) or any(term and term in text for term in gap_ratio_terms)
    has_quality_reason = any(term in text for term in quality_terms)

    if decision == "abstain" and runtime is not None:
        quality_reference = runtime.window_quality_reference("week")
        low_coverage = (
            quality_reference.get("abstain_observed_fraction_below") is not None
            and gold.get("observed_fraction") is not None
            and float(gold["observed_fraction"]) < float(quality_reference["abstain_observed_fraction_below"])
        )
        high_gap = (
            quality_reference.get("abstain_gap_ratio_above") is not None
            and gold.get("gap_ratio") is not None
            and float(gold["gap_ratio"]) > float(quality_reference["abstain_gap_ratio_above"])
        )
        if low_coverage and not high_gap:
            return has_coverage_reason or has_quality_reason
        if high_gap and not low_coverage:
            return has_gap_reason or has_quality_reason

    return has_coverage_reason or has_gap_reason or has_quality_reason


def render_operator_answer(
    example: dict[str, Any],
    final_answer: dict[str, Any],
    runtime: ToolStoreRuntime | None = None,
) -> str:
    family = example["task_family"]
    metadata = example.get("metadata", {})
    descriptor = metadata.get("equipment_label") or metadata.get("location_label") or final_answer.get("stream_id") or "the requested point"
    sample_suffix = ""
    if "sample_checked_timestamp" in final_answer:
        sample_suffix = f" The raw sample check was an exact match at {final_answer['sample_checked_timestamp']}."
    if family == "point_disambiguation":
        return f"Use stream {final_answer['stream_id']} for {descriptor}.{sample_suffix}"
    if family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
        return f"The average value for {descriptor} over the requested time window was {final_answer['mean_value']}.{sample_suffix}"
    if family == "window_pairwise_compare":
        winner = final_answer["winning_stream_id"]
        winner_desc = winner
        if runtime is not None:
            winner_desc = runtime.describe_stream(winner).get("equipment_label") or winner
        return (
            f"{winner_desc} had the higher average value. "
            f"The two averages were {final_answer['left_mean_value']} and {final_answer['right_mean_value']}.{sample_suffix}"
        )
    if family == "window_rank":
        winner = final_answer["stream_id"]
        winner_desc = winner
        if runtime is not None:
            winner_desc = runtime.describe_stream(winner).get("equipment_label") or winner
        if winner_desc != winner:
            return (
                f"The top stream was {winner} for {winner_desc}, "
                f"with an average value of {final_answer['mean_value']}.{sample_suffix}"
            )
        return f"The top stream was {winner}, with an average value of {final_answer['mean_value']}.{sample_suffix}"
    if family == "rank_stability_assessment":
        first_stream = str(final_answer.get("first_stream_id") or "the first month's winner")
        second_stream = str(final_answer.get("second_stream_id") or "the second month's winner")
        status = str(final_answer.get("stability_status") or "")
        if status == "same_winner":
            return (
                f"The top-ranked stream stayed the same across the two months: {second_stream}.{sample_suffix}"
            )
        return (
            f"The top-ranked stream changed between the two months, from {first_stream} to {second_stream}.{sample_suffix}"
        )
    if family == "timestamp_value_lookup":
        return f"The logged reading was {final_answer['value']} at {final_answer['observed_timestamp']}.{sample_suffix}"
    if family == "timestamp_nearest_lookup":
        return (
            "There was no exact logged reading at the requested time. "
            f"The nearest logged time was {final_answer['observed_timestamp']} and the value was {final_answer['value']}.{sample_suffix}"
        )
    if family == "quality_gate":
        if final_answer["decision"] == "abstain":
            return (
                "I would abstain because the data quality is not reliable enough. "
                f"Observed coverage was {final_answer['observed_fraction']} and the gap ratio was {final_answer['gap_ratio']}.{sample_suffix}"
            )
        return (
            "The data looks healthy enough to answer. "
            f"Observed coverage was {final_answer['observed_fraction']} and the gap ratio was {final_answer['gap_ratio']}.{sample_suffix}"
        )
    if family == "quality_trend_assessment":
        status = str(final_answer.get("trend_status") or "")
        if status == "quality_improved":
            return f"The data quality improved from the first interval to the second.{sample_suffix}"
        if status == "quality_worsened":
            return f"The data quality worsened from the first interval to the second.{sample_suffix}"
        return f"The data quality stayed about the same across the two intervals.{sample_suffix}"
    if family == "quality_preference":
        ref = final_answer["preferred_reference"]
        reason = str(final_answer.get("reason") or "")
        if reason == "healthy":
            reason_text = " The quality looks healthy."
        elif reason == "low_coverage":
            reason_text = " Coverage is too low."
        elif reason == "long_gap":
            reason_text = " The gap ratio is too high."
        else:
            reason_text = " The quality is marginal."
        return (
            f"I would trust the {ref} result more for reporting, and I would {final_answer['decision']} for it. "
            f"Observed coverage was {final_answer['observed_fraction']} and the gap ratio was {final_answer['gap_ratio']}.{reason_text}{sample_suffix}"
        )
    if family == "timestamp_preference":
        ref = final_answer["preferred_reference"]
        cue = (
            "It was an exact logged reading."
            if bool(final_answer.get("exact_match_found", False))
            else "There was no exact logged reading, so this is the nearest available one."
        )
        return (
            f"I would trust the {ref} timestamp result more for reporting. "
            f"The selected reading was {final_answer['value']} at {final_answer['observed_timestamp']}. {cue}{sample_suffix}"
        )
    if family == "timestamp_resolution_context":
        status = str(final_answer.get("resolution_status") or "")
        observed = final_answer.get("observed_timestamp")
        offset = final_answer.get("offset_seconds")
        if status == "exact":
            return f"This is an exact logged match at {observed}.{sample_suffix}"
        if status == "unique_nearest":
            return (
                f"This is a unique nearest match. The nearest logged time was {observed}, "
                f"with an offset of {offset} seconds.{sample_suffix}"
            )
        return (
            f"This is too ambiguous to report from the public time alone. "
            f"The nearest logged time was {observed}, with an offset of {offset} seconds.{sample_suffix}"
        )
    if family in {"reporting_commitment", "timestamp_reportability_decision"}:
        action = str(final_answer.get("commitment_action") or "")
        reason = str(final_answer.get("reason") or "")
        if action == "answer":
            if reason == "exact_timestamp":
                return f"I would report it as-is because the timestamp is exact.{sample_suffix}"
            if reason == "nearest_but_acceptable":
                return f"I would report it as-is because the nearest available timestamp is close enough.{sample_suffix}"
            return f"I would report it as-is because the result is reliable enough to report.{sample_suffix}"
        if action == "abstain":
            if reason == "low_coverage":
                return f"I would abstain because coverage is too low to report confidently.{sample_suffix}"
            if reason == "long_gap":
                return f"I would abstain because the gap ratio is too high to report confidently.{sample_suffix}"
            return f"I would abstain rather than report it.{sample_suffix}"
        clarification_request = str(final_answer.get("clarification_request") or "")
        if clarification_request == "more_precise_timestamp":
            return f"I would ask for a more precise timestamp before reporting it because the current time is too imprecise.{sample_suffix}"
        return f"I would ask for a narrower time range before reporting it because the current quality is marginal.{sample_suffix}"
    return str(final_answer)


def check_operator_answer(
    example: dict[str, Any],
    answer_text: str,
    runtime: ToolStoreRuntime,
    executed_calls: list[ExecutedCall],
    *,
    include_core_fields: bool = True,
    include_reporting_fields: bool = True,
) -> tuple[bool, float, list[str]]:
    text = normalize_text(answer_text)
    gold = example["gold_final_answer"]
    family = example["task_family"]
    issues: list[str] = []
    checks = 0
    passed = 0
    matched_fields: set[str] = set()

    def require_match(name: str, variants: list[str], *, field_key: str | None = None) -> None:
        nonlocal checks, passed
        checks += 1
        if any(variant and variant in text for variant in variants):
            passed += 1
            if field_key:
                matched_fields.add(field_key)
        else:
            issues.append(f"missing_answer_fact:{name}")

    def required_field_variants(field: str) -> list[str]:
        if field == "stream_id" and isinstance(gold.get("stream_id"), str):
            return descriptor_candidates(runtime, gold["stream_id"])
        if field == "winning_stream_id" and isinstance(gold.get("winning_stream_id"), str):
            return descriptor_candidates(runtime, gold["winning_stream_id"])
        if field in {"left_mean_value", "right_mean_value", "mean_value", "value", "observed_fraction", "gap_ratio"}:
            return _format_number(gold.get(field))
        if field == "offset_seconds":
            return _format_number(gold.get(field))
        if field in {"observed_timestamp", "sample_checked_timestamp"}:
            return _format_timestamp(gold.get(field))
        if field == "resolution_status":
            status = str(gold.get("resolution_status") or "")
            if status == "exact":
                return ["exact match", "exact logged match", "exact logged reading"]
            if status == "unique_nearest":
                return ["unique nearest match", "nearest match", "nearest available one"]
            if status == "ambiguous_nearest":
                return ["too ambiguous", "ambiguous", "public time alone", "more precise timestamp"]
            return [status] if status else []
        if field == "stability_status":
            status = str(gold.get("stability_status") or "")
            if status == "same_winner":
                return ["stayed the same", "same winner", "same top-ranked stream", "same top stream"]
            if status == "winner_changed":
                return ["changed", "winner changed", "top-ranked stream changed", "different winner"]
            return [status] if status else []
        if field == "trend_status":
            status = str(gold.get("trend_status") or "")
            if status == "quality_improved":
                return ["improved", "got better", "quality improved", "more reliable", "better"]
            if status == "quality_worsened":
                return ["worsened", "got worse", "quality worsened", "less reliable", "worse"]
            if status == "quality_stable":
                return ["stayed about the same", "stayed the same", "roughly the same", "quality stayed similar", "about the same", "same"]
            return [status] if status else []
        if field == "decision":
            if gold.get("decision") == "abstain":
                return [
                    "i would abstain",
                    "would abstain",
                    "cannot answer",
                    "can't answer",
                    "not reliable enough",
                    "insufficient",
                    "not enough data",
                ]
            return [
                "i would answer",
                "would answer",
                "healthy enough to answer",
                "reliable enough to answer",
                "looks healthy enough to answer",
            ]
        if field == "preferred_reference":
            return _preferred_reference_variants(gold)
        if field == "reason":
            reason = str(gold.get("reason") or "")
            if reason == "low_coverage":
                return ["coverage", "low coverage", "insufficient coverage", "not enough data"]
            if reason == "long_gap":
                return ["gap", "longest gap", "large gap", "big gap", "missing data"]
            if reason == "healthy":
                return ["healthy", "reliable enough", "looks healthy", "quality looks healthy"]
            if reason == "healthy_quality":
                return [
                    "reliable enough to report",
                    "result is reliable enough to report",
                    "quality is healthy enough to report",
                    "quality is healthy",
                    "looks healthy",
                ]
            if reason == "marginal_quality":
                return ["marginal", "not reliable enough", "coverage", "gap ratio", "quality"]
            if reason == "exact_timestamp":
                return ["exact timestamp", "timestamp is exact", "exact logged reading", "exact reading"]
            if reason == "nearest_but_acceptable":
                return [
                    "nearest available timestamp is close enough",
                    "nearest available timestamp",
                    "close enough",
                    "nearest is acceptable",
                ]
            if reason == "timestamp_too_imprecise":
                return [
                    "current time is too imprecise",
                    "time is too imprecise",
                    "need a more precise timestamp",
                    "more precise timestamp",
                ]
            return [normalize_text(reason)] if reason else []
        if field == "fallback_reason":
            return ["nearest", "closest", "no exact", "nearest available"]
        if field == "commitment_action":
            action = str(gold.get("commitment_action") or "")
            if action == "answer":
                return [
                    "report it as-is",
                    "report it as is",
                    "would report it",
                    "report it",
                    "report the reading as-is",
                    "report the reading as is",
                    "report the value as-is",
                    "report the value as is",
                ]
            if action == "abstain":
                return [
                    "abstain",
                    "would abstain",
                    "abstain rather than report",
                    "cannot report",
                    "not report it",
                    "not report it as-is",
                    "not report it as is",
                    "would not report it as-is",
                    "would not report it as is",
                    "do not report it as-is",
                    "do not report it as is",
                    "would not report the reading as-is",
                    "would not report the reading as is",
                    "would not report the value as-is",
                    "would not report the value as is",
                ]
            if action == "re_clarify":
                return [
                    "ask for a more precise timestamp",
                    "ask for a narrower time range",
                    "ask for more detail",
                    "ask for more time detail",
                    "need more time detail",
                    "need a more precise timestamp",
                    "need a narrower time range",
                    "before reporting it",
                ]
        if field == "clarification_request":
            request = str(gold.get("clarification_request") or "")
            if request == "more_precise_timestamp":
                return ["more precise timestamp", "precise timestamp", "more exact time"]
            if request == "narrower_time_range":
                return ["narrower time range", "narrower window", "more precise time range"]
        return []

    required_fields = example.get("task_accomplish_verifier", {}).get("final_answer_checks", {}).get("required_fields", [])
    if include_core_fields:
        if family == "point_disambiguation":
            require_match("stream_id", [normalize_text(gold["stream_id"])], field_key="stream_id")
        elif family in {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}:
            require_match("mean_value", _format_number(gold["mean_value"]), field_key="mean_value")
        elif family == "window_pairwise_compare":
            require_match(
                "winning_stream",
                descriptor_candidates(runtime, gold["winning_stream_id"]),
                field_key="winning_stream_id",
            )
            require_match("comparative_cue", ["higher", "larger", "greater", "higher average", "higher mean"])
            require_match("left_mean_value", _format_number(gold["left_mean_value"]), field_key="left_mean_value")
            require_match("right_mean_value", _format_number(gold["right_mean_value"]), field_key="right_mean_value")
        elif family == "window_rank":
            require_match("winning_stream", descriptor_candidates(runtime, gold["stream_id"]), field_key="stream_id")
            require_match("mean_value", _format_number(gold["mean_value"]), field_key="mean_value")
        elif family == "rank_stability_assessment":
            require_match("stability_status", required_field_variants("stability_status"), field_key="stability_status")
        elif family == "timestamp_value_lookup":
            require_match("observed_timestamp", _format_timestamp(gold["observed_timestamp"]), field_key="observed_timestamp")
            require_match("value", _format_number(gold["value"]), field_key="value")
        elif family == "timestamp_nearest_lookup":
            require_match("nearest_cue", ["nearest", "closest", "no exact"], field_key="fallback_reason")
            require_match("observed_timestamp", _format_timestamp(gold["observed_timestamp"]), field_key="observed_timestamp")
            require_match("value", _format_number(gold["value"]), field_key="value")
        elif family == "quality_gate":
            if gold["decision"] == "abstain":
                require_match(
                    "decision",
                    [
                        "i would abstain",
                        "would abstain",
                        "cannot answer",
                        "can't answer",
                        "not reliable enough",
                        "insufficient",
                        "not enough data",
                    ],
                    field_key="decision",
                )
            else:
                require_match(
                    "decision",
                    [
                        "i would answer",
                        "would answer",
                        "healthy enough to answer",
                        "reliable enough to answer",
                        "looks healthy enough to answer",
                    ],
                    field_key="decision",
                )
        elif family == "quality_trend_assessment":
            require_match("trend_status", required_field_variants("trend_status"), field_key="trend_status")
        elif family == "quality_preference":
            if "preferred_reference" in required_fields or not required_fields:
                require_match("preferred_reference", required_field_variants("preferred_reference"), field_key="preferred_reference")
            if "decision" in required_fields:
                if gold["decision"] == "abstain":
                    require_match(
                        "decision",
                        [
                            "i would abstain",
                            "would abstain",
                            "not reliable enough",
                            "insufficient",
                            "cannot answer",
                        ],
                        field_key="decision",
                    )
                else:
                    require_match(
                        "decision",
                        [
                            "i would answer",
                            "would answer",
                            "healthy enough",
                            "reliable enough",
                        ],
                        field_key="decision",
                    )
        elif family == "timestamp_preference":
            require_match("preferred_reference", required_field_variants("preferred_reference"), field_key="preferred_reference")
            if bool(gold.get("exact_match_found", False)):
                require_match("exact_cue", ["exact logged", "exact match", "exact reading"])
            else:
                require_match("nearest_cue", ["nearest", "closest", "no exact"], field_key="fallback_reason")
            require_match("observed_timestamp", _format_timestamp(gold["observed_timestamp"]), field_key="observed_timestamp")
            require_match("value", _format_number(gold["value"]), field_key="value")
        elif family == "timestamp_resolution_context":
            require_match("resolution_status", required_field_variants("resolution_status"), field_key="resolution_status")
            require_match("observed_timestamp", _format_timestamp(gold["observed_timestamp"]), field_key="observed_timestamp")
            require_match("offset_seconds", _format_number(gold["offset_seconds"]), field_key="offset_seconds")
        elif family in {"reporting_commitment", "timestamp_reportability_decision"}:
            require_match("commitment_action", required_field_variants("commitment_action"), field_key="commitment_action")
        else:
            require_match("fallback", [normalize_text(str(gold))])

    if include_core_fields:
        for field in required_fields:
            if field in REPORTING_FIELDS or field in matched_fields:
                continue
            if family in {"reporting_commitment", "timestamp_reportability_decision"} and field == "reason":
                # The commitment prompt asks for the action (report / abstain / re-clarify),
                # not for an explicit verbalization of the latent commitment rationale.
                # Keep `reason` in gold for deterministic contract binding, but do not
                # penalize answers that choose the correct action without spelling it out.
                continue
            variants = required_field_variants(field)
            if variants:
                require_match(field, variants, field_key=field)
    if include_reporting_fields:
        if "sample_exact_match_found" in required_fields:
            require_match("sample_exact_match_found", ["exact match", "exact logged", "exact sample", "matched exactly"])
        if "sample_checked_timestamp" in required_fields and gold.get("sample_checked_timestamp") is not None:
            require_match("sample_checked_timestamp", _format_timestamp(gold["sample_checked_timestamp"]))

    score = 1.0 if checks == 0 else passed / checks
    return score == 1.0, score, issues
