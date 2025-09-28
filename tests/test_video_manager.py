from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.main import _handle_stop_recording, WebSocketLike
from app.services.video_manager import VideoManager


class DummyProc:
    def __init__(self, alive: bool) -> None:
        self._alive = alive
        self.terminated = False

    def poll(self) -> Optional[int]:
        return None if self._alive else 1

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False


class DummySocket(WebSocketLike):
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def send_json(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)


def test_stop_recording_returns_path_when_process_crashed():
    manager = VideoManager(
        _recording_proc=DummyProc(alive=False),
        _recording_path=Path("/tmp/output.mp4"),
    )

    path = manager.stop_recording()

    assert path == Path("/tmp/output.mp4")
    assert manager._recording_proc is None
    assert manager._recording_path is None
    assert manager._recording_started_at is None


def test_handle_stop_recording_includes_path_even_when_process_dead():
    manager = VideoManager(
        _recording_proc=DummyProc(alive=False),
        _recording_path=Path("/tmp/output.mp4"),
    )
    socket = DummySocket()

    asyncio.run(_handle_stop_recording(manager, socket))

    assert socket.messages == [
        {
            "type": "recording-stopped",
            "status": "ok",
            "path": "/tmp/output.mp4",
        }
    ]


def test_handle_stop_recording_warns_when_no_path():
    manager = VideoManager()
    socket = DummySocket()

    asyncio.run(_handle_stop_recording(manager, socket))

    assert socket.messages == [
        {
            "type": "recording-stopped",
            "status": "warning",
            "error": "Recording was already stopped",
        }
    ]
