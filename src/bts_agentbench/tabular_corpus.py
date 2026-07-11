from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from .preprocess import (
    compute_quality_metrics,
    downsample_preview,
    normalize_text,
    nullable_text,
    summarize_by_period,
    summarize_calendar_profile,
)


INVENTORY_COLUMNS = [
    "site_id",
    "stream_id",
    "point_class",
    "point_type",
    "point_label",
    "equipment_type",
    "equipment_label",
    "location_type",
    "location_label",
    "mean_value",
    "std_value",
    "min_value",
    "max_value",
    "count",
    "first_timestamp",
    "last_timestamp",
    "n_unique",
]


def _build_inventory(metadata: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    summary = (
        observations.groupby(["site_id", "stream_id"], as_index=False)
        .agg(
            mean_value=("value", "mean"),
            std_value=("value", "std"),
            min_value=("value", "min"),
            max_value=("value", "max"),
            count=("value", "count"),
            first_timestamp=("timestamp", "min"),
            last_timestamp=("timestamp", "max"),
            n_unique=("value", "nunique"),
        )
        .copy()
    )
    summary["std_value"] = summary["std_value"].fillna(0.0)
    inventory = metadata.merge(summary, on=["site_id", "stream_id"], how="inner")
    for column in INVENTORY_COLUMNS:
        if column not in inventory.columns:
            inventory[column] = None
    inventory = inventory[INVENTORY_COLUMNS].copy()
    inventory["site_norm"] = inventory["site_id"].map(normalize_text)
    inventory["point_class_norm"] = inventory["point_class"].map(normalize_text)
    inventory["point_label_norm"] = inventory["point_label"].map(normalize_text)
    inventory["equipment_label_norm"] = inventory["equipment_label"].map(normalize_text)
    inventory["equipment_type_norm"] = inventory["equipment_type"].map(normalize_text)
    inventory["location_label_norm"] = inventory["location_label"].map(normalize_text)
    inventory["location_type_norm"] = inventory["location_type"].map(normalize_text)
    inventory["candidate_key"] = (
        inventory["site_norm"].fillna("")
        + "|"
        + inventory["point_class_norm"].fillna("")
        + "|"
        + inventory["equipment_label_norm"].fillna("")
        + "|"
        + inventory["location_label_norm"].fillna("")
    )
    inventory["candidate_ambiguity"] = inventory.groupby("candidate_key")["stream_id"].transform("count").astype(int)
    inventory["raw_zip_path"] = None
    inventory["raw_member_name"] = None
    inventory["raw_n_points"] = inventory["count"]
    inventory["raw_first_timestamp"] = inventory["first_timestamp"].astype(str)
    inventory["raw_last_timestamp"] = inventory["last_timestamp"].astype(str)
    inventory["has_raw"] = True
    return inventory


def _build_quality_table(inventory: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    quality_rows: list[dict] = []
    grouped = observations.groupby("stream_id", sort=True)
    inventory_lookup = inventory.set_index("stream_id")
    for stream_id, frame in grouped:
        frame = frame.sort_values("timestamp").copy()
        timestamps = pd.DatetimeIndex(frame["timestamp"])
        values = frame["value"].astype(float).to_numpy()
        metrics = compute_quality_metrics(timestamps, values)
        inv = inventory_lookup.loc[stream_id]
        quality_rows.append(
            {
                "site_id": inv["site_id"],
                "stream_id": stream_id,
                "point_class": nullable_text(inv["point_class"]),
                "equipment_label": nullable_text(inv["equipment_label"]),
                "location_label": nullable_text(inv["location_label"]),
                **metrics,
            }
        )
    return pd.DataFrame(quality_rows)


def _build_aggregate_tables(inventory: pd.DataFrame, observations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    inventory_lookup = inventory.set_index("stream_id")
    daily_frames: list[pd.DataFrame] = []
    weekly_frames: list[pd.DataFrame] = []
    monthly_frames: list[pd.DataFrame] = []
    profile_frames: list[pd.DataFrame] = []
    preview_rows: list[dict] = []
    for stream_id, frame in observations.groupby("stream_id", sort=True):
        frame = frame.sort_values("timestamp").copy()
        timestamps = pd.DatetimeIndex(frame["timestamp"])
        values = frame["value"].astype(float).to_numpy()
        site_id = str(inventory_lookup.loc[stream_id]["site_id"])
        daily_frames.append(summarize_by_period(stream_id, site_id, timestamps, values, "day"))
        weekly_frames.append(summarize_by_period(stream_id, site_id, timestamps, values, "week"))
        monthly_frames.append(summarize_by_period(stream_id, site_id, timestamps, values, "month"))
        profile_frames.append(summarize_calendar_profile(stream_id, site_id, timestamps, values))
        preview = downsample_preview(timestamps, values)
        preview_rows.append(
            {
                "site_id": site_id,
                "stream_id": stream_id,
                "preview_timestamps_json": json.dumps(preview["preview_timestamps"]),
                "preview_values_json": json.dumps(preview["preview_values"]),
            }
        )
    empty_agg = pd.DataFrame(
        columns=[
            "stream_id",
            "site_id",
            "period",
            "window_start",
            "window_end",
            "count",
            "mean_value",
            "std_value",
            "min_value",
            "max_value",
        ]
    )
    empty_profile = pd.DataFrame(
        columns=[
            "stream_id",
            "site_id",
            "day_of_week",
            "hour_of_day",
            "count",
            "mean_value",
            "std_value",
            "min_value",
            "max_value",
        ]
    )
    return (
        pd.concat(daily_frames, ignore_index=True) if daily_frames else empty_agg.copy(),
        pd.concat(weekly_frames, ignore_index=True) if weekly_frames else empty_agg.copy(),
        pd.concat(monthly_frames, ignore_index=True) if monthly_frames else empty_agg.copy(),
        pd.concat(profile_frames, ignore_index=True) if profile_frames else empty_profile,
        pd.DataFrame(preview_rows),
    )


def build_tool_store_from_tabular_corpus(
    *,
    metadata: pd.DataFrame,
    observations: pd.DataFrame,
    out_dir: Path,
    entities: pd.DataFrame | None = None,
    relations: pd.DataFrame | None = None,
    source_name: str,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    observations = observations.copy()
    observations["timestamp"] = pd.to_datetime(observations["timestamp"], utc=True, errors="coerce")
    observations = observations.dropna(subset=["site_id", "stream_id", "timestamp", "value"]).copy()
    observations["site_id"] = observations["site_id"].astype(str)
    observations["stream_id"] = observations["stream_id"].astype(str)
    observations["value"] = observations["value"].astype(float)

    inventory = _build_inventory(metadata.copy(), observations)
    quality_metrics = _build_quality_table(inventory, observations)
    daily, weekly, monthly, profiles, previews = _build_aggregate_tables(inventory, observations)

    point_inventory_path = out_dir / "point_inventory.parquet"
    tool_ready_points_path = out_dir / "tool_ready_points.parquet"
    raw_obs_path = out_dir / "raw_observations.parquet"
    quality_path = out_dir / "quality_metrics.parquet"
    daily_path = out_dir / "daily_aggregates.parquet"
    weekly_path = out_dir / "weekly_aggregates.parquet"
    monthly_path = out_dir / "monthly_aggregates.parquet"
    profiles_path = out_dir / "calendar_profiles.parquet"
    previews_path = out_dir / "stream_previews.parquet"

    inventory.to_parquet(point_inventory_path, index=False)
    inventory.to_parquet(tool_ready_points_path, index=False)
    observations.to_parquet(raw_obs_path, index=False)
    quality_metrics.to_parquet(quality_path, index=False)
    daily.to_parquet(daily_path, index=False)
    weekly.to_parquet(weekly_path, index=False)
    monthly.to_parquet(monthly_path, index=False)
    profiles.to_parquet(profiles_path, index=False)
    previews.to_parquet(previews_path, index=False)
    if entities is not None:
        entities.to_parquet(out_dir / "entities.parquet", index=False)
    if relations is not None:
        relations.to_parquet(out_dir / "relations.parquet", index=False)

    con = duckdb.connect(str(out_dir / "tool_store.duckdb"))
    try:
        con.execute(f"create or replace table point_inventory as select * from read_parquet('{point_inventory_path.as_posix()}')")
        con.execute(f"create or replace table tool_ready_points as select * from read_parquet('{tool_ready_points_path.as_posix()}')")
        con.execute(f"create or replace table raw_observations as select * from read_parquet('{raw_obs_path.as_posix()}')")
        con.execute(f"create or replace table quality_metrics as select * from read_parquet('{quality_path.as_posix()}')")
        con.execute(f"create or replace table daily_aggregates as select * from read_parquet('{daily_path.as_posix()}')")
        con.execute(f"create or replace table weekly_aggregates as select * from read_parquet('{weekly_path.as_posix()}')")
        con.execute(f"create or replace table monthly_aggregates as select * from read_parquet('{monthly_path.as_posix()}')")
        con.execute(f"create or replace table calendar_profiles as select * from read_parquet('{profiles_path.as_posix()}')")
        con.execute(f"create or replace table stream_previews as select * from read_parquet('{previews_path.as_posix()}')")
        if entities is not None:
            con.execute(f"create or replace table entities as select * from read_parquet('{(out_dir / 'entities.parquet').as_posix()}')")
        if relations is not None:
            con.execute(f"create or replace table relations as select * from read_parquet('{(out_dir / 'relations.parquet').as_posix()}')")
    finally:
        con.close()

    summary = {
        "source_name": source_name,
        "stream_count": int(len(inventory)),
        "observation_count": int(len(observations)),
        "site_count": int(inventory["site_id"].nunique()),
        "tables": [
            "point_inventory",
            "tool_ready_points",
            "raw_observations",
            "quality_metrics",
            "daily_aggregates",
            "weekly_aggregates",
            "monthly_aggregates",
            "calendar_profiles",
            "stream_previews",
        ],
    }
    if entities is not None:
        summary["tables"].append("entities")
    if relations is not None:
        summary["tables"].append("relations")
    (out_dir / "preprocess_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
