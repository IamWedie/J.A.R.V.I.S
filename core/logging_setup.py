"""Centralized logging for JARVIS with rotating file handler."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_configured = False


def setup_logging(log_dir=None, level=logging.INFO):
    global _configured
    if _configured:
        return
    _configured = True

    if log_dir is None:
        from core.config import data_dir
        log_dir = os.path.join(data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "jarvis.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name):
    return logging.getLogger(name)
