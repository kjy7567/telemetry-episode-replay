from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .operator_answer import check_operator_answer
from .runtime import ExecutedCall, ToolStoreRuntime

REPORTING_FIELDS = {"sample_exact_match_found", "sample_checked_timestamp"}
RELAXED_AGGREGATE_FAMILIES = {"day_mean_lookup", "relative_24h_mean_lookup", "window_mean_lookup"}
TEMPORAL_TOOL_FIELDS = {
    "aggregate_window": ("window_start", "window_end", "period"),
    "compare_window": ("window_start", "window_end", "period"),
    "rank_window": ("window_start", "window_end", "period"),
    "inspect_quality_window": ("window_start", "window_end", "period"),
    "lookup_observation": ("timestamp", "mode"),
}


@dataclass
class VerificationResult:
    label: str
    strict_label: str
    process_ok: bool
    process_score: float
    final_ok: bool
    final_score: float
    evidence_ok: bool
    evidence_score: float
    core_ok: bool
    core_score: float
    reporting_ok: bool
    reporting_score: float
    grounding_ok: bool
    grounding_score: float
    temporal_ok: bool
    temporal_score: float
    phase_ok: bool
    phase_score: float
    task_ok: bool
    task_score: float
    process_issues: list[str]
    final_issues: list[str]
    evidence_issues: list[str]
    core_issues: list[str]
    reporting_issues: list[str]
    grounding_issues: list[str]
    temporal_issues: list[str]
    phase_issues: list[str]
    issues: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "strict_label": self.strict_label,
            "process_ok": self.process_ok,
            "process_score": self.process_score,
            "final_ok": self.final_ok,
            "final_score": self.final_score,
            "evidence_ok": self.evidence_ok,
            "evidence_score": self.evidence_score,
            "core_ok": self.core_ok,
            "core_score": self.core_score,
            "reporting_ok": self.reporting_ok,
            "reporting_score": self.reporting_score,
            "grounding_ok": self.grounding_ok,
            "grounding_score": self.grounding_score,
            "temporal_ok": self.temporal_ok,
            "temporal_score": self.temporal_score,
            "phase_ok": self.phase_ok,
            "phase_score": self.phase_score,
            "task_ok": self.task_ok,
            "task_score": self.task_score,
            "process_issues": self.process_issues,
            "final_issues": self.final_issues,
            "evidence_issues": self.evidence_issues,
            "core_issues": self.core_issues,
            "reporting_issues": self.reporting_issues,
            "grounding_issues": self.grounding_issues,
            "temporal_issues": self.temporal_issues,
            "phase_issues": self.phase_issues,
            "issues": self.issues,
        }


def _normalize_call(call: dict[str, Any]) -> tuple[str, tuple[tuple[str, Any], ...]]:
    items = tuple(sorted((key, _freeze(value)) for key, value in call["arguments"].items()))
    return call["tool_name"], items


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value


def _safe_overlap(required: list[str], predicted: list[str]) -> float:
    if not required:
        return 1.0
    predicted_set = set(predicted)
    matched = sum(1 for item in required if item in predicted_set)
    return matched / len(required)


def _collect_stream_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        collected: list[str] = []
        if "stream_id" in value and isinstance(value["stream_id"], str):
            collected.append(value["stream_id"])
        for nested in value.values():
            collected.extend(_collect_stream_ids(nested))
        return collected
    if isinstance(value, list):
        collected: list[str] = []
        for item in value:
            collected.extend(_collect_stream_ids(item))
        return collected
    return []


def _numeric_equal(left: Any, right: Any, tol: float) -> bool:
    try:
        return abs(float(left) - float(right)) <= tol
    except Exception:
        return False


def _timestamp_equal(left: Any, right: Any) -> bool:
    try:
        left_ts = pd.Timestamp(left)
        right_ts = pd.Timestamp(right)
    except Exception:
        return False
    if left_ts.tzinfo is None:
        left_ts = left_ts.tz_localize("UTC")
    else:
        left_ts = left_ts.tz_convert("UTC")
    if right_ts.tzinfo is None:
        right_ts = right_ts.tz_localize("UTC")
    else:
        right_ts = right_ts.tz_convert("UTC")
    return left_ts == right_ts


