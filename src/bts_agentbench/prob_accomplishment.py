from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
import torch
from torch import nn

from .operator_answer import descriptor_candidates, normalize_text
from .runtime import ToolStoreRuntime


FAMILY_ORDER = [
    "point_disambiguation",
    "day_mean_lookup",
    "relative_24h_mean_lookup",
    "window_mean_lookup",
    "window_pairwise_compare",
    "window_rank",
    "timestamp_value_lookup",
    "timestamp_nearest_lookup",
    "quality_gate",
]

TOOL_ORDER = [
    "resolve_point",
    "list_points",
    "aggregate_window",
    "compare_window",
    "rank_window",
    "lookup_observation",
    "inspect_quality",
    "inspect_quality_window",
]

TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\+00:00|Z)?)?")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?")
STREAMISH_RE = re.compile(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+){2,}")


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    return value


def _collect_stream_ids(value: Any) -> list[str]:
    collected: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "stream_id" and isinstance(item, str):
                collected.append(item)
            else:
                collected.extend(_collect_stream_ids(item))
    elif isinstance(value, list):
        for item in value:
            collected.extend(_collect_stream_ids(item))
    return collected


def _collect_numbers(value: Any) -> list[float]:
    collected: list[float] = []
    if isinstance(value, bool):
        return collected
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except Exception:
            return collected
        if math.isfinite(numeric):
            collected.append(numeric)
        return collected
    if isinstance(value, dict):
        for item in value.values():
            collected.extend(_collect_numbers(item))
    elif isinstance(value, list):
        for item in value:
            collected.extend(_collect_numbers(item))
    return collected


def _collect_timestamps(value: Any) -> list[str]:
    collected: list[str] = []
    if isinstance(value, str):
        if TIMESTAMP_RE.search(value):
            collected.append(value)
        return collected
    if isinstance(value, dict):
        for item in value.values():
            collected.extend(_collect_timestamps(item))
    elif isinstance(value, list):
        for item in value:
            collected.extend(_collect_timestamps(item))
    return collected


def _format_number_variants(value: float) -> list[str]:
    variants = {
        f"{value:.4f}",
        f"{value:.3f}",
        f"{value:.2f}",
        f"{value:.1f}",
        str(round(value, 4)),
    }
    out: set[str] = set()
    for variant in variants:
        trimmed = variant.rstrip("0").rstrip(".")
        out.add(normalize_text(variant))
        if trimmed:
            out.add(normalize_text(trimmed))
    return sorted(out)


def _format_timestamp_variants(value: str) -> list[str]:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return []
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    variants = {
        ts.isoformat(),
        ts.strftime("%Y-%m-%d %H:%M:%S%z").replace("+0000", "+00:00"),
        ts.strftime("%Y-%m-%d %H:%M:%S"),
        ts.strftime("%Y-%m-%d %H:%M"),
        ts.strftime("%Y-%m-%d"),
        ts.strftime("%H:%M:%S"),
        ts.strftime("%H:%M"),
        ts.strftime("%b %d, %Y %H:%M"),
        ts.strftime("%B %d, %Y %H:%M"),
    }
    return sorted({normalize_text(item) for item in variants if item})


def _overlap_ratio(text: str, variants: list[str]) -> float:
    if not variants:
        return 0.0
    matched = sum(1 for variant in variants if variant and variant in text)
    return matched / len(variants)


def _binary(flag: bool) -> float:
    return 1.0 if flag else 0.0


def _label_target(verification: dict[str, Any]) -> float:
    label = verification["label"]
    if label == "accomplished":
        return 1.0
    if label == "partially_accomplished":
        return 0.0
    return 0.0


def _utility_target(verification: dict[str, Any]) -> float:
    process = float(verification.get("process_score", 0.0))
    final = float(verification.get("final_score", 0.0))
    evidence = float(verification.get("evidence_score", 0.0))
    utility = 0.6 * process + 0.3 * final + 0.1 * evidence
    return max(0.0, min(1.0, utility))


