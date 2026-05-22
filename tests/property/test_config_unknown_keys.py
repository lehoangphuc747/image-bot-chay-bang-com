"""Property tests for unknown config key reporting in :mod:`ankivn_image_picker.config`.

Feature: ankivn-image-picker, Property 2: Unknown config keys are reported exactly.

The design document (``Correctness Properties`` section) states:

    For any dict ``raw``, the set of keys reported by
    ``validate(raw, log=...)`` as unknown equals exactly
    ``set(raw.keys()) - ConfigLoader.KNOWN_KEYS``, and the returned
    ``Config`` does not depend on the values of keys outside
    ``ConfigLoader.KNOWN_KEYS``.

This property is encoded as a single test function that:

1. Generates an arbitrary dict with a mix of known and unknown keys.
2. Captures the warning log output from ``validate``.
3. Asserts the set of keys named in the warning matches exactly the
   expected unknown keys.
4. Asserts the returned ``Config`` is identical regardless of the
   values assigned to unknown keys (by calling ``validate`` twice with
   different unknown-key values and comparing the results).

**Validates: Requirements 1.9**
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.config import Config, ConfigLoader


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Arbitrary JSON-like values that might appear in a config dict.
_json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=30),
    lambda children: st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=10), children, max_size=5),
    max_leaves=10,
)

#: Keys that are NOT in ConfigLoader.KNOWN_KEYS. We generate short text
#: keys and filter out any that happen to collide with known keys.
_unknown_keys = st.text(min_size=1, max_size=20).filter(
    lambda k: k not in ConfigLoader.KNOWN_KEYS
)

#: Values for known keys — can be anything (valid or invalid); the
#: property only cares that the *unknown* keys are reported correctly
#: and that the Config is independent of unknown key values.
_known_key_values = _json_values


@st.composite
def raw_config_dicts(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a raw config dict with an arbitrary mix of known and unknown keys."""
    result: dict[str, Any] = {}

    # Optionally include some known keys with arbitrary values.
    for key in ConfigLoader.KNOWN_KEYS:
        if draw(st.booleans()):
            result[key] = draw(_known_key_values)

    # Include 0–5 unknown keys.
    unknown_count = draw(st.integers(min_value=0, max_value=5))
    for _ in range(unknown_count):
        key = draw(_unknown_keys)
        result[key] = draw(_json_values)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _WarningCollector(logging.Handler):
    """A logging handler that collects warning messages."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self.messages.append(record.getMessage())


def _extract_reported_unknown_keys(messages: list[str]) -> set[str]:
    """Parse the set of unknown keys from the log warning messages.

    The implementation logs unknown keys in the format:
        "Ignoring unknown config key(s): 'key1', 'key2', ..."

    Keys are formatted with ``repr()``, so we use ``ast.literal_eval``
    to recover the original string value (handling escape sequences
    like ``\\x1f`` correctly).
    """
    reported: set[str] = set()
    for msg in messages:
        if "unknown config key" in msg.lower():
            # Find all repr'd string literals (single or double quoted).
            for match in re.finditer(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")", msg):
                try:
                    reported.add(ast.literal_eval(match.group(0)))
                except (ValueError, SyntaxError):
                    pass
    return reported


def _make_logger_with_collector() -> tuple[logging.Logger, _WarningCollector]:
    """Create a fresh logger with a warning collector attached."""
    logger = logging.getLogger(f"test_config_unknown_keys_{id(object())}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    collector = _WarningCollector()
    logger.addHandler(collector)
    return logger, collector


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(raw=raw_config_dicts())
def test_unknown_config_keys_reported_exactly(raw: dict[str, Any]) -> None:
    """**Validates: Requirements 1.9**

    Property 2 from ``design.md``: Unknown config keys are reported exactly.

    Asserts two things:
    1. The set of keys reported as unknown in the log warning equals
       exactly ``set(raw.keys()) - ConfigLoader.KNOWN_KEYS``.
    2. The returned ``Config`` does not depend on the values of keys
       outside ``ConfigLoader.KNOWN_KEYS`` — i.e., changing only the
       unknown keys' values produces the same ``Config``.
    """

    expected_unknown = set(raw.keys()) - ConfigLoader.KNOWN_KEYS

    # --- Part 1: Verify the reported unknown keys match exactly ---
    logger, collector = _make_logger_with_collector()
    config = ConfigLoader.validate(raw, log=logger)

    reported_unknown = _extract_reported_unknown_keys(collector.messages)

    # If there are no unknown keys, no warning should be emitted about them.
    if not expected_unknown:
        assert reported_unknown == set()
    else:
        assert reported_unknown == expected_unknown, (
            f"Expected unknown keys {expected_unknown!r}, "
            f"but got {reported_unknown!r}"
        )

    # --- Part 2: Config is independent of unknown key values ---
    # Build a second dict with the same known keys but different unknown
    # key values (all replaced with a sentinel that differs from any
    # plausible original value).
    raw_modified = dict(raw)
    sentinel = {"__sentinel__": True}
    for key in expected_unknown:
        raw_modified[key] = sentinel

    logger2, _ = _make_logger_with_collector()
    config2 = ConfigLoader.validate(raw_modified, log=logger2)

    assert config == config2, (
        f"Config should not depend on unknown key values.\n"
        f"Original: {config!r}\n"
        f"Modified: {config2!r}"
    )