def _as_utc_timestamp(value: Any) -> pd.Timestamp | None:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _score_label(ok: bool, score: float) -> str:
    if ok:
        return "accomplished"
    if score > 0.0:
        return "partially_accomplished"
    return "not_accomplished"


def _mean_score(*scores: float) -> float:
    active = [float(score) for score in scores]
    return sum(active) / len(active) if active else 0.0


def _materialize_gold_variants(example: dict, runtime: ToolStoreRuntime) -> list[list[dict[str, Any]]]:
    variants: list[list[dict[str, Any]]] = []
    for variant in example["acceptable_tool_call_sets"]:
        materialized = runtime.execute_call_sequence(variant)
        variants.append([call.as_dict() if isinstance(call, ExecutedCall) else call for call in materialized])
    return variants


def _core_required_fields(example: dict[str, Any]) -> list[str]:
    required = example["task_accomplish_verifier"]["final_answer_checks"].get("required_fields", [])
    return [field for field in required if field not in REPORTING_FIELDS]


def _reporting_required_fields(example: dict[str, Any]) -> list[str]:
    required = example["task_accomplish_verifier"]["final_answer_checks"].get("required_fields", [])
    return [field for field in required if field in REPORTING_FIELDS]


def _check_structured_final_answer(
    example: dict,
    predicted: dict[str, Any],
    *,
    required_fields_override: list[str] | None = None,
) -> tuple[bool, float, list[str]]:
    verifier = example["task_accomplish_verifier"]["final_answer_checks"]
    gold = example["gold_final_answer"]
    issues: list[str] = []
    required_fields = required_fields_override if required_fields_override is not None else verifier.get("required_fields", [])
    numeric_tolerance = verifier.get("numeric_tolerance", {})
    categorical_exact_match = verifier.get("categorical_exact_match", [])
    checks = 0
    passed = 0

    for field in required_fields:
        checks += 1
        if field not in predicted:
            issues.append(f"missing_final_field:{field}")
            continue
        if field in numeric_tolerance:
            if _numeric_equal(predicted[field], gold[field], float(numeric_tolerance[field])):
                passed += 1
            else:
                issues.append(f"numeric_mismatch:{field}")
            continue
        if field in categorical_exact_match:
            if predicted[field] == gold[field]:
                passed += 1
            else:
                issues.append(f"categorical_mismatch:{field}")
            continue
        if predicted[field] == gold[field]:
            passed += 1
        else:
            issues.append(f"value_mismatch:{field}")

    score = 1.0 if checks == 0 else passed / checks
    return score == 1.0, score, issues


def _allowed_aggregate_periods(example: dict[str, Any]) -> set[str | None]:
    periods: set[str | None] = set()
    for variant in example.get("acceptable_tool_call_sets", []):
        for call in variant:
            if call.get("tool_name") != "aggregate_window":
                continue
            args = call.get("arguments", {})
            if isinstance(args, dict):
                periods.add(args.get("period"))
    if not periods:
        gold_period = example.get("metadata", {}).get("period")
        if example.get("task_family") == "relative_24h_mean_lookup":
            periods.update({"custom", "day"})
        elif gold_period is not None:
            periods.update({gold_period, "custom"})
    return periods


def _gold_stream_id(example: dict[str, Any]) -> str | None:
    gold = example.get("gold_final_answer", {})
    if isinstance(gold, dict) and isinstance(gold.get("stream_id"), str):
        return gold.get("stream_id")
    evidence = example.get("evidence", {})
    if isinstance(evidence, dict):
        stream_ids = evidence.get("stream_ids", [])
        if stream_ids:
            return str(stream_ids[0])
    return None


def _nearest_observation_timestamp(
    runtime: ToolStoreRuntime,
    stream_id: str,
    timestamp: pd.Timestamp,
) -> pd.Timestamp | None:
    out = runtime.lookup_observation(
        {"stream_id": stream_id, "timestamp": timestamp.isoformat(), "mode": "nearest"}
    )
    observed = out.get("observed_timestamp")
    return _as_utc_timestamp(observed)


