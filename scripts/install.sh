#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Este script debe ejecutarse como root." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
OWNER="${SUDO_USER:-}"
if [[ -z "$OWNER" ]]; then
  OWNER="$(stat -c '%U' "$PROJECT_DIR" 2>/dev/null || echo root)"
fi

raspi-config nonint do_camera 0 || true

apt-get update
apt-get install -y python3-venv python3-pip ffmpeg ustreamer

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$PROJECT_DIR/requirements.txt"

deactivate

install -d -m 775 "$PROJECT_DIR/recordings"
install -d -m 775 "$PROJECT_DIR/recordings/photos"
chown -R "$OWNER":"$OWNER" "$PROJECT_DIR/recordings"

echo "Instalación completada. Recuerde configurar el servicio systemd." 
