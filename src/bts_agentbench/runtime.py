from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .preprocess import compute_quality_metrics
from .raw import extract_stream_history


@dataclass
class ExecutedCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    output: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "output": self.output,
        }


class ToolStoreRuntime:
    def __init__(self, tool_store_db: Path | str):
        self.db_path = str(tool_store_db)
        db_path = Path(tool_store_db).resolve()
        self.project_root = db_path.parents[3] if len(db_path.parents) >= 4 else db_path.parent
        self.con = duckdb.connect(self.db_path, read_only=True)
        self._quality_reference: dict[str, float | None] | None = None
        self._window_quality_references: dict[str, dict[str, float | None]] = {}
        self.has_raw_observations = bool(
            self.con.execute(
                """
                select count(*)
                from information_schema.tables
                where table_name = 'raw_observations'
                """
            ).fetchone()[0]
        )

    def close(self) -> None:
        self.con.close()

    def _one(self, query: str, params: tuple[Any, ...]) -> tuple[Any, ...] | None:
        return self.con.execute(query, params).fetchone()

    @staticmethod
    def _normalize_label(site_id: str, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        prefix = f"{site_id} "
        lowered = text.casefold()
        if lowered.startswith(prefix.casefold()):
            text = text[len(prefix) :]
        return " ".join(text.casefold().replace("“", '"').replace("”", '"').replace("`", "").split())

    @staticmethod
    def _point_class_aliases(point_class: str) -> list[str]:
        aliases = [point_class]
        alias_map = {
            "Electric_Power_Sensor": ["Electrical_Power_Sensor"],
            "Power_Sensor": ["Electrical_Power_Sensor"],
            "Electrical_Power": ["Electrical_Power_Sensor"],
            "Peak_Power_Demand": ["Peak_Power_Demand_Sensor"],
            "Air_Flow": ["Air_Flow_Sensor"],
            "Air_Differential_Pressure": ["Air_Differential_Pressure_Sensor"],
        }
        for alias in alias_map.get(point_class, []):
            if alias not in aliases:
                aliases.append(alias)
        return aliases

    @staticmethod
    def _dedupe_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for match in matches:
            stream_id = str(match.get("stream_id"))
            if stream_id in seen:
                continue
            seen.add(stream_id)
            deduped.append(match)
        return deduped

    def quality_reference(self) -> dict[str, float | None]:
        if self._quality_reference is None:
            row = self.con.execute(
                """
                select
                    quantile_cont(observed_fraction, 0.10) as abstain_observed_fraction_below,
                    quantile_cont(observed_fraction, 0.75) as answer_observed_fraction_at_least,
                    quantile_cont(longest_gap_seconds / nullif(median_step_seconds, 0), 0.50) as answer_gap_ratio_at_most,
                    quantile_cont(longest_gap_seconds / nullif(median_step_seconds, 0), 0.95) as abstain_gap_ratio_above
                from quality_metrics
                """
            ).fetchone()
            names = (
                "abstain_observed_fraction_below",
                "answer_observed_fraction_at_least",
                "answer_gap_ratio_at_most",
                "abstain_gap_ratio_above",
            )
            self._quality_reference = {
                name: round(float(value), 4) if value is not None else None for name, value in zip(names, row)
            }
        return dict(self._quality_reference)

    def window_quality_reference(self, period: str = "week") -> dict[str, float | None]:
        period = str(period or "week")
        if period in self._window_quality_references:
            return dict(self._window_quality_references[period])

        if period == "week":
            sample_rows = self.con.execute(
                """
                with weekly_window_support as (
                    select
                        t.stream_id,
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
                        and regexp_matches(t.point_class, '(^[A-Z][A-Za-z0-9_]*$)')
                        and regexp_matches(t.point_class, '(_Sensor|_Setpoint|_Limit)$')
                        and not regexp_matches(t.point_class, '(^Time_|^Duration_Sensor$|_Energy_Sensor$|_Parameter$|^Min_Limit$|^Max_Limit$)')
                        and q.median_step_seconds is not null
                        and q.median_step_seconds > 0
                )
                select stream_id, window_start, window_end
                from weekly_window_support
                where per_stream_rank <= 8 and site_rank <= 8000
                order by stream_id, window_start
                """
            ).fetchall()
            observed_values: list[float] = []
            gap_values: list[float] = []
            for stream_id, window_start, window_end in sample_rows:
                history = self._load_history_for_stream(str(stream_id)).copy()
                timestamp_series = pd.to_datetime(history["timestamp"], utc=True)
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
                window = history.loc[(timestamp_series >= start) & (timestamp_series < end)].copy()
                if window.empty:
                    continue
                metrics = compute_quality_metrics(
                    pd.DatetimeIndex(pd.to_datetime(window["timestamp"], utc=True)),
                    window["value"].astype(float).to_numpy(),
                )
                observed = metrics.get("observed_fraction")
                median_step = metrics.get("median_step_seconds")
                longest_gap = metrics.get("longest_gap_seconds")
                gap_ratio = None
                if median_step is not None and longest_gap is not None and float(median_step) > 0:
                    gap_ratio = float(longest_gap) / float(median_step)
                if observed is not None:
                    observed_values.append(float(observed))
                if gap_ratio is not None:
                    gap_values.append(float(gap_ratio))
                if len(observed_values) >= 2000 and len(gap_values) >= 2000:
                    break
            if observed_values:
                observed_series = pd.Series(observed_values)
                gap_series = pd.Series(gap_values) if gap_values else None
                self._window_quality_references[period] = {
                    "abstain_observed_fraction_below": round(float(observed_series.quantile(0.10)), 4),
                    "answer_observed_fraction_at_least": round(float(observed_series.quantile(0.75)), 4),
                    "answer_gap_ratio_at_most": round(float(gap_series.quantile(0.50)), 4) if gap_series is not None and not gap_series.empty else None,
                    "abstain_gap_ratio_above": round(float(gap_series.quantile(0.85)), 4) if gap_series is not None and not gap_series.empty else None,
                }
                return dict(self._window_quality_references[period])

        aggregate_table = {
            "day": "daily_aggregates",
            "week": "weekly_aggregates",
            "month": "monthly_aggregates",
        }.get(period)
        if aggregate_table is None:
            raise KeyError(f"Unsupported quality-reference period: {period}")

        coverage_row = self.con.execute(
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
        global_ref = self.quality_reference()
        self._window_quality_references[period] = {
            "abstain_observed_fraction_below": round(float(coverage_row[0]), 4) if coverage_row and coverage_row[0] is not None else None,
            "answer_observed_fraction_at_least": round(float(coverage_row[1]), 4) if coverage_row and coverage_row[1] is not None else None,
            "answer_gap_ratio_at_most": global_ref.get("answer_gap_ratio_at_most"),
            "abstain_gap_ratio_above": global_ref.get("abstain_gap_ratio_above"),
        }
        return dict(self._window_quality_references[period])

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, list):
            return [ToolStoreRuntime._normalize_value(v) for v in value]
        if isinstance(value, dict):
            return {k: ToolStoreRuntime._normalize_value(v) for k, v in value.items()}
        return value

    @staticmethod
    def _replace_refs(value: Any, call_outputs: dict[str, dict[str, Any]]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            ref = value[1:]
            call_id, path = ref.split(".", 1)
            current: Any = call_outputs[call_id]
            for part in path.split("."):
                if "[" in part and part.endswith("]"):
                    field, index_text = part[:-1].split("[", 1)
                    if field:
                        current = current[field]
                    current = current[int(index_text)]
                else:
                    current = current[part]
            return current
        if isinstance(value, list):
            return [ToolStoreRuntime._replace_refs(v, call_outputs) for v in value]
        if isinstance(value, dict):
            return {k: ToolStoreRuntime._replace_refs(v, call_outputs) for k, v in value.items()}
        return value

    @staticmethod
    @lru_cache(maxsize=8192)
    def _load_history(zip_path: str, member_name: str | None, stream_id: str) -> pd.DataFrame:
        frame = extract_stream_history(Path(zip_path), stream_id, member_name)
        frame = frame.dropna(subset=["timestamp", "value"]).sort_values("timestamp").reset_index(drop=True)
        return frame

    def _resolve_zip_path(self, zip_path: str) -> str:
        path = Path(zip_path)
        if path.is_absolute():
            return str(path)
        return str((self.project_root / path).resolve())

    def _load_history_from_table(self, stream_id: str) -> pd.DataFrame:
        if not self.has_raw_observations:
            raise KeyError(f"No raw observation table found for stream {stream_id}")
        frame = self.con.execute(
            """
            select timestamp, value
            from raw_observations
            where stream_id = ?
            order by timestamp
            """,
            (stream_id,),
        ).fetchdf()
        if frame.empty:
            raise KeyError(f"No raw observations found for stream {stream_id}")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.dropna(subset=["timestamp", "value"]).sort_values("timestamp").reset_index(drop=True)
        return frame

    def _load_history_for_stream(self, stream_id: str) -> pd.DataFrame:
        raw_row = self._one(
            """
            select raw_zip_path, raw_member_name
            from tool_ready_points
            where stream_id = ?
            """,
            (stream_id,),
        )
        if raw_row is not None and raw_row[0] is not None:
            return self._load_history(self._resolve_zip_path(str(raw_row[0])), raw_row[1], stream_id)
        return self._load_history_from_table(stream_id)

    @lru_cache(maxsize=16384)
    def describe_stream(self, stream_id: str) -> dict[str, Any]:
        row = self._one(
            """
            select
                stream_id,
                site_id,
                point_class,
                point_label,
                equipment_label,
                location_label
            from tool_ready_points
            where stream_id = ?
            """,
            (stream_id,),
        )
        if row is None:
            row = self._one(
                """
                select
                    stream_id,
                    site_id,
                    point_class,
                    point_label,
                    equipment_label,
                    location_label
                from point_inventory
                where stream_id = ?
                """,
                (stream_id,),
            )
        if row is None:
            return {"stream_id": stream_id}
        return {
            "stream_id": row[0],
            "site_id": row[1],
            "point_class": row[2],
            "point_label": row[3],
            "equipment_label": row[4],
            "location_label": row[5],
        }

    def resolve_point(self, args: dict[str, Any]) -> dict[str, Any]:
        where = ["site_id = ?", "point_class = ?"]
        params: list[Any] = [args["site_id"], args["point_class"]]
        if args.get("equipment_label") is not None:
            where.append("lower(equipment_label) = lower(?)")
            params.append(args["equipment_label"])
        if args.get("location_label") is not None:
            where.append("lower(location_label) = lower(?)")
            params.append(args["location_label"])
        rows = self.con.execute(
            f"""
            select
                stream_id,
                point_class,
                equipment_label,
                location_label,
                point_label
            from tool_ready_points
            where {' and '.join(where)}
            order by stream_id
            """,
            tuple(params),
        ).fetchall()
        matches = [
            {
                "stream_id": row[0],
                "point_class": row[1],
                "equipment_label": row[2],
                "location_label": row[3],
                "point_label": row[4],
            }
            for row in rows
        ]
        result = {"match_count": len(matches), "matches": matches}
        if len(matches) == 1:
            result["stream_id"] = matches[0]["stream_id"]
            return result
        if matches:
            return result

        site_id = str(args["site_id"])
        equipment_norm = self._normalize_label(site_id, args.get("equipment_label"))
        location_norm = self._normalize_label(site_id, args.get("location_label"))
        point_class_aliases = self._point_class_aliases(str(args["point_class"]))

        rows = self.con.execute(
            """
            select
                stream_id,
                point_class,
                equipment_label,
                location_label,
                point_label
            from tool_ready_points
            where site_id = ? and point_class in ({})
            order by stream_id
            """.format(",".join("?" for _ in point_class_aliases)),
            tuple([site_id, *point_class_aliases]),
        ).fetchall()

        fallback_matches: list[dict[str, Any]] = []
        for row in rows:
            equipment_label = row[2]
            location_label = row[3]
            if equipment_norm is not None and self._normalize_label(site_id, equipment_label) != equipment_norm:
                continue
            if location_norm is not None and self._normalize_label(site_id, location_label) != location_norm:
                continue
            fallback_matches.append(
                {
                    "stream_id": row[0],
                    "point_class": row[1],
                    "equipment_label": row[2],
                    "location_label": row[3],
                    "point_label": row[4],
                }
            )
        fallback_matches = self._dedupe_matches(fallback_matches)
        fallback = {"match_count": len(fallback_matches), "matches": fallback_matches}
        if len(fallback_matches) == 1:
            fallback["stream_id"] = fallback_matches[0]["stream_id"]
        if fallback_matches:
            fallback["resolution_strategy"] = "normalized_fallback"
            if point_class_aliases != [args["point_class"]]:
                fallback["point_class_aliases"] = point_class_aliases
        return fallback

    def list_points(self, args: dict[str, Any]) -> dict[str, Any]:
        where = ["site_id = ?"]
        params: list[Any] = [args["site_id"]]
        for key in ["point_class", "equipment_type", "location_type"]:
            if args.get(key) is not None:
                where.append(f"{key} = ?")
                params.append(args[key])
        rows = self.con.execute(
            f"""
            select
                stream_id,
                point_class,
                equipment_type,
                equipment_label,
                location_type,
                location_label
            from tool_ready_points
            where {' and '.join(where)}
            order by stream_id
            """,
            tuple(params),
        ).fetchall()
        points = [
            {
                "stream_id": row[0],
                "point_class": row[1],
                "equipment_type": row[2],
                "equipment_label": row[3],
                "location_type": row[4],
                "location_label": row[5],
            }
            for row in rows
        ]
        return {
            "stream_ids": [point["stream_id"] for point in points],
            "count": len(points),
            "points": points,
        }

    def aggregate_window(self, args: dict[str, Any]) -> dict[str, Any]:
        stream_id = args["stream_id"]
        metric = args["metric"]
        window_start = pd.Timestamp(args["window_start"])
        window_end = pd.Timestamp(args["window_end"])
        period = args.get("period")

        table_map = {"day": "daily_aggregates", "week": "weekly_aggregates", "month": "monthly_aggregates"}
        if period in table_map:
            row = self._one(
                f"""
                select count, mean_value, std_value, min_value, max_value
                from {table_map[period]}
                where stream_id = ? and window_start = ? and window_end = ?
                """,
                (stream_id, window_start, window_end),
            )
            if row is not None:
                return {
                    "stream_id": stream_id,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "period": period,
                    "count": int(row[0]),
                    "mean_value": round(float(row[1]), 4) if row[1] is not None else None,
                    "std_value": round(float(row[2]), 4) if row[2] is not None else None,
                    "min_value": round(float(row[3]), 4) if row[3] is not None else None,
                    "max_value": round(float(row[4]), 4) if row[4] is not None else None,
                    metric: round(float({"mean_value": row[1], "std_value": row[2], "min_value": row[3], "max_value": row[4]}[metric]), 4)
                    if {"mean_value": row[1], "std_value": row[2], "min_value": row[3], "max_value": row[4]}.get(metric) is not None
                    else None,
                }

        history = self._load_history_for_stream(stream_id)
        mask = (history["timestamp"] >= window_start) & (history["timestamp"] < window_end)
        frame = history.loc[mask]
        if frame.empty:
            return {
                "stream_id": stream_id,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "period": period,
                "count": 0,
                "mean_value": None,
                "std_value": None,
                "min_value": None,
                "max_value": None,
                metric: None,
            }
        stats = {
            "mean_value": float(frame["value"].mean()),
            "std_value": float(frame["value"].std(ddof=0)),
            "min_value": float(frame["value"].min()),
            "max_value": float(frame["value"].max()),
        }
        return {
            "stream_id": stream_id,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "period": period,
            "count": int(len(frame)),
            "mean_value": round(stats["mean_value"], 4),
            "std_value": round(stats["std_value"], 4),
            "min_value": round(stats["min_value"], 4),
            "max_value": round(stats["max_value"], 4),
            metric: round(stats[metric], 4),
        }

    def compare_window(self, args: dict[str, Any]) -> dict[str, Any]:
        left = self.aggregate_window(
            {
                "stream_id": args["left_stream_id"],
                "metric": args["metric"],
                "window_start": args["window_start"],
                "window_end": args["window_end"],
                "period": args.get("period"),
            }
        )
        right = self.aggregate_window(
            {
                "stream_id": args["right_stream_id"],
                "metric": args["metric"],
                "window_start": args["window_start"],
                "window_end": args["window_end"],
                "period": args.get("period"),
            }
        )
        left_value = left[args["metric"]]
        right_value = right[args["metric"]]
        winning_stream_id = None
        if left_value is not None and right_value is not None:
            winning_stream_id = args["left_stream_id"] if left_value > right_value else args["right_stream_id"]
        return {
            "metric": args["metric"],
            "left_stream_id": args["left_stream_id"],
            "right_stream_id": args["right_stream_id"],
            "left_value": left_value,
            "right_value": right_value,
            "winning_stream_id": winning_stream_id,
            "window_start": pd.Timestamp(args["window_start"]).isoformat(),
            "window_end": pd.Timestamp(args["window_end"]).isoformat(),
        }

    def rank_window(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = []
        for stream_id in args["stream_ids"]:
            stats = self.aggregate_window(
                {
                    "stream_id": stream_id,
                    "metric": args["metric"],
                    "window_start": args["window_start"],
                    "window_end": args["window_end"],
                    "period": args.get("period"),
                }
            )
            value = stats.get(args["metric"])
            if value is None:
                continue
            rows.append({"stream_id": stream_id, args["metric"]: value})
        reverse = args.get("order", "desc") == "desc"
        rows.sort(key=lambda row: (row[args["metric"]], row["stream_id"]), reverse=reverse)
        topk = int(args.get("topk", 1))
        return {
            "metric": args["metric"],
            "window_start": pd.Timestamp(args["window_start"]).isoformat(),
            "window_end": pd.Timestamp(args["window_end"]).isoformat(),
            "order": args.get("order", "desc"),
            "topk": topk,
            "ranked_streams": rows[:topk],
        }

    def lookup_observation(self, args: dict[str, Any]) -> dict[str, Any]:
        history = self._load_history_for_stream(args["stream_id"]).copy()
        target = pd.Timestamp(args["timestamp"])
        timestamp_series = pd.to_datetime(history["timestamp"], utc=True)
        exact_matches = history.loc[timestamp_series == target]
        mode = args.get("mode", "exact")
        if mode == "exact":
            if exact_matches.empty:
                return {
                    "stream_id": args["stream_id"],
                    "requested_timestamp": target.isoformat(),
                    "exact_match_found": False,
                }
            row = exact_matches.iloc[0]
            return {
                "stream_id": args["stream_id"],
                "requested_timestamp": target.isoformat(),
                "observed_timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
                "value": round(float(row["value"]), 4),
                "exact_match_found": True,
            }

        history["delta_seconds"] = (timestamp_series - target).abs().dt.total_seconds()
        best = history.sort_values(["delta_seconds", "timestamp"]).iloc[0]
        return {
            "stream_id": args["stream_id"],
            "requested_timestamp": target.isoformat(),
            "observed_timestamp": pd.Timestamp(best["timestamp"]).isoformat(),
            "value": round(float(best["value"]), 4),
            "exact_match_found": bool(not exact_matches.empty),
            "fallback_reason": "nearest_available_observation",
            "offset_seconds": round(float(best["delta_seconds"]), 3),
        }

    def inspect_quality(self, args: dict[str, Any]) -> dict[str, Any]:
        row = self._one(
            """
            select
                observed_fraction,
                longest_gap_seconds / nullif(median_step_seconds, 0) as gap_ratio,
                duplicate_timestamp_fraction,
                nan_fraction
            from quality_metrics
            where stream_id = ?
            """,
            (args["stream_id"],),
        )
        if row is None:
            raise KeyError(f"No quality metrics found for stream {args['stream_id']}")
        return {
            "stream_id": args["stream_id"],
            "observed_fraction": round(float(row[0]), 4) if row[0] is not None else None,
            "gap_ratio": round(float(row[1]), 4) if row[1] is not None else None,
            "duplicate_timestamp_fraction": round(float(row[2]), 4) if row[2] is not None else None,
            "nan_fraction": round(float(row[3]), 4) if row[3] is not None else None,
            "quality_reference": self.quality_reference(),
        }

    def inspect_quality_window(self, args: dict[str, Any]) -> dict[str, Any]:
        history = self._load_history_for_stream(args["stream_id"]).copy()
        window_start = pd.Timestamp(args["window_start"])
        window_end = pd.Timestamp(args["window_end"])
        if window_start.tzinfo is None:
            window_start = window_start.tz_localize("UTC")
        else:
            window_start = window_start.tz_convert("UTC")
        if window_end.tzinfo is None:
            window_end = window_end.tz_localize("UTC")
        else:
            window_end = window_end.tz_convert("UTC")

        timestamp_series = pd.to_datetime(history["timestamp"], utc=True)
        window = history.loc[(timestamp_series >= window_start) & (timestamp_series < window_end)].copy()
        period = str(args.get("period", "week"))
        if window.empty:
            return {
                "stream_id": args["stream_id"],
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "period": period,
                "observed_fraction": None,
                "gap_ratio": None,
                "duplicate_timestamp_fraction": None,
                "nan_fraction": None,
                "quality_reference": self.window_quality_reference(period),
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
            "stream_id": args["stream_id"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "period": period,
            "observed_fraction": round(float(metrics["observed_fraction"]), 4) if metrics.get("observed_fraction") is not None else None,
            "gap_ratio": round(float(gap_ratio), 4) if gap_ratio is not None else None,
            "duplicate_timestamp_fraction": round(float(metrics["duplicate_timestamp_fraction"]), 4) if metrics.get("duplicate_timestamp_fraction") is not None else None,
            "nan_fraction": round(float(metrics["nan_fraction"]), 4) if metrics.get("nan_fraction") is not None else None,
            "quality_reference": self.window_quality_reference(period),
        }

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "resolve_point":
            return self.resolve_point(arguments)
        if tool_name == "list_points":
            return self.list_points(arguments)
        if tool_name == "aggregate_window":
            return self.aggregate_window(arguments)
        if tool_name == "compare_window":
            return self.compare_window(arguments)
        if tool_name == "rank_window":
            return self.rank_window(arguments)
        if tool_name == "lookup_observation":
            return self.lookup_observation(arguments)
        if tool_name == "inspect_quality":
            return self.inspect_quality(arguments)
        if tool_name == "inspect_quality_window":
            return self.inspect_quality_window(arguments)
        raise KeyError(f"Unknown tool {tool_name}")

    def execute_call_sequence(self, calls: list[dict[str, Any]]) -> list[ExecutedCall]:
        call_outputs: dict[str, dict[str, Any]] = {}
        executed: list[ExecutedCall] = []
        for idx, call in enumerate(calls, start=1):
            call_id = call.get("call_id", f"c{idx}")
            resolved_args = self._replace_refs(call["arguments"], call_outputs)
            output = self.execute_tool(call["tool_name"], resolved_args)
            output = self._normalize_value(output)
            call_outputs[call_id] = output
            executed.append(
                ExecutedCall(
                    call_id=call_id,
                    tool_name=call["tool_name"],
                    arguments=self._normalize_value(resolved_args),
                    output=output,
                )
            )
        return executed

    @staticmethod
    def compact_observation(executed_call: ExecutedCall) -> str:
        payload = {
            "call_id": executed_call.call_id,
            "tool_name": executed_call.tool_name,
            "output": executed_call.output,
        }
        return json.dumps(payload, ensure_ascii=False)