def _aggregate_relaxation_candidates(
    example: dict[str, Any],
    runtime: ToolStoreRuntime,
) -> list[dict[str, Any]]:
    if example.get("task_family") not in RELAXED_AGGREGATE_FAMILIES:
        return []
    gold = example.get("gold_final_answer", {})
    stream_id = _gold_stream_id(example)
    gold_start = _as_utc_timestamp(gold.get("window_start"))
    gold_end = _as_utc_timestamp(gold.get("window_end"))
    if stream_id is None or gold_start is None or gold_end is None:
        return []

    nominal_duration = gold_end - gold_start
    nearest_start = _nearest_observation_timestamp(runtime, stream_id, gold_start)
    nearest_end = _nearest_observation_timestamp(runtime, stream_id, gold_end)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[pd.Timestamp, pd.Timestamp]] = set()

    def add(label: str, start: pd.Timestamp | None, end: pd.Timestamp | None) -> None:
        if start is None or end is None or end <= start:
            return
        key = (start, end)
        if key in seen:
            return
        seen.add(key)
        candidates.append({"label": label, "start": start, "end": end})

    add("exact", gold_start, gold_end)
    add("exact_start_nearest_end", gold_start, nearest_end)
    if nearest_start is not None:
        add("nearest_start_fixed_duration", nearest_start, nearest_start + nominal_duration)
        add("nearest_start_nearest_end", nearest_start, nearest_end)
    return candidates


def _find_relaxed_aggregate_call(
    example: dict[str, Any],
    executed_calls: list[ExecutedCall],
    runtime: ToolStoreRuntime,
) -> ExecutedCall | None:
    if example.get("task_family") not in RELAXED_AGGREGATE_FAMILIES:
        return None
    stream_id = _gold_stream_id(example)
    if stream_id is None:
        return None
    allowed_periods = _allowed_aggregate_periods(example)
    candidates = _aggregate_relaxation_candidates(example, runtime)
    if not candidates:
        return None

    for call in executed_calls:
        if call.tool_name != "aggregate_window":
            continue
        pred_stream = call.output.get("stream_id") or call.arguments.get("stream_id")
        if pred_stream != stream_id:
            continue
        start = _as_utc_timestamp(call.arguments.get("window_start"))
        end = _as_utc_timestamp(call.arguments.get("window_end"))
        if start is None or end is None:
            continue
        period = call.arguments.get("period")
        if allowed_periods and period not in allowed_periods:
            continue
        if any(start == candidate["start"] and end == candidate["end"] for candidate in candidates):
            return call
    return None


def _relaxed_example_for_aggregate_mean(
    example: dict[str, Any],
    executed_calls: list[ExecutedCall],
    runtime: ToolStoreRuntime,
) -> dict[str, Any]:
    relaxed_call = _find_relaxed_aggregate_call(example, executed_calls, runtime)
    if relaxed_call is None:
        return example
    mean_value = relaxed_call.output.get("mean_value")
    if mean_value is None:
        return example

    patched = copy.deepcopy(example)
    gold = dict(patched.get("gold_final_answer", {}))
    gold["window_start"] = relaxed_call.arguments.get("window_start", gold.get("window_start"))
    gold["window_end"] = relaxed_call.arguments.get("window_end", gold.get("window_end"))
    gold["mean_value"] = mean_value
    patched["gold_final_answer"] = gold

    acceptable = list(patched.get("acceptable_tool_call_sets", []))
    if acceptable:
        variant = copy.deepcopy(acceptable[0])
        for call in variant:
            if call.get("tool_name") != "aggregate_window":
                continue
            args = dict(call.get("arguments", {}))
            args["window_start"] = relaxed_call.arguments.get("window_start", args.get("window_start"))
            args["window_end"] = relaxed_call.arguments.get("window_end", args.get("window_end"))
            args["period"] = relaxed_call.arguments.get("period", args.get("period"))
            call["arguments"] = args
            break
        acceptable.append(variant)
        patched["acceptable_tool_call_sets"] = acceptable
    return patched


