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
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_configured = False


def _make_file_handler() -> logging.FileHandler:
    handler = logging.FileHandler(paths.logs_dir() / "mtgo_overlay.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


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

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(stream)

    if to_file:
        try:
            logger.addHandler(_make_file_handler())
        except OSError:
            # Never let logging setup take the app down.
            logger.warning("Could not open log file; continuing with stream only.")

    logger.propagate = False
    _configured = True
    return logger


def close_log_file() -> None:
    """Detach and close the app-log file handler so the underlying file can be
    deleted — Windows holds an open log file locked."""
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()


def reopen_log_file() -> None:
    """Reattach a fresh file handler after :func:`close_log_file` (e.g. once the
    log dir has been cleared). No-op if one is already attached."""
    logger = logging.getLogger(_LOGGER_NAME)
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return
    try:
        logger.addHandler(_make_file_handler())
    except OSError:
        logger.warning("Could not reopen log file; continuing with stream only.")


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
