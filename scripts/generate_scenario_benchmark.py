#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bts_agentbench.scenario_benchmark import generate_scenario_benchmark


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-store-db", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--heldout-site-id", action="append", default=[])
    parser.add_argument("--family", action="append", default=[])
    parser.add_argument(
        "--selection-contract",
        type=Path,
        help="Frozen retained-row contract used to reconstruct a fixed benchmark release.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        help="Directory containing Site_Aaa.zip, Site_Baa.zip, and Site_Caa.zip.",
    )
    args = parser.parse_args()

    if args.raw_dir is not None:
        os.environ["BTS_RAW_DIR"] = str(args.raw_dir.resolve())

    manifest = generate_scenario_benchmark(
        args.tool_store_db,
        args.out_dir,
        heldout_site_ids=args.heldout_site_id or None,
        include_families=args.family or None,
        selection_contract_path=args.selection_contract,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