def _check_final_answer(
    example: dict,
    predicted: dict[str, Any] | str | None,
    runtime: ToolStoreRuntime,
    executed_calls: list[ExecutedCall],
) -> tuple[bool, float, list[str]]:
    if isinstance(predicted, str):
        return check_operator_answer(example, predicted, runtime, executed_calls)
    if isinstance(predicted, dict):
        return _check_structured_final_answer(example, predicted)
    return False, 0.0, ["missing_answer_fact:final_answer"]


def _check_core_answer(
    example: dict,
    predicted: dict[str, Any] | str | None,
    runtime: ToolStoreRuntime,
    executed_calls: list[ExecutedCall],
) -> tuple[bool, float, list[str]]:
    if isinstance(predicted, str):
        return check_operator_answer(
            example,
            predicted,
            runtime,
            executed_calls,
            include_core_fields=True,
            include_reporting_fields=False,
        )
    if isinstance(predicted, dict):
        return _check_structured_final_answer(
            example,
            predicted,
            required_fields_override=_core_required_fields(example),
        )
    return False, 0.0, ["missing_answer_fact:final_answer"]


def _check_reporting(
    example: dict,
    predicted: dict[str, Any] | str | None,
    runtime: ToolStoreRuntime,
    executed_calls: list[ExecutedCall],
) -> tuple[bool, float, list[str]]:
    reporting_fields = _reporting_required_fields(example)
    if not reporting_fields:
        return True, 1.0, []
    if isinstance(predicted, str):
        return check_operator_answer(
            example,
            predicted,
            runtime,
            executed_calls,
            include_core_fields=False,
            include_reporting_fields=True,
        )
    if isinstance(predicted, dict):
        return _check_structured_final_answer(
            example,
            predicted,
            required_fields_override=reporting_fields,
        )
    return False, 0.0, [f"missing_reporting_field:{field}" for field in reporting_fields]


def _default_phase_examples(example: dict[str, Any]) -> list[dict[str, Any]]:
    phase_golds = example.get("phase_gold_final_answers") or [example.get("gold_final_answer", {})]
    required_fields = (
        example.get("task_accomplish_verifier", {})
        .get("final_answer_checks", {})
        .get("required_fields", [])
    )
    phase_examples: list[dict[str, Any]] = []
    for gold in phase_golds:
        phase_examples.append(
            {
                "task_family": example["task_family"],
                "gold_final_answer": copy.deepcopy(gold),
                "task_accomplish_verifier": {
                    "final_answer_checks": {
                        "required_fields": list(required_fields),
                        "numeric_tolerance": {},
                        "categorical_exact_match": [],
                    }
                },
            }
        )
    return phase_examples


def _materialize_phase_example(example: dict[str, Any], phase_example: dict[str, Any]) -> dict[str, Any]:
    materialized = copy.deepcopy(example)
    materialized["task_family"] = phase_example.get("task_family", example["task_family"])
    materialized["gold_final_answer"] = copy.deepcopy(phase_example.get("gold_final_answer", example.get("gold_final_answer", {})))
    if "task_accomplish_verifier" in phase_example:
        materialized["task_accomplish_verifier"] = copy.deepcopy(phase_example["task_accomplish_verifier"])
    return materialized


def _check_phase_answers(
    example: dict[str, Any],
    phase_answers: list[str] | None,
    runtime: ToolStoreRuntime,
) -> tuple[bool, float, list[str]]:
    phase_examples = example.get("phase_examples") or _default_phase_examples(example)
    if not phase_examples:
        return True, 1.0, []
    predicted = list(phase_answers or [])
    if len(predicted) < len(phase_examples):
        missing = [f"missing_phase_answer:{idx+1}" for idx in range(len(predicted), len(phase_examples))]
        return False, 0.0, missing

    checks = 0
    passed = 0
    issues: list[str] = []
    for idx, phase_example in enumerate(phase_examples):
        phase_text = str(predicted[idx] or "").strip()
        checks += 1
        phase_materialized = _materialize_phase_example(example, phase_example)
        ok, _, phase_issues = check_operator_answer(
            phase_materialized,
            phase_text,
            runtime,
            [],
            include_core_fields=True,
            include_reporting_fields=False,
        )
        if ok:
            passed += 1
            continue
        if phase_issues:
            issues.extend([f"phase_{idx+1}:{issue}" for issue in phase_issues])
        else:
            issues.append(f"phase_{idx+1}:invalid_answer")
    score = passed / checks if checks else 1.0
    return score == 1.0, score, issues


