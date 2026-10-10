"""Central logging configuration for the portable desktop application."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_dir=None, level=logging.INFO) -> Path:
    """Configure one rotating file handler and one console handler, once."""
    log_dir = Path(log_dir or Path(__file__).parent / "logs")
    log_path = log_dir / "ocr_app.log"
    root = logging.getLogger()
    if any(getattr(handler, "_ocr_vbhc_handler", False) for handler in root.handlers):
        return log_path

    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handlers = [
        RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3,
            encoding="utf-8", delay=True,
        ),
        logging.StreamHandler(),
    ]
    for handler in handlers:
        handler._ocr_vbhc_handler = True
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return log_path
