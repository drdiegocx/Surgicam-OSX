"""Punto de entrada de la aplicación FastAPI."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routes import manager, router


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("mini_dvr").setLevel(level)


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(title="Mini-DVR Raspberry Pi", version="1.0.0")
    static_dir = settings.BASE_DIR / "app" / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    application.include_router(router)

    @application.on_event("startup")
    async def startup_event() -> None:
        logging.getLogger("mini_dvr").info("Aplicación iniciada, verificando vista previa.")
        try:
            await manager.ensure_preview()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("mini_dvr").error(
                "No se pudo iniciar la vista previa MJPEG: %s", exc
            )

    @application.on_event("shutdown")
    async def shutdown_event() -> None:
        await manager.shutdown()

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
