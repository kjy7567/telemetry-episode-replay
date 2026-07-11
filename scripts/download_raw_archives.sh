#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-./data/local-build/raw}"
mkdir -p "$ROOT"

download_one() {
  local name="$1"
  local url="$2"
  local expected_sha256="$3"
  local part="$ROOT/${name}.part"
  local final="$ROOT/${name}"

  if [[ -f "$final" ]]; then
    printf '%s  %s\n' "$expected_sha256" "$final" | sha256sum --check --status
    echo "skip $name"
    return 0
  fi

  echo "download $name"
  curl -L --fail -C - -o "$part" "$url"

  python - <<PY
from pathlib import Path
from zipfile import ZipFile
path = Path(r"$part")
with ZipFile(path) as zf:
    bad = zf.testzip()
    if bad:
        raise SystemExit(f"zip validation failed: {bad}")
print("validated", path.name, path.stat().st_size)
PY

  printf '%s  %s\n' "$expected_sha256" "$part" | sha256sum --check --status

  mv "$part" "$final"
  echo "completed $name"
}

download_one "Site_Baa.zip" "https://ndownloader.figshare.com/files/53354039" "fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8"
download_one "Site_Aaa.zip" "https://ndownloader.figshare.com/files/53366168" "ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f"
download_one "Site_Caa.zip" "https://ndownloader.figshare.com/files/53386793" "fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b"
