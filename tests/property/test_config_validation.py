"""Property test for :meth:`ankivn_image_picker.config.ConfigLoader.validate`.

Implements **Property 1** from the design document:

    For any ``raw`` value (whether ``None``, a dict with arbitrary keys
    and arbitrary value types, a partially-populated dict, or a dict
    whose values are out-of-range integers, empty strings, or wrong
    types), ``ConfigLoader.validate(raw, log=...)`` returns a ``Config``
    instance whose every field satisfies its documented invariant:

    * ``source_field`` is a non-empty string
    * ``target_field`` is a non-empty string
    * ``providers`` is a non-empty tuple of strings
    * ``max_results_per_provider`` is an int in ``[1, 50]``
    * ``thumbnail_cache_max_mb`` is an int in ``[1, 1024]``

    Furthermore, for every known key whose value is missing or invalid
    in ``raw``, the corresponding field in the returned ``Config``
    equals ``ConfigLoader.DEFAULTS``'s value for that field.

**Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.10, 1.11**

Test layout
-----------

* :func:`raw_inputs` is a Hypothesis strategy that produces every
  shape of input contemplated by the property: ``None``, non-mapping
  scalars, and dicts mixing known keys (with both valid and invalid
  values drawn separately so the search budget actually hits the
  "valid" arm) with arbitrary unknown keys.
* :func:`_is_valid_*` mirror the per-key validity predicates the
  loader applies, so the test can decide *independently* whether each
  field should fall back to its default.
* :func:`test_validate_is_total_and_produces_valid_config` is the
  single property test required by task 2.5; it asserts (a) the
  invariant on every field and (b) the per-key fall-back to defaults
  when missing or invalid.

Aside from the property, two example-based regression tests pin the
two non-recursive corner cases (``None`` and "raw is not a mapping")
so a failure in those branches is reported with a minimal
counterexample rather than emerging from shrinking.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.config import Config, ConfigLoader


# ---------------------------------------------------------------------------
# Independent restatement of the validity predicates
# ---------------------------------------------------------------------------
#
# These mirror the rules in ``ankivn_image_picker.config`` but are written
# as a near-literal transcription of the design document so the test can
# decide validity *without* relying on the implementation it is verifying.

_MAX_RESULTS_RANGE = (1, 50)
_CACHE_MB_RANGE = (1, 1024)


def _is_valid_str(value: Any) -> bool:
    """A non-empty Python ``str``."""
    return isinstance(value, str) and value != ""


def _is_valid_int_in_range(value: Any, lo: int, hi: int) -> bool:
    """An ``int`` in ``[lo, hi]``. ``bool`` is rejected per Req 1.11."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) and lo <= value <= hi


def _is_valid_providers(value: Any) -> bool:
    """A list/tuple containing at least one non-empty string."""
    if not isinstance(value, (list, tuple)):
        return False
    return any(isinstance(item, str) and item != "" for item in value)


def _expected_providers(value: Any) -> tuple[str, ...]:
    """Reference implementation of the providers coercion."""
    assert _is_valid_providers(value)
    return tuple(
        item for item in value if isinstance(item, str) and item != ""
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
#
# Two-pronged design: each known key is generated via a ``st.one_of`` of a
# "valid for this key" branch and an "invalid for this key" branch. That
# way Hypothesis spends a meaningful share of its budget on inputs whose
# fields *should* be preserved (otherwise the per-key fall-back assertion
# is only ever checked in its trivial form).

# Values that are deliberately invalid for *any* key: ``None``, ``bool``,
# floats (no documented float key), and a few oddball types.
_invalid_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.binary(max_size=8),
)

# Recursive JSON-ish blob used as both unknown-key values and as
# wrong-type values for known keys. Bounded so each example runs fast.
_json_value = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=10), children, max_size=4),
    max_leaves=8,
)

# Per-key strategies. The "invalid" branch unions empty strings, wrong
# types (anything but the right type), and out-of-range ints where
# applicable, mirroring the "or" inside Req 1.11.
_valid_source_field = st.text(min_size=1, max_size=20)
_invalid_source_field = st.one_of(
    st.just(""),
    _invalid_scalar,
    st.integers(),
    st.lists(st.text(max_size=5), max_size=3),
)

_valid_target_field = st.text(min_size=1, max_size=20)
_invalid_target_field = _invalid_source_field

