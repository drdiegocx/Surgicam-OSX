"""Gestores de procesos para el Mini-DVR."""
from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from .config import settings

logger = logging.getLogger("mini_dvr")


@dataclass
class Roi:
    """Representación normalizada de un recorte ROI."""

    x: float
    y: float
    width: float
    height: float
    zoom: float = 1.0

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Roi":
        try:
            raw_x = float(payload.get("x", 0.0))
            raw_y = float(payload.get("y", 0.0))
            raw_width = float(payload.get("width", 1.0))
            raw_height = float(payload.get("height", 1.0))
            raw_zoom = float(payload.get("zoom", 1.0))
        except (TypeError, ValueError) as exc:  # noqa: BLE001
            raise ValueError("Valores de ROI inválidos") from exc

        width = max(0.01, min(1.0, raw_width))
        height = max(0.01, min(1.0, raw_height))
        x = max(0.0, min(raw_x, 1.0 - width))
        y = max(0.0, min(raw_y, 1.0 - height))
        zoom = max(1.0, raw_zoom)
        return cls(x=x, y=y, width=width, height=height, zoom=zoom)

    def is_full_frame(self) -> bool:
        return (
            abs(self.x) < 1e-3
            and abs(self.y) < 1e-3
            and abs(self.width - 1.0) < 1e-3
            and abs(self.height - 1.0) < 1e-3
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
            "zoom": round(self.zoom, 4),
        }


@dataclass
class ProcessInfo:
    """Metadatos del proceso de grabación."""

    start_time: datetime
    first_segment: str
    roi: Optional[Roi] = None


