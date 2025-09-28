#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Uso: $0 <rama-o-tag>" >&2
  exit 1
fi

TARGET_REF="$1"

if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "Error: este script debe ejecutarse dentro de un repositorio Git." >&2
  exit 1
fi

cd "$REPO_ROOT"

echo "Actualizando repositorio desde origin $TARGET_REF..."
git pull origin "$TARGET_REF"
