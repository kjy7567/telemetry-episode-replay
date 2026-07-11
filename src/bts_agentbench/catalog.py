from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF


GENERIC_TYPES = {
    "NamedIndividual",
    "Thing",
    "Entity",
    "Point",
}

STREAM_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{12}"
)

CANONICAL_COLUMN_MAP = {
    "stream_id": [
        "stream_id",
        "streamid",
        "stream_guid",
        "stream_uuid",
        "stream_guid_",
        "uuid",
        "point_uuid",
    ],
    "point_name": [
        "point_name",
        "pointname",
        "parameter_name",
        "parameter",
        "bacnet_name",
        "name",
        "display_name",
        "label",
    ],
    "point_class": [
        "point_class",
        "brick_class",
        "brick",
        "point_type",
        "entity_type",
        "type",
        "class",
    ],
    "unit": ["unit", "units"],
    "count": ["count", "n", "points", "num_points"],
    "mean_value": ["mean", "mean_value", "avg", "average"],
    "std_value": ["std", "std_value", "standard_deviation"],
    "min_value": ["min", "min_value", "minimum"],
    "max_value": ["max", "max_value", "maximum"],
    "first_timestamp": ["first_timestamp", "start_timestamp", "min_timestamp", "first_ts", "first_t"],
    "last_timestamp": ["last_timestamp", "end_timestamp", "max_timestamp", "last_ts", "last_t"],
}


