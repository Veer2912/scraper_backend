import logging
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

from app.api.acis import router as acis_router
from app.config import LOG_DIR


def configure_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        return

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = RotatingFileHandler(
        LOG_DIR / "acis_backend.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


configure_logging()

app = FastAPI(
    title="Remote Desktop Scraper Backend",
    version="1.0.0"
)

app.include_router(acis_router)


@app.get("/")
async def root():
    return {
        "service": "Remote Desktop Scraper Backend",
        "status": "running"
    }