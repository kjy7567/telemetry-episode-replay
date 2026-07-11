#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bts_agentbench.bts_e2e import build_bts_e2e


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_bts_e2e(args.static_dir, args.out_dir)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
