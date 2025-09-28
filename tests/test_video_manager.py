from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Ensure the application package is importable when tests are executed directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


def test_writer_not_called_after_release(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = MagicMock()
    fake_cv2 = SimpleNamespace(VideoWriter=MagicMock(return_value=writer))
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    # Reload to ensure any cached references are updated for the fake module.
    import app.services.video_manager as video_manager

    importlib.reload(video_manager)

    manager = video_manager.VideoManager()
    cv2 = importlib.import_module("cv2")
    manager.set_record_writer(cv2.VideoWriter("out.mp4", None, 30.0, (640, 480)))
    manager._recording_path = Path("recordings/out.mp4")
    manager._recording_started_at = dt.datetime.utcnow()

    stop_event = threading.Event()

    def capture_loop() -> None:
        while not stop_event.is_set():
            manager.record_frame("frame")
            time.sleep(0.001)

    thread = threading.Thread(target=capture_loop, daemon=True)
    thread.start()

    time.sleep(0.02)
    asyncio.run(manager.stop_recording())
    stop_event.set()
    thread.join(timeout=1.0)

    calls = writer.method_calls
    release_index = next(i for i, call in enumerate(calls) if call[0] == "release")
    post_release_calls = calls[release_index + 1 :]
    assert not any(call[0] == "write" for call in post_release_calls)