def _check_evidence(
    example: dict,
    predicted_evidence: dict[str, Any] | None,
    executed_calls: list[ExecutedCall],
) -> tuple[bool, float, list[str]]:
    required = example["task_accomplish_verifier"]["evidence_checks"].get("required_stream_ids", [])
    if isinstance(predicted_evidence, dict) and "stream_ids" in predicted_evidence:
        predicted = _collect_stream_ids(predicted_evidence["stream_ids"])
    else:
        predicted = _collect_stream_ids(predicted_evidence)
    if not predicted:
        for call in executed_calls:
            predicted.extend(_collect_stream_ids(call.output))
    score = _safe_overlap(required, predicted)
    if score == 1.0:
        return True, score, []
    missing = [stream_id for stream_id in required if stream_id not in set(predicted)]
    return False, score, [f"missing_evidence:{stream_id}" for stream_id in missing]


def _check_process(
    example: dict,
    executed_calls: list[ExecutedCall],
    runtime: ToolStoreRuntime,
) -> tuple[bool, float, list[str]]:
    required_tools = example["task_accomplish_verifier"]["process_checks"].get("required_tools", [])
    predicted_names = [call.tool_name for call in executed_calls]
    tool_score = _safe_overlap(required_tools, predicted_names)

    gold_variants = [[_normalize_call(call) for call in variant] for variant in _materialize_gold_variants(example, runtime)]
    predicted_norm = [_normalize_call(call.as_dict()) for call in executed_calls]

    best_match = 0.0
    for variant in gold_variants:
        if not variant:
            best_match = max(best_match, 1.0)
            continue
        matched = 0
        for idx, gold_call in enumerate(variant):
            if idx >= len(predicted_norm):
                break
            pred_tool, pred_args = predicted_norm[idx]
            gold_tool, gold_args = gold_call
            if pred_tool != gold_tool:
                continue
            gold_arg_dict = dict(gold_args)
            pred_arg_dict = dict(pred_args)
            if all(pred_arg_dict.get(key) == value for key, value in gold_arg_dict.items()):
                matched += 1
        best_match = max(best_match, matched / len(variant))

    score = max(tool_score, best_match)
    issues: list[str] = []
    if tool_score < 1.0:
        missing = [tool for tool in required_tools if tool not in set(predicted_names)]
        issues.extend([f"missing_tool:{tool}" for tool in missing])
    if best_match < 1.0:
        issues.append("call_sequence_mismatch")
    return score == 1.0, score, issues


