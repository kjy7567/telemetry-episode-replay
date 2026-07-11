#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bts_agentbench.catalog import build_catalog


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize BTS metadata CSV and Brick graphs into the processed catalog."
    )
    parser.add_argument("--meta-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_catalog(args.meta_dir, args.out_dir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
