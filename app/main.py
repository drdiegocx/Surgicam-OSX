"""WebSocket handlers for Surgicam."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from .services.video_manager import VideoManager

LOGGER = logging.getLogger(__name__)


class WebSocketLike:
    async def send_json(self, message: Dict[str, Any]) -> None:  # pragma: no cover - protocol
        raise NotImplementedError


async def _handle_stop_recording(video_manager: VideoManager, websocket: WebSocketLike) -> None:
    """Handle a stop-recording message from the client."""

    try:
        recording_path = video_manager.stop_recording()
    except Exception:
        LOGGER.exception("Failed to stop recording")
        await websocket.send_json(
            {
                "type": "recording-stopped",
                "status": "error",
                "error": "Failed to stop recording",
            }
        )
        return

    if recording_path is not None:
        await websocket.send_json(
            {
                "type": "recording-stopped",
                "status": "ok",
                "path": str(recording_path),
            }
        )
        return

    warning = "Recording was already stopped"
    await websocket.send_json(
        {
            "type": "recording-stopped",
            "status": "warning",
            "error": warning,
        }
    )


async def main() -> None:  # pragma: no cover - demonstration stub
    video_manager = VideoManager()

    class _MockSocket(WebSocketLike):
        async def send_json(self, message: Dict[str, Any]) -> None:
            LOGGER.info("payload=%s", message)

    websocket = _MockSocket()
    await _handle_stop_recording(video_manager, websocket)


if __name__ == "__main__":  # pragma: no cover - script entry point
    asyncio.run(main())
