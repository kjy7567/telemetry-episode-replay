#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    return parser.parse_args()


def zip_info(name: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def source_files() -> list[Path]:
    excluded_parts = {".git", "__pycache__", ".pytest_cache", "dist", "local-build"}
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT)
        if any(part in excluded_parts for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        files.append(relative)
    return sorted(files, key=lambda value: value.as_posix())


def build_source_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in source_files():
            source = REPO_ROOT / relative
            executable = bool(source.stat().st_mode & stat.S_IXUSR)
            member = f"telemetry-episode-replay/{relative.as_posix()}"
            archive.writestr(zip_info(member, executable=executable), source.read_bytes())


def dataset_overrides() -> dict[str, bytes]:
    model_root = REPO_ROOT / "reports" / "model-runs"
    return {
        "dataset_bundle/README.md": (REPO_ROOT / "release" / "public-dataset-README.md").read_bytes(),
        "dataset_bundle/runners/README.md": (REPO_ROOT / "release" / "public-runners-README.md").read_bytes(),
        "dataset_bundle/runners/run_bts_e2e_openai_eval.py": (REPO_ROOT / "scripts" / "run_bts_e2e_openai_eval.py").read_bytes(),
        "dataset_bundle/runners/gpt55_bts.sh": (REPO_ROOT / "runners" / "gpt55_bts.sh").read_bytes(),
        "dataset_bundle/runners/gemini31pro_bts_openrouter.sh": (REPO_ROOT / "runners" / "gemini31pro_bts_openrouter.sh").read_bytes(),
        "dataset_bundle/runners/opus47_bts_openrouter.sh": (REPO_ROOT / "runners" / "opus47_bts_openrouter.sh").read_bytes(),
        "dataset_bundle/runners/gpt55_xai4heat.sh": (REPO_ROOT / "runners" / "gpt55_xai4heat.sh").read_bytes(),
        "dataset_bundle/runners/gpt55_bts.run_config.json": (model_root / "gpt-5.5" / "run_config.json").read_bytes(),
        "dataset_bundle/runners/gemini31pro_bts_openrouter.run_config.json": (model_root / "gemini-3.1-pro-openrouter" / "run_config.json").read_bytes(),
        "dataset_bundle/runners/opus47_bts_openrouter.run_config.json": (model_root / "claude-opus-4.7-openrouter" / "run_config.json").read_bytes(),
        "dataset_bundle/runners/gpt55_xai4heat.run_config.json": (model_root / "gpt-5.5-xai4heat" / "run_config.json").read_bytes(),
        "dataset_bundle/runs/gpt55_xai4heat_full/test41_full_summary.json": (model_root / "gpt-5.5-xai4heat" / "summary.json").read_bytes(),
    }


def build_dataset_zip(path: Path) -> None:
    source_path = REPO_ROOT / "release" / "submitted-dataset-bundle.zip"
    overrides = dataset_overrides()
    executable_members = {
        name for name in overrides if name.endswith(".sh") or name.endswith(".py")
    }
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as output:
        existing = set(source.namelist())
        for name in sorted(existing | set(overrides)):
            payload = overrides.get(name)
            if payload is None:
                payload = source.read(name)
            output.writestr(
                zip_info(name, executable=name in executable_members),
                payload,
            )


def count_jsonl(payload: bytes) -> int:
    return sum(bool(line.strip()) for line in payload.splitlines())


def verify_dataset_zip(path: Path) -> dict[str, int]:
    expected = {
        "dataset_bundle/bts_agentbench_532/train.jsonl": 356,
        "dataset_bundle/bts_agentbench_532/dev.jsonl": 87,
        "dataset_bundle/bts_agentbench_532/test.jsonl": 89,
        "dataset_bundle/xai4heat_agentbench_204/train.jsonl": 132,
        "dataset_bundle/xai4heat_agentbench_204/dev.jsonl": 31,
        "dataset_bundle/xai4heat_agentbench_204/test.jsonl": 41,
        "dataset_bundle/runs/gpt55_xai4heat_full/test41_full.jsonl": 41,
    }
    with zipfile.ZipFile(path) as archive:
        counts = {name: count_jsonl(archive.read(name)) for name in expected}
        missing_runners = [
            name
            for name in (
                "dataset_bundle/runners/gpt55_bts.sh",
                "dataset_bundle/runners/gemini31pro_bts_openrouter.sh",
                "dataset_bundle/runners/opus47_bts_openrouter.sh",
                "dataset_bundle/runners/gpt55_xai4heat.sh",
            )
            if name not in archive.namelist()
        ]
    if counts != expected:
        raise RuntimeError(f"dataset row-count mismatch: {counts}")
    if missing_runners:
        raise RuntimeError(f"dataset runners missing: {missing_runners}")
    return counts


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_zip = args.output_dir / "source.zip"
    dataset_zip = args.output_dir / "dataset.zip"
    checksums = args.output_dir / "SHA256SUMS"
    for path in (source_zip, dataset_zip, checksums):
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing output: {path}")

    build_source_zip(source_zip)
    build_dataset_zip(dataset_zip)
    counts = verify_dataset_zip(dataset_zip)
    checksum_text = "".join(
        f"{sha256(path)}  {path.name}\n" for path in (source_zip, dataset_zip)
    )
    checksums.write_text(checksum_text, encoding="utf-8")
    print(
        json.dumps(
            {
                "source_zip": str(source_zip),
                "source_bytes": source_zip.stat().st_size,
                "dataset_zip": str(dataset_zip),
                "dataset_bytes": dataset_zip.stat().st_size,
                "dataset_counts": counts,
                "checksums": str(checksums),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
