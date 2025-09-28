"""Video streaming and recording process management utilities."""

import asyncio
import datetime as dt
import logging
import os
import signal
import uuid
from pathlib import Path
from typing import Optional


class ProcessError(RuntimeError):
    """Raised when a managed process fails to start."""


logger = logging.getLogger("surgicam.video")


class VideoManager:
    """Controls the preview stream and high-resolution recordings.

    Parameters
    ----------
    device : str
        Path to the camera device (e.g. ``"/dev/video0"``).
    preview_host : str
        Network interface for the ustreamer preview service.
    preview_port : int
        Port for the ustreamer preview service.
    preview_resolution : tuple[int, int]
        Width and height of the low-resolution preview stream.
    record_resolution : tuple[int, int]
        Width and height of the high-resolution recording.
    record_dir : str | os.PathLike[str]
        Directory where recordings should be stored.
    ustreamer_bin : str
        Path to the ``ustreamer`` executable.
    ffmpeg_bin : str
        Path to the ``ffmpeg`` executable used for recordings.
    """

    def __init__(
        self,
        *,
        device: str = "/dev/video0",
        preview_host: str = "0.0.0.0",
        preview_port: int = 8080,
        preview_resolution: tuple[int, int] = (640, 480),
        record_resolution: tuple[int, int] = (1920, 1080),
        record_dir: str | os.PathLike[str] = "recordings",
        ustreamer_bin: str | None = None,
        ffmpeg_bin: str | None = None,
    ) -> None:
        self.device = device
        self.preview_host = preview_host
        self.preview_port = preview_port
        self.preview_resolution = preview_resolution
        self.record_resolution = record_resolution
        self.record_dir = Path(record_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.ustreamer_bin = ustreamer_bin or os.environ.get("USTREAMER_BIN", "/usr/bin/ustreamer")
        self.ffmpeg_bin = ffmpeg_bin or os.environ.get("FFMPEG_BIN", "ffmpeg")

        self._preview_proc: Optional[asyncio.subprocess.Process] = None
        self._recording_proc: Optional[asyncio.subprocess.Process] = None
        self._recording_path: Optional[Path] = None
        self._recording_started_at: Optional[dt.datetime] = None
        self._lock = asyncio.Lock()
        self._drain_tasks: set[asyncio.Task[None]] = set()

    @property
    def preview_running(self) -> bool:
        return self._preview_proc is not None and self._preview_proc.returncode is None

    @property
    def preview_url(self) -> str:
        host = self.preview_host
        if host in {"0.0.0.0", "::"}:
            host = "localhost"
        return f"http://{host}:{self.preview_port}/stream"

    @property
    def recording_running(self) -> bool:
        return self._recording_proc is not None and self._recording_proc.returncode is None

    @property
    def recording_path(self) -> Optional[Path]:
        return self._recording_path

    @property
    def recording_started_at(self) -> Optional[dt.datetime]:
        return self._recording_started_at

    async def ensure_preview(self) -> None:
        """Start the preview stream if it is not already running."""

        async with self._lock:
            if self.preview_running:
                return

            width, height = self.preview_resolution
            command = [
                self.ustreamer_bin,
                "--device",
                self.device,
                "--host",
                self.preview_host,
                "--port",
                str(self.preview_port),
                "--format",
                "MJPEG",
                "--resolution",
                f"{width}x{height}",
                "--persistent",
                "--allow-origin",
                "*",
            ]

            logger.debug("Launching preview command: %s", " ".join(command))
            self._preview_proc = await self._spawn_process(command)
            if self._preview_proc.pid is not None:
                logger.info("Vista previa iniciada (pid=%s) a %sx%s", self._preview_proc.pid, width, height)

    async def start_recording(self) -> Path:
        """Start a high-resolution recording.

        Returns
        -------
        pathlib.Path
            The path to the recording file.
        """

        async with self._lock:
            if self.recording_running:
                raise RuntimeError("Recording already in progress")

            width, height = self.record_resolution
            recording_id = uuid.uuid4().hex
            output_path = self.record_dir / f"recording_{recording_id}.mp4"

            command = [
                self.ffmpeg_bin,
                "-y",
                "-f",
                "v4l2",
                "-input_format",
                "mjpeg",
                "-video_size",
                f"{width}x{height}",
                "-i",
                self.device,
                "-vcodec",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]

            logger.debug("Launching recording command: %s", " ".join(command))
            self._recording_proc = await self._spawn_process(command)
            self._recording_path = output_path
            self._recording_started_at = dt.datetime.utcnow()
            if self._recording_proc.pid is not None:
                logger.info(
                    "Proceso de grabación iniciado (pid=%s) a %sx%s en %s",
                    self._recording_proc.pid,
                    width,
                    height,
                    output_path,
                )
            return output_path

    async def stop_recording(self) -> Optional[Path]:
        """Stop the recording process if running and return the output path."""

        async with self._lock:
            if not self.recording_running:
                return None

            assert self._recording_proc is not None
            self._recording_proc.send_signal(signal.SIGINT)
            await self._recording_proc.wait()

            output = self._recording_path
            self._recording_proc = None
            self._recording_path = None
            self._recording_started_at = None
            return output

    async def shutdown(self) -> None:
        """Terminate all managed processes."""

        async with self._lock:
            if self.preview_running:
                assert self._preview_proc is not None
                self._preview_proc.terminate()
                await self._preview_proc.wait()
                self._preview_proc = None

            if self.recording_running:
                assert self._recording_proc is not None
                self._recording_proc.terminate()
                await self._recording_proc.wait()
                self._recording_proc = None
            self._recording_path = None
            self._recording_started_at = None

            for task in list(self._drain_tasks):
                task.cancel()
            self._drain_tasks.clear()

    async def _spawn_process(self, command: list[str]) -> asyncio.subprocess.Process:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:  # pragma: no cover - environment specific
            raise ProcessError(f"Executable not found: {command[0]}") from exc

        await asyncio.sleep(0.1)
        logger.debug("Spawned %s with pid=%s", command[0], getattr(process, "pid", "?"))
        if process.returncode is not None:
            stderr = await process.stderr.read()
            logger.error(
                "Proceso %s finalizó inmediatamente con código %s", command[0], process.returncode
            )
            raise ProcessError(
                f"Failed to start process {command[0]} (code={process.returncode}). "
                f"Stderr: {stderr.decode().strip()}"
            )

        self._start_drain_task(process.stderr, command[0])
        return process

    def _start_drain_task(self, stream: asyncio.StreamReader | None, name: str) -> None:
        if stream is None:
            return

        async def _drain() -> None:
            try:
                while True:
                    chunk = await stream.readline()
                    if not chunk:
                        break
                    logger.debug("%s: %s", name, chunk.decode(errors="ignore").rstrip())
            except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
                raise
            except Exception:  # pragma: no cover - best effort logging
                logger.exception("Error draining output for %%s", name)

        task = asyncio.create_task(_drain())
        self._drain_tasks.add(task)

        def _cleanup(t: asyncio.Task[None]) -> None:
            self._drain_tasks.discard(t)
        task.add_done_callback(_cleanup)
