"""Cargas y renderiza las plantillas de comandos externos."""
from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path
from typing import Dict

from .config import settings

logger = logging.getLogger("mini_dvr")

DEFAULT_COMMAND_TEMPLATES: Dict[str, str] = {
    "ustreamer": (
        "ustreamer --device={ustreamer_device} --format=MJPEG --encoder=CPU "
        "--resolution={ustreamer_resolution} --desired-fps={ustreamer_fps} "
        "--allow-origin=* --host {ustreamer_host} --port {ustreamer_port} "
        "--persistent --tcp-nodelay --image-default --buffers=4 --workers=4 "
        "--verbose --io-method=MMAP --min-frame-size=64"
    ),
    "ffmpeg": (
        "ffmpeg -hide_banner -loglevel {ffmpeg_loglevel} -fflags nobuffer "
        "-flags low_delay -tcp_nodelay 1 -f mpjpeg -i {ffmpeg_url} -map 0:v"
        "{filter_clause}{encoder_clause}{preset_clause}{tune_clause}{crf_clause}"
        "{pixel_format_clause} -f segment -segment_time {ffmpeg_segment_seconds} "
        "-segment_atclocktime 1 -reset_timestamps 1 -movflags +faststart "
        "-strftime 1 {segment_pattern}"
    ),
}


class CommandTemplates:
    """Proporciona acceso a las plantillas de comandos configurables."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._templates = DEFAULT_COMMAND_TEMPLATES.copy()
        self._load_from_file()

    def _load_from_file(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except json.JSONDecodeError as exc:  # noqa: PERF203
            logger.error(
                "Archivo de plantillas inválido '%s': %s", self.path, exc
            )
            return
        except OSError as exc:
            logger.error("No se pudo leer '%s': %s", self.path, exc)
            return

        if not isinstance(data, dict):
            logger.error(
                "El archivo de plantillas '%s' debe contener un objeto JSON.",
                self.path,
            )
            return

        for key, value in data.items():
            if key not in DEFAULT_COMMAND_TEMPLATES:
                logger.warning(
                    "Clave de comando desconocida '%s' en '%s'.", key, self.path
                )
                continue
            if isinstance(value, str) and value.strip():
                self._templates[key] = value
            else:
                logger.warning(
                    "El comando '%s' debe ser una cadena no vacía; se usa el valor por defecto.",
                    key,
                )

    def render(self, name: str, context: Dict[str, object]) -> list[str]:
        template = self._templates.get(name)
        if not template:
            raise KeyError(f"Comando '{name}' no disponible")
        normalized_context = {
            key: "" if value is None else str(value)
            for key, value in context.items()
        }
        try:
            command_string = template.format(**normalized_context)
        except KeyError as exc:  # noqa: PERF203
            missing = exc.args[0]
            raise KeyError(
                f"Falta la variable '{missing}' para el comando '{name}'"
            ) from exc
        return shlex.split(command_string)


command_templates = CommandTemplates(settings.COMMANDS_FILE)
