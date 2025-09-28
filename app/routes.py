"""Rutas y websockets de la aplicación."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from threading import Lock
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

try:  # Compatibilidad con Pydantic v2 y v1
    from pydantic import model_validator as _model_validator
except ImportError:  # pragma: no cover - entornos con Pydantic v1
    _model_validator = None

if _model_validator is None:  # pragma: no cover - entornos con Pydantic v1
    from pydantic import root_validator as _root_validator
else:  # pragma: no cover - entornos con Pydantic v2
    _root_validator = None

from .config import settings
from .manager import RecorderManager
from .v4l2 import ControlInfo, V4L2Error, list_controls, reset_control, set_control

logger = logging.getLogger("mini_dvr")

router = APIRouter()
manager = RecorderManager()

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "app" / "templates"))


_controls_cache: List[ControlInfo] = []
_controls_cache_timestamp: float = 0.0
_controls_cache_lock = Lock()


def _controls_snapshot(force: bool = False) -> List[ControlInfo]:
    """Obtiene la lista de controles reutilizando un caché de corta duración."""

    global _controls_cache_timestamp

    now = time.monotonic()
    with _controls_cache_lock:
        if (
            not force
            and _controls_cache
            and now - _controls_cache_timestamp <= settings.CONTROLS_CACHE_TTL
        ):
            return list(_controls_cache)

    controls = list_controls()
    with _controls_cache_lock:
        _controls_cache.clear()
        _controls_cache.extend(controls)
        _controls_cache_timestamp = time.monotonic()
        return list(_controls_cache)


def _update_controls_cache(control: ControlInfo) -> None:
    with _controls_cache_lock:
        for index, existing in enumerate(_controls_cache):
            if existing.identifier == control.identifier:
                _controls_cache[index] = control
                break
        else:
            _controls_cache.append(control)
        # refresca el timestamp para que el caché continúe vigente
        global _controls_cache_timestamp
        _controls_cache_timestamp = time.monotonic()


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


class ControlUpdate(BaseModel):
    """Carga útil para actualizar controles V4L2."""

    value: Any | None = None
    action: str | None = None

    if _model_validator is not None:  # pragma: no branch - se evalúa en tiempo de importación

        @_model_validator(mode="after")
        def _validate(cls, model: "ControlUpdate") -> "ControlUpdate":
            value = model.value
            action = model.action
            if action is not None:
                if action != "default":
                    raise ValueError("Acción no soportada")
                if value is not None:
                    raise ValueError("No se puede combinar 'action' y 'value'")
            elif value is None:
                raise ValueError("Debe indicar un valor o una acción")
            return model

    elif _root_validator is not None:  # pragma: no branch - compatibilidad Pydantic v1

        @_root_validator
        def check_payload(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            value = values.get("value")
            action = values.get("action")
            if action is not None:
                if action != "default":
                    raise ValueError("Acción no soportada")
                if value is not None:
                    raise ValueError("No se puede combinar 'action' y 'value'")
            elif value is None:
                raise ValueError("Debe indicar un valor o una acción")
            return values


def _normalize_value(control: Dict[str, Any], raw_value: Any) -> Any:
    ctrl_type = (control.get("type") or "").lower()
    if raw_value is None:
        raise ValueError("Valor no proporcionado")
    if ctrl_type in {"bool", "boolean"}:
        if isinstance(raw_value, bool):
            return raw_value
        lowered = str(raw_value).strip().lower()
        if lowered in {"1", "true", "si", "sí"}:
            return True
        if lowered in {"0", "false", "no"}:
            return False
        raise ValueError("Valor booleano inválido")
    if ctrl_type in {"menu", "intmenu", "integer_menu", "integer menu"}:
        return int(raw_value)
    if ctrl_type in {"int", "integer", "int64"}:
        return int(float(raw_value))
    if ctrl_type in {"float", "double"}:
        return float(raw_value)
    return raw_value


def _validate_range(control: Dict[str, Any], value: Any) -> None:
    min_value = control.get("min")
    max_value = control.get("max")
    if isinstance(value, bool):
        numeric = 1 if value else 0
    else:
        numeric = value
    if min_value is not None and numeric < min_value:
        raise ValueError(
            f"El valor {numeric} es inferior al mínimo permitido ({min_value})"
        )
    if max_value is not None and numeric > max_value:
        raise ValueError(
            f"El valor {numeric} supera el máximo permitido ({max_value})"
        )


@router.get("/api/controls", response_class=JSONResponse)
async def get_controls(refresh: bool = False) -> JSONResponse:
    try:
        controls = await asyncio.to_thread(_controls_snapshot, refresh)
    except V4L2Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload: List[Dict[str, Any]] = [control.as_dict() for control in controls]
    return JSONResponse(status_code=200, content={"controls": payload})


@router.post("/api/controls/{identifier}", response_class=JSONResponse)
async def update_control(identifier: str, update: ControlUpdate) -> JSONResponse:
    try:
        controls = await asyncio.to_thread(_controls_snapshot)
    except V4L2Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    control_map = {item.identifier: item for item in controls}
    target = control_map.get(identifier)
    if target is None:
        raise HTTPException(status_code=404, detail="Control no encontrado")

    try:
        if update.action == "default":
            updated = await asyncio.to_thread(reset_control, identifier, target)
        else:
            normalized = _normalize_value(target.as_dict(), update.value)
            _validate_range(target.as_dict(), normalized)
            updated = await asyncio.to_thread(set_control, identifier, normalized, target)
    except V4L2Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _update_controls_cache(updated)
    return JSONResponse(status_code=200, content={"control": updated.as_dict()})


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