def _gather_textual_support(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, float]:
    answer = row.get("final_answer")
    answer_text = normalize_text(answer) if isinstance(answer, str) else ""
    executed_calls = row.get("executed_calls", [])
    outputs = [call.get("output", {}) for call in executed_calls]
    last_output = outputs[-1] if outputs else {}

    last_numbers = []
    for number in _collect_numbers(last_output):
        last_numbers.extend(_format_number_variants(number))

    last_timestamps = []
    for ts in _collect_timestamps(last_output):
        last_timestamps.extend(_format_timestamp_variants(ts))

    last_stream_ids = sorted({normalize_text(item) for item in _collect_stream_ids(last_output)})
    all_stream_ids = sorted({normalize_text(item) for item in _collect_stream_ids(outputs)})

    descriptor_terms: set[str] = set()
    for stream_id in _collect_stream_ids(outputs):
        for item in descriptor_candidates(runtime, stream_id):
            descriptor_terms.add(item)

    return {
        "answer_len_tokens": float(len(answer_text.split())),
        "answer_has_digits": _binary(bool(NUMBER_RE.search(answer_text))),
        "answer_has_timestamp_pattern": _binary(bool(TIMESTAMP_RE.search(answer_text))),
        "answer_has_streamish": _binary(bool(STREAMISH_RE.search(answer_text))),
        "answer_mentions_average": _binary(any(word in answer_text for word in ["average", "mean"])),
        "answer_mentions_nearest": _binary(any(word in answer_text for word in ["nearest", "closest"])),
        "answer_mentions_compare": _binary(any(word in answer_text for word in ["higher", "greater", "larger"])),
        "answer_mentions_top": _binary(any(word in answer_text for word in ["top", "highest", "rank"])),
        "answer_mentions_abstain": _binary(any(word in answer_text for word in ["abstain", "not reliable", "not healthy"])),
        "last_number_overlap": _overlap_ratio(answer_text, sorted(set(last_numbers))),
        "last_timestamp_overlap": _overlap_ratio(answer_text, sorted(set(last_timestamps))),
        "last_stream_overlap": _overlap_ratio(answer_text, last_stream_ids),
        "all_stream_overlap": _overlap_ratio(answer_text, all_stream_ids),
        "descriptor_overlap": _overlap_ratio(answer_text, sorted(descriptor_terms)),
    }


def rollout_features(row: dict[str, Any], runtime: ToolStoreRuntime) -> dict[str, float]:
    executed_calls = row.get("executed_calls", [])
    tool_names = [call.get("tool_name", "") for call in executed_calls]
    parse_error = row.get("parse_error") or ""
    features: dict[str, float] = {
        "bias": 1.0,
        "num_steps": float(len(executed_calls)),
        "has_final_answer": _binary(row.get("final_answer") is not None),
        "final_answer_is_text": _binary(isinstance(row.get("final_answer"), str)),
        "has_parse_error": _binary(bool(parse_error)),
        "parse_error_json": _binary("json_parse_error" in parse_error),
        "parse_error_tool_exec": _binary("tool_execution_error" in parse_error),
        "parse_error_missing": _binary("missing_tool_call_or_final_answer" in parse_error),
        "any_zero_match": 0.0,
        "any_positive_aggregate_count": 0.0,
    }

    for family in FAMILY_ORDER:
        features[f"family::{family}"] = _binary(row.get("task_family") == family)

    for tool in TOOL_ORDER:
        count = sum(1 for name in tool_names if name == tool)
        features[f"tool_count::{tool}"] = float(count)
        features[f"last_tool::{tool}"] = _binary(bool(tool_names) and tool_names[-1] == tool)

    for call in executed_calls:
        output = call.get("output", {})
        if output.get("match_count") == 0:
            features["any_zero_match"] = 1.0
        if isinstance(output.get("count"), (int, float)) and float(output["count"]) > 0.0:
            features["any_positive_aggregate_count"] = 1.0

    features.update(_gather_textual_support(row, runtime))
    return features


@dataclass
class ProbFeatureBatch:
    feature_names: list[str]
    x: torch.Tensor
    y: torch.Tensor


def build_feature_batch(
    rows: list[dict[str, Any]],
    runtimes: dict[str, ToolStoreRuntime],
    feature_names: list[str] | None = None,
    target_mode: str = "binary",
) -> ProbFeatureBatch:
    feature_dicts = [rollout_features(row, runtimes[row["site_id"]]) for row in rows]
    if feature_names is None:
        ordered = sorted({name for item in feature_dicts for name in item})
    else:
        ordered = list(feature_names)
    x_rows = []
    for item in feature_dicts:
        x_rows.append([float(item.get(name, 0.0)) for name in ordered])
    if target_mode == "binary":
        y = [_label_target(row["verification"]) for row in rows]
    elif target_mode == "utility":
        y = [_utility_target(row["verification"]) for row in rows]
    else:
        raise ValueError(f"Unsupported target_mode: {target_mode}")
    return ProbFeatureBatch(
        feature_names=ordered,
        x=torch.tensor(x_rows, dtype=torch.float32),
        y=torch.tensor(y, dtype=torch.float32),
    )


