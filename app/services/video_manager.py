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
    """Controls preview snapshots and high-resolution recordings using GStreamer."""

    def __init__(
        self,
        *,
        device: str = "/dev/video0",
        preview_resolution: tuple[int, int] = (640, 480),
        record_resolution: tuple[int, int] = (1920, 1080),
        record_dir: str | os.PathLike[str] = "recordings",
        preview_cache: str | os.PathLike[str] = "recordings/preview",
        gst_bin: str | None = None,
    ) -> None:
        self.device = device
        self.preview_resolution = preview_resolution
        self.record_resolution = record_resolution
        self.record_dir = Path(record_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir = Path(preview_cache)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.gst_bin = gst_bin or os.environ.get("GST_LAUNCH_BIN", "gst-launch-1.0")

        self._preview_proc: Optional[asyncio.subprocess.Process] = None
        self._recording_proc: Optional[asyncio.subprocess.Process] = None
        self._recording_path: Optional[Path] = None
        self._recording_started_at: Optional[dt.datetime] = None
        self._lock = asyncio.Lock()
        self._drain_tasks: set[asyncio.Task[None]] = set()

    @property
    def preview_running(self) -> bool:
        preview_alive = self._preview_proc is not None and self._preview_proc.returncode is None
        recording_alive = self._recording_proc is not None and self._recording_proc.returncode is None
        return preview_alive or recording_alive

    @property
    def preview_url(self) -> str:
        return "/preview.jpg"

    @property
    def recording_running(self) -> bool:
        return self._recording_proc is not None and self._recording_proc.returncode is None

    @property
    def recording_path(self) -> Optional[Path]:
        return self._recording_path

    @property
    def recording_started_at(self) -> Optional[dt.datetime]:
        return self._recording_started_at

    def latest_preview_frame(self) -> Optional[Path]:
        """Return the most recent preview frame written by GStreamer."""

        entries: list[tuple[float, Path]] = []
        for path in self.preview_dir.glob("frame_*.jpg"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            entries.append((mtime, path))

        if not entries:
            return None

        entries.sort(key=lambda item: item[0], reverse=True)
        return entries[0][1]

    async def read_preview_frame(self, frame_path: Path) -> Optional[bytes]:
        """Read a preview frame asynchronously, returning its bytes."""

        try:
            return await asyncio.to_thread(frame_path.read_bytes)
        except FileNotFoundError:
            return None
        except OSError as exc:  # pragma: no cover - depends on filesystem timing
            logger.debug("No se pudo leer el frame %s: %s", frame_path, exc)
            return None

    async def ensure_preview(self) -> None:
        """Start the preview snapshots pipeline if required."""

        async with self._lock:
            if self.preview_running:
                return

            await self._start_preview_locked()

    async def start_recording(self) -> Path:
        """Start a high-resolution recording and return the output directory."""

        async with self._lock:
            if self.recording_running:
                raise RuntimeError("Recording already in progress")

            recording_id = uuid.uuid4().hex
            output_dir = self.record_dir / f"recording_{recording_id}"
            output_dir.mkdir(parents=True, exist_ok=True)

            if self._preview_proc is not None and self._preview_proc.returncode is None:
                await self._stop_process(self._preview_proc)
            self._preview_proc = None

            commands = self._recording_commands(output_dir)
            attempts = len(commands)
            last_error: ProcessError | None = None
            for index, command in enumerate(commands, start=1):
                logger.debug(
                    "Launching recording command (%s/%s): %s",
                    index,
                    attempts,
                    " ".join(command),
                )
                try:
                    self._recording_proc = await self._spawn_process(command)
                except ProcessError as exc:
                    last_error = exc
                    logger.warning(
                        "No se pudo iniciar la grabación con el pipeline #%s/%s: %s",
                        index,
                        attempts,
                        exc,
                    )
                    continue
                if index > 1:
                    logger.warning(
                        "Grabación iniciada usando pipeline alternativo #%s",
                        index,
                    )
                break
            else:  # no break
                assert last_error is not None
                await self._start_preview_locked()
                raise last_error
            self._recording_path = output_dir
            self._recording_started_at = dt.datetime.utcnow()
            if self._recording_proc.pid is not None:
                width, height = self.record_resolution
                logger.info(
                    "Proceso de grabación iniciado (pid=%s) a %sx%s en %s",
                    self._recording_proc.pid,
                    width,
                    height,
                    output_dir,
                )
            return output_dir

    async def stop_recording(self) -> Optional[Path]:
        """Stop the recording process if running and return the output directory."""

        async with self._lock:
            if not self.recording_running:
                return None

            assert self._recording_proc is not None
            await self._stop_process(self._recording_proc)

            output = self._recording_path
            self._recording_proc = None
            self._recording_path = None
            self._recording_started_at = None

            await self._start_preview_locked()
            return output

    async def shutdown(self) -> None:
        """Terminate all managed processes."""

        async with self._lock:
            if self._preview_proc is not None:
                await self._stop_process(self._preview_proc)
                self._preview_proc = None

            if self._recording_proc is not None:
                await self._stop_process(self._recording_proc)
                self._recording_proc = None

            self._recording_path = None
            self._recording_started_at = None

            for task in list(self._drain_tasks):
                task.cancel()
            self._drain_tasks.clear()

    async def _start_preview_locked(self) -> None:
        commands = self._preview_commands()
        attempts = len(commands)
        last_error: ProcessError | None = None
        for index, command in enumerate(commands, start=1):
            logger.debug(
                "Launching preview command (%s/%s): %s",
                index,
                attempts,
                " ".join(command),
            )
            try:
                self._preview_proc = await self._spawn_process(command)
            except ProcessError as exc:
                last_error = exc
                logger.warning(
                    "No se pudo iniciar la vista previa con el pipeline #%s/%s: %s",
                    index,
                    attempts,
                    exc,
                )
                continue

            if index > 1:
                logger.warning(
                    "Vista previa iniciada usando pipeline alternativo #%s",
                    index,
                )
            break
        else:  # no break
            assert last_error is not None
            raise last_error

        if self._preview_proc and self._preview_proc.pid is not None:
            width, height = self.preview_resolution
            logger.info(
                "Vista previa iniciada (pid=%s) a %sx%s",
                self._preview_proc.pid,
                width,
                height,
            )

    def _preview_commands(self) -> list[list[str]]:
        width, height = self.preview_resolution
        location = str(self.preview_dir / "frame_%06d.jpg")

        base_tail = [
            "queue",
            "leaky=downstream",
            "max-size-buffers=1",
            "!",
            "multifilesink",
            f"location={location}",
            "max-files=5",
            "post-messages=true",
        ]

        commands: list[list[str]] = []

        def _mjpeg_command(*, use_dmabuf: bool, set_resolution: bool) -> list[str]:
            command = [
                self.gst_bin,
                "-q",
                "v4l2src",
                f"device={self.device}",
            ]
            if use_dmabuf:
                command.append("io-mode=dmabuf")
            command.extend([
                "!",
                "image/jpeg"
                + (f",width={width},height={height}" if set_resolution else ""),
                "!",
                *base_tail,
            ])
            return command

        commands.append(_mjpeg_command(use_dmabuf=True, set_resolution=True))
        commands.append(_mjpeg_command(use_dmabuf=True, set_resolution=False))
        commands.append(_mjpeg_command(use_dmabuf=False, set_resolution=True))
        commands.append(_mjpeg_command(use_dmabuf=False, set_resolution=False))

        transcode_command = [
            self.gst_bin,
            "-q",
            "v4l2src",
            f"device={self.device}",
            "!",
            "videoconvert",
            "!",
            "videoscale",
            "!",
            f"video/x-raw,width={width},height={height}",
            "!",
            "jpegenc",
            "quality=85",
            "!",
            *base_tail,
        ]
        commands.append(transcode_command)
        return commands

    def _recording_commands(self, output_dir: Path) -> list[list[str]]:
        record_location = str(output_dir / "frame_%06d.jpg")
        preview_location = str(self.preview_dir / "frame_%06d.jpg")
        r_width, r_height = self.record_resolution
        p_width, p_height = self.preview_resolution

        preview_branch = [
            "queue",
            "leaky=downstream",
            "max-size-buffers=1",
            "!",
            "jpegdec",
            "!",
            "videoscale",
            "!",
            f"video/x-raw,width={p_width},height={p_height}",
            "!",
            "videoconvert",
            "!",
            "jpegenc",
            "quality=85",
            "!",
            "multifilesink",
            f"location={preview_location}",
            "max-files=5",
            "post-messages=true",
        ]

        def _tee_pipeline(source: list[str]) -> list[str]:
            return [
                *source,
                "!",
                "tee",
                "name=t",
                "t.",
                "!",
                *preview_branch,
                "t.",
                "!",
                "queue",
                "!",
                "multifilesink",
                f"location={record_location}",
            ]

        def _mjpeg_source(*, use_dmabuf: bool, set_resolution: bool) -> list[str]:
            command = [
                self.gst_bin,
                "-q",
                "v4l2src",
                f"device={self.device}",
            ]
            if use_dmabuf:
                command.append("io-mode=dmabuf")
            caps = "image/jpeg"
            if set_resolution:
                caps += f",width={r_width},height={r_height}"
            command.extend(["!", caps])
            return command

        commands: list[list[str]] = []
        for use_dmabuf in (True, False):
            commands.append(_tee_pipeline(_mjpeg_source(use_dmabuf=use_dmabuf, set_resolution=True)))
            commands.append(_tee_pipeline(_mjpeg_source(use_dmabuf=use_dmabuf, set_resolution=False)))

        transcode_source = [
            self.gst_bin,
            "-q",
            "v4l2src",
            f"device={self.device}",
            "!",
            "videoconvert",
            "!",
            "videoscale",
            "!",
            f"video/x-raw,width={r_width},height={r_height}",
            "!",
            "jpegenc",
            "quality=90",
        ]
        commands.append(_tee_pipeline(transcode_source))

        return commands

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

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
