"""
Structured logging configuration.

Usage:
    from utils.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Hello %s", "world")
"""

import logging
import sys
from config import log_cfg

_configured = False


def _configure_root() -> None:
    """Apply the global log format once."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_cfg.level.upper(), logging.INFO))

    if not root.handlers:
        # Windows consoles default to a legacy code page (cp1252). The log
        # format uses box-drawing characters and the client logs emoji, so
        # without forcing UTF-8 every log line raises UnicodeEncodeError.
        stream = sys.stdout
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            logging.Formatter(fmt=log_cfg.format, datefmt=log_cfg.date_format)
        )
        root.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "websockets", "discord.gateway", "discord.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger with the shared config."""
    _configure_root()
    return logging.getLogger(name)
