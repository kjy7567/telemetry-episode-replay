#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from bts_agentbench.raw import build_raw_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip-path", type=Path, required=True)
    parser.add_argument("--out-path", type=Path, required=True)
    args = parser.parse_args()

    frame = build_raw_index(args.zip_path, args.out_path)
    print(frame.head(5).to_string(index=False))
    print(f"rows={len(frame)}")


if __name__ == "__main__":
    main()
