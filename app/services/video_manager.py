"""Video streaming and recording management using a single UVC device."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


class ProcessError(RuntimeError):
    """Raised when the camera or recorder cannot be initialised."""


logger = logging.getLogger("surgicam.video")


class VideoManager:
    """Capture frames from a UVC device and provide preview/recording.

    A background thread pulls frames from the configured device. Preview
    subscribers receive downscaled JPEG frames via asyncio queues so the
    FastAPI application can forward them over WebSocket connections. When
    a recording is requested, the same frames are written to disk without
    interrupting the preview flow.
    """

    def __init__(
        self,
        *,
        device: str = "/dev/video0",
        preview_resolution: tuple[int, int] = (640, 480),
        record_resolution: tuple[int, int] = (1920, 1080),
        preview_fps: float = 15.0,
        record_fps: float = 30.0,
        jpeg_quality: int = 80,
        record_dir: str | Path = "recordings",
    ) -> None:
        self.device = device
        self.preview_resolution = preview_resolution
        self.record_resolution = record_resolution
        self.preview_fps = preview_fps
        self.record_fps = record_fps
        self.jpeg_quality = max(1, min(int(jpeg_quality), 100))
        self.record_dir = Path(record_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)

        self._async_lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._preview_consumers: dict[str, asyncio.Queue[Optional[bytes]]] = {}

        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._running = threading.Event()
        self._last_error: Optional[str] = None

        self._record_lock = threading.Lock()
        self._record_writer: Optional[cv2.VideoWriter] = None
        self._recording_path: Optional[Path] = None
        self._recording_started_at: Optional[dt.datetime] = None

        self._preview_interval = 1.0 / self.preview_fps if self.preview_fps > 0 else 0.0
        self._last_preview_timestamp = 0.0

    # ------------------------------------------------------------------
    # Properties for external status inspection
    @property
    def preview_running(self) -> bool:
        return self._running.is_set()

    @property
    def recording_running(self) -> bool:
        with self._record_lock:
            return self._record_writer is not None

    @property
    def recording_path(self) -> Optional[Path]:
        return self._recording_path

    @property
    def recording_started_at(self) -> Optional[dt.datetime]:
        return self._recording_started_at

    # ------------------------------------------------------------------
    async def ensure_preview(self) -> None:
        """Start the capture thread when required."""

        async with self._async_lock:
            if self.preview_running:
                return

            self._loop = asyncio.get_running_loop()
            self._stop_event.clear()
            self._ready_event.clear()
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="surgicam-capture",
                daemon=True,
            )
            self._capture_thread.start()

        # Wait for the capture thread to confirm startup or error
        await asyncio.to_thread(self._ready_event.wait)

        if not self.preview_running:
            error = self._last_error or "Camera thread failed to start"
            raise ProcessError(error)

    async def start_recording(self) -> Path:
        async with self._async_lock:
            if not self.preview_running:
                await self.ensure_preview()

            if self.recording_running:
                raise RuntimeError("Recording already in progress")

            recording_id = uuid.uuid4().hex
            output_path = self.record_dir / f"recording_{recording_id}.mp4"
            width, height = self.record_resolution

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                str(output_path),
                fourcc,
                float(self.record_fps),
                (int(width), int(height)),
            )

            if not writer.isOpened():
                writer.release()
                raise ProcessError("No se pudo inicializar el grabador de video")

            with self._record_lock:
                self._record_writer = writer
                self._recording_path = output_path
                self._recording_started_at = dt.datetime.utcnow()

            logger.info("Grabación iniciada en %s", output_path)
            return output_path

    async def stop_recording(self) -> Optional[Path]:
        async with self._async_lock:
            if not self.recording_running:
                return None

            with self._record_lock:
                writer = self._record_writer
                self._record_writer = None
                output = self._recording_path
                self._recording_path = None
                self._recording_started_at = None

            if writer is not None:
                writer.release()
                logger.info("Grabación detenida")

            return output

    async def shutdown(self) -> None:
        async with self._async_lock:
            if not self.preview_running and not self.recording_running:
                return

            self._stop_event.set()

        if self._capture_thread is not None:
            await asyncio.to_thread(self._capture_thread.join)
            self._capture_thread = None

        with self._record_lock:
            writer = self._record_writer
            self._record_writer = None
            self._recording_path = None
            self._recording_started_at = None

        if writer is not None:
            writer.release()

        self._running.clear()
        self._ready_event.clear()
        self._stop_event.clear()

        # Notify any waiting preview subscribers so they can exit cleanly
        for queue in list(self._preview_consumers.values()):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._preview_consumers.clear()

    # ------------------------------------------------------------------
    def subscribe_preview(self) -> tuple[str, asyncio.Queue[Optional[bytes]]]:
        if self._loop is None:
            raise RuntimeError("Preview loop not initialised")

        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=1)
        token = uuid.uuid4().hex
        self._preview_consumers[token] = queue
        return token, queue

    def unsubscribe_preview(self, token: str) -> None:
        queue = self._preview_consumers.pop(token, None)
        if queue is not None:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    def _capture_loop(self) -> None:
        try:
            capture = cv2.VideoCapture(self.device)
            if not capture.isOpened():
                self._last_error = f"No se pudo abrir la cámara {self.device}"
                logger.error(self._last_error)
                return

            width, height = self.record_resolution
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
            if self.record_fps > 0:
                capture.set(cv2.CAP_PROP_FPS, float(self.record_fps))

            self._running.set()
            self._last_error = None
            self._ready_event.set()

            jpeg_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]

            while not self._stop_event.is_set():
                ok, frame = capture.read()
                if not ok:
                    logger.warning("Fallo al leer frame de la cámara")
                    time.sleep(0.05)
                    continue

                with self._record_lock:
                    writer = self._record_writer
                if writer is not None:
                    writer.write(frame)

                now = time.monotonic()
                if self._preview_interval == 0 or now - self._last_preview_timestamp >= self._preview_interval:
                    preview_frame = self._prepare_preview_frame(frame)
                    if preview_frame is not None:
                        success, buffer = cv2.imencode(".jpg", preview_frame, jpeg_params)
                        if success:
                            self._schedule_broadcast(buffer.tobytes())
                    self._last_preview_timestamp = now

        except Exception:  # pragma: no cover - camera failures are hardware specific
            logger.exception("Error en el hilo de captura de video")
            self._last_error = "Fallo inesperado en la captura de video"
        finally:
            self._running.clear()
            self._ready_event.set()
            for queue in list(self._preview_consumers.values()):
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            self._capture_thread = None
            try:
                capture.release()
            except UnboundLocalError:
                # capture was never created successfully
                pass

    def _prepare_preview_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        width, height = self.preview_resolution
        if width <= 0 or height <= 0:
            return frame
        try:
            return cv2.resize(frame, (int(width), int(height)))
        except Exception:  # pragma: no cover - defensive programming
            logger.exception("No se pudo redimensionar el frame de vista previa")
            return frame

    def _schedule_broadcast(self, frame_bytes: bytes) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(self._broadcast_frame, frame_bytes)

    def _broadcast_frame(self, frame_bytes: bytes) -> None:
        for queue in list(self._preview_consumers.values()):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame_bytes)
            except asyncio.QueueFull:
                # Another coroutine pushed a sentinel concurrently; ignore
                pass

