from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
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


def _parse_float(env_var: str, fallback: float) -> float:
    value = os.getenv(env_var)
    if not value:
        return fallback
    try:
        parsed = float(value)
        if parsed <= 0:
            raise ValueError
        return parsed
    except ValueError:
        logger.warning(
            "Valor inválido para %s=%r. Usando valor por defecto %s.",
            env_var,
            value,
            fallback,
        )
        return fallback


def _parse_int(env_var: str, fallback: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    value = os.getenv(env_var)
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except ValueError:
        logger.warning("Valor inválido para %s=%r. Usando %s.", env_var, value, fallback)
        return fallback

    if minimum is not None and parsed < minimum:
        logger.warning(
            "Valor demasiado bajo para %s=%r. Usando %s.",
            env_var,
            value,
            fallback,
        )
        return fallback
    if maximum is not None and parsed > maximum:
        logger.warning(
            "Valor demasiado alto para %s=%r. Usando %s.",
            env_var,
            value,
            fallback,
        )
        return fallback
    return parsed


preview_resolution = _parse_resolution("PREVIEW_RESOLUTION", (640, 480))
record_resolution = _parse_resolution("RECORD_RESOLUTION", (1920, 1080))
preview_fps = _parse_float("PREVIEW_FPS", 15.0)
record_fps = _parse_float("RECORD_FPS", 30.0)
jpeg_quality = _parse_int("PREVIEW_JPEG_QUALITY", 80, minimum=10, maximum=100)

video_manager = VideoManager(
    device=os.getenv("VIDEO_DEVICE", "/dev/video0"),
    preview_resolution=preview_resolution,
    record_resolution=record_resolution,
    preview_fps=preview_fps,
    record_fps=record_fps,
    jpeg_quality=jpeg_quality,
)


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


def _current_status(websocket: WebSocket | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {
        "type": "status",
        "preview_active": video_manager.preview_running,
        "preview_fps": video_manager.preview_fps,
        "recording": video_manager.recording_running,
        "recording_path": str(video_manager.recording_path) if video_manager.recording_path else None,
        "recording_started_at": video_manager.recording_started_at.isoformat() if video_manager.recording_started_at else None,
    }
    return status


@app.on_event("startup")
async def startup_event() -> None:
    logger.info(
        "Configuración de video - Vista previa: %sx%s@%sfps, Grabación: %sx%s@%sfps",
        preview_resolution[0],
        preview_resolution[1],
        preview_fps,
        record_resolution[0],
        record_resolution[1],
        record_fps,
    )
    try:
        await video_manager.ensure_preview()
    except ProcessError as exc:
        logger.error("No se pudo iniciar la cámara: %s", exc)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await video_manager.shutdown()


@app.get("/")
async def index() -> HTMLResponse:
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf8"))


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


@app.websocket("/ws/preview")
async def preview_stream(websocket: WebSocket) -> None:
    try:
        await video_manager.ensure_preview()
    except ProcessError as exc:
        await websocket.close(code=1011, reason=str(exc))
        return
    await websocket.accept()
    token, queue = video_manager.subscribe_preview()
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                break
            await websocket.send_bytes(frame)
    except WebSocketDisconnect:
        pass
    finally:
        video_manager.unsubscribe_preview(token)


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
