#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
EXPECTED_ROWS = {
    "bts": {"train": 356, "dev": 87, "test": 89},
    "xai4heat": {"train": 132, "dev": 31, "test": 41},
}
MODEL_RUNS = {
    "gpt-5.5": ("bts", 89),
    "gemini-3.1-pro-openrouter": ("bts", 89),
    "claude-opus-4.7-openrouter": ("bts", 89),
    "gpt-5.5-xai4heat": ("xai4heat", 41),
}
RAW_ARCHIVES = {
    "Site_Aaa.zip": {
        "bytes": 8_475_679_488,
        "sha256": "ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f",
    },
    "Site_Baa.zip": {
        "bytes": 1_513_172_125,
        "sha256": "fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8",
    },
    "Site_Caa.zip": {
        "bytes": 8_984_334_527,
        "sha256": "fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b",
    },
}
INTERNAL_LABEL = re.compile(
    r"(?:\b(?:canonical|dal)-v\d+\b|\bv\d+\b|"
    r"[A-Za-z][A-Za-z0-9_-]*[_-]v\d+[A-Za-z0-9_-]*)",
    re.IGNORECASE,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def file_record(path: Path, *, rows: int | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"missing release file: {path}")
    record: dict[str, Any] = {
        "path": relative(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if rows is not None:
        record["rows"] = rows
    return record


def split_records(root: Path, expected: dict[str, int]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    records: dict[str, Any] = {}
    rows_by_split: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for split in SPLITS:
        path = root / f"{split}.jsonl"
        rows = load_jsonl(path)
        if len(rows) != expected[split]:
            raise RuntimeError(f"unexpected row count for {path}: {len(rows)}")
        ids = [str(row["scenario_id"]) for row in rows]
        if len(ids) != len(set(ids)) or seen.intersection(ids):
            raise RuntimeError(f"duplicate scenario ID in {path}")
        if any(str(row.get("split")) != split for row in rows):
            raise RuntimeError(f"split field mismatch in {path}")
        seen.update(ids)
        records[split] = file_record(path, rows=len(rows))
        rows_by_split[split] = rows
    return records, rows_by_split


def family_counts(rows_by_split: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for rows in rows_by_split.values():
        counts.update(str(row["task_family"]) for row in rows)
    return dict(sorted(counts.items()))


def assert_clean_records(roots: list[Path]) -> None:
    findings: list[str] = []
    for root in roots:
        for path in sorted([*root.rglob("*.json"), *root.rglob("*.jsonl")]):
            text = path.read_text(encoding="utf-8")
            if INTERNAL_LABEL.search(text):
                findings.append(f"numeric internal label: {relative(path)}")
    if findings:
        raise RuntimeError("unclean release records: " + "; ".join(findings[:20]))


def corpus_record(name: str, final_root: Path, static_root: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    final_records, final_rows = split_records(final_root, EXPECTED_ROWS[name])
    static_records, static_rows = split_records(static_root, EXPECTED_ROWS[name])
    for split in SPLITS:
        final_ids = [str(row["scenario_id"]) for row in final_rows[split]]
        static_ids = [str(row["scenario_id"]) for row in static_rows[split]]
        if final_ids != static_ids:
            raise RuntimeError(f"static/final scenario order mismatch: {name}/{split}")
    preflight_path = final_root / "contract_preflight_report.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if int(preflight["issue_count"]) != 0:
        raise RuntimeError(f"contract preflight failed: {name}")
    return (
        {
            "episode_rows": sum(EXPECTED_ROWS[name].values()),
            "episode_splits": final_records,
            "static_task_splits": static_records,
            "families": family_counts(final_rows),
            "contract_preflight": file_record(preflight_path),
        },
        final_rows,
    )


def controller_record(
    corpus: str,
    rows_by_split: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    prefix = "bts" if corpus == "bts" else "xai4heat"
    audit_path = REPO_ROOT / "replay" / f"{prefix}_controller_audit.json"
    failure_path = REPO_ROOT / "replay" / f"{prefix}_controller_failure_analysis.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    expected_total = (
        sum(len(rows) for rows in rows_by_split.values())
        if corpus == "bts"
        else len(rows_by_split["test"])
    )
    if int(audit["totals"]["scenario_count"]) != expected_total:
        raise RuntimeError(f"controller scenario count mismatch: {corpus}")
    if int(audit["totals"]["accomplished_count"]) != 0:
        raise RuntimeError(f"controller accomplished a release row: {corpus}")
    witness_splits = SPLITS if corpus == "bts" else ("test",)
    witnesses: dict[str, Any] = {}
    for split in witness_splits:
        path = REPO_ROOT / "replay" / f"{prefix}-controller-witnesses" / split / "phase_complete_stronger_controller.jsonl"
        rows = load_jsonl(path)
        expected_ids = {str(row["scenario_id"]) for row in rows_by_split[split]}
        if {str(row["scenario_id"]) for row in rows} != expected_ids:
            raise RuntimeError(f"controller witness coverage mismatch: {corpus}/{split}")
        if any(row.get("label") == "accomplished" for row in rows):
            raise RuntimeError(f"controller witness accomplished a row: {corpus}/{split}")
        witnesses[split] = file_record(path, rows=len(rows))
    return {
        "audit": file_record(audit_path),
        "failure_analysis": file_record(failure_path),
        "witnesses": witnesses,
        "accomplished": 0,
        "evaluated_rows": expected_total,
    }


def model_records(corpus_rows: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for run_name, (corpus, expected_count) in MODEL_RUNS.items():
        root = REPO_ROOT / "reports" / "model-runs" / run_name
        traces = load_jsonl(root / "test.jsonl")
        expected_ids = {str(row["scenario_id"]) for row in corpus_rows[corpus]["test"]}
        if len(traces) != expected_count or {str(row["scenario_id"]) for row in traces} != expected_ids:
            raise RuntimeError(f"model trace coverage mismatch: {run_name}")
        records[run_name] = {
            "trace": file_record(root / "test.jsonl", rows=len(traces)),
            "summary": file_record(root / "summary.json"),
            "run_config": file_record(root / "run_config.json"),
            "accomplished": sum(row.get("label") == "accomplished" for row in traces),
        }
        family_summary = root / "family_summary.json"
        if family_summary.is_file():
            records[run_name]["family_summary"] = file_record(family_summary)
    return records


def build_manifest() -> dict[str, Any]:
    bts, bts_rows = corpus_record(
        "bts",
        REPO_ROOT / "artifacts" / "bts-agentbench",
        REPO_ROOT / "artifacts" / "bts-static-tasks",
    )
    xai, xai_rows = corpus_record(
        "xai4heat",
        REPO_ROOT / "artifacts" / "xai4heat-agentbench",
        REPO_ROOT / "artifacts" / "xai4heat-static-tasks",
    )
    selection_path = REPO_ROOT / "provenance" / "release_static_selection.jsonl"
    selection_rows = load_jsonl(selection_path)
    if {str(row["scenario_id"]) for row in selection_rows} != {
        str(row["scenario_id"]) for rows in bts_rows.values() for row in rows
    }:
        raise RuntimeError("release selection contract does not cover the BTS static release")

    assert_clean_records(
        [
            REPO_ROOT / "artifacts" / "bts-agentbench",
            REPO_ROOT / "artifacts" / "bts-static-tasks",
            REPO_ROOT / "artifacts" / "xai4heat-agentbench",
            REPO_ROOT / "artifacts" / "xai4heat-static-tasks",
        ]
    )
    bts["controller"] = controller_record("bts", bts_rows)
    xai["controller"] = controller_record("xai4heat", xai_rows)
    trace_audit_path = REPO_ROOT / "reports" / "model-runs" / "trace_audit.json"
    trace_audit = json.loads(trace_audit_path.read_text(encoding="utf-8"))
    if trace_audit.get("report_type") != "retained-trace-clean-release-audit":
        raise RuntimeError("unexpected retained-trace audit")

    catalog_root = REPO_ROOT / "data" / "source" / "bts-processed-catalog"
    catalog = {
        path.name: file_record(path)
        for path in sorted(catalog_root.iterdir())
        if path.is_file()
    }
    return {
        "manifest_type": "telemetry-episode-release",
        "corpora": {"bts": bts, "xai4heat": xai},
        "construction_inputs": {
            "bts_raw_archives": RAW_ARCHIVES,
            "raw_archives_redistributed": False,
            "bts_normalized_catalog": catalog,
            "bts_release_selection": file_record(selection_path, rows=len(selection_rows)),
            "bts_stream_lineage": file_record(REPO_ROOT / "provenance" / "release_stream_lineage.csv"),
        },
        "construction_replay": file_record(REPO_ROOT / "replay" / "release_replay_report.json"),
        "model_trace_audit": file_record(trace_audit_path),
        "model_runs": model_records({"bts": bts_rows, "xai4heat": xai_rows}),
        "worked_example": {
            "scenario_id": "test_timestamp_value_lookup_00051",
            "trace": file_record(REPO_ROOT / "examples" / "REPLAY_TRACE.md"),
            "structured_lineage": file_record(
                REPO_ROOT / "provenance" / "examples" / "test_timestamp_value_lookup_00051.json"
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the deterministic public release manifest.")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "release" / "release_manifest.json",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest()
    if args.check:
        retained = json.loads(args.output.read_text(encoding="utf-8"))
        if retained != manifest:
            raise RuntimeError("release manifest does not match repository artifacts")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "manifest": str(args.output),
                "bts_rows": manifest["corpora"]["bts"]["episode_rows"],
                "xai4heat_rows": manifest["corpora"]["xai4heat"]["episode_rows"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