# Lists with at least one non-empty string keep the valid entries.
_valid_providers_value = st.lists(
    st.text(min_size=1, max_size=15), min_size=1, max_size=5
)
# Mix of empty strings and non-strings, plus the all-empty / wrong-type
# cases. Filter out anything that happens to satisfy the validity rule
# (``[..., "ok"]`` slipping in via the wrong-type list, for example).
_invalid_providers_value = st.one_of(
    st.just([]),
    st.just(()),
    st.lists(st.just(""), min_size=1, max_size=4),
    st.lists(_invalid_scalar, min_size=1, max_size=4),
    _invalid_scalar,
    st.text(max_size=10),  # plain string, not a list/tuple
).filter(lambda v: not _is_valid_providers(v))

_valid_max_results = st.integers(
    min_value=_MAX_RESULTS_RANGE[0], max_value=_MAX_RESULTS_RANGE[1]
)
_invalid_max_results = st.one_of(
    st.integers(max_value=_MAX_RESULTS_RANGE[0] - 1),
    st.integers(min_value=_MAX_RESULTS_RANGE[1] + 1),
    st.just(""),
    st.text(max_size=10),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.lists(st.integers(), max_size=3),
)

_valid_cache_mb = st.integers(
    min_value=_CACHE_MB_RANGE[0], max_value=_CACHE_MB_RANGE[1]
)
_invalid_cache_mb = st.one_of(
    st.integers(max_value=_CACHE_MB_RANGE[0] - 1),
    st.integers(min_value=_CACHE_MB_RANGE[1] + 1),
    st.just(""),
    st.text(max_size=10),
    st.booleans(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.lists(st.integers(), max_size=3),
)

# Per-key value strategy: 50/50 split between valid and invalid so both
# arms of the per-key fall-back rule are exercised.
_per_key_value: dict[str, st.SearchStrategy[Any]] = {
    "source_field": st.one_of(_valid_source_field, _invalid_source_field),
    "target_field": st.one_of(_valid_target_field, _invalid_target_field),
    "providers": st.one_of(_valid_providers_value, _invalid_providers_value),
    "max_results_per_provider": st.one_of(
        _valid_max_results, _invalid_max_results
    ),
    "thumbnail_cache_max_mb": st.one_of(
        _valid_cache_mb, _invalid_cache_mb
    ),
}


@st.composite
def raw_dict_inputs(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a dict with an arbitrary subset of known keys (mixed
    valid/invalid values) plus a sprinkling of unknown keys with
    arbitrary values.

    Each known key is independently included or omitted so the
    "missing key" case (Req 1.10) is reached without any extra
    plumbing.
    """

    result: dict[str, Any] = {}
    for key, value_strategy in _per_key_value.items():
        if draw(st.booleans()):
            result[key] = draw(value_strategy)

    unknown_count = draw(st.integers(min_value=0, max_value=4))
    for _ in range(unknown_count):
        unknown_key = draw(
            st.text(min_size=1, max_size=15).filter(
                lambda k: k not in ConfigLoader.KNOWN_KEYS
            )
        )
        result[unknown_key] = draw(_json_value)

    return result


# Top-level input strategy: ``None``, a non-mapping scalar (so the
# "raw is not a Mapping" defensive branch is exercised), or a dict
# with mixed key/value composition.
raw_inputs = st.one_of(
    st.just(None),
    _invalid_scalar,
    st.integers(),
    st.text(max_size=20),
    raw_dict_inputs(),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_quiet_logger() -> logging.Logger:
    """Return a real ``Logger`` that swallows every record.

    Property 1 is about the *return value*, not about logging side
    effects (those are covered by tasks 2.6 and 2.10), so we keep the
    handler trivial and silent. ``propagate=False`` keeps records out
    of the root logger and pytest's ``caplog`` fixture.
    """

    logger = logging.getLogger(
        f"ankivn_image_picker.tests.property.config_validation.{id(object())}"
    )
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.CRITICAL + 1)
    logger.addHandler(logging.NullHandler())
    return logger


def _expected_field(key: str, raw: Any) -> Any:
    """Return the value the field should have, per the design rules.

    * If ``raw`` is not a mapping, every key is "missing" -> default.
    * Else if ``raw`` does not contain ``key`` -> default (Req 1.10).
    * Else if ``raw[key]`` is invalid for ``key`` -> default (Req 1.11).
    * Else -> the (coerced) value from ``raw``.
    """

    defaults = ConfigLoader.DEFAULTS
    default_value = getattr(defaults, key)

    if not isinstance(raw, Mapping):
        return default_value
    if key not in raw:
        return default_value

    value = raw[key]

    if key in ("source_field", "target_field"):
        return value if _is_valid_str(value) else default_value
    if key == "providers":
        return _expected_providers(value) if _is_valid_providers(value) else default_value
    if key == "max_results_per_provider":
        return value if _is_valid_int_in_range(value, *_MAX_RESULTS_RANGE) else default_value
    if key == "thumbnail_cache_max_mb":
        return value if _is_valid_int_in_range(value, *_CACHE_MB_RANGE) else default_value

    raise AssertionError(f"unexpected key {key!r}")


def _assert_field_invariants(config: Config) -> None:
    """Assert each field of ``config`` satisfies its documented invariant.

    Written as a stand-alone helper so a failure here pinpoints which
    invariant was violated rather than blending into the per-key
    fall-back assertion below.
    """

    # source_field / target_field: non-empty strings (Req 1.2, 1.3).
    assert isinstance(config.source_field, str)
    assert config.source_field != ""
    assert isinstance(config.target_field, str)
    assert config.target_field != ""

    # providers: non-empty tuple of non-empty strings (Req 1.4).
    assert isinstance(config.providers, tuple)
    assert len(config.providers) >= 1
    for entry in config.providers:
        assert isinstance(entry, str)
        assert entry != ""

    # max_results_per_provider: int (not bool) in [1, 50] (Req 1.5).
    assert isinstance(config.max_results_per_provider, int)
    assert not isinstance(config.max_results_per_provider, bool)
    assert _MAX_RESULTS_RANGE[0] <= config.max_results_per_provider <= _MAX_RESULTS_RANGE[1]

    # thumbnail_cache_max_mb: int (not bool) in [1, 1024] (Req 1.6).
    assert isinstance(config.thumbnail_cache_max_mb, int)
    assert not isinstance(config.thumbnail_cache_max_mb, bool)
    assert _CACHE_MB_RANGE[0] <= config.thumbnail_cache_max_mb <= _CACHE_MB_RANGE[1]


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(raw=raw_inputs)
def test_validate_is_total_and_produces_valid_config(raw: Any) -> None:
    """**Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.10, 1.11**

    Property 1 from ``design.md``: ``validate`` is a total function
    producing a fully-valid ``Config``, with per-key fall-back to
    ``DEFAULTS`` whenever the raw value is missing or invalid.
    """

    log = _make_quiet_logger()

    # Totality: the call must never raise, regardless of input shape.
    config = ConfigLoader.validate(raw, log=log)

    # Type post-condition.
    assert isinstance(config, Config)

    # Per-field invariants (Req 1.2-1.6).
    _assert_field_invariants(config)

    # Per-key fall-back to defaults when missing or invalid
    # (Req 1.10 + Req 1.11). For valid values, the field must reflect
    # the (coerced) input.
    for key in ConfigLoader.KNOWN_KEYS:
        expected = _expected_field(key, raw)
        actual = getattr(config, key)
        assert actual == expected, (
            f"field {key!r}: expected {expected!r}, got {actual!r} "
            f"for raw={raw!r}"
        )


# ---------------------------------------------------------------------------
# Regression pins for the two non-recursive corner cases
# ---------------------------------------------------------------------------
#
# Hypothesis will eventually shrink any failure here down to one of these
# inputs, but pinning them as plain examples means a regression in the
# corner cases is reported with a clear failure rather than via shrinking.


def test_validate_none_returns_defaults() -> None:
    """``raw=None`` makes every field equal to ``DEFAULTS``."""

    log = MagicMock(spec=logging.Logger)
    config = ConfigLoader.validate(None, log=log)
    assert config == ConfigLoader.DEFAULTS
    _assert_field_invariants(config)


def test_validate_non_mapping_returns_defaults() -> None:
    """A non-``Mapping`` ``raw`` is treated as the empty dict.

    The implementation comment explicitly documents this defensive
    behavior (``mw.addonManager.getConfig`` is documented to return
    ``dict | None`` but we tolerate anything). Pinning it as an
    example guards the fallback against accidental removal.
    """

    log = _make_quiet_logger()
    for raw in (42, "not a dict", b"\x00\x01", 3.14, []):
        config = ConfigLoader.validate(raw, log=log)
        assert config == ConfigLoader.DEFAULTS, (
            f"non-Mapping raw={raw!r} should produce DEFAULTS"
        )
        _assert_field_invariants(config)