def _temporal_requirements_for_variant(variant: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    requirements: list[tuple[str, dict[str, Any]]] = []
    for call in variant:
        tool_name = str(call.get("tool_name", ""))
        call_id = str(call.get("call_id", ""))
        if tool_name not in TEMPORAL_TOOL_FIELDS:
            continue
        if "sample" in call_id or "quality" in call_id:
            continue
        arguments = call.get("arguments", {})
        if not isinstance(arguments, dict):
            continue
        fields = TEMPORAL_TOOL_FIELDS[tool_name]
        relevant = {field: arguments.get(field) for field in fields if field in arguments}
        if relevant:
            requirements.append((tool_name, relevant))
    return requirements


def _temporal_arg_equal(key: str, predicted: Any, gold: Any) -> bool:
    if key in {"window_start", "window_end", "timestamp"}:
        return _timestamp_equal(predicted, gold)
    return predicted == gold


def _check_temporal(
    example: dict,
    executed_calls: list[ExecutedCall],
    runtime: ToolStoreRuntime,
) -> tuple[bool, float, list[str]]:
    if _find_relaxed_aggregate_call(example, executed_calls, runtime) is not None:
        return True, 1.0, []

    gold_variants = _materialize_gold_variants(example, runtime)
    predicted_calls = [call.as_dict() for call in executed_calls]
    best_score = -1.0
    best_issues: list[str] = []
    any_requirements = False

    for variant in gold_variants:
        requirements = _temporal_requirements_for_variant(variant)
        if not requirements:
            continue
        any_requirements = True
        matched = 0
        variant_issues: list[str] = []
        for tool_name, required_args in requirements:
            matched_call = False
            for pred_call in predicted_calls:
                if pred_call.get("tool_name") != tool_name:
                    continue
                pred_args = pred_call.get("arguments", {})
                if not isinstance(pred_args, dict):
                    continue
                if all(_temporal_arg_equal(key, pred_args.get(key), value) for key, value in required_args.items()):
                    matched_call = True
                    break
            if matched_call:
                matched += 1
            else:
                fields = ",".join(required_args.keys())
                variant_issues.append(f"temporal_mismatch:{tool_name}:{fields}")
        score = matched / len(requirements)
        if score > best_score:
            best_score = score
            best_issues = variant_issues
        elif score == 1.0:
            best_score = score
            best_issues = variant_issues

    if not any_requirements:
        return True, 1.0, []
    return best_score == 1.0, best_score, best_issues


def verify_prediction(
    example: dict,
    executed_calls: list[ExecutedCall],
    predicted_final_answer: dict[str, Any] | str | None,
    predicted_evidence: dict[str, Any] | None,
    runtime: ToolStoreRuntime,
    phase_answers: list[str] | None = None,
) -> VerificationResult:
    final_answer = predicted_final_answer
    effective_example = _relaxed_example_for_aggregate_mean(example, executed_calls, runtime)
    final_example = _materialize_phase_example(
        effective_example,
        effective_example.get("final_phase_example")
        or ((effective_example.get("phase_examples") or [])[-1] if effective_example.get("phase_examples") else {}),
    ) if effective_example.get("final_phase_example") or effective_example.get("phase_examples") else effective_example
    process_ok, process_score, process_issues = _check_process(effective_example, executed_calls, runtime)
    final_ok, final_score, final_issues = _check_final_answer(final_example, final_answer, runtime, executed_calls)
    core_ok, core_score, core_issues = _check_core_answer(final_example, final_answer, runtime, executed_calls)
    reporting_ok, reporting_score, reporting_issues = _check_reporting(final_example, final_answer, runtime, executed_calls)
    evidence_ok, evidence_score, evidence_issues = _check_evidence(effective_example, predicted_evidence, executed_calls)
    temporal_ok, temporal_score, temporal_issues = _check_temporal(effective_example, executed_calls, runtime)
    phase_ok, phase_score, phase_issues = _check_phase_answers(effective_example, phase_answers, runtime)
    grounding_ok = evidence_ok and reporting_ok
    grounding_score = _mean_score(evidence_score, reporting_score)
    grounding_issues = reporting_issues + evidence_issues
    task_ok = core_ok and grounding_ok and temporal_ok and phase_ok
    task_score = _mean_score(core_score, grounding_score, temporal_score, phase_score)
    issues = process_issues + temporal_issues + final_issues + evidence_issues + phase_issues

    label = _score_label(task_ok, task_score)
    strict_label = _score_label(process_ok and task_ok, _mean_score(process_score, task_score))

    return VerificationResult(
        label=label,
        strict_label=strict_label,
        process_ok=process_ok,
        process_score=round(process_score, 4),
        final_ok=final_ok,
        final_score=round(final_score, 4),
        evidence_ok=evidence_ok,
        evidence_score=round(evidence_score, 4),
        core_ok=core_ok,
        core_score=round(core_score, 4),
        reporting_ok=reporting_ok,
        reporting_score=round(reporting_score, 4),
        grounding_ok=grounding_ok,
        grounding_score=round(grounding_score, 4),
        temporal_ok=temporal_ok,
        temporal_score=round(temporal_score, 4),
        phase_ok=phase_ok,
        phase_score=round(phase_score, 4),
        task_ok=task_ok,
        task_score=round(task_score, 4),
        process_issues=process_issues,
        final_issues=final_issues,
        evidence_issues=evidence_issues,
        core_issues=core_issues,
        reporting_issues=reporting_issues,
        grounding_issues=grounding_issues,
        temporal_issues=temporal_issues,
        phase_issues=phase_issues,
        issues=issues,
    )
