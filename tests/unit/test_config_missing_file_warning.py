"""Unit test for the absent-config-file warning behavior of
:meth:`ankivn_image_picker.config.ConfigLoader.validate`.

This file covers Req 1.8: when the raw config is ``None`` (i.e. Anki's
``mw.addonManager.getConfig`` returns ``None`` because the config file
was not found), the loader must:

1. Return :data:`ConfigLoader.DEFAULTS` unchanged.
2. Emit exactly one warning to the injected logger.
3. The warning text must identify the missing config file (so a user
   reading the Anki debug console can tell what happened).

The test exercises both supported logger surfaces:

* a real :class:`logging.Logger` whose handler captures records, which
  also doubles as the "no other levels are emitted" guard;
* a :class:`unittest.mock.MagicMock` with ``spec=logging.Logger``,
  which doubles as the "only ``warning`` is called" guard at the API
  boundary.

Both paths must agree, so a regression in either the logging level
or the message identification surfaces here.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from ankivn_image_picker.config import ConfigLoader


# A keyword the warning must contain so a user can grep the debug
# console and find the absent-config message. The implementation says
# "Config file is absent"; we assert on the substring "config file" to
# stay tolerant of minor copy edits while still proving the warning
# identifies the missing config file.
_MISSING_CONFIG_KEYWORD = "config file"


class _ListHandler(logging.Handler):
    """Tiny handler that captures every emitted :class:`LogRecord`."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.records.append(record)


def _make_capturing_logger(name: str) -> tuple[logging.Logger, _ListHandler]:
    """Return a fresh logger + handler isolated from the global config.

    Each test gets a uniquely-named logger so handlers added by one
    test never leak into another test, and ``propagate=False`` keeps
    records away from the root logger (and pytest's caplog).
    """

    logger = logging.getLogger(name)
    # Reset state in case a previous test reused the same name.
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = _ListHandler()
    logger.addHandler(handler)
    return logger, handler


def test_validate_none_returns_defaults_unchanged() -> None:
    """``validate(None, ...)`` returns the documented defaults verbatim."""

    logger, _handler = _make_capturing_logger(
        "ankivn_image_picker.tests.missing_file.returns_defaults"
    )

    result = ConfigLoader.validate(None, log=logger)

    # Identity is the strongest assertion (the loader returns the
    # singleton DEFAULTS rather than rebuilding it), and it is also
    # what the implementation contract promises.
    assert result is ConfigLoader.DEFAULTS


def test_validate_none_emits_exactly_one_warning_record() -> None:
    """Exactly one record at WARNING level is emitted, nothing else."""

    logger, handler = _make_capturing_logger(
        "ankivn_image_picker.tests.missing_file.exactly_one_warning"
    )

    ConfigLoader.validate(None, log=logger)

    assert len(handler.records) == 1, (
        f"expected exactly one log record, got {len(handler.records)}: "
        f"{[r.getMessage() for r in handler.records]}"
    )
    record = handler.records[0]
    assert record.levelno == logging.WARNING, (
        f"expected WARNING level, got {logging.getLevelName(record.levelno)}"
    )


def test_validate_none_warning_identifies_missing_config_file() -> None:
    """The single warning's message identifies the absent config file."""

    logger, handler = _make_capturing_logger(
        "ankivn_image_picker.tests.missing_file.identifies_missing"
    )

    ConfigLoader.validate(None, log=logger)

    assert len(handler.records) == 1
    message = handler.records[0].getMessage()
    assert _MISSING_CONFIG_KEYWORD in message.lower(), (
        f"warning message does not identify the missing config file: "
        f"{message!r}"
    )


def test_validate_none_uses_only_logger_warning_api() -> None:
    """A spec'd ``Logger`` mock receives exactly one ``warning`` call.

    This complements the real-logger tests: it asserts the loader uses
    the ``Logger.warning`` API specifically (not ``log``, not
    ``error``, not ``info``) and that no other logger method is
    invoked along the way.
    """

    log = MagicMock(spec=logging.Logger)

    result = ConfigLoader.validate(None, log=log)

    assert result is ConfigLoader.DEFAULTS

    # Only ``warning`` was called, and exactly once.
    assert log.warning.call_count == 1, (
        f"expected one warning() call, got {log.warning.call_count}"
    )
    for forbidden in ("debug", "info", "error", "critical", "exception", "log"):
        method = getattr(log, forbidden)
        assert method.call_count == 0, (
            f"unexpected call to logger.{forbidden}() during "
            f"validate(None, ...)"
        )

    # The warning's rendered text identifies the missing config file.
    args, kwargs = log.warning.call_args
    # ``Logger.warning(msg, *args)`` formats ``msg % args``; we
    # reproduce that here so f-string-style or %-style messages both
    # pass the assertion.
    rendered = args[0] % args[1:] if len(args) > 1 else str(args[0])
    assert _MISSING_CONFIG_KEYWORD in rendered.lower(), (
        f"warning message does not identify the missing config file: "
        f"{rendered!r}"
    )
    assert not kwargs, (
        f"warning() was called with unexpected keyword arguments: {kwargs!r}"
    )
