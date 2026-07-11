#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bts_agentbench.scenario_benchmark import generate_scenario_benchmark  # noqa: E402
from bts_agentbench.tabular_corpus import build_tool_store_from_tabular_corpus  # noqa: E402
from bts_agentbench.xai4heat import load_xai4heat_tables  # noqa: E402


XAI4HEAT_FAMILIES = [
    "day_mean_lookup",
    "relative_24h_mean_lookup",
    "window_mean_lookup",
    "timestamp_value_lookup",
    "timestamp_nearest_lookup",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Adapt XAI4HEAT tables to the shared read-only telemetry benchmark schema."
    )
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--tool-store-dir", type=Path, required=True)
    parser.add_argument("--benchmark-dir", type=Path, required=True)
    parser.add_argument("--heldout-site-id", action="append", default=["XAI4HEAT_L17"])
    args = parser.parse_args()

    metadata, observations = load_xai4heat_tables(args.raw_dir)
    preprocess_summary = build_tool_store_from_tabular_corpus(
        metadata=metadata,
        observations=observations,
        out_dir=args.tool_store_dir,
        source_name="xai4heat",
    )
    manifest = generate_scenario_benchmark(
        args.tool_store_dir / "tool_store.duckdb",
        args.benchmark_dir,
        heldout_site_ids=args.heldout_site_id,
        include_families=XAI4HEAT_FAMILIES,
    )
    print(
        json.dumps(
            {
                "preprocess_summary": preprocess_summary,
                "benchmark_manifest": manifest,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
