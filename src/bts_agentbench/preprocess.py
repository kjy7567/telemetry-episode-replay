from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .catalog import normalize_stream_id


RAW_INDEX_SCHEMA = pa.schema(
    [
        ("site_id", pa.string()),
        ("zip_path", pa.string()),
        ("member_name", pa.string()),
        ("stream_id", pa.string()),
        ("point_class", pa.string()),
        ("equipment_label", pa.string()),
        ("location_label", pa.string()),
        ("n_points", pa.int64()),
        ("first_timestamp", pa.string()),
        ("last_timestamp", pa.string()),
    ]
)

QUALITY_SCHEMA = pa.schema(
    [
        ("site_id", pa.string()),
        ("stream_id", pa.string()),
        ("point_class", pa.string()),
        ("equipment_label", pa.string()),
        ("location_label", pa.string()),
        ("n_points", pa.int64()),
        ("first_timestamp", pa.string()),
        ("last_timestamp", pa.string()),
        ("median_step_seconds", pa.float64()),
        ("longest_gap_seconds", pa.float64()),
        ("observed_fraction", pa.float64()),
        ("nan_fraction", pa.float64()),
        ("zero_fraction", pa.float64()),
        ("constant_fraction", pa.float64()),
        ("duplicate_timestamp_fraction", pa.float64()),
    ]
)

AGGREGATE_SCHEMA = pa.schema(
    [
        ("stream_id", pa.string()),
        ("site_id", pa.string()),
        ("period", pa.string()),
        ("window_start", pa.timestamp("us", tz="UTC")),
        ("window_end", pa.timestamp("us", tz="UTC")),
        ("count", pa.int64()),
        ("mean_value", pa.float64()),
        ("std_value", pa.float64()),
        ("min_value", pa.float64()),
        ("max_value", pa.float64()),
    ]
)

PROFILE_SCHEMA = pa.schema(
    [
        ("stream_id", pa.string()),
        ("site_id", pa.string()),
        ("day_of_week", pa.int64()),
        ("hour_of_day", pa.int64()),
        ("count", pa.int64()),
        ("mean_value", pa.float64()),
        ("std_value", pa.float64()),
        ("min_value", pa.float64()),
        ("max_value", pa.float64()),
    ]
)

PREVIEW_SCHEMA = pa.schema(
    [
        ("site_id", pa.string()),
        ("stream_id", pa.string()),
        ("preview_timestamps_json", pa.string()),
        ("preview_values_json", pa.string()),
    ]
)


def normalize_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    if not text or text in {"nan", "none"}:
        return None
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    text = " ".join(text.split())
    return text or None


def nullable_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def payload_stream_id(token: object) -> str | None:
    if token is None:
        return None
    value = str(token)
    name = Path(value).name
    for prefix in ("Site_A_", "Site_B_", "Site_C_"):
        if name.startswith(prefix):
            return normalize_stream_id(name[len(prefix) : -len(".pickle")])
    return normalize_stream_id(name)


def to_timestamp_series(ts_array: np.ndarray) -> pd.DatetimeIndex:
    return pd.to_datetime(ts_array, utc=True)


def compute_quality_metrics(timestamps: pd.DatetimeIndex, values: np.ndarray) -> dict:
    n_points = int(len(values))
    finite_mask = np.isfinite(values)
    finite_values = values[finite_mask]

    if n_points == 0:
        return {
            "n_points": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "median_step_seconds": None,
            "longest_gap_seconds": None,
            "observed_fraction": None,
            "nan_fraction": None,
            "zero_fraction": None,
            "constant_fraction": None,
            "duplicate_timestamp_fraction": None,
        }

    diffs_seconds = pd.Series(timestamps).diff().dt.total_seconds().dropna()
    positive_diffs = diffs_seconds[diffs_seconds > 0]
    median_step_seconds = float(positive_diffs.median()) if not positive_diffs.empty else None
    longest_gap_seconds = float(diffs_seconds.max()) if not diffs_seconds.empty else None

    if median_step_seconds and median_step_seconds > 0:
        coverage_seconds = float((timestamps[-1] - timestamps[0]).total_seconds())
        expected_points = int(round(coverage_seconds / median_step_seconds)) + 1
        observed_fraction = min(1.0, n_points / expected_points) if expected_points > 0 else None
    else:
        observed_fraction = None

    duplicate_timestamp_fraction = 1.0 - (pd.Series(timestamps).nunique() / n_points)
    nan_fraction = float(1.0 - (finite_mask.sum() / n_points))
    zero_fraction = float(np.mean(finite_values == 0)) if len(finite_values) else None
    constant_fraction = float(np.mean(np.diff(finite_values) == 0)) if len(finite_values) >= 2 else None

    return {
        "n_points": n_points,
        "first_timestamp": pd.Timestamp(timestamps[0]).isoformat(),
        "last_timestamp": pd.Timestamp(timestamps[-1]).isoformat(),
        "median_step_seconds": median_step_seconds,
        "longest_gap_seconds": longest_gap_seconds,
        "observed_fraction": observed_fraction,
        "nan_fraction": nan_fraction,
        "zero_fraction": zero_fraction,
        "constant_fraction": constant_fraction,
        "duplicate_timestamp_fraction": duplicate_timestamp_fraction,
    }


