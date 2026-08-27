#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_release_manifest import build_manifest  # noqa: E402


ARCHIVE_ROOT = "telemetry-episode-replay"
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
MAX_BUNDLE_BYTES = 200 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic public source and dataset bundles.")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    return parser.parse_args()


def zip_info(name: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def files_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        (
            path.relative_to(REPO_ROOT)
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ),
        key=lambda path: path.as_posix(),
    )


def existing(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for value in paths:
        path = REPO_ROOT / value
        if not path.is_file():
            raise RuntimeError(f"missing package input: {path}")
        result.append(Path(value))
    return result


def source_files() -> list[Path]:
    files = existing(
        [
            ".gitignore",
            "ARTIFACTS.md",
            "CONSTRUCTION_WALKTHROUGH.md",
            "DATA_SOURCES.md",
            "LICENSE",
            "Makefile",
            "MODEL_EVALUATION.md",
            "PORTABILITY_XAI4HEAT.md",
            "README.md",
            "REPRODUCIBILITY.md",
            "pyproject.toml",
            "requirements.lock",
            "examples/REPLAY_TRACE.md",
            "provenance/release_static_selection.jsonl",
            "provenance/release_stream_lineage.csv",
            "provenance/examples/test_timestamp_value_lookup_00051.json",
            "release/release_manifest.json",
            "release/public-dataset-README.md",
            "release/public-runners-README.md",
            "replay/release_replay_report.json",
        ]
    )
    for root in (
        REPO_ROOT / ".github" / "workflows",
        REPO_ROOT / "data" / "source",
        REPO_ROOT / "runners",
        REPO_ROOT / "scripts",
        REPO_ROOT / "src",
    ):
        files.extend(files_under(root))
    return sorted(set(files), key=lambda path: path.as_posix())


def dataset_files() -> list[Path]:
    files = existing(
        [
            "DATA_SOURCES.md",
            "LICENSE",
            "MODEL_EVALUATION.md",
            "examples/REPLAY_TRACE.md",
            "provenance/release_static_selection.jsonl",
            "provenance/release_stream_lineage.csv",
            "provenance/examples/test_timestamp_value_lookup_00051.json",
            "release/release_manifest.json",
            "release/public-dataset-README.md",
            "release/public-runners-README.md",
            "replay/release_replay_report.json",
            "replay/bts_controller_audit.json",
            "replay/bts_controller_failure_analysis.json",
            "replay/xai4heat_controller_audit.json",
            "replay/xai4heat_controller_failure_analysis.json",
            "scripts/run_bts_e2e_openai_eval.py",
        ]
    )
    for root in (
        REPO_ROOT / "artifacts",
        REPO_ROOT / "reports" / "model-runs",
        REPO_ROOT / "runners",
    ):
        files.extend(files_under(root))
    for corpus in ("bts", "xai4heat"):
        root = REPO_ROOT / "replay" / f"{corpus}-controller-witnesses"
        for path in root.rglob("phase_complete_stronger_controller.jsonl"):
            files.append(path.relative_to(REPO_ROOT))
    return sorted(set(files), key=lambda path: path.as_posix())


def build_zip(path: Path, files: list[Path]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in files:
            source = REPO_ROOT / relative
            member = f"{ARCHIVE_ROOT}/{relative.as_posix()}"
            executable = bool(source.stat().st_mode & stat.S_IXUSR)
            archive.writestr(zip_info(member, executable=executable), source.read_bytes())
    temporary.replace(path)


def jsonl_count(payload: bytes) -> int:
    return sum(bool(line.strip()) for line in payload.splitlines())


def verify_dataset_zip(path: Path) -> dict[str, int]:
    expected = {
        f"{ARCHIVE_ROOT}/artifacts/bts-agentbench/train.jsonl": 356,
        f"{ARCHIVE_ROOT}/artifacts/bts-agentbench/dev.jsonl": 87,
        f"{ARCHIVE_ROOT}/artifacts/bts-agentbench/test.jsonl": 89,
        f"{ARCHIVE_ROOT}/artifacts/bts-static-tasks/train.jsonl": 356,
        f"{ARCHIVE_ROOT}/artifacts/bts-static-tasks/dev.jsonl": 87,
        f"{ARCHIVE_ROOT}/artifacts/bts-static-tasks/test.jsonl": 89,
        f"{ARCHIVE_ROOT}/artifacts/xai4heat-agentbench/train.jsonl": 132,
        f"{ARCHIVE_ROOT}/artifacts/xai4heat-agentbench/dev.jsonl": 31,
        f"{ARCHIVE_ROOT}/artifacts/xai4heat-agentbench/test.jsonl": 41,
        f"{ARCHIVE_ROOT}/reports/model-runs/gpt-5.5/test.jsonl": 89,
        f"{ARCHIVE_ROOT}/reports/model-runs/gemini-3.1-pro-openrouter/test.jsonl": 89,
        f"{ARCHIVE_ROOT}/reports/model-runs/claude-opus-4.7-openrouter/test.jsonl": 89,
        f"{ARCHIVE_ROOT}/reports/model-runs/gpt-5.5-xai4heat/test.jsonl": 41,
    }
    required_runners = {
        f"{ARCHIVE_ROOT}/runners/gpt55_bts.sh",
        f"{ARCHIVE_ROOT}/runners/gemini31pro_bts_openrouter.sh",
        f"{ARCHIVE_ROOT}/runners/opus47_bts_openrouter.sh",
        f"{ARCHIVE_ROOT}/runners/gpt55_xai4heat.sh",
        f"{ARCHIVE_ROOT}/scripts/run_bts_e2e_openai_eval.py",
    }
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("dataset ZIP CRC failure")
        names = set(archive.namelist())
        counts = {name: jsonl_count(archive.read(name)) for name in expected}
    if counts != expected:
        raise RuntimeError(f"dataset row-count mismatch: {counts}")
    if not required_runners.issubset(names):
        raise RuntimeError(f"dataset runners missing: {sorted(required_runners - names)}")
    return counts


def verify_source_zip(path: Path) -> None:
    required = {
        f"{ARCHIVE_ROOT}/scripts/replay_release.py",
        f"{ARCHIVE_ROOT}/scripts/build_release_manifest.py",
        f"{ARCHIVE_ROOT}/scripts/verify_packaged_release.py",
        f"{ARCHIVE_ROOT}/src/bts_agentbench/scenario_benchmark.py",
        f"{ARCHIVE_ROOT}/data/source/bts-processed-catalog/streams.parquet",
        f"{ARCHIVE_ROOT}/provenance/release_static_selection.jsonl",
        f"{ARCHIVE_ROOT}/release/release_manifest.json",
    }
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("source ZIP CRC failure")
        names = set(archive.namelist())
    if not required.issubset(names):
        raise RuntimeError(f"source files missing: {sorted(required - names)}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest()
    manifest_path = REPO_ROOT / "release" / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    source_zip = args.output_dir / "source.zip"
    dataset_zip = args.output_dir / "dataset.zip"
    checksums = args.output_dir / "SHA256SUMS"
    build_zip(source_zip, source_files())
    build_zip(dataset_zip, dataset_files())
    verify_source_zip(source_zip)
    counts = verify_dataset_zip(dataset_zip)
    for path in (source_zip, dataset_zip):
        if path.stat().st_size >= MAX_BUNDLE_BYTES:
            raise RuntimeError(f"bundle exceeds 200 MiB: {path}")
    checksum_text = "".join(
        f"{sha256(path)}  {path.name}\n" for path in (source_zip, dataset_zip)
    )
    checksums.write_text(checksum_text, encoding="utf-8")
    report = {
        "source_zip": str(source_zip),
        "source_bytes": source_zip.stat().st_size,
        "dataset_zip": str(dataset_zip),
        "dataset_bytes": dataset_zip.stat().st_size,
        "dataset_counts": counts,
        "checksums": str(checksums),
    }
    (args.output_dir / "package_report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