class EventBroker:
    """Publicador simple para eventos asincrónicos."""

    def __init__(self) -> None:
        self._listeners: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def register(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._listeners.add(queue)
        return queue

    async def unregister(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._listeners.discard(queue)

    async def broadcast(self, event: Dict[str, Any]) -> None:
        async with self._lock:
            listeners = list(self._listeners)
        for queue in listeners:
            await queue.put(event)


class RecorderManager:
    """Controla la vista previa y las grabaciones del sistema."""

    def __init__(self) -> None:
        self.recordings_dir: Path = settings.RECORDINGS_DIR
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self._ustreamer_process: Optional[subprocess.Popen] = None
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._ffmpeg_info: Optional[ProcessInfo] = None
        self._ffmpeg_monitor: Optional[asyncio.Task] = None
        self._stop_requested: bool = False
        self._lock = asyncio.Lock()
        self.events = EventBroker()
        self._source_resolution: Tuple[int, int] = self._parse_resolution(
            settings.USTREAMER_RESOLUTION
        )

    @property
    def is_preview_running(self) -> bool:
        return bool(
            self._ustreamer_process and self._ustreamer_process.poll() is None
        )

    @property
    def is_recording(self) -> bool:
        return bool(self._ffmpeg_process and self._ffmpeg_process.poll() is None)

    @property
    def source_resolution(self) -> Tuple[int, int]:
        return self._source_resolution

    async def ensure_preview(self) -> None:
        if self.is_preview_running:
            return
        command = [
            "ustreamer",
            f"--device={settings.USTREAMER_DEVICE}",
            "--format=MJPEG",
            "--encoder=HW",
            f"--resolution={settings.USTREAMER_RESOLUTION}",
            f"--desired-fps={settings.USTREAMER_FPS}",
            "--allow-origin=*",
            "--host",
            settings.USTREAMER_HOST,
            "--port",
            str(settings.USTREAMER_PORT),
            "--persistent",
        ]
        logger.info("Iniciando uStreamer con comando: %s", " ".join(command))
        try:
            self._ustreamer_process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            logger.error("No se encontró uStreamer: %s", exc)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Error al iniciar uStreamer: %s", exc)
            raise

    @staticmethod
    def _parse_resolution(resolution: str) -> Tuple[int, int]:
        try:
            width_str, height_str = resolution.lower().split("x", maxsplit=1)
            width = int(width_str)
            height = int(height_str)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Resolución '%s' inválida, usando 1280x720 por defecto.",
                resolution,
            )
            return (1280, 720)
        if width <= 0 or height <= 0:
            logger.warning(
                "Resolución '%s' con dimensiones no positivas, usando 1280x720.",
                resolution,
            )
            return (1280, 720)
        return (width, height)

    @staticmethod
    def _even(value: int) -> int:
        if value % 2 == 0:
            return value
        if value > 1:
            return value - 1
        return value + 1

    def _compute_crop_box(self, roi: Roi) -> Tuple[int, int, int, int]:
        source_width, source_height = self._source_resolution
        crop_width = min(source_width, max(16, round(source_width * roi.width)))
        crop_height = min(source_height, max(16, round(source_height * roi.height)))
        crop_width = self._even(crop_width)
        crop_height = self._even(crop_height)

        max_x = max(0, source_width - crop_width)
        max_y = max(0, source_height - crop_height)
        crop_x = min(max(0, round(source_width * roi.x)), max_x)
        crop_y = min(max(0, round(source_height * roi.y)), max_y)
        crop_x = min(max_x, self._even(crop_x))
        crop_y = min(max_y, self._even(crop_y))

        return crop_x, crop_y, crop_width, crop_height

    def _build_ffmpeg_command(
        self, segment_pattern: str, roi: Optional[Roi]
    ) -> Tuple[list[str], Optional[Tuple[int, int, int, int]]]:
        command = [
            "ffmpeg",
            "-loglevel",
            "info",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-tcp_nodelay",
            "1",
            "-f",
            "mpjpeg",
            "-i",
            settings.FFMPG_URL,
            "-map",
            "0:v",
        ]

        filters = []
        crop_box: Optional[Tuple[int, int, int, int]] = None
        if roi and not roi.is_full_frame():
            crop_box = self._compute_crop_box(roi)
            x, y, width, height = crop_box
            filters.append(f"crop={width}:{height}:{x}:{y}")

        if filters:
            command.extend(["-vf", ",".join(filters)])
            encoder = settings.FFMPEG_CROP_ENCODER or "libx264"
            command.extend(["-c:v", encoder])
            preset = settings.FFMPEG_CROP_PRESET
            pixel_format = settings.FFMPEG_CROP_PIXEL_FORMAT
            if encoder == "libx264":
                if preset:
                    command.extend(["-preset", preset])
                command.extend(["-crf", str(settings.FFMPEG_CROP_CRF)])
                if pixel_format:
                    command.extend(["-pix_fmt", pixel_format])
            else:
                if preset:
                    command.extend(["-preset", preset])
                if pixel_format:
                    command.extend(["-pix_fmt", pixel_format])
        else:
            command.extend(["-c", "copy"])

        command.extend(
            [
                "-f",
                "segment",
                "-segment_time",
                str(settings.FFMPG_SEGMENT_SECONDS),
                "-segment_atclocktime",
                "1",
                "-reset_timestamps",
                "1",
                "-movflags",
                "+faststart",
                "-strftime",
                "1",
                segment_pattern,
            ]
        )
        return command, crop_box

    async def start_recording(self, roi: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with self._lock:
            if self.is_recording:
                logger.warning("Se solicitó iniciar grabación, pero ya está activa.")
                return {
                    "status": "recording",
                    "file": self._ffmpeg_info.first_segment if self._ffmpeg_info else "",
                }
            await self.ensure_preview()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            first_segment = f"{timestamp}.mp4"
            segment_pattern = str(self.recordings_dir / "%Y%m%d_%H%M%S.mp4")
            roi_obj: Optional[Roi] = None
            if roi is not None:
                try:
                    roi_obj = Roi.from_payload(roi)
                except ValueError as exc:
                    logger.error("ROI inválido recibido: %s", exc)
                    raise
            command, crop_box = self._build_ffmpeg_command(segment_pattern, roi_obj)
            logger.info("Iniciando grabación con comando: %s", " ".join(command))
            self._stop_requested = False
            try:
                self._ffmpeg_process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError as exc:
                logger.error("No se encontró FFmpeg: %s", exc)
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Error al iniciar FFmpeg: %s", exc)
                raise
            self._ffmpeg_info = ProcessInfo(
                start_time=datetime.now(),
                first_segment=first_segment,
                roi=roi_obj,
            )
            self._ffmpeg_monitor = asyncio.create_task(self._monitor_ffmpeg())

        event: Dict[str, Any] = {"status": "recording", "file": first_segment}
        if roi_obj:
            event["roi"] = roi_obj.as_dict()
            if crop_box:
                event["crop"] = {
                    "x": crop_box[0],
                    "y": crop_box[1],
                    "width": crop_box[2],
                    "height": crop_box[3],
                }
        await self.events.broadcast(event)
        return event

    async def stop_recording(self) -> Dict[str, Any]:
        async with self._lock:
            if not self.is_recording or not self._ffmpeg_process:
                logger.warning("Se solicitó detener grabación, pero no había proceso activo.")
                return {"status": "idle"}
            self._stop_requested = True
            logger.info("Deteniendo proceso de grabación.")
            self._ffmpeg_process.send_signal(signal.SIGINT)
            await asyncio.to_thread(self._ffmpeg_process.wait)
            last_segment = (
                self._ffmpeg_info.first_segment if self._ffmpeg_info else None
            )
            self._ffmpeg_process = None
            self._ffmpeg_info = None
            if self._ffmpeg_monitor:
                self._ffmpeg_monitor.cancel()
                self._ffmpeg_monitor = None
            self._stop_requested = False

        event = {"status": "idle"}
        if last_segment:
            event["file"] = last_segment
        await self.events.broadcast(event)
        return event

    async def _monitor_ffmpeg(self) -> None:
        process = self._ffmpeg_process
        if not process:
            return
        returncode = await asyncio.to_thread(process.wait)
        if self._stop_requested:
            logger.info("FFmpeg se detuvo correctamente con código %s", returncode)
            self._stop_requested = False
            return
        self._ffmpeg_process = None
        self._ffmpeg_info = None
        logger.error("FFmpeg finalizó inesperadamente con código %s", returncode)
        await self.events.broadcast(
            {
                "status": "error",
                "detail": "La grabación se interrumpió de forma inesperada.",
            }
        )
        await self.events.broadcast({"status": "idle"})

    async def shutdown(self) -> None:
        logger.info("Cerrando Mini-DVR.")
        if self.is_recording and self._ffmpeg_process:
            self._stop_requested = True
            self._ffmpeg_process.send_signal(signal.SIGINT)
            await asyncio.to_thread(self._ffmpeg_process.wait)
            self._stop_requested = False
        if self.is_preview_running and self._ustreamer_process:
            logger.info("Deteniendo uStreamer.")
            self._ustreamer_process.terminate()
            try:
                await asyncio.to_thread(self._ustreamer_process.wait, timeout=5)
            except TypeError:
                await asyncio.to_thread(self._ustreamer_process.wait)
        self._ustreamer_process = None
        self._ffmpeg_process = None
        self._ffmpeg_info = None
        if self._ffmpeg_monitor:
            self._ffmpeg_monitor.cancel()
            self._ffmpeg_monitor = None

    def status_snapshot(self) -> Dict[str, Any]:
        """Obtiene el estado actual para API y health-check."""

        info: Dict[str, Any] = {
            "preview": "running" if self.is_preview_running else "stopped",
            "recording": "running" if self.is_recording else "stopped",
        }
        if self._ffmpeg_info:
            info["current_file"] = self._ffmpeg_info.first_segment
            info["recording_started_at"] = self._ffmpeg_info.start_time.isoformat()
            if self._ffmpeg_info.roi:
                info["roi"] = self._ffmpeg_info.roi.as_dict()
        return info
