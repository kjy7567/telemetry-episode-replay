from __future__ import annotations

from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


BLOCK_ID_MAP = {
    "335546928": "PLEIA_A",
    "335546926": "PLEIA_B",
    "335546927": "PLEIA_C",
}
BLOCK_CODE_MAP = {
    "A": "PLEIA_A",
    "B": "PLEIA_B",
    "C": "PLEIA_C",
}


def _find_member(archive: ZipFile, *basenames: str) -> str:
    candidates = {name.lower() for name in basenames}
    for member in archive.namelist():
        if Path(member).name.lower() in candidates:
            return member
    raise FileNotFoundError(f"Could not find any of {basenames} in archive")


def _read_csv(archive: ZipFile, member: str, **kwargs) -> pd.DataFrame:
    with archive.open(member) as handle:
        return pd.read_csv(handle, sep=";", **kwargs)


def _iter_csv_chunks(archive: ZipFile, member: str, **kwargs):
    with archive.open(member) as handle:
        text = TextIOWrapper(handle, encoding="utf-8")
        yield from pd.read_csv(text, sep=";", chunksize=kwargs.pop("chunksize"), **kwargs)


def _room_label(block: str, room: object) -> str:
    room_text = str(room).strip()
    return f"{block} Room {room_text}"


def _site_alias(block: object) -> str | None:
    block_text = str(block).strip()
    return BLOCK_ID_MAP.get(block_text) or BLOCK_CODE_MAP.get(block_text)


def _aggregate_hourly(frame: pd.DataFrame, value_col: str, extra_group_cols: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["Date"], utc=True, errors="coerce").dt.floor("h")
    frame = frame.dropna(subset=["timestamp", value_col])
    grouped = (
        frame.groupby(extra_group_cols + ["timestamp"], as_index=False)[value_col]
        .mean()
        .rename(columns={value_col: "value"})
    )
    return grouped


