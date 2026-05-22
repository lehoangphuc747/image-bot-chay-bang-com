"""Thin wrapper around the standard ``logging`` module.

By default the package logger uses a :class:`logging.NullHandler` so
nothing is written to ``stderr``. Anki forwards add-on stderr output
to its debug console and pops it up automatically, which is noisy
during normal use. If you need to enable verbose logging while
debugging, edit ``_DEBUG_TO_STDERR`` below or call
``logging.getLogger("ankivn_image_picker").addHandler(...)`` from a
separate dev script.

The standard :mod:`logging` module is imported under an underscored
alias because this file lives at ``ankivn_image_picker/logging.py``;
absolute imports inside the package still resolve ``import logging``
to the standard library, but the alias makes the intent explicit.
"""

from __future__ import annotations

import logging as _stdlib_logging
import sys
from typing import Optional

_LOGGER_NAME = "ankivn_image_picker"
# Default level for the package logger. WARNING is conservative —
# routine INFO from startup is suppressed.
_DEFAULT_LEVEL = _stdlib_logging.WARNING
# When True, attach a stderr handler so logs appear in Anki's debug
# console. When False (default), use NullHandler — nothing is printed
# anywhere, so Anki never auto-shows its debug popup for our messages.
_DEBUG_TO_STDERR: bool = False
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

_configured = False


def _configure_root() -> _stdlib_logging.Logger:
    """Configure the package logger exactly once and return it."""

    global _configured
    logger = _stdlib_logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(_DEFAULT_LEVEL)
    if _DEBUG_TO_STDERR:
        has_stream_handler = any(
            isinstance(h, _stdlib_logging.StreamHandler)
            for h in logger.handlers
        )
        if not has_stream_handler:
            handler = _stdlib_logging.StreamHandler(stream=sys.stderr)
            handler.setFormatter(_stdlib_logging.Formatter(_LOG_FORMAT))
            logger.addHandler(handler)
    else:
        # NullHandler swallows every record. We still set it so the
        # logger has at least one handler (the standard library
        # otherwise prints a warning about a logger having no
        # handlers when WARNING-or-above is emitted).
        has_null_handler = any(
            isinstance(h, _stdlib_logging.NullHandler)
            for h in logger.handlers
        )
        if not has_null_handler:
            logger.addHandler(_stdlib_logging.NullHandler())
    # Prevent double-printing if Anki configures the root logger.
    logger.propagate = False

    _configured = True
    return logger


def get_logger(name: Optional[str] = None) -> _stdlib_logging.Logger:
    """Return the package logger or a named child of it.

    Parameters
    ----------
    name:
        Optional sub-name. ``get_logger("config")`` returns a logger
        named ``"ankivn_image_picker.config"``; ``get_logger()`` returns
        the package root logger itself.
    """

    root = _configure_root()
    if name is None or name == "":
        return root
    return root.getChild(name)


__all__ = ["get_logger"]
