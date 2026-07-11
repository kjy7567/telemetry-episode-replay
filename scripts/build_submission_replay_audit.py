#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "dev", "test")
SUBMISSION_PREFIX = "dataset_bundle/bts_agentbench_532/"
PROVENANCE_FIELDS = {"agentic_lifting", "generation_history", "metadata"}
SUBMITTED_DATASET_SHA256 = "70ad2e641a2332fe94a5d81e612279ba9f8e90914fa605b083c8441a2ab01f76"

FAMILIES = (
    ("point_disambiguation", "Point disambiguation", 4_263),
    ("day_mean_lookup", "Day mean lookup", 2_193_431),
    ("relative_24h_mean_lookup", "Relative 24h mean lookup", 2_193_431),
    ("window_mean_lookup", "Window mean lookup", 315_929),
    ("window_pairwise_compare", "Window pairwise compare", 5_989_083),
    ("window_rank", "Window rank", 1_084),
    ("timestamp_value_lookup", "Timestamp value lookup", 2_123),
    ("timestamp_nearest_lookup", "Timestamp nearest lookup", 2_123),
    ("quality_gate", "Quality gate", 315),
)

MODELS = (
    ("gpt-5.5", "GPT-5.5"),
    ("gemini-3.1-pro-openrouter", "Gemini 3.1 Pro"),
    ("claude-opus-4.7-openrouter", "Claude Opus 4.7"),
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl_bytes(payload: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]


def load_directory(directory: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    for split in SPLITS:
        payload = (directory / f"{split}.jsonl").read_bytes()
        rows[split] = load_jsonl_bytes(payload)
        hashes[split] = sha256_bytes(payload)
    return rows, hashes


def load_submission(path: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submitted dataset archive failed CRC validation")
        for split in SPLITS:
            payload = archive.read(f"{SUBMISSION_PREFIX}{split}.jsonl")
            rows[split] = load_jsonl_bytes(payload)
            hashes[split] = sha256_bytes(payload)
    return rows, hashes


def semantic_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in PROVENANCE_FIELDS}


def canonical_hash(rows: list[dict[str, Any]], *, semantic: bool) -> str:
    normalized: list[str] = []
    for row in sorted(rows, key=lambda item: str(item["scenario_id"])):
        value = semantic_projection(row) if semantic else row
        normalized.append(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return sha256_bytes(("\n".join(normalized) + "\n").encode("utf-8"))


def row_semantic_hash(row: dict[str, Any]) -> str:
    payload = json.dumps(
        semantic_projection(row), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def index_rows(splits: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {
        str(row["scenario_id"]): row
        for split in SPLITS
        for row in splits[split]
    }


def family_rows(
    splits: dict[str, list[dict[str, Any]]], family: str
) -> list[dict[str, Any]]:
    return [
        row
        for split in SPLITS
        for row in splits[split]
        if row.get("task_family") == family
    ]


def split_counts(splits: dict[str, list[dict[str, Any]]], family: str) -> dict[str, int]:
    return {
        split: sum(row.get("task_family") == family for row in splits[split])
        for split in SPLITS
    }


def accomplished(model_audit: dict[str, Any], model: str, family: str) -> tuple[int, int]:
    labels = model_audit["models"][model]["by_family"][family]
    return int(labels.get("accomplished", 0)), sum(int(value) for value in labels.values())


def resolve_labels(row: dict[str, Any]) -> list[str]:
    return [
        str(call.get("arguments", {}).get("equipment_label"))
        for call in row.get("canonical_tool_calls", [])
        if call.get("tool_name") == "resolve_point"
    ]


def rank_windows(row: dict[str, Any]) -> list[list[str]]:
    return [
        [
            str(call.get("arguments", {}).get("window_start")),
            str(call.get("arguments", {}).get("window_end")),
        ]
        for call in row.get("canonical_tool_calls", [])
        if call.get("tool_name") == "rank_window"
    ]


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    if sha256_file(args.submission_zip) != SUBMITTED_DATASET_SHA256:
        raise RuntimeError("submitted dataset archive hash does not match the frozen submission")

    submitted, submitted_split_hashes = load_submission(args.submission_zip)
    current, current_split_hashes = load_directory(args.current_dir)
    run_a, run_a_split_hashes = load_directory(args.run_a_dir)
    run_b, run_b_split_hashes = load_directory(args.run_b_dir)
    model_audit = json.loads(args.model_audit.read_text(encoding="utf-8"))
    controller = json.loads(args.controller_report.read_text(encoding="utf-8"))

    submitted_by_id = index_rows(submitted)
    current_by_id = index_rows(current)
    run_a_by_id = index_rows(run_a)
    run_b_by_id = index_rows(run_b)
    id_sets_equal = (
        submitted_by_id.keys()
        == current_by_id.keys()
        == run_a_by_id.keys()
        == run_b_by_id.keys()
    )
    if not id_sets_equal:
        raise RuntimeError("scenario ID sets differ across submitted, current, run A, and run B")

    full_equal = sum(submitted_by_id[sid] == current_by_id[sid] for sid in submitted_by_id)
    semantic_equal = sum(
        semantic_projection(submitted_by_id[sid]) == semantic_projection(current_by_id[sid])
        for sid in submitted_by_id
    )

    family_reports: list[dict[str, Any]] = []
    for family, display_name, candidate_count in FAMILIES:
        submitted_family = family_rows(submitted, family)
        current_family = family_rows(current, family)
        run_a_family = family_rows(run_a, family)
        run_b_family = family_rows(run_b, family)
        submitted_family_by_id = {str(row["scenario_id"]): row for row in submitted_family}
        current_family_by_id = {str(row["scenario_id"]): row for row in current_family}
        changed_ids = sorted(
            sid
            for sid in submitted_family_by_id
            if semantic_projection(submitted_family_by_id[sid])
            != semantic_projection(current_family_by_id[sid])
        )
        hashes = {
            "submitted_semantic": canonical_hash(submitted_family, semantic=True),
            "current_semantic": canonical_hash(current_family, semantic=True),
            "run_a_semantic": canonical_hash(run_a_family, semantic=True),
            "run_b_semantic": canonical_hash(run_b_family, semantic=True),
            "current_full": canonical_hash(current_family, semantic=False),
            "run_a_full": canonical_hash(run_a_family, semantic=False),
            "run_b_full": canonical_hash(run_b_family, semantic=False),
        }
        replay_exact = hashes["current_full"] == hashes["run_a_full"] == hashes["run_b_full"]
        if not replay_exact:
            raise RuntimeError(f"family replay mismatch: {family}")

        representative = sorted(
            (row for row in submitted["test"] if row.get("task_family") == family),
            key=lambda row: str(row["scenario_id"]),
        )[0]
        representative_id = str(representative["scenario_id"])
        representative_hashes = {
            "submitted": row_semantic_hash(submitted_by_id[representative_id]),
            "current": row_semantic_hash(current_by_id[representative_id]),
            "run_a": row_semantic_hash(run_a_by_id[representative_id]),
            "run_b": row_semantic_hash(run_b_by_id[representative_id]),
        }
        scores = {}
        for model, display in MODELS:
            successful, total = accomplished(model_audit, model, family)
            scores[display] = {"accomplished": successful, "total": total}
        controller_labels = controller["by_family"][family]
        family_reports.append(
            {
                "family": family,
                "display_name": display_name,
                "candidate_count": candidate_count,
                "submitted_split_counts": split_counts(submitted, family),
                "current_split_counts": split_counts(current, family),
                "semantic_unchanged_count": len(submitted_family) - len(changed_ids),
                "row_count": len(submitted_family),
                "semantic_changed_ids": changed_ids,
                "hashes": hashes,
                "replay_a_b_current_exact": replay_exact,
                "controller": {
                    "accomplished": int(controller_labels.get("accomplished", 0)),
                    "total": sum(int(value) for value in controller_labels.values()),
                },
                "submitted_model_scores": scores,
                "representative": {
                    "scenario_id": representative_id,
                    "initial_user_message": representative.get("initial_user_message"),
                    "gold_final_answer": representative.get("gold_final_answer"),
                    "tool_path": [
                        call.get("tool_name") for call in representative.get("canonical_tool_calls", [])
                    ],
                    "semantic_hashes": representative_hashes,
                    "semantic_equal_across_all_snapshots": len(set(representative_hashes.values())) == 1,
                },
            }
        )

    changed_ids = sorted(
        sid
        for sid in submitted_by_id
        if semantic_projection(submitted_by_id[sid]) != semantic_projection(current_by_id[sid])
    )
    pairwise_deltas = [
        {
            "scenario_id": sid,
            "submitted_points": resolve_labels(submitted_by_id[sid]),
            "current_points": resolve_labels(current_by_id[sid]),
            "submitted_query": submitted_by_id[sid].get("initial_user_message"),
            "current_query": current_by_id[sid].get("initial_user_message"),
            "reason": "complete deterministic tie-break for equal-gap candidates",
        }
        for sid in changed_ids
        if submitted_by_id[sid].get("task_family") == "window_pairwise_compare"
    ]
    rank_deltas = [
        {
            "scenario_id": sid,
            "submitted_revision": submitted_by_id[sid].get("goal_revision_turns", [None])[0],
            "current_revision": current_by_id[sid].get("goal_revision_turns", [None])[0],
            "submitted_rank_windows": rank_windows(submitted_by_id[sid]),
            "current_rank_windows": rank_windows(current_by_id[sid]),
            "reason": "align adjacent-month wording with the executable next-month window",
        }
        for sid in changed_ids
        if submitted_by_id[sid].get("task_family") == "window_rank"
    ]

    replay_exact_by_split = {
        split: current_split_hashes[split] == run_a_split_hashes[split] == run_b_split_hashes[split]
        for split in SPLITS
    }
    if not all(replay_exact_by_split.values()):
        raise RuntimeError("split replay mismatch")

    return {
        "report_version": "submitted-to-replay-audit-v1",
        "definitions": {
            "submitted_snapshot": "the exact dataset.zip supplied with the paper",
            "current_snapshot": "the reproducibility-maintenance release",
            "semantic_projection_excludes": sorted(PROVENANCE_FIELDS),
            "semantic_projection_includes": (
                "all user turns, canonical and acceptable tool paths, phase golds, final gold, evidence, "
                "task verifier, interaction verifier, split, and release filter"
            ),
        },
        "submitted_dataset_archive": {
            "sha256": SUBMITTED_DATASET_SHA256,
            "bytes": args.submission_zip.stat().st_size,
        },
        "scenario_id_sets_equal": id_sets_equal,
        "row_count": len(submitted_by_id),
        "full_object_equal_count": full_equal,
        "provenance_only_changed_count": semantic_equal - full_equal,
        "semantic_equal_count": semantic_equal,
        "semantic_changed_count": len(changed_ids),
        "semantic_changed_ids": changed_ids,
        "split_hashes": {
            "submitted": submitted_split_hashes,
            "current": current_split_hashes,
            "run_a": run_a_split_hashes,
            "run_b": run_b_split_hashes,
        },
        "replay_exact_by_split": replay_exact_by_split,
        "family_reports": family_reports,
        "maintenance_deltas": {"pairwise": pairwise_deltas, "rank": rank_deltas},
    }


def short(value: str) -> str:
    return value[:12]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Submitted Snapshot to Replayed Release Audit",
        "",
        "This document separates the benchmark supplied with the paper from the later reproducibility-maintenance release. Replay is an artifact-construction result, not a second model evaluation.",
        "",
        "## Evidence Chain",
        "",
        "```text",
        "checksummed raw BTS archives",
        "  -> rebuilt read-only tool store",
        "  -> retained static executable tasks",
        "  -> clean replay A and clean replay B",
        "  -> reproducibility-maintenance release",
        "",
        "submitted dataset.zip -----------------------> semantic comparison",
        "```",
        "",
        "The submitted ZIP is preserved byte-for-byte. The semantic comparison excludes only the top-level `metadata`, `generation_history`, and `agentic_lifting` provenance records. It includes every evaluator-facing user turn, tool path, phase target, final gold, evidence field, verifier, split, and release-filter field.",
        "",
        "## Snapshot Results",
        "",
        f'- Submitted dataset bundle SHA-256: `{report["submitted_dataset_archive"]["sha256"]}`',
        f'- Scenario ID sets preserved: `{str(report["scenario_id_sets_equal"]).lower()}` ({report["row_count"]}/532)',
        f'- Exact full-object equality, submitted versus maintained: {report["full_object_equal_count"]}/532',
        f'- Provenance-label-only changes: {report["provenance_only_changed_count"]}/532',
        f'- Evaluator-facing semantic equality: {report["semantic_equal_count"]}/532',
        f'- Evaluator-facing maintenance changes: {report["semantic_changed_count"]}/532, all in train',
        "",
        "| Split | Submitted SHA-256 | Current/A/B SHA-256 | A = B = current |",
        "|---|---|---|:---:|",
    ]
    for split in SPLITS:
        lines.append(
            f'| {split} | `{short(report["split_hashes"]["submitted"][split])}` | '
            f'`{short(report["split_hashes"]["current"][split])}` | '
            f'{"yes" if report["replay_exact_by_split"][split] else "no"} |'
        )

    lines.extend(
        [
            "",
            "All 532 submitted objects carry legacy internal provenance labels, which were replaced by stable public artifact labels. Those label changes explain the zero full-object equality count. After removing only the three provenance records named above, 522 rows are unchanged; the remaining 10 train rows are enumerated below. Dev and test semantics are unchanged.",
            "",
            "## Family Audit",
            "",
            "| Family | Candidate space | Submitted T/D/Test | Semantic unchanged | Train delta | A/B/current | GPT | Gemini | Opus | Controller |",
            "|---|---:|---:|---:|---:|:---:|---:|---:|---:|---:|",
        ]
    )
    for family in report["family_reports"]:
        counts = family["submitted_split_counts"]
        scores = family["submitted_model_scores"]
        lines.append(
            f'| {family["display_name"]} | {family["candidate_count"]:,} | '
            f'{counts["train"]}/{counts["dev"]}/{counts["test"]} | '
            f'{family["semantic_unchanged_count"]}/{family["row_count"]} | '
            f'{len(family["semantic_changed_ids"])} | '
            f'{"yes" if family["replay_a_b_current_exact"] else "no"} | '
            f'{scores["GPT-5.5"]["accomplished"]}/{scores["GPT-5.5"]["total"]} | '
            f'{scores["Gemini 3.1 Pro"]["accomplished"]}/{scores["Gemini 3.1 Pro"]["total"]} | '
            f'{scores["Claude Opus 4.7"]["accomplished"]}/{scores["Claude Opus 4.7"]["total"]} | '
            f'{family["controller"]["accomplished"]}/{family["controller"]["total"]} |'
        )

    lines.extend(["", "## Representative Family Contracts", ""])
    for family in report["family_reports"]:
        representative = family["representative"]
        hashes = representative["semantic_hashes"]
        lines.extend(
            [
                f'### {family["display_name"]}',
                "",
                f'- Scenario: `{representative["scenario_id"]}`',
                f'- Submitted initial request: {representative["initial_user_message"]}',
                f'- Tool path: `{" -> ".join(representative["tool_path"])}`',
                f'- Gold final answer: `{json.dumps(representative["gold_final_answer"], ensure_ascii=False, sort_keys=True)}`',
                f'- Semantic hashes (submitted/current/A/B): `{short(hashes["submitted"])}` / `{short(hashes["current"])}` / `{short(hashes["run_a"])}` / `{short(hashes["run_b"])}`',
                f'- Equal across all snapshots: `{"yes" if representative["semantic_equal_across_all_snapshots"] else "no"}`',
                "",
            ]
        )

    lines.extend(
        [
            "## Enumerated Maintenance Delta",
            "",
            "### Pairwise deterministic ordering",
            "",
            "Eight training rows changed when stream IDs were added as the final ordering key for equal-gap candidate pairs. No dev or test row changed.",
            "",
            "| Scenario | Submitted point pair | Maintained point pair |",
            "|---|---|---|",
        ]
    )
    for delta in report["maintenance_deltas"]["pairwise"]:
        lines.append(
            f'| `{delta["scenario_id"]}` | {" vs. ".join(delta["submitted_points"])} | '
            f'{" vs. ".join(delta["current_points"])} |'
        )
    lines.extend(
        [
            "",
            "### Rank month-direction alignment",
            "",
            "Two January training rows already executed a next-month fallback because the preceding month had no data, while their submitted revision text said `previous month`. The maintained rows preserve the executable windows and change the text to `next month`.",
            "",
            "| Scenario | Submitted revision | Maintained revision |",
            "|---|---|---|",
        ]
    )
    for delta in report["maintenance_deltas"]["rank"]:
        lines.append(
            f'| `{delta["scenario_id"]}` | {delta["submitted_revision"]} | {delta["current_revision"]} |'
        )

    lines.extend(
        [
            "",
            "## Reproduce This Audit",
            "",
            "After rebuilding the raw tool store and running `scripts/replay_release.py`, execute:",
            "",
            "```bash",
            "python scripts/build_submission_replay_audit.py",
            "```",
            "",
            "The command exits on a submitted-ZIP hash mismatch, scenario-set mismatch, family replay mismatch, or split replay mismatch, and rewrites both this Markdown file and the machine-readable JSON report.",
            "",
            "## Interpretation",
            "",
            "The submitted benchmark and maintained release have identical dev/test evaluator semantics, so the submitted 89-row model traces remain attached to the same test contracts. The current construction pipeline reproducibly generates the maintained release from the retained static layer and the newly rebuilt tool store. It does not claim that the submitted ZIP and maintained release are byte-identical; their provenance labels differ throughout, and the 10 disclosed train contracts were deliberately corrected.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def verify_recorded_audit(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    submission_zip = repo_root / "release" / "submitted-dataset-bundle.zip"
    current_dir = repo_root / "artifacts" / "bts-canonical-final"
    audit_path = repo_root / "replay" / "submission_to_replay_audit.json"
    replay_path = repo_root / "replay" / "replay_report.json"
    if sha256_file(submission_zip) != SUBMITTED_DATASET_SHA256:
        raise RuntimeError("recorded audit submission snapshot hash mismatch")

    submitted, submitted_hashes = load_submission(submission_zip)
    current, current_hashes = load_directory(current_dir)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    submitted_by_id = index_rows(submitted)
    current_by_id = index_rows(current)
    if submitted_by_id.keys() != current_by_id.keys():
        raise RuntimeError("recorded audit scenario ID mismatch")

    full_equal = sum(submitted_by_id[sid] == current_by_id[sid] for sid in submitted_by_id)
    semantic_equal = sum(
        semantic_projection(submitted_by_id[sid]) == semantic_projection(current_by_id[sid])
        for sid in submitted_by_id
    )
    changed_ids = sorted(
        sid
        for sid in submitted_by_id
        if semantic_projection(submitted_by_id[sid]) != semantic_projection(current_by_id[sid])
    )
    expected_scalars = {
        "row_count": len(submitted_by_id),
        "full_object_equal_count": full_equal,
        "semantic_equal_count": semantic_equal,
        "semantic_changed_count": len(changed_ids),
    }
    for key, expected in expected_scalars.items():
        if audit.get(key) != expected:
            raise RuntimeError(f"recorded audit mismatch: {key}")
    if audit.get("semantic_changed_ids") != changed_ids:
        raise RuntimeError("recorded audit changed-ID mismatch")
    if audit["split_hashes"]["submitted"] != submitted_hashes:
        raise RuntimeError("recorded audit submitted split hash mismatch")
    if audit["split_hashes"]["current"] != current_hashes:
        raise RuntimeError("recorded audit current split hash mismatch")

    replay_runs = replay.get("runs", [])
    if len(replay_runs) != 2:
        raise RuntimeError("recorded release replay must contain two runs")
    if replay.get("expected_split_hashes") != current_hashes:
        raise RuntimeError("recorded release expected hashes do not match current release")
    for index, run in enumerate(replay_runs):
        if run.get("split_hashes") != current_hashes:
            raise RuntimeError(f"recorded release run {index + 1} does not match current release")
        audit_key = "run_a" if index == 0 else "run_b"
        if audit["split_hashes"][audit_key] != current_hashes:
            raise RuntimeError(f"recorded family audit {audit_key} hashes do not match current release")

    family_by_name = {item["family"]: item for item in audit["family_reports"]}
    for family, _, _ in FAMILIES:
        submitted_family = family_rows(submitted, family)
        current_family = family_rows(current, family)
        family_report = family_by_name[family]
        if family_report["hashes"]["submitted_semantic"] != canonical_hash(
            submitted_family, semantic=True
        ):
            raise RuntimeError(f"recorded submitted family hash mismatch: {family}")
        if family_report["hashes"]["current_semantic"] != canonical_hash(
            current_family, semantic=True
        ):
            raise RuntimeError(f"recorded current family hash mismatch: {family}")
        if family_report.get("replay_a_b_current_exact") is not True:
            raise RuntimeError(f"recorded family replay is not exact: {family}")

    return {
        "status": "ok",
        "rows": len(submitted_by_id),
        "semantic_equal": semantic_equal,
        "semantic_changed": len(changed_ids),
        "families": len(FAMILIES),
        "recorded_replay_runs": len(replay_runs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the submitted BTS snapshot with clean replay A/B and the maintained release."
    )
    parser.add_argument(
        "--submission-zip",
        type=Path,
        default=REPO_ROOT / "release" / "submitted-dataset-bundle.zip",
    )
    parser.add_argument(
        "--current-dir", type=Path, default=REPO_ROOT / "artifacts" / "bts-canonical-final"
    )
    parser.add_argument(
        "--run-a-dir",
        type=Path,
        default=REPO_ROOT / "data" / "local-build" / "replay" / "run_a" / "final",
    )
    parser.add_argument(
        "--run-b-dir",
        type=Path,
        default=REPO_ROOT / "data" / "local-build" / "replay" / "run_b" / "final",
    )
    parser.add_argument(
        "--model-audit",
        type=Path,
        default=REPO_ROOT / "reports" / "model-runs" / "trace_audit.json",
    )
    parser.add_argument(
        "--controller-report",
        type=Path,
        default=REPO_ROOT / "reports" / "controller" / "explicit_controller_audit_report.json",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT / "replay" / "submission_to_replay_audit.json",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=REPO_ROOT / "replay" / "SUBMISSION_TO_REPLAY.md",
    )
    parser.add_argument(
        "--verify-recorded",
        action="store_true",
        help="Verify the checked audit from submitted/current artifacts and recorded A/B split hashes.",
    )
    args = parser.parse_args()

    if args.verify_recorded:
        print(json.dumps(verify_recorded_audit(), indent=2))
        return

    report = build_report(args)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "output_markdown": str(args.output_markdown),
                "rows": report["row_count"],
                "semantic_equal": report["semantic_equal_count"],
                "semantic_changed": report["semantic_changed_count"],
                "replay_exact_by_split": report["replay_exact_by_split"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
