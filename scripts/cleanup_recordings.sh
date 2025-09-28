#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$HOME/recordings}"
MAX_DAYS="${MAX_DAYS:-7}"

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Directorio $TARGET_DIR inexistente, nada que limpiar."
  exit 0
fi

find "$TARGET_DIR" -type f -name '*.mp4' -mtime "+$MAX_DAYS" -print -delete
