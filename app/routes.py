"""Rutas y websockets de la aplicación."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .config import settings
from .manager import RecorderManager

logger = logging.getLogger("mini_dvr")

router = APIRouter()
manager = RecorderManager()

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "app" / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    preview_port = settings.USTREAMER_PORT
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "preview_port": preview_port,
        },
    )


@router.get("/health", response_class=JSONResponse)
async def health() -> JSONResponse:
    status = manager.status_snapshot()
    if status["preview"] != "running":
        raise HTTPException(status_code=503, detail="uStreamer no está disponible")
    return JSONResponse(status_code=200, content=status)


@router.get("/status", response_class=JSONResponse)
async def status() -> JSONResponse:
    return JSONResponse(status_code=200, content=manager.status_snapshot())


async def _event_forwarder(websocket: WebSocket, queue: asyncio.Queue) -> None:
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Finalizando reenviador de eventos: %s", exc)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = await manager.events.register()
    forward_task = asyncio.create_task(_event_forwarder(websocket, queue))
    try:
        await websocket.send_json({"status": "snapshot", **manager.status_snapshot()})
        while True:
            message = await websocket.receive_text()
            try:
                payload: Dict[str, Any] = json.loads(message)
            except json.JSONDecodeError as exc:
                logger.error("Mensaje WebSocket inválido: %s", exc)
                await websocket.send_json(
                    {
                        "status": "error",
                        "detail": "Formato de mensaje inválido.",
                    }
                )
                continue
            command = payload.get("command")
            if command == "start":
                try:
                    response = await manager.start_recording()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error al iniciar grabación: %s", exc)
                    await websocket.send_json(
                        {
                            "status": "error",
                            "detail": "No se pudo iniciar la grabación.",
                        }
                    )
                else:
                    await websocket.send_json(response)
            elif command == "stop":
                try:
                    response = await manager.stop_recording()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error al detener grabación: %s", exc)
                    await websocket.send_json(
                        {
                            "status": "error",
                            "detail": "No se pudo detener la grabación.",
                        }
                    )
                else:
                    await websocket.send_json(response)
            else:
                await websocket.send_json(
                    {
                        "status": "error",
                        "detail": "Comando no reconocido.",
                    }
                )
    except WebSocketDisconnect:
        logger.info("Cliente WebSocket desconectado")
    finally:
        forward_task.cancel()
        await manager.events.unregister(queue)
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