def snake_case(name: str) -> str:
    value = name.strip()
    value = re.sub(r"[^0-9a-zA-Z]+", "_", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_").lower()


def normalize_stream_id(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    match = STREAM_ID_RE.search(str(value))
    if not match:
        return None
    return match.group(0).replace("-", "_").lower()


def local_name(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if "#" in text:
        text = text.rsplit("#", 1)[-1]
    elif "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text or None


def title_case_identifier(value: str | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.title()


def infer_site_id(path: Path) -> str:
    stem = path.stem
    match = re.search(r"site[_-]?([abc])", stem, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not infer site id from {path}")
    return f"BTS_{match.group(1).upper()}"


def first_existing(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def load_metadata(meta_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(meta_dir.glob("Site_*_metadata.csv")):
        site_id = infer_site_id(path)
        frame = pd.read_csv(path, low_memory=False)
        frame.columns = [snake_case(col) for col in frame.columns]
        frame["site_id"] = site_id
        frame["source_csv"] = path.name

        for canonical_name, candidates in CANONICAL_COLUMN_MAP.items():
            existing = first_existing(frame, [snake_case(name) for name in candidates])
            if existing:
                frame[canonical_name] = frame[existing]
            elif canonical_name not in frame.columns:
                frame[canonical_name] = None

        frame["stream_id"] = frame["stream_id"].map(normalize_stream_id)
        for numeric_col in ["count", "mean_value", "std_value", "min_value", "max_value"]:
            frame[numeric_col] = pd.to_numeric(frame[numeric_col], errors="coerce")
        for ts_col in ["first_timestamp", "last_timestamp"]:
            frame[ts_col] = pd.to_datetime(frame[ts_col], errors="coerce", utc=True)

        frame["point_label"] = frame["point_name"].fillna(frame["point_class"]).fillna(frame["stream_id"])
        frames.append(frame)

    if not frames:
        raise FileNotFoundError(f"No metadata CSV files found in {meta_dir}")

    streams = pd.concat(frames, ignore_index=True)
    streams = streams[streams["stream_id"].notna()].copy()
    streams["point_label"] = streams["point_label"].astype(str)
    return streams


def choose_primary_type(type_names: list[str]) -> str | None:
    filtered = [name for name in type_names if name not in GENERIC_TYPES]
    pool = filtered or type_names
    return sorted(pool)[0] if pool else None


def parse_site_graphs(meta_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    entity_records: list[dict] = []
    relation_records: list[dict] = []
    target_records: list[dict] = []
    parse_issues: list[dict] = []

    for ttl_path in sorted(meta_dir.glob("Site_*.ttl")):
        site_id = infer_site_id(ttl_path)
        graph = Graph()
        try:
            graph.parse(ttl_path)
        except Exception as exc:  # pragma: no cover - depends on remote file integrity
            parse_issues.append(
                {
                    "site_id": site_id,
                    "ttl_file": ttl_path.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue

        type_map: defaultdict[str, list[str]] = defaultdict(list)
        outgoing: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
        incoming: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
        stream_to_point: dict[str, str] = {}
        all_entities: set[str] = set()

        triples = sorted(graph, key=lambda triple: tuple(str(part) for part in triple))
        for subject, predicate, obj in triples:
            if isinstance(subject, URIRef):
                all_entities.add(str(subject))
            if isinstance(obj, URIRef):
                all_entities.add(str(obj))

            if predicate == RDF.type and isinstance(subject, URIRef) and isinstance(obj, URIRef):
                type_map[str(subject)].append(local_name(obj))
                continue

            pred_name = local_name(predicate)
            if pred_name == "stream_id" and isinstance(subject, URIRef) and isinstance(obj, Literal):
                stream_id = normalize_stream_id(str(obj))
                if stream_id:
                    stream_to_point[stream_id] = str(subject)
                continue

            if isinstance(subject, URIRef) and isinstance(obj, URIRef):
                source_entity = str(subject)
                target_entity = str(obj)
                outgoing[source_entity].append((pred_name, target_entity))
                incoming[target_entity].append((pred_name, source_entity))
                relation_records.append(
                    {
                        "site_id": site_id,
                        "source_entity": source_entity,
                        "predicate": pred_name,
                        "target_entity": target_entity,
                    }
                )

        for entity_id in sorted(all_entities):
            type_names = sorted({name for name in type_map.get(entity_id, []) if name})
            primary_type = choose_primary_type(type_names)
            entity_records.append(
                {
                    "site_id": site_id,
                    "entity_id": entity_id,
                    "entity_local_name": local_name(entity_id),
                    "entity_label": title_case_identifier(local_name(entity_id)),
                    "primary_type": primary_type,
                    "all_types_json": json.dumps(type_names),
                }
            )

        for stream_id, point_entity in sorted(stream_to_point.items()):
            direct_equipment = sorted({
                target for pred, target in outgoing.get(point_entity, []) if pred in {"isPointOf", "hasPoint"}
            })
            incoming_equipment = sorted({
                source for pred, source in incoming.get(point_entity, []) if pred in {"hasPoint", "isPointOf"}
            })
            equipment_entity = (direct_equipment or incoming_equipment or [None])[0]

            direct_location = sorted({
                target
                for pred, target in outgoing.get(point_entity, [])
                if pred in {"hasLocation", "isLocatedIn", "locatedIn"}
            })
            inherited_location = []
            if equipment_entity:
                inherited_location = sorted({
                    target
                    for pred, target in outgoing.get(equipment_entity, [])
                    if pred in {"hasLocation", "isLocatedIn", "locatedIn"}
                })
            location_entity = (direct_location or inherited_location or [None])[0]

            point_types = type_map.get(point_entity, [])
            equipment_types = type_map.get(equipment_entity, []) if equipment_entity else []
            location_types = type_map.get(location_entity, []) if location_entity else []
            target_records.append(
                {
                    "site_id": site_id,
                    "stream_id": stream_id,
                    "point_entity": point_entity,
                    "point_label": title_case_identifier(local_name(point_entity)),
                    "point_type": choose_primary_type(point_types),
                    "equipment_entity": equipment_entity,
                    "equipment_label": title_case_identifier(local_name(equipment_entity)),
                    "equipment_type": choose_primary_type(equipment_types),
                    "location_entity": location_entity,
                    "location_label": title_case_identifier(local_name(location_entity)),
                    "location_type": choose_primary_type(location_types),
                }
            )

    entities = pd.DataFrame(entity_records)
    relations = pd.DataFrame(relation_records)
    if not relations.empty:
        relations = relations.sort_values(
            ["site_id", "source_entity", "predicate", "target_entity"]
        ).reset_index(drop=True)
    stream_targets = pd.DataFrame(target_records)
    return entities, relations, stream_targets, parse_issues


def build_catalog(meta_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    streams = load_metadata(meta_dir)
    entities, relations, stream_targets, parse_issues = parse_site_graphs(meta_dir)
    if not entities.empty:
        entities = entities.sort_values(["site_id", "primary_type", "entity_id"]).copy()
        entities["primary_type"] = entities["primary_type"].fillna("Entity")
        entities["type_rank"] = entities.groupby(["site_id", "primary_type"]).cumcount() + 1
        entities["entity_alias"] = entities.apply(
            lambda row: (
                f"{row.site_id} {title_case_identifier(row.primary_type) or 'Entity'} {int(row.type_rank):03d}"
            ),
            axis=1,
        )

    streams = streams.merge(
        stream_targets,
        on=["site_id", "stream_id"],
        how="left",
        suffixes=("", "_graph"),
    )
    streams["point_label"] = streams["point_name"].fillna(streams["point_label_graph"]).fillna(streams["stream_id"])
    streams["equipment_label"] = streams["equipment_label"].fillna(streams["equipment_type"])
    streams["location_label"] = streams["location_label"].fillna(streams["location_type"])

    if not stream_targets.empty and not entities.empty:
        alias_map = entities[["site_id", "entity_id", "entity_alias"]]
        stream_targets = stream_targets.merge(
            alias_map.rename(columns={"entity_id": "point_entity", "entity_alias": "point_alias"}),
            on=["site_id", "point_entity"],
            how="left",
        )
        stream_targets = stream_targets.merge(
            alias_map.rename(columns={"entity_id": "equipment_entity", "entity_alias": "equipment_alias"}),
            on=["site_id", "equipment_entity"],
            how="left",
        )
        stream_targets = stream_targets.merge(
            alias_map.rename(columns={"entity_id": "location_entity", "entity_alias": "location_alias"}),
            on=["site_id", "location_entity"],
            how="left",
        )
        streams = streams.drop(
            columns=[
                col
                for col in ["point_entity", "point_label_graph", "point_type", "equipment_entity", "equipment_label", "equipment_type", "location_entity", "location_label", "location_type"]
                if col in streams.columns
            ]
        ).merge(
            stream_targets,
            on=["site_id", "stream_id"],
            how="left",
            suffixes=("", "_graph2"),
        )
        streams["equipment_label"] = streams["equipment_alias"].fillna(streams["equipment_label"])
        streams["location_label"] = streams["location_alias"].fillna(streams["location_label"])
        streams["point_label"] = streams["point_alias"].fillna(streams["point_label"])

    streams_path = out_dir / "streams.parquet"
    entities_path = out_dir / "entities.parquet"
    relations_path = out_dir / "relations.parquet"
    targets_path = out_dir / "stream_targets.parquet"
    summary_path = out_dir / "catalog_summary.json"
    duckdb_path = out_dir / "bts_catalog.duckdb"

    streams.to_parquet(streams_path, index=False)
    entities.to_parquet(entities_path, index=False)
    relations.to_parquet(relations_path, index=False)
    stream_targets.to_parquet(targets_path, index=False)

    con = duckdb.connect(str(duckdb_path))
    try:
        con.execute(f"CREATE OR REPLACE TABLE streams AS SELECT * FROM read_parquet('{streams_path.as_posix()}')")
        con.execute(f"CREATE OR REPLACE TABLE entities AS SELECT * FROM read_parquet('{entities_path.as_posix()}')")
        con.execute(f"CREATE OR REPLACE TABLE relations AS SELECT * FROM read_parquet('{relations_path.as_posix()}')")
        con.execute(f"CREATE OR REPLACE TABLE stream_targets AS SELECT * FROM read_parquet('{targets_path.as_posix()}')")
    finally:
        con.close()

    summary = {
        "sites": sorted(streams["site_id"].dropna().unique().tolist()),
        "stream_count": int(streams["stream_id"].nunique()),
        "entity_count": int(len(entities)),
        "relation_count": int(len(relations)),
        "streams_per_site": {
            site: int(value) for site, value in streams.groupby("site_id")["stream_id"].nunique().items()
        },
        "graph_parse_issues": parse_issues,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
