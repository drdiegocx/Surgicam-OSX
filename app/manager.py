"""Gestores de procesos para el Mini-DVR."""
from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .config import settings

logger = logging.getLogger("mini_dvr")


@dataclass
class ProcessInfo:
    """Metadatos del proceso de grabación."""

    start_time: datetime
    first_segment: str


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

    @property
    def is_preview_running(self) -> bool:
        return bool(
            self._ustreamer_process and self._ustreamer_process.poll() is None
        )

    @property
    def is_recording(self) -> bool:
        return bool(self._ffmpeg_process and self._ffmpeg_process.poll() is None)

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

    async def start_recording(self) -> Dict[str, Any]:
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
                "-c",
                "copy",
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
            )
            self._ffmpeg_monitor = asyncio.create_task(self._monitor_ffmpeg())

        event = {"status": "recording", "file": first_segment}
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
        return info
