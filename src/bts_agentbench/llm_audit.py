from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


LLM_AUDIT_SCHEMA_VERSION = "llm_audit_v1"


RUBRIC_DIMENSIONS = [
    "query_naturalness",
    "tool_path_plausibility",
    "grounding_sufficiency",
    "gold_answer_validity",
    "fallback_or_abstention_appropriateness",
]


SYSTEM_PROMPT = """You are a strict benchmark-construction auditor for building-operations agent scenarios.

Your job is to audit scenario quality, not to solve the scenario.
Evaluate whether the scenario is well-formed, semantically plausible, grounded in the provided metadata, and suitable for exact programmatic evaluation.

You must return only valid JSON.
Do not include markdown, prose outside JSON, or code fences.
"""


@dataclass
class JudgeConfig:
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.0
    max_tokens: int = 1200
    timeout_seconds: int = 120


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def scenario_rows(benchmark_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in ["train", "dev", "test"]:
        rows.extend(load_jsonl(benchmark_dir / f"{split}.jsonl"))
    return rows


def compact_scenario_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": row["scenario_id"],
        "split": row["split"],
        "task_family": row["task_family"],
        "site_id": row["site_id"],
        "query": row["query"],
        "canonical_tool_calls": row["canonical_tool_calls"],
        "acceptable_tool_call_sets": row.get("acceptable_tool_call_sets"),
        "gold_final_answer": row["gold_final_answer"],
        "task_accomplish_verifier": row["task_accomplish_verifier"],
        "difficulty_proxy": row["difficulty_proxy"],
        "metadata": row["metadata"],
        "evidence": row["evidence"],
    }


def build_user_prompt(row: dict[str, Any]) -> str:
    payload = {
        "task": "Audit the following benchmark scenario for dataset quality.",
        "decision_labels": ["keep", "revise", "drop"],
        "rubric_dimensions": RUBRIC_DIMENSIONS,
        "instructions": {
            "query_naturalness": "Is the query natural, readable, and plausible for a building-operations user?",
            "tool_path_plausibility": "Do the canonical tool calls form a plausible minimal path to solve the scenario?",
            "grounding_sufficiency": "Is the scenario sufficiently grounded by site, equipment, location, point class, evidence, and verifier fields?",
            "gold_answer_validity": "Does the gold answer appear internally consistent with the scenario and suitable for exact evaluation?",
            "fallback_or_abstention_appropriateness": "If the scenario uses fallback, nearest-time retrieval, or abstention, is that behavior justified and clearly specified?",
        },
        "required_json_schema": {
            "schema_version": LLM_AUDIT_SCHEMA_VERSION,
            "scenario_id": "string",
            "decision": "keep | revise | drop",
            "overall_score": "integer 1-5",
            "dimension_scores": {
                name: "integer 1-5" for name in RUBRIC_DIMENSIONS
            },
            "issues": [
                {
                    "category": "string",
                    "severity": "low | medium | high",
                    "message": "string",
                }
            ],
            "revision_suggestion": "string or null",
            "rationale": "short string",
        },
        "scenario": compact_scenario_view(row),
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("Empty judge response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def validate_judgment(judgment: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    judgment = dict(judgment)
    judgment.setdefault("schema_version", LLM_AUDIT_SCHEMA_VERSION)
    judgment.setdefault("scenario_id", scenario_id)
    judgment.setdefault("issues", [])
    judgment.setdefault("revision_suggestion", None)
    judgment.setdefault("rationale", "")

    if judgment["scenario_id"] != scenario_id:
        raise ValueError(f"Judge scenario_id mismatch: {judgment['scenario_id']} != {scenario_id}")

    if judgment.get("decision") not in {"keep", "revise", "drop"}:
        raise ValueError("Invalid judge decision")

    if not isinstance(judgment.get("overall_score"), int) or not (1 <= judgment["overall_score"] <= 5):
        raise ValueError("Invalid overall_score")

    dim_scores = judgment.get("dimension_scores")
    if not isinstance(dim_scores, dict):
        raise ValueError("Missing dimension_scores")
    for name in RUBRIC_DIMENSIONS:
        value = dim_scores.get(name)
        if not isinstance(value, int) or not (1 <= value <= 5):
            raise ValueError(f"Invalid dimension score for {name}")

    normalized_issues = []
    for issue in judgment["issues"]:
        if not isinstance(issue, dict):
            continue
        category = str(issue.get("category", "unspecified"))
        severity = str(issue.get("severity", "medium"))
        message = str(issue.get("message", "")).strip()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        if not message:
            continue
        normalized_issues.append(
            {"category": category, "severity": severity, "message": message}
        )
    judgment["issues"] = normalized_issues
    judgment["rationale"] = str(judgment.get("rationale", "")).strip()
    if not judgment["rationale"]:
        raise ValueError("Missing rationale")
    if judgment["revision_suggestion"] is not None:
        judgment["revision_suggestion"] = str(judgment["revision_suggestion"]).strip() or None
    return judgment


def judge_scenario(config: JudgeConfig, row: dict[str, Any]) -> tuple[dict[str, Any], str]:
    user_prompt = build_user_prompt(row)
    response = requests.post(
        f"{config.base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    judgment = validate_judgment(parse_json_object(content), row["scenario_id"])
    return judgment, content


def config_from_env(model: str, base_url: str, api_key_env: str) -> JudgeConfig:
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"Missing API key in environment variable {api_key_env}")
    return JudgeConfig(model=model, base_url=base_url, api_key=api_key)


def summarize_judgments(judgments: list[dict[str, Any]]) -> dict[str, Any]:
    decision_counts = {"keep": 0, "revise": 0, "drop": 0}
    dimension_totals = {name: 0 for name in RUBRIC_DIMENSIONS}
    issue_counts: dict[str, int] = {}

    for row in judgments:
        decision_counts[row["decision"]] += 1
        for name in RUBRIC_DIMENSIONS:
            dimension_totals[name] += row["dimension_scores"][name]
        for issue in row["issues"]:
            key = f"{issue['category']}::{issue['severity']}"
            issue_counts[key] = issue_counts.get(key, 0) + 1

    n = max(1, len(judgments))
    return {
        "schema_version": LLM_AUDIT_SCHEMA_VERSION,
        "scenario_count": len(judgments),
        "decision_counts": decision_counts,
        "mean_overall_score": sum(row["overall_score"] for row in judgments) / n,
        "mean_dimension_scores": {name: dimension_totals[name] / n for name in RUBRIC_DIMENSIONS},
        "issue_counts": dict(sorted(issue_counts.items())),
    }
