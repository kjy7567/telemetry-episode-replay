#!/usr/bin/env python
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
SUBMITTED_DATASET_SHA256 = "70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def deterministic_zip(output: Path, files: list[tuple[Path, str]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, archive_name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def artifact_record(directory: Path) -> dict[str, Any]:
    return {
        split: {
            "path": str((directory / f"{split}.jsonl").relative_to(REPO_ROOT)),
            "rows": jsonl_count(directory / f"{split}.jsonl"),
            "bytes": (directory / f"{split}.jsonl").stat().st_size,
            "sha256": sha256(directory / f"{split}.jsonl"),
        }
        for split in SPLITS
    }


def main() -> None:
    static_dir = REPO_ROOT / "artifacts" / "bts-static-seed"
    final_dir = REPO_ROOT / "artifacts" / "bts-canonical-final"
    release_dir = REPO_ROOT / "release"
    archive_path = release_dir / "bts-canonical-final.zip"
    human_archive_path = release_dir / "human-validation-packet.zip"
    submitted_dataset_path = release_dir / "submitted-dataset-bundle.zip"
    if sha256(submitted_dataset_path) != SUBMITTED_DATASET_SHA256:
        raise RuntimeError("submitted dataset snapshot is missing or does not match the frozen submission")
    files = [
        (final_dir / name, f"bts-canonical-final/{name}")
        for name in (
            "train.jsonl",
            "dev.jsonl",
            "test.jsonl",
            "manifest.json",
            "contract_preflight_report.json",
            "final_build_report.json",
        )
    ]
    deterministic_zip(archive_path, files)
    human_root = REPO_ROOT / "human_validation"
    human_files = [
        (human_root / "PROTOCOL.md", "human-validation/PROTOCOL.md"),
        (
            human_root / "PARTICIPANT_INFORMATION.md",
            "human-validation/PARTICIPANT_INFORMATION.md",
        ),
    ]
    human_files.extend(
        (path, f"human-validation/packet/{path.name}")
        for path in sorted((human_root / "packet").iterdir())
        if path.is_file()
    )
    deterministic_zip(human_archive_path, human_files)
    manifest = {
        "manifest_version": "release-integrity-v1",
        "static": artifact_record(static_dir),
        "final": artifact_record(final_dir),
        "contract_preflight_issue_count": json.loads(
            (final_dir / "contract_preflight_report.json").read_text(encoding="utf-8")
        )["issue_count"],
        "archive": {
            "path": str(archive_path.relative_to(REPO_ROOT)),
            "bytes": archive_path.stat().st_size,
            "sha256": sha256(archive_path),
        },
        "human_validation_archive": {
            "path": str(human_archive_path.relative_to(REPO_ROOT)),
            "bytes": human_archive_path.stat().st_size,
            "sha256": sha256(human_archive_path),
            "completed_results_included": False,
        },
        "submitted_dataset_snapshot": {
            "path": str(submitted_dataset_path.relative_to(REPO_ROOT)),
            "bytes": submitted_dataset_path.stat().st_size,
            "sha256": sha256(submitted_dataset_path),
        },
    }
    (release_dir / "release_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
