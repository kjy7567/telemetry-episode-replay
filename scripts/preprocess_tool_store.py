#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bts_agentbench.preprocess import preprocess_raw_archives


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    summary = preprocess_raw_archives(args.raw_dir, args.processed_dir, args.out_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