def summarize_by_period(
    stream_id: str,
    site_id: str,
    timestamps: pd.DatetimeIndex,
    values: np.ndarray,
    period: str,
) -> pd.DataFrame:
    frame = pd.DataFrame({"timestamp": timestamps, "value": values})
    frame = frame[np.isfinite(frame["value"])].copy()
    if frame.empty:
        return pd.DataFrame(
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

    naive_timestamps = frame["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)

    if period == "day":
        frame["window_start"] = frame["timestamp"].dt.floor("D")
        default_days = 1
    elif period == "week":
        frame["window_start"] = naive_timestamps.dt.to_period("W-MON").dt.start_time.dt.tz_localize("UTC")
        default_days = 7
    elif period == "month":
        frame["window_start"] = naive_timestamps.dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")
        default_days = 31
    else:
        raise ValueError(f"Unsupported period: {period}")

    grouped = (
        frame.groupby("window_start", sort=True)["value"]
        .agg(["count", "mean", "std", "min", "max"])
        .reset_index()
        .rename(
            columns={
                "mean": "mean_value",
                "std": "std_value",
                "min": "min_value",
                "max": "max_value",
            }
        )
    )
    grouped["window_end"] = grouped["window_start"].shift(-1)
    grouped["window_end"] = grouped["window_end"].fillna(grouped["window_start"] + pd.Timedelta(days=default_days))
    grouped["stream_id"] = stream_id
    grouped["site_id"] = site_id
    grouped["period"] = period
    return grouped[
        [
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
    ]


def summarize_calendar_profile(stream_id: str, site_id: str, timestamps: pd.DatetimeIndex, values: np.ndarray) -> pd.DataFrame:
    frame = pd.DataFrame({"timestamp": timestamps, "value": values})
    frame = frame[np.isfinite(frame["value"])].copy()
    if frame.empty:
        return pd.DataFrame(
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

    frame["day_of_week"] = frame["timestamp"].dt.dayofweek
    frame["hour_of_day"] = frame["timestamp"].dt.hour
    grouped = (
        frame.groupby(["day_of_week", "hour_of_day"], sort=True)["value"]
        .agg(["count", "mean", "std", "min", "max"])
        .reset_index()
        .rename(
            columns={
                "mean": "mean_value",
                "std": "std_value",
                "min": "min_value",
                "max": "max_value",
            }
        )
    )
    grouped["stream_id"] = stream_id
    grouped["site_id"] = site_id
    return grouped[
        [
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
    ]


def downsample_preview(timestamps: pd.DatetimeIndex, values: np.ndarray, target_points: int = 96) -> dict:
    if len(values) == 0:
        return {"preview_timestamps": [], "preview_values": []}
    if len(values) <= target_points:
        idx = np.arange(len(values))
    else:
        idx = np.linspace(0, len(values) - 1, target_points).astype(int)
    return {
        "preview_timestamps": [pd.Timestamp(timestamps[i]).isoformat() for i in idx.tolist()],
        "preview_values": [float(values[i]) if np.isfinite(values[i]) else None for i in idx.tolist()],
    }


@dataclass
class ParquetBatchWriter:
    path: Path
    schema: pa.Schema | None = None
    writer: pq.ParquetWriter | None = None

    def write_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        if self.schema is None:
            table = pa.Table.from_pandas(frame, preserve_index=False)
        else:
            arrays = []
            for field in self.schema:
                series = frame[field.name] if field.name in frame.columns else pd.Series([None] * len(frame))
                if pa.types.is_timestamp(field.type):
                    series = pd.to_datetime(series, utc=True, errors="coerce")
                    arrays.append(pa.Array.from_pandas(series, type=field.type))
                else:
                    arrays.append(pa.array(series.tolist(), type=field.type, from_pandas=True))
            table = pa.Table.from_arrays(arrays, schema=self.schema)
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(self.path, self.schema or table.schema)
        self.writer.write_table(table)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None


def build_point_inventory(processed_dir: Path, out_dir: Path) -> pd.DataFrame:
    streams = pd.read_parquet(processed_dir / "streams.parquet")
    inventory = streams[
        [
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
    ].copy()
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
    inventory.to_parquet(out_dir / "point_inventory.parquet", index=False)
    return inventory


def preprocess_raw_archives(raw_dir: Path, processed_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_point_inventory(processed_dir, out_dir)
    inventory_lookup = inventory.set_index("stream_id")

    raw_index_writer = ParquetBatchWriter(out_dir / "raw_stream_index.parquet", schema=RAW_INDEX_SCHEMA)
    quality_writer = ParquetBatchWriter(out_dir / "quality_metrics.parquet", schema=QUALITY_SCHEMA)
    daily_writer = ParquetBatchWriter(out_dir / "daily_aggregates.parquet", schema=AGGREGATE_SCHEMA)
    weekly_writer = ParquetBatchWriter(out_dir / "weekly_aggregates.parquet", schema=AGGREGATE_SCHEMA)
    monthly_writer = ParquetBatchWriter(out_dir / "monthly_aggregates.parquet", schema=AGGREGATE_SCHEMA)
    profile_writer = ParquetBatchWriter(out_dir / "calendar_profiles.parquet", schema=PROFILE_SCHEMA)
    preview_writer = ParquetBatchWriter(out_dir / "stream_previews.parquet", schema=PREVIEW_SCHEMA)

    site_stats: dict[str, dict[str, int]] = {}
    skipped_members: list[dict[str, str]] = []
    raw_streams_seen: set[str] = set()

    try:
        for zip_path in sorted(raw_dir.glob("Site_*aa.zip")):
            site_id = f"BTS_{zip_path.stem.split('_')[1][0].upper()}"
            stream_rows = 0
            daily_rows = 0
            weekly_rows = 0
            monthly_rows = 0
            profile_rows = 0
            with ZipFile(zip_path) as archive:
                pickle_members = [member for member in archive.namelist() if member.endswith(".pickle")]
                print(f"[preprocess] {site_id}: {len(pickle_members)} raw pickle members in {zip_path.name}")
                for member_name in pickle_members:
                    try:
                        with archive.open(member_name) as handle:
                            payload = pickle.load(handle)
                    except Exception as exc:
                        skipped_members.append(
                            {
                                "site_id": site_id,
                                "zip_path": zip_path.name,
                                "member_name": member_name,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        )
                        if len(skipped_members) <= 10:
                            print(
                                f"[preprocess] skipping unreadable member {member_name} from {zip_path.name}: "
                                f"{type(exc).__name__}: {exc}"
                            )
                        continue

                    stream_id = payload_stream_id(payload[0]) or payload_stream_id(member_name)
                    if not stream_id:
                        continue
                    raw_streams_seen.add(stream_id)

                    timestamps = to_timestamp_series(payload[1])
                    values = np.asarray(payload[2], dtype=np.float64)

                    quality = compute_quality_metrics(timestamps, values)
                    preview = downsample_preview(timestamps, values)

                    inventory_row = inventory_lookup.loc[stream_id] if stream_id in inventory_lookup.index else None
                    point_class = nullable_text(inventory_row["point_class"]) if inventory_row is not None else None
                    equipment_label = (
                        nullable_text(inventory_row["equipment_label"]) if inventory_row is not None else None
                    )
                    location_label = nullable_text(inventory_row["location_label"]) if inventory_row is not None else None

                    raw_index_writer.write_frame(
                        pd.DataFrame(
                            [
                                {
                                    "site_id": site_id,
                                    "zip_path": str(zip_path),
                                    "member_name": member_name,
                                    "stream_id": stream_id,
                                    "point_class": point_class,
                                    "equipment_label": equipment_label,
                                    "location_label": location_label,
                                    "n_points": quality["n_points"],
                                    "first_timestamp": quality["first_timestamp"],
                                    "last_timestamp": quality["last_timestamp"],
                                }
                            ]
                        )
                    )
                    quality_writer.write_frame(
                        pd.DataFrame(
                            [
                                {
                                    "site_id": site_id,
                                    "stream_id": stream_id,
                                    "point_class": point_class,
                                    "equipment_label": equipment_label,
                                    "location_label": location_label,
                                    **quality,
                                }
                            ]
                        )
                    )
                    preview_writer.write_frame(
                        pd.DataFrame(
                            [
                                {
                                    "site_id": site_id,
                                    "stream_id": stream_id,
                                    "preview_timestamps_json": json.dumps(preview["preview_timestamps"]),
                                    "preview_values_json": json.dumps(preview["preview_values"]),
                                }
                            ]
                        )
                    )

                    daily = summarize_by_period(stream_id, site_id, timestamps, values, "day")
                    weekly = summarize_by_period(stream_id, site_id, timestamps, values, "week")
                    monthly = summarize_by_period(stream_id, site_id, timestamps, values, "month")
                    profile = summarize_calendar_profile(stream_id, site_id, timestamps, values)
                    daily_writer.write_frame(daily)
                    weekly_writer.write_frame(weekly)
                    monthly_writer.write_frame(monthly)
                    profile_writer.write_frame(profile)

                    stream_rows += 1
                    daily_rows += len(daily)
                    weekly_rows += len(weekly)
                    monthly_rows += len(monthly)
                    profile_rows += len(profile)
                    if stream_rows % 250 == 0:
                        print(
                            f"[preprocess] {site_id}: processed {stream_rows}/{len(pickle_members)} streams "
                            f"(daily_rows={daily_rows}, weekly_rows={weekly_rows}, monthly_rows={monthly_rows})"
                        )

            site_stats[site_id] = {
                "streams_processed": stream_rows,
                "daily_rows": daily_rows,
                "weekly_rows": weekly_rows,
                "monthly_rows": monthly_rows,
                "calendar_profile_rows": profile_rows,
                "skipped_members": sum(1 for item in skipped_members if item["site_id"] == site_id),
            }
            print(f"[preprocess] {site_id}: completed {stream_rows} streams")
    finally:
        raw_index_writer.close()
        quality_writer.close()
        daily_writer.close()
        weekly_writer.close()
        monthly_writer.close()
        profile_writer.close()
        preview_writer.close()

    raw_index = pd.read_parquet(out_dir / "raw_stream_index.parquet")
    raw_index = raw_index.sort_values(["site_id", "stream_id", "member_name"]).drop_duplicates("stream_id", keep="first")
    inventory = inventory.merge(
        raw_index[
            [
                "stream_id",
                "zip_path",
                "member_name",
                "n_points",
                "first_timestamp",
                "last_timestamp",
            ]
        ].rename(
            columns={
                "zip_path": "raw_zip_path",
                "member_name": "raw_member_name",
                "n_points": "raw_n_points",
                "first_timestamp": "raw_first_timestamp",
                "last_timestamp": "raw_last_timestamp",
            }
        ),
        on="stream_id",
        how="left",
    )
    inventory["has_raw"] = inventory["raw_member_name"].notna()
    inventory.to_parquet(out_dir / "point_inventory.parquet", index=False)
    inventory[inventory["has_raw"]].copy().to_parquet(out_dir / "tool_ready_points.parquet", index=False)

    con = duckdb.connect(str(out_dir / "tool_store.duckdb"))
    try:
        con.execute(
            f"CREATE OR REPLACE TABLE point_inventory AS SELECT * FROM read_parquet('{(out_dir / 'point_inventory.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE tool_ready_points AS SELECT * FROM read_parquet('{(out_dir / 'tool_ready_points.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE raw_stream_index AS SELECT * FROM read_parquet('{(out_dir / 'raw_stream_index.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE quality_metrics AS SELECT * FROM read_parquet('{(out_dir / 'quality_metrics.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE daily_aggregates AS SELECT * FROM read_parquet('{(out_dir / 'daily_aggregates.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE weekly_aggregates AS SELECT * FROM read_parquet('{(out_dir / 'weekly_aggregates.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE monthly_aggregates AS SELECT * FROM read_parquet('{(out_dir / 'monthly_aggregates.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE calendar_profiles AS SELECT * FROM read_parquet('{(out_dir / 'calendar_profiles.parquet').as_posix()}')"
        )
        con.execute(
            f"CREATE OR REPLACE TABLE stream_previews AS SELECT * FROM read_parquet('{(out_dir / 'stream_previews.parquet').as_posix()}')"
        )
        relations_path = processed_dir / "relations.parquet"
        entities_path = processed_dir / "entities.parquet"
        if relations_path.exists():
            con.execute(f"CREATE OR REPLACE TABLE relations AS SELECT * FROM read_parquet('{relations_path.as_posix()}')")
        if entities_path.exists():
            con.execute(f"CREATE OR REPLACE TABLE entities AS SELECT * FROM read_parquet('{entities_path.as_posix()}')")
    finally:
        con.close()

    summary = {
        "site_stats": site_stats,
        "raw_coverage": {
            "metadata_streams": int(len(inventory)),
            "raw_streams_seen": int(len(raw_streams_seen)),
            "matched_streams": int(inventory["has_raw"].sum()),
            "metadata_only_streams": int((~inventory["has_raw"]).sum()),
            "skipped_members": len(skipped_members),
        },
        "tables": [
            "point_inventory",
            "tool_ready_points",
            "raw_stream_index",
            "quality_metrics",
            "daily_aggregates",
            "weekly_aggregates",
            "monthly_aggregates",
            "calendar_profiles",
            "stream_previews",
            "entities",
            "relations",
        ],
    }
    (out_dir / "preprocess_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if skipped_members:
        (out_dir / "skipped_members.json").write_text(json.dumps(skipped_members, indent=2), encoding="utf-8")
    return summary
