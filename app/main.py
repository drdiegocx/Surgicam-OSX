from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .services.video_manager import ProcessError, VideoManager

BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("surgicam")

app = FastAPI(title="SurgiCam Stream Controller")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

def _parse_resolution(env_var: str, fallback: tuple[int, int]) -> tuple[int, int]:
    value = os.getenv(env_var)
    if not value:
        return fallback
    try:
        width_str, height_str = value.lower().split("x", 1)
        width = int(width_str)
        height = int(height_str)
        if width <= 0 or height <= 0:
            raise ValueError
        return (width, height)
    except ValueError:
        logger.warning(
            "Valor inválido para %s=%r. Usando resolución por defecto %sx%s.",
            env_var,
            value,
            fallback[0],
            fallback[1],
        )
        return fallback


preview_resolution = _parse_resolution("PREVIEW_RES", (640, 480))
record_resolution = _parse_resolution("RECORD_RES", (1920, 1080))
device_path = os.getenv("DEVICE_PATH", "/dev/video0")

video_manager = VideoManager(
    device=device_path,
    preview_resolution=preview_resolution,
    record_resolution=record_resolution,
)

PREVIEW_FPS = 30


class ClientRegistry:
    """Tracks connected WebSocket clients for status broadcasts."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                await client.send_json(payload)
            except RuntimeError:
                await self.disconnect(client)

    async def broadcast_status(self) -> None:
        async with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                await client.send_json(_current_status(client))
            except RuntimeError:
                await self.disconnect(client)


clients = ClientRegistry()


def _current_status(_websocket: WebSocket | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {
        "type": "status",
        "preview_active": video_manager.preview_running,
        "recording": video_manager.recording_running,
        "recording_path": str(video_manager.recording_path) if video_manager.recording_path else None,
        "recording_started_at": video_manager.recording_started_at.isoformat() if video_manager.recording_started_at else None,
        "preview_stream": "websocket",
    }
    return status


@app.on_event("startup")
async def startup_event() -> None:
    logger.info(
        "Configuración de resolución - Vista previa: %sx%s, Grabación: %sx%s",
        preview_resolution[0],
        preview_resolution[1],
        record_resolution[0],
        record_resolution[1],
    )
    logger.info("Dispositivo de captura: %s", device_path)
    try:
        await video_manager.ensure_preview()
    except ProcessError as exc:
        logger.error("No se pudo iniciar la vista previa con GStreamer: %s", exc)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await video_manager.shutdown()


@app.get("/")
async def index() -> HTMLResponse:
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf8"))


@app.get("/preview.jpg")
async def preview_image() -> FileResponse:
    await video_manager.ensure_preview()
    frame_path = video_manager.latest_preview_frame()
    if frame_path is None or not frame_path.exists():
        raise HTTPException(status_code=503, detail="Vista previa no disponible")

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }
    return FileResponse(frame_path, media_type="image/jpeg", headers=headers)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await clients.connect(websocket)
    try:
        await websocket.send_json(_current_status(websocket))
        while True:
            message = await websocket.receive_json()
            action = message.get("action")
            if action == "start_recording":
                await _handle_start_recording(websocket)
            elif action == "stop_recording":
                await _handle_stop_recording(websocket)
            elif action == "refresh_status":
                await websocket.send_json(_current_status(websocket))
            else:
                await websocket.send_json({"type": "error", "detail": f"Unknown action: {action}"})
    except WebSocketDisconnect:
        pass
    finally:
        await clients.disconnect(websocket)


@app.websocket("/preview-stream")
async def preview_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        await video_manager.ensure_preview()
    except ProcessError as exc:
        await websocket.close(code=1011, reason=str(exc))
        return

    frame_interval = 1.0 / PREVIEW_FPS
    last_frame_path: Path | None = None
    last_mtime: float = 0.0
    last_payload: bytes | None = None

    try:
        while True:
            frame_path = video_manager.latest_preview_frame()
            if frame_path is not None:
                try:
                    mtime = frame_path.stat().st_mtime
                except (FileNotFoundError, OSError):
                    mtime = 0.0
                if (
                    last_payload is None
                    or frame_path != last_frame_path
                    or mtime > last_mtime
                ):
                    frame_bytes = await video_manager.read_preview_frame(frame_path)
                    if frame_bytes:
                        last_payload = frame_bytes
                        last_frame_path = frame_path
                        last_mtime = mtime

            if last_payload is not None:
                await websocket.send_bytes(last_payload)

            await asyncio.sleep(frame_interval)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error en la transmisión de vista previa: %s", exc)
        try:
            await websocket.close(code=1011, reason="preview stream error")
        except RuntimeError:
            pass


async def _handle_start_recording(websocket: WebSocket) -> None:
    try:
        output_path = await video_manager.start_recording()
    except ProcessError as exc:
        logger.error("Error al iniciar la grabación: %s", exc)
        await websocket.send_json({"type": "error", "detail": str(exc)})
        return
    except RuntimeError as exc:
        logger.warning("Grabación ya en curso: %s", exc)
        await websocket.send_json({"type": "error", "detail": str(exc)})
        return

    payload = {
        "type": "recording_started",
        "path": str(output_path),
        "started_at": video_manager.recording_started_at.isoformat() if video_manager.recording_started_at else None,
    }
    logger.info("Grabación iniciada en %s", output_path)
    await clients.broadcast(payload)
    await clients.broadcast_status()


async def _handle_stop_recording(websocket: WebSocket) -> None:
    output_path = await video_manager.stop_recording()
    payload = {
        "type": "recording_stopped",
        "path": str(output_path) if output_path else None,
    }
    if output_path is not None:
        logger.info("Grabación detenida. Archivo: %s", output_path)
    await clients.broadcast(payload)
    await clients.broadcast_status()
