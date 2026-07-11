from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd


def safe_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def round_answer(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def best_label(*values: object) -> str | None:
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none"}:
            return text
    return None


@dataclass
class Example:
    split: str
    task_family: str
    site_id: str
    query: str
    answer: dict
    program: dict
    tool_plan: list[dict]
    evidence: dict
    verifier: dict
    metadata: dict

    def as_dict(self, example_id: str) -> dict:
        payload = {
            "example_id": example_id,
            "split": self.split,
            "task_family": self.task_family,
            "site_id": self.site_id,
            "query": self.query,
            "answer": self.answer,
            "program": self.program,
            "tool_plan": self.tool_plan,
            "evidence": self.evidence,
            "verifier": self.verifier,
            "metadata": self.metadata,
        }
        return payload


def load_catalog(catalog_dir: Path) -> pd.DataFrame:
    frame = pd.read_parquet(catalog_dir / "streams.parquet")
    frame = frame[frame["stream_id"].notna()].copy()
    frame["point_class"] = frame["point_class"].fillna(frame.get("point_type"))
    frame["point_label"] = frame.apply(
        lambda row: best_label(
            row.get("point_label"),
            row.get("point_name"),
            row.get("point_class"),
            row.get("point_type"),
            row["stream_id"],
        ),
        axis=1,
    )
    frame["equipment_label"] = frame.apply(
        lambda row: best_label(row.get("equipment_label"), row.get("equipment_type")),
        axis=1,
    )
    frame["location_label"] = frame.apply(
        lambda row: best_label(row.get("location_label"), row.get("location_type")),
        axis=1,
    )
    return frame


def build_example_id(split: str, task_family: str, index: int) -> str:
    return f"{split}_{task_family}_{index:05d}"


def assign_split(site_id: str, ordinal: int) -> str:
    if site_id == "BTS_C":
        return "test"
    if ordinal % 5 == 0:
        return "dev"
    return "train"


def compact_call(call_id: str, tool: str, **kwargs: object) -> dict:
    return {
        "id": call_id,
        "tool": tool,
        "args": {key: value for key, value in kwargs.items() if value is not None},
    }


def unique_rows(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["site_id", "equipment_label", "location_label", "point_class"]
    counts = frame.groupby(keys)["stream_id"].nunique().reset_index(name="n")
    valid = counts[counts["n"] == 1][keys]
    return frame.merge(valid, on=keys, how="inner")


def gen_point_mean_lookup(frame: pd.DataFrame, limit: int) -> list[Example]:
    rows = unique_rows(frame)
    rows = rows[rows["mean_value"].notna()].sort_values(["site_id", "count"], ascending=[True, False])
    examples = []
    for ordinal, row in enumerate(rows.itertuples(index=False), start=1):
        query = (
            f"In {row.site_id}, what is the overall mean value of the {best_label(row.point_class, row.point_type, row.point_label)} "
            f"point attached to {best_label(row.equipment_label, row.equipment_type)}"
        )
        if best_label(row.location_label):
            query += f" in {row.location_label}"
        query += "?"
        examples.append(
            Example(
                split=assign_split(row.site_id, ordinal),
                task_family="point_mean_lookup",
                site_id=row.site_id,
                query=query,
                answer={
                    "stream_id": row.stream_id,
                    "mean_value": round_answer(safe_float(row.mean_value)),
                    "unit": best_label(row.unit),
                },
                program={
                    "tools": ["resolve_point", "get_stream_summary"],
                    "args": {
                        "site_id": row.site_id,
                        "point_class": best_label(row.point_class),
                        "equipment_label": best_label(row.equipment_label),
                        "location_label": best_label(row.location_label),
                        "metric": "mean",
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "resolve_point",
                        site=row.site_id,
                        pc=best_label(row.point_class),
                        eq=best_label(row.equipment_label),
                        loc=best_label(row.location_label),
                    ),
                    compact_call("c2", "stream_summary", sid="$c1.stream_id", fields=["mean_value"]),
                ],
                evidence={"stream_ids": [row.stream_id]},
                verifier={"type": "structured_exact_match", "fields": ["stream_id", "mean_value"]},
                metadata={
                    "point_class": best_label(row.point_class),
                    "equipment_type": best_label(row.equipment_type),
                    "location_type": best_label(row.location_type),
                },
            )
        )
        if len(examples) >= limit:
            break
    return examples


def gen_point_extrema_lookup(frame: pd.DataFrame, limit: int) -> list[Example]:
    rows = unique_rows(frame)
    rows = rows[rows["max_value"].notna() & rows["min_value"].notna()].sort_values(["site_id", "count"], ascending=[True, False])
    examples = []
    for ordinal, row in enumerate(rows.itertuples(index=False), start=1):
        metric = "max_value" if ordinal % 2 else "min_value"
        metric_label = "maximum" if metric == "max_value" else "minimum"
        query = (
            f"For {row.site_id}, what is the overall {metric_label} recorded by the {best_label(row.point_class, row.point_type, row.point_label)} "
            f"point on {best_label(row.equipment_label, row.equipment_type)}"
        )
        if best_label(row.location_label):
            query += f" in {row.location_label}"
        query += "?"
        value = safe_float(getattr(row, metric))
        examples.append(
            Example(
                split=assign_split(row.site_id, ordinal),
                task_family="point_extrema_lookup",
                site_id=row.site_id,
                query=query,
                answer={
                    "stream_id": row.stream_id,
                    metric: round_answer(value),
                    "unit": best_label(row.unit),
                },
                program={
                    "tools": ["resolve_point", "get_stream_summary"],
                    "args": {
                        "site_id": row.site_id,
                        "point_class": best_label(row.point_class),
                        "equipment_label": best_label(row.equipment_label),
                        "location_label": best_label(row.location_label),
                        "metric": metric.replace("_value", ""),
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "resolve_point",
                        site=row.site_id,
                        pc=best_label(row.point_class),
                        eq=best_label(row.equipment_label),
                        loc=best_label(row.location_label),
                    ),
                    compact_call("c2", "stream_summary", sid="$c1.stream_id", fields=[metric]),
                ],
                evidence={"stream_ids": [row.stream_id]},
                verifier={"type": "structured_exact_match", "fields": ["stream_id", metric]},
                metadata={
                    "point_class": best_label(row.point_class),
                    "equipment_type": best_label(row.equipment_type),
                    "location_type": best_label(row.location_type),
                },
            )
        )
        if len(examples) >= limit:
            break
    return examples


def gen_coverage_lookup(frame: pd.DataFrame, limit: int) -> list[Example]:
    rows = unique_rows(frame)
    rows = rows[rows["count"].notna() & rows["first_timestamp"].notna() & rows["last_timestamp"].notna()]
    rows = rows.sort_values(["site_id", "count"], ascending=[True, False])
    examples = []
    for ordinal, row in enumerate(rows.itertuples(index=False), start=1):
        query = (
            f"In {row.site_id}, how many observations are available for the {best_label(row.point_class, row.point_type, row.point_label)} "
            f"point on {best_label(row.equipment_label, row.equipment_type)}"
        )
        if best_label(row.location_label):
            query += f" in {row.location_label}"
        query += ", and what is the overall time coverage?"
        examples.append(
            Example(
                split=assign_split(row.site_id, ordinal),
                task_family="coverage_lookup",
                site_id=row.site_id,
                query=query,
                answer={
                    "stream_id": row.stream_id,
                    "count": int(row.count),
                    "first_timestamp": pd.Timestamp(row.first_timestamp).isoformat(),
                    "last_timestamp": pd.Timestamp(row.last_timestamp).isoformat(),
                },
                program={
                    "tools": ["resolve_point", "get_stream_summary"],
                    "args": {
                        "site_id": row.site_id,
                        "point_class": best_label(row.point_class),
                        "equipment_label": best_label(row.equipment_label),
                        "location_label": best_label(row.location_label),
                        "metrics": ["count", "first_timestamp", "last_timestamp"],
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "resolve_point",
                        site=row.site_id,
                        pc=best_label(row.point_class),
                        eq=best_label(row.equipment_label),
                        loc=best_label(row.location_label),
                    ),
                    compact_call(
                        "c2",
                        "stream_summary",
                        sid="$c1.stream_id",
                        fields=["count", "first_timestamp", "last_timestamp"],
                    ),
                ],
                evidence={"stream_ids": [row.stream_id]},
                verifier={"type": "structured_exact_match", "fields": ["stream_id", "count", "first_timestamp", "last_timestamp"]},
                metadata={
                    "point_class": best_label(row.point_class),
                    "equipment_type": best_label(row.equipment_type),
                    "location_type": best_label(row.location_type),
                },
            )
        )
        if len(examples) >= limit:
            break
    return examples


def gen_rank_by_mean(frame: pd.DataFrame, limit: int) -> list[Example]:
    examples = []
    grouped = (
        frame[
            frame["mean_value"].notna()
            & frame["equipment_type"].notna()
            & frame["point_class"].notna()
            & frame["location_type"].notna()
        ]
        .groupby(["site_id", "equipment_type", "location_type", "point_class"])
    )
    ordinal = 0
    for group_key, group in grouped:
        if len(group) < 3:
            continue
        group = group.sort_values("mean_value", ascending=False)
        top = group.iloc[0]
        runner_up = group.iloc[1]
        if abs(float(top["mean_value"]) - float(runner_up["mean_value"])) < 0.25:
            continue
        ordinal += 1
        site_id, equipment_type, location_type, point_class = group_key
        query = (
            f"In {site_id}, among {equipment_type} points of type {point_class} located in {location_type}, "
            f"which stream has the highest overall mean value?"
        )
        examples.append(
            Example(
                split=assign_split(site_id, ordinal),
                task_family="rank_by_mean",
                site_id=site_id,
                query=query,
                answer={
                    "stream_id": top["stream_id"],
                    "mean_value": round_answer(safe_float(top["mean_value"])),
                },
                program={
                    "tools": ["list_points", "rank_stream_summary"],
                    "args": {
                        "site_id": site_id,
                        "equipment_type": equipment_type,
                        "location_type": location_type,
                        "point_class": point_class,
                        "metric": "mean",
                        "order": "desc",
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "list_points",
                        site=site_id,
                        et=equipment_type,
                        lt=location_type,
                        pc=point_class,
                    ),
                    compact_call(
                        "c2",
                        "rank_summary",
                        sids="$c1.stream_ids",
                        metric="mean_value",
                        order="desc",
                        topk=1,
                    ),
                ],
                evidence={"stream_ids": group["stream_id"].tolist()},
                verifier={"type": "structured_exact_match", "fields": ["stream_id", "mean_value"]},
                metadata={
                    "point_class": point_class,
                    "equipment_type": equipment_type,
                    "location_type": location_type,
                },
            )
        )
        if len(examples) >= limit:
            break
    return examples


def gen_pairwise_mean_compare(frame: pd.DataFrame, limit: int) -> list[Example]:
    examples = []
    grouped = frame[
        frame["mean_value"].notna()
        & frame["point_class"].notna()
        & frame["equipment_type"].notna()
        & frame["location_label"].notna()
    ].groupby(["site_id", "point_class", "equipment_type"])

    ordinal = 0
    for (site_id, point_class, equipment_type), group in grouped:
        if len(group) < 2:
            continue
        group = group.sort_values("mean_value", ascending=False).head(2)
        first = group.iloc[0]
        second = group.iloc[1]
        if abs(float(first["mean_value"]) - float(second["mean_value"])) < 0.25:
            continue
        ordinal += 1
        query = (
            f"In {site_id}, between the {point_class} points on {first['equipment_label']} ({first['location_label']}) "
            f"and {second['equipment_label']} ({second['location_label']}), which has the larger overall mean value?"
        )
        examples.append(
            Example(
                split=assign_split(site_id, ordinal),
                task_family="pairwise_mean_compare",
                site_id=site_id,
                query=query,
                answer={
                    "winning_stream_id": first["stream_id"],
                    "losing_stream_id": second["stream_id"],
                    "winning_mean_value": round_answer(safe_float(first["mean_value"])),
                    "losing_mean_value": round_answer(safe_float(second["mean_value"])),
                },
                program={
                    "tools": ["resolve_point", "compare_stream_summary"],
                    "args": {
                        "site_id": site_id,
                        "point_class": point_class,
                        "left_stream_id": first["stream_id"],
                        "right_stream_id": second["stream_id"],
                        "metric": "mean",
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "compare_summary",
                        left=first["stream_id"],
                        right=second["stream_id"],
                        metric="mean_value",
                    )
                ],
                evidence={"stream_ids": [first["stream_id"], second["stream_id"]]},
                verifier={"type": "structured_exact_match", "fields": ["winning_stream_id", "losing_stream_id"]},
                metadata={
                    "point_class": point_class,
                    "equipment_type": equipment_type,
                },
            )
        )
        if len(examples) >= limit:
            break
    return examples


def gen_point_disambiguation(frame: pd.DataFrame, limit: int) -> list[Example]:
    examples = []
    grouped = frame[
        frame["point_class"].notna() & frame["equipment_label"].notna() & frame["location_label"].notna()
    ].groupby(["site_id", "point_class"])
    ordinal = 0
    for (site_id, point_class), group in grouped:
        if len(group) < 2:
            continue
        sampled = group.drop_duplicates(subset=["equipment_label", "location_label"]).head(2)
        if len(sampled) < 2:
            continue
        row = sampled.iloc[0]
        ordinal += 1
        query = (
            f"In {site_id}, which stream corresponds to the {point_class} point on "
            f"{row['equipment_label']} located in {row['location_label']}?"
        )
        examples.append(
            Example(
                split=assign_split(site_id, ordinal),
                task_family="point_disambiguation",
                site_id=site_id,
                query=query,
                answer={
                    "stream_id": row["stream_id"],
                    "equipment_label": row["equipment_label"],
                    "location_label": row["location_label"],
                },
                program={
                    "tools": ["resolve_point"],
                    "args": {
                        "site_id": site_id,
                        "point_class": point_class,
                        "equipment_label": row["equipment_label"],
                        "location_label": row["location_label"],
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "resolve_point",
                        site=site_id,
                        pc=point_class,
                        eq=row["equipment_label"],
                        loc=row["location_label"],
                    )
                ],
                evidence={"stream_ids": sampled["stream_id"].tolist()},
                verifier={"type": "structured_exact_match", "fields": ["stream_id"]},
                metadata={"point_class": point_class},
            )
        )
        if len(examples) >= limit:
            break
    return examples


def gen_location_variability_rank(frame: pd.DataFrame, limit: int) -> list[Example]:
    examples = []
    grouped = frame[
        frame["std_value"].notna()
        & frame["point_class"].notna()
        & frame["location_type"].notna()
    ].groupby(["site_id", "location_type", "point_class"])
    ordinal = 0
    for (site_id, location_type, point_class), group in grouped:
        if len(group) < 3:
            continue
        group = group.sort_values("std_value", ascending=False)
        top = group.iloc[0]
        runner_up = group.iloc[1]
        if abs(float(top["std_value"]) - float(runner_up["std_value"])) < 0.1:
            continue
        ordinal += 1
        query = (
            f"In {site_id}, among {point_class} points located in {location_type}, "
            f"which stream shows the highest overall variability?"
        )
        examples.append(
            Example(
                split=assign_split(site_id, ordinal),
                task_family="location_variability_rank",
                site_id=site_id,
                query=query,
                answer={
                    "stream_id": top["stream_id"],
                    "std_value": round_answer(safe_float(top["std_value"])),
                },
                program={
                    "tools": ["list_points", "rank_stream_summary"],
                    "args": {
                        "site_id": site_id,
                        "location_type": location_type,
                        "point_class": point_class,
                        "metric": "std",
                        "order": "desc",
                    },
                },
                tool_plan=[
                    compact_call(
                        "c1",
                        "list_points",
                        site=site_id,
                        lt=location_type,
                        pc=point_class,
                    ),
                    compact_call(
                        "c2",
                        "rank_summary",
                        sids="$c1.stream_ids",
                        metric="std_value",
                        order="desc",
                        topk=1,
                    ),
                ],
                evidence={"stream_ids": group["stream_id"].tolist()},
                verifier={"type": "structured_exact_match", "fields": ["stream_id", "std_value"]},
                metadata={"point_class": point_class, "location_type": location_type},
            )
        )
        if len(examples) >= limit:
            break
    return examples


TASK_GENERATORS: dict[str, Callable[[pd.DataFrame, int], list[Example]]] = {
    "point_mean_lookup": gen_point_mean_lookup,
    "point_extrema_lookup": gen_point_extrema_lookup,
    "coverage_lookup": gen_coverage_lookup,
    "rank_by_mean": gen_rank_by_mean,
    "pairwise_mean_compare": gen_pairwise_mean_compare,
    "point_disambiguation": gen_point_disambiguation,
    "location_variability_rank": gen_location_variability_rank,
}


TOOL_REGISTRY = {
    "version": "compact-v1",
    "design_target": "small-tool-using-llms",
    "notes": [
        "Short argument keys are used to reduce prompt length.",
        "The registry is model-agnostic and does not assume OpenAI function-calling.",
        "The same calls can be rendered as plain JSON, XML, or tagged text for smaller local models.",
    ],
    "tools": [
        {
            "tool": "resolve_point",
            "args": {
                "site": "site_id",
                "pc": "point_class",
                "eq": "equipment_label",
                "loc": "location_label",
            },
            "returns": {"stream_id": "resolved stream identifier"},
        },
        {
            "tool": "stream_summary",
            "args": {"sid": "stream_id", "fields": "list of summary fields"},
            "returns": {"summary": "requested summary fields for the stream"},
        },
        {
            "tool": "list_points",
            "args": {
                "site": "site_id",
                "pc": "point_class",
                "et": "equipment_type",
                "lt": "location_type",
            },
            "returns": {"stream_ids": "candidate stream identifiers"},
        },
        {
            "tool": "rank_summary",
            "args": {
                "sids": "candidate stream_ids",
                "metric": "summary metric name",
                "order": "asc or desc",
                "topk": "number of returned items",
            },
            "returns": {"ranked": "ranked stream summaries"},
        },
        {
            "tool": "compare_summary",
            "args": {
                "left": "left stream_id",
                "right": "right stream_id",
                "metric": "summary metric name",
            },
            "returns": {"winner": "stream with larger value", "values": "both compared values"},
        },
    ],
}


def generate_benchmark(catalog_dir: Path, out_dir: Path, per_family: int = 60) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = load_catalog(catalog_dir)

    splits: dict[str, list[dict]] = {"train": [], "dev": [], "test": []}
    family_summary: dict[str, int] = {}
    for family_name, generator in TASK_GENERATORS.items():
        examples = generator(frame, per_family)
        family_summary[family_name] = len(examples)
        for index, example in enumerate(examples, start=1):
            payload = example.as_dict(build_example_id(example.split, family_name, index))
            splits[example.split].append(payload)

    if not splits["test"]:
        fallback_rows = splits["train"][::7] + splits["dev"][::3]
        fallback_rows = fallback_rows[: max(1, min(50, len(fallback_rows)))]
        fallback_ids = {row["example_id"] for row in fallback_rows}
        splits["train"] = [row for row in splits["train"] if row["example_id"] not in fallback_ids]
        splits["dev"] = [row for row in splits["dev"] if row["example_id"] not in fallback_ids]
        for row in fallback_rows:
            row["split"] = "test"
        splits["test"].extend(fallback_rows)

    for split_name, rows in splits.items():
        output_path = out_dir / f"{split_name}.jsonl"
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "splits": {name: len(rows) for name, rows in splits.items()},
        "task_families": family_summary,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "tool_registry.json").write_text(json.dumps(TOOL_REGISTRY, indent=2), encoding="utf-8")
    return manifest
