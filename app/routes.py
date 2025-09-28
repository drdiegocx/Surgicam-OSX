"""Rutas y websockets de la aplicación."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, List

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.responses import StreamingResponse

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


async def _list_controls_async(refresh: bool = False) -> List[ControlInfo]:
    return await asyncio.to_thread(_controls_snapshot, refresh)


async def _controls_payload(refresh: bool = False) -> List[Dict[str, Any]]:
    controls = await _list_controls_async(refresh)
    return [control.as_dict() for control in controls]


async def _apply_control_update(
    identifier: str, *, value: Any | None, action: str | None
) -> ControlInfo:
    if action is not None and action != "default":
        raise ValueError("Acción no soportada")
    if action is None and value is None:
        raise ValueError("Debe indicar un valor o una acción")

    controls = await _list_controls_async()
    control_map = {item.identifier: item for item in controls}
    target = control_map.get(identifier)
    if target is None:
        raise LookupError("Control no encontrado")

    if action == "default":
        updated = await asyncio.to_thread(reset_control, identifier, target)
    else:
        normalized = _normalize_value(target.as_dict(), value)
        _validate_range(target.as_dict(), normalized)
        updated = await asyncio.to_thread(set_control, identifier, normalized, target)

    _update_controls_cache(updated)
    return updated


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    preview_port = settings.USTREAMER_PORT
    source_width, source_height = manager.source_resolution
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "preview_port": preview_port,
            "source_width": source_width,
            "source_height": source_height,
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


@router.get("/api/media", response_class=JSONResponse)
async def media_index() -> JSONResponse:
    return JSONResponse(status_code=200, content=manager.list_media())


_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def _iter_file_chunks(path: Path, start: int, end: int, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Lee un archivo en segmentos delimitados por rango."""

    with path.open("rb") as file_obj:
        file_obj.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = file_obj.read(read_size)
            if not data:
                break
            remaining -= len(data)
            yield data


def _serve_video_file(path: Path, request: Request) -> Response:
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if range_header:
        match = _RANGE_RE.fullmatch(range_header.strip())
        if not match:
            raise HTTPException(status_code=416, detail="Encabezado Range inválido.")
        start = int(match.group(1))
        end_group = match.group(2)
        end = int(end_group) if end_group else file_size - 1
        if start >= file_size or end < start:
            raise HTTPException(status_code=416, detail="Rango fuera de los límites del recurso.")
        end = min(end, file_size - 1)
        chunk_generator = _iter_file_chunks(path, start, end)
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Disposition": f"inline; filename={path.name}",
        }
        return StreamingResponse(
            chunk_generator,
            status_code=206,
            media_type="video/mp4",
            headers=headers,
        )

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename={path.name}",
    }
    return FileResponse(path, filename=path.name, media_type="video/mp4", headers=headers)


@router.get("/media/{category}/{name}")
async def media_download(category: str, name: str, request: Request) -> Response:
    try:
        path = manager.resolve_media_path(category, name)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.") from exc
    if category == "videos":
        return _serve_video_file(path, request)

    media_type = None
    if category == "photos":
        media_type = "image/jpeg"

    return FileResponse(path, filename=path.name, media_type=media_type)


@router.delete("/api/media/{category}/{name}", response_class=JSONResponse)
async def media_delete(category: str, name: str) -> JSONResponse:
    try:
        payload = await manager.delete_media(category, name)
    except ValueError as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Error al eliminar medio: %s", exc)
        raise HTTPException(status_code=500, detail="No se pudo eliminar el recurso.") from exc
    return JSONResponse(status_code=200, content=payload)


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
        payload = await _controls_payload(refresh)
    except V4L2Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(status_code=200, content={"controls": payload})


@router.post("/api/controls/{identifier}", response_class=JSONResponse)
async def update_control(identifier: str, update: ControlUpdate) -> JSONResponse:
    try:
        updated = await _apply_control_update(
            identifier, value=update.value, action=update.action
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Control no encontrado") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except V4L2Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse(status_code=200, content={"control": updated.as_dict()})


async def _ws_emit_controls_list(
    websocket: WebSocket,
    *,
    refresh: bool,
    request_id: str | None,
) -> None:
    try:
        payload = await _controls_payload(refresh)
    except V4L2Error as exc:
        await websocket.send_json(
            {
                "status": "controls:error",
                "scope": "list",
                "detail": str(exc),
                "request_id": request_id,
            }
        )
    else:
        await websocket.send_json(
            {
                "status": "controls",
                "scope": "list",
                "controls": payload,
                "request_id": request_id,
            }
        )


async def _ws_apply_control(
    websocket: WebSocket,
    *,
    identifier: str,
    value: Any | None,
    action: str | None,
    request_id: str | None,
) -> None:
    try:
        updated = await _apply_control_update(identifier, value=value, action=action)
    except LookupError:
        await websocket.send_json(
            {
                "status": "controls:error",
                "scope": "update",
                "identifier": identifier,
                "detail": "Control no encontrado",
                "request_id": request_id,
            }
        )
    except ValueError as exc:
        await websocket.send_json(
            {
                "status": "controls:error",
                "scope": "update",
                "identifier": identifier,
                "detail": str(exc),
                "request_id": request_id,
                "refresh": True,
            }
        )
    except V4L2Error as exc:
        await websocket.send_json(
            {
                "status": "controls:error",
                "scope": "update",
                "identifier": identifier,
                "detail": str(exc),
                "request_id": request_id,
                "refresh": True,
            }
        )
    else:
        await websocket.send_json(
            {
                "status": "controls",
                "scope": "update",
                "identifier": identifier,
                "control": updated.as_dict(),
                "request_id": request_id,
            }
        )


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
            request_id = payload.get("request_id")
            if command == "start":
                roi_payload = payload.get("roi")
                try:
                    response = await manager.start_recording(roi=roi_payload)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error al iniciar grabación: %s", exc)
                    await websocket.send_json(
                        {
                            "status": "error",
                            "detail": (
                                "Parámetros de ROI inválidos."
                                if isinstance(exc, ValueError)
                                else "No se pudo iniciar la grabación."
                            ),
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
            elif command == "controls:list":
                refresh = bool(payload.get("refresh"))
                await _ws_emit_controls_list(
                    websocket, refresh=refresh, request_id=request_id
                )
            elif command == "controls:update":
                identifier = payload.get("identifier")
                if not identifier:
                    await websocket.send_json(
                        {
                            "status": "controls:error",
                            "scope": "update",
                            "detail": "Debe indicar el identificador del control.",
                            "request_id": request_id,
                        }
                    )
                    continue
                await _ws_apply_control(
                    websocket,
                    identifier=identifier,
                    value=payload.get("value"),
                    action=payload.get("action"),
                    request_id=request_id,
                )
            elif command == "snapshot":
                try:
                    media = await manager.capture_snapshot()
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error al capturar fotografía: %s", exc)
                    response = {
                        "status": "snapshot:error",
                        "detail": "No se pudo capturar la fotografía.",
                    }
                    if request_id:
                        response["request_id"] = request_id
                    await websocket.send_json(response)
                else:
                    response = {"status": "snapshot:saved", "media": media}
                    if request_id:
                        response["request_id"] = request_id
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
