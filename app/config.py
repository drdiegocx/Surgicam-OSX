"""Configuraciones de la aplicación Mini-DVR."""
from __future__ import annotations

import os
from pathlib import Path


class Settings:
    """Gestor simple de configuración basada en variables de entorno."""

    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    RECORDINGS_DIR: Path = Path(
        os.getenv("MINIDVR_RECORDINGS_DIR", BASE_DIR / "recordings")
    )
    USTREAMER_PORT: int = int(os.getenv("MINIDVR_PREVIEW_PORT", 8000))
    USTREAMER_HOST: str = os.getenv("MINIDVR_PREVIEW_HOST", "0.0.0.0")
    USTREAMER_DEVICE: str = os.getenv("MINIDVR_DEVICE", "/dev/video0")
    USTREAMER_RESOLUTION: str = os.getenv("MINIDVR_RESOLUTION", "1280x720")
    USTREAMER_FPS: int = int(os.getenv("MINIDVR_FPS", 30))

    APP_HOST: str = os.getenv("MINIDVR_APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("MINIDVR_APP_PORT", 8080))

    FFMPG_SEGMENT_SECONDS: int = int(os.getenv("MINIDVR_SEGMENT_SECONDS", 600))
    FFMPG_URL: str = os.getenv(
        "MINIDVR_STREAM_URL",
        f"http://127.0.0.1:{USTREAMER_PORT}/stream",
    )

    LOG_LEVEL: str = os.getenv("MINIDVR_LOG_LEVEL", "INFO")

    CONTROLS_CACHE_TTL: float = float(
        os.getenv("MINIDVR_CONTROLS_CACHE_TTL", "1.0")
    )


settings = Settings()