class ProbabilisticAccomplishmentModel(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(dim, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.net(x)).squeeze(-1)


@dataclass
class ProbModelBundle:
    model: ProbabilisticAccomplishmentModel
    feature_names: list[str]
    mean: torch.Tensor
    std: torch.Tensor
    target_mode: str = "binary"

    def predict_proba(self, rows: list[dict[str, Any]], runtimes: dict[str, ToolStoreRuntime]) -> torch.Tensor:
        batch = build_feature_batch(rows, runtimes, self.feature_names, target_mode=self.target_mode)
        x = (batch.x - self.mean) / self.std
        with torch.inference_mode():
            return self.model(x).cpu()


def train_prob_model(
    rows: list[dict[str, Any]],
    runtimes: dict[str, ToolStoreRuntime],
    epochs: int = 1,
    batch_size: int = 64,
    learning_rate: float = 1e-3,
    target_mode: str = "binary",
) -> tuple[ProbModelBundle, dict[str, float]]:
    batch = build_feature_batch(rows, runtimes, target_mode=target_mode)
    mean = batch.x.mean(dim=0)
    std = batch.x.std(dim=0)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    x = (batch.x - mean) / std
    y = batch.y

    model = ProbabilisticAccomplishmentModel(x.shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loss_fn = nn.BCELoss()

    metrics: dict[str, float] = {}
    for epoch in range(epochs):
        permutation = torch.randperm(x.shape[0])
        total_loss = 0.0
        for start in range(0, x.shape[0], batch_size):
            index = permutation[start : start + batch_size]
            xb = x[index]
            yb = y[index]
            optimizer.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * len(index)
        metrics[f"epoch_{epoch+1}_loss"] = total_loss / x.shape[0]

    with torch.inference_mode():
        pred = model(x)
        if target_mode == "binary":
            metrics["train_accuracy"] = float(((pred >= 0.5) == (y >= 0.5)).float().mean().item())
            metrics["train_positive_rate"] = float(y.mean().item())
        else:
            metrics["train_mae"] = float(torch.abs(pred - y).mean().item())
            metrics["train_target_mean"] = float(y.mean().item())
        metrics["train_pred_mean"] = float(pred.mean().item())
    model.eval()

    return ProbModelBundle(model=model, feature_names=batch.feature_names, mean=mean, std=std, target_mode=target_mode), metrics


def save_bundle(bundle: ProbModelBundle, path: str) -> None:
    payload = {
        "state_dict": bundle.model.state_dict(),
        "feature_names": bundle.feature_names,
        "mean": bundle.mean,
        "std": bundle.std,
        "target_mode": bundle.target_mode,
    }
    torch.save(payload, path)


def load_bundle(path: str) -> ProbModelBundle:
    payload = torch.load(path, map_location="cpu")
    model = ProbabilisticAccomplishmentModel(len(payload["feature_names"]))
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return ProbModelBundle(
        model=model,
        feature_names=list(payload["feature_names"]),
        mean=payload["mean"],
        std=payload["std"],
        target_mode=str(payload.get("target_mode", "binary")),
    )


def rollout_utility(row: dict[str, Any]) -> tuple[int, float, float, float]:
    verification = row["verification"]
    label_rank = {
        "not_accomplished": 0,
        "partially_accomplished": 1,
        "accomplished": 2,
    }.get(verification["label"], 0)
    return (
        label_rank,
        float(verification.get("final_score", 0.0)),
        float(verification.get("process_score", 0.0)),
        float(verification.get("evidence_score", 0.0)),
    )


def summarize_rollouts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    label_counts: dict[str, int] = {}
    for row in rows:
        label = row["verification"]["label"]
        label_counts[label] = label_counts.get(label, 0) + 1
    count = len(rows)
    return {
        "scenario_count": count,
        "label_counts": label_counts,
        "mean_process_score": round(sum(float(row["verification"]["process_score"]) for row in rows) / count, 4) if count else 0.0,
        "mean_final_score": round(sum(float(row["verification"]["final_score"]) for row in rows) / count, 4) if count else 0.0,
        "mean_evidence_score": round(sum(float(row["verification"]["evidence_score"]) for row in rows) / count, 4) if count else 0.0,
    }


def compact_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": row["scenario_id"],
        "task_family": row["task_family"],
        "site_id": row["site_id"],
        "query": row["query"],
        "verification": row["verification"],
        "parse_error": row.get("parse_error"),
        "executed_calls": row.get("executed_calls", []),
        "final_answer": row.get("final_answer"),
        "evidence": row.get("evidence"),
        "raw_model_responses": row.get("raw_model_responses", []),
    }


def load_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
