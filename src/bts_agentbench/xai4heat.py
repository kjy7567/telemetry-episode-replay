from __future__ import annotations

from pathlib import Path

import pandas as pd


POINT_MAP = {
    "t_amb": {
        "point_class": "Outdoor_Temperature_Sensor",
        "point_label": "Outdoor ambient temperature",
    },
    "t_ref": {
        "point_class": "Reference_Temperature_Setpoint",
        "point_label": "Reference temperature setpoint",
    },
    "t_sup_prim": {
        "point_class": "Primary_Supply_Temperature_Sensor",
        "point_label": "Primary supply temperature",
    },
    "t_ret_prim": {
        "point_class": "Primary_Return_Temperature_Sensor",
        "point_label": "Primary return temperature",
    },
    "t_sup_sec": {
        "point_class": "Secondary_Supply_Temperature_Sensor",
        "point_label": "Secondary supply temperature",
    },
    "t_ret_sec": {
        "point_class": "Secondary_Return_Temperature_Sensor",
        "point_label": "Secondary return temperature",
    },
    "delta_e": {
        "point_class": "Energy_Transfer_Sensor",
        "point_label": "Hourly energy transfer",
    },
}


def load_xai4heat_tables(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw_dir = Path(raw_dir)
    area = pd.read_csv(raw_dir / "xai4heat_heating_area.csv")
    area["site_id"] = area["substation_id"].map(lambda sid: f"XAI4HEAT_{sid}")
    area_lookup = area.set_index("substation_id")["heating_area_m2"].to_dict()

    metadata_rows: list[dict] = []
    observation_frames: list[pd.DataFrame] = []
    for csv_path in sorted(raw_dir.glob("xai4heat_scada_*_processed.csv")):
        substation_id = csv_path.stem.split("_")[2]
        site_id = f"XAI4HEAT_{substation_id}"
        equipment_label = f"XAI4HEAT Substation {substation_id}"
        frame = pd.read_csv(csv_path)
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
        for column, spec in POINT_MAP.items():
            stream_id = f"{site_id}__{column}"
            metadata_rows.append(
                {
                    "site_id": site_id,
                    "stream_id": stream_id,
                    "point_class": spec["point_class"],
                    "point_type": spec["point_class"],
                    "point_label": f"{equipment_label} {spec['point_label']}",
                    "equipment_type": "Heating_Substation",
                    "equipment_label": equipment_label,
                    "location_type": "District_Heating_Substation",
                    "location_label": substation_id,
                    "heating_area_m2": area_lookup.get(substation_id),
                }
            )
            obs = frame[["datetime", column]].rename(columns={"datetime": "timestamp", column: "value"}).copy()
            obs["site_id"] = site_id
            obs["stream_id"] = stream_id
            observation_frames.append(obs[["site_id", "stream_id", "timestamp", "value"]])
    metadata = pd.DataFrame(metadata_rows).drop_duplicates(["site_id", "stream_id"]).reset_index(drop=True)
    observations = pd.concat(observation_frames, ignore_index=True)
    return metadata, observations