def load_pleia_tables(zip_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    zip_path = Path(zip_path)
    metadata_rows: list[dict] = []
    observation_frames: list[pd.DataFrame] = []
    with ZipFile(zip_path) as archive:
        cons_member = _find_member(archive, "data-cons.csv")
        sensor_member = _find_member(archive, "data-sensor.csv")
        hvac_member = _find_member(archive, "data-hvac.csv")
        rel_sensor_member = _find_member(
            archive,
            "relation-sensor.csv",
            "relation-sensors.csv",
            "relations-sensor.csv",
            "relations-sensors.csv",
        )
        rel_hvac_member = _find_member(
            archive,
            "relation-hvac.csv",
            "relations-hvac.csv",
        )

        rel_sensor = _read_csv(archive, rel_sensor_member)
        rel_sensor.columns = [str(c).strip() for c in rel_sensor.columns]
        rel_sensor["site_id"] = rel_sensor["block"].map(_site_alias)
        rel_sensor = rel_sensor.dropna(subset=["site_id"]).copy()
        rel_sensor["equipment_label"] = rel_sensor.apply(lambda row: _room_label(str(row["site_id"]), row["room"]), axis=1)
        rel_sensor = rel_sensor.rename(columns={"ID": "IDdevice"})

        rel_hvac = _read_csv(archive, rel_hvac_member)
        rel_hvac.columns = [str(c).strip() for c in rel_hvac.columns]
        rel_hvac["site_id"] = rel_hvac["block"].map(_site_alias)
        rel_hvac = rel_hvac.dropna(subset=["site_id"]).copy()
        rel_hvac["equipment_label"] = rel_hvac.apply(lambda row: _room_label(str(row["site_id"]), row["room"]), axis=1)
        rel_hvac = rel_hvac.rename(columns={"ID": "IDdevice"})

        # Consumption by block.
        for chunk in _iter_csv_chunks(archive, cons_member, usecols=["IDdevice", "Date", "V22"], chunksize=200_000):
            chunk["site_id"] = chunk["IDdevice"].astype(str).map(_site_alias)
            chunk = chunk.dropna(subset=["site_id"])
            grouped = _aggregate_hourly(chunk, "V22", ["site_id"])
            grouped["stream_id"] = grouped["site_id"] + "__block_consumption"
            observation_frames.append(grouped[["site_id", "stream_id", "timestamp", "value"]])
        for site_id in sorted(BLOCK_ID_MAP.values()):
            metadata_rows.append(
                {
                    "site_id": site_id,
                    "stream_id": f"{site_id}__block_consumption",
                    "point_class": "Block_Energy_Consumption_Sensor",
                    "point_type": "Block_Energy_Consumption_Sensor",
                    "point_label": f"{site_id} total HVAC energy consumption",
                    "equipment_type": "Building_Block",
                    "equipment_label": site_id,
                    "location_type": "Building_Block",
                    "location_label": site_id,
                }
            )

        # Indoor temperature sensors.
        sensor_meta_ids: set[str] = set()
        for chunk in _iter_csv_chunks(archive, sensor_member, usecols=["IDdevice", "Date", "V2"], chunksize=500_000):
            chunk = chunk.merge(rel_sensor[["IDdevice", "site_id", "equipment_label", "room"]], on="IDdevice", how="inner")
            grouped = _aggregate_hourly(chunk, "V2", ["site_id", "IDdevice"])
            grouped["stream_id"] = grouped["site_id"] + "__temp__" + grouped["IDdevice"].astype(str)
            observation_frames.append(grouped[["site_id", "stream_id", "timestamp", "value"]])
            for row in chunk[["site_id", "IDdevice", "equipment_label", "room"]].drop_duplicates().to_dict(orient="records"):
                key = str(row["IDdevice"])
                if key in sensor_meta_ids:
                    continue
                sensor_meta_ids.add(key)
                metadata_rows.append(
                    {
                        "site_id": row["site_id"],
                        "stream_id": f"{row['site_id']}__temp__{row['IDdevice']}",
                        "point_class": "Indoor_Temperature_Sensor",
                        "point_type": "Indoor_Temperature_Sensor",
                        "point_label": f"{row['equipment_label']} indoor temperature",
                        "equipment_type": "Room",
                        "equipment_label": row["equipment_label"],
                        "location_type": "Room",
                        "location_label": str(row["room"]),
                    }
                )

        # HVAC state and setpoint.
        hvac_meta_ids: set[str] = set()
        for chunk in _iter_csv_chunks(
            archive,
            hvac_member,
            usecols=["IDdevice", "Date", "V4", "V12"],
            chunksize=400_000,
        ):
            chunk = chunk.merge(rel_hvac[["IDdevice", "site_id", "equipment_label", "room"]], on="IDdevice", how="inner")
            state_grouped = _aggregate_hourly(chunk, "V4", ["site_id", "IDdevice"])
            state_grouped["stream_id"] = state_grouped["site_id"] + "__hvac_state__" + state_grouped["IDdevice"].astype(str)
            observation_frames.append(state_grouped[["site_id", "stream_id", "timestamp", "value"]])

            setpoint = chunk.dropna(subset=["V12"]).copy()
            if not setpoint.empty:
                set_grouped = _aggregate_hourly(setpoint, "V12", ["site_id", "IDdevice"])
                set_grouped["stream_id"] = set_grouped["site_id"] + "__hvac_setpoint__" + set_grouped["IDdevice"].astype(str)
                observation_frames.append(set_grouped[["site_id", "stream_id", "timestamp", "value"]])

            for row in chunk[["site_id", "IDdevice", "equipment_label", "room"]].drop_duplicates().to_dict(orient="records"):
                key = str(row["IDdevice"])
                if key in hvac_meta_ids:
                    continue
                hvac_meta_ids.add(key)
                metadata_rows.extend(
                    [
                        {
                            "site_id": row["site_id"],
                            "stream_id": f"{row['site_id']}__hvac_state__{row['IDdevice']}",
                            "point_class": "HVAC_State_Sensor",
                            "point_type": "HVAC_State_Sensor",
                            "point_label": f"{row['equipment_label']} HVAC state",
                            "equipment_type": "HVAC_Unit",
                            "equipment_label": row["equipment_label"],
                            "location_type": "Room",
                            "location_label": str(row["room"]),
                        },
                        {
                            "site_id": row["site_id"],
                            "stream_id": f"{row['site_id']}__hvac_setpoint__{row['IDdevice']}",
                            "point_class": "HVAC_Setpoint_Temperature_Setpoint",
                            "point_type": "HVAC_Setpoint_Temperature_Setpoint",
                            "point_label": f"{row['equipment_label']} HVAC setpoint",
                            "equipment_type": "HVAC_Unit",
                            "equipment_label": row["equipment_label"],
                            "location_type": "Room",
                            "location_label": str(row["room"]),
                        },
                    ]
                )

    metadata = pd.DataFrame(metadata_rows).drop_duplicates(["site_id", "stream_id"]).reset_index(drop=True)
    observations = pd.concat(observation_frames, ignore_index=True)
    observations = observations.dropna(subset=["site_id", "stream_id", "timestamp", "value"]).reset_index(drop=True)
    return metadata, observations
