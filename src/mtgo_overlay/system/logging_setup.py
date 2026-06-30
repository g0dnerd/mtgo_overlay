"""Stdlib-logging setup plus the ``log_info``/``log_warning``/``log_exception``
shims the old modules called. Ported modules keep their call sites; new code can
use ``logging.getLogger(__name__)`` directly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import paths

_LOGGER_NAME = "mtgo_overlay"
_configured = False


def setup(level: int | None = None, *, to_file: bool = True) -> logging.Logger:
    """Configure the root app logger once. Idempotent.

    With no explicit ``level``, ``MTGO_OVERLAY_DEBUG`` (any non-empty value)
    selects DEBUG so detailed recognition diagnostics can be captured without a
    code change; otherwise INFO.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    if level is None:
        level = logging.DEBUG if os.environ.get("MTGO_OVERLAY_DEBUG") else logging.INFO
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if to_file:
        try:
            log_path = paths.logs_dir() / "mtgo_overlay.log"
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except OSError:
            # Never let logging setup take the app down.
            logger.warning("Could not open log file; continuing with stream only.")

    logger.propagate = False
    _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    base = logging.getLogger(_LOGGER_NAME)
    return base.getChild(name) if name else base


# --- module-level logging helpers -------------------------------------------

def log_info(message: object) -> None:
    get_logger().info("%s", message)


def log_warning(message: object) -> None:
    get_logger().warning("%s", message)


def log_exception(message: object) -> None:
    get_logger().error("%s", message)


def resource_log_dir() -> Path:
    return paths.logs_dir()
