"""Video recording management helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol


_LOGGER = logging.getLogger(__name__)


@dataclass
class VideoManager:
    """Manage the lifecycle of an ffmpeg recording process."""

    _recording_proc: Optional["_SupportsPoll"] = None
    _recording_path: Optional[Path] = None
    _recording_started_at: Optional[datetime] = None
    _logger: logging.Logger = field(default=_LOGGER, repr=False)

    def recording_running(self) -> bool:
        """Return ``True`` if the underlying recording process is still alive."""

        return self._recording_proc is not None and self._recording_proc.poll() is None

    def stop_recording(self) -> Optional[Path]:
        """Stop the recording process if one is running.

        Returns the path to the finished recording when available. ``None`` is
        returned when no recording was active.
        """

        if not self.recording_running():
            if self._recording_path is not None:
                recording_path = self._recording_path
                self._logger.warning(
                    "Recording process died unexpectedly; returning last known file: %s",
                    recording_path,
                )
                self._reset_state()
                return recording_path
            return None

        assert self._recording_proc is not None
        self._recording_proc.terminate()

        recording_path = self._recording_path
        self._reset_state()
        return recording_path

    def _reset_state(self) -> None:
        self._recording_proc = None
        self._recording_path = None
        self._recording_started_at = None


class _SupportsPoll(Protocol):
    def poll(self) -> Optional[int]:
        ...

    def terminate(self) -> None:
        ...
