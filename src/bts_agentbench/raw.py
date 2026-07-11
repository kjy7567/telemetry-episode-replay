from __future__ import annotations

import pickle
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from .catalog import normalize_stream_id


def iter_zip_pickles(zip_path: Path):
    with ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if member.endswith(".pickle"):
                yield archive, member


def payload_stream_id(token: object) -> str | None:
    return normalize_stream_id(token)


def build_raw_index(zip_path: Path, output_path: Path) -> pd.DataFrame:
    rows = []
    with ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if not member.endswith(".pickle"):
                continue
            with archive.open(member) as handle:
                payload = pickle.load(handle)
            token = payload[0] if isinstance(payload, (list, tuple)) and payload else None
            stream_id = payload_stream_id(token) or payload_stream_id(member)
            if not stream_id:
                continue
            timestamps = pd.to_datetime(payload[1], utc=True)
            values = pd.to_numeric(payload[2], errors="coerce")
            rows.append(
                {
                    "zip_path": str(zip_path),
                    "member_name": member,
                    "stream_id": stream_id,
                    "n_points": int(len(values)),
                    "first_timestamp": timestamps.min(),
                    "last_timestamp": timestamps.max(),
                }
            )
    index_frame = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    index_frame.to_parquet(output_path, index=False)
    return index_frame


def extract_stream_history(zip_path: Path, stream_id: str, member_name: str | None = None) -> pd.DataFrame:
    stream_id = normalize_stream_id(stream_id)
    if not stream_id:
        raise ValueError("Invalid stream_id")
    with ZipFile(zip_path) as archive:
        target_member = member_name
        if target_member is None:
            for candidate in archive.namelist():
                if not candidate.endswith(".pickle"):
                    continue
                with archive.open(candidate) as handle:
                    payload = pickle.load(handle)
                token = payload[0] if isinstance(payload, (list, tuple)) and payload else None
                candidate_stream = payload_stream_id(token) or payload_stream_id(candidate)
                if candidate_stream == stream_id:
                    target_member = candidate
                    break
        if target_member is None:
            raise KeyError(f"Could not locate stream {stream_id} in {zip_path}")
        with archive.open(target_member) as handle:
            payload = pickle.load(handle)
    timestamps = pd.to_datetime(payload[1], utc=True)
    values = pd.to_numeric(payload[2], errors="coerce")
    return pd.DataFrame({"timestamp": timestamps, "value": values})
