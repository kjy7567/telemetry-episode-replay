#!/usr/bin/env python
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
CORPORA = {
    "bts": {
        "episodes": REPO_ROOT / "artifacts" / "bts-agentbench",
        "static": REPO_ROOT / "artifacts" / "bts-static-tasks",
        "heldout_site": "BTS_C",
    },
    "xai4heat": {
        "episodes": REPO_ROOT / "artifacts" / "xai4heat-agentbench",
        "static": REPO_ROOT / "artifacts" / "xai4heat-static-tasks",
        "heldout_site": "XAI4HEAT_L17",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def split_manifest(root: Path) -> tuple[dict[str, Any], dict[str, int]]:
    splits: dict[str, Any] = {}
    families: Counter[str] = Counter()
    seen: set[str] = set()
    for split in SPLITS:
        path = root / f"{split}.jsonl"
        rows = load_jsonl(path)
        ids = [str(row["scenario_id"]) for row in rows]
        if len(ids) != len(set(ids)) or seen.intersection(ids):
            raise RuntimeError(f"duplicate scenario ID: {path}")
        if any(str(row.get("split")) != split for row in rows):
            raise RuntimeError(f"split mismatch: {path}")
        seen.update(ids)
        families.update(str(row["task_family"]) for row in rows)
        splits[split] = {
            "path": f"{split}.jsonl",
            "rows": len(rows),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    return splits, dict(sorted(families.items()))


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    for corpus, config in CORPORA.items():
        static_splits, static_families = split_manifest(config["static"])
        episode_splits, episode_families = split_manifest(config["episodes"])
        if static_families != episode_families:
            raise RuntimeError(f"static/episode family mismatch: {corpus}")
        selection = (
            "provenance/release_static_selection.jsonl"
            if corpus == "bts"
            else "deterministic XAI4HEAT adapter and held-out-site split"
        )
        write_manifest(
            config["static"] / "manifest.json",
            {
                "artifact_type": "executable-static-telemetry-tasks",
                "corpus": corpus,
                "heldout_site": config["heldout_site"],
                "selection_contract": selection,
                "splits": static_splits,
                "families": static_families,
            },
        )
        preflight_path = config["episodes"] / "contract_preflight_report.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        if int(preflight["issue_count"]) != 0:
            raise RuntimeError(f"contract preflight failed: {corpus}")
        write_manifest(
            config["episodes"] / "manifest.json",
            {
                "artifact_type": "evidence-grounded-multi-turn-agent-episodes",
                "corpus": corpus,
                "heldout_site": config["heldout_site"],
                "static_task_artifact": f"artifacts/{config['static'].name}",
                "splits": episode_splits,
                "families": episode_families,
                "contract_preflight_issue_count": 0,
            },
        )
    print(json.dumps({"status": "ok", "corpora": sorted(CORPORA)}, indent=2))


if __name__ == "__main__":
    main()
