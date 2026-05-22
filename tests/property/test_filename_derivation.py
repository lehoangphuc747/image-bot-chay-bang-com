"""Property test for :func:`ankivn_image_picker.filename.derive_filename`.

Implements Property 13 from the design document's "Correctness
Properties" section. The property is:

    For any query ``q``, any allowed extension ``ext``, and any set
    ``T`` of "taken" filenames, let
    ``f = derive_filename(q, ext, taken=lambda name: name in T)``.
    Then:

    - ``f`` is a valid Anki media filename: contains no path
      separator (``/``, ``\\``), no NUL, no characters in
      ``<>:"|?*``, is not a Windows reserved stem (``CON``, ``PRN``,
      ``AUX``, ``NUL``, ``COM1``-``COM9``, ``LPT1``-``LPT9``), is
      non-empty, and ends in ``.{ext.lower()}``.
    - ``f not in T`` (uniqueness).
    - The non-suffix part of ``f`` (the stem before any ``_n`` and
      before the extension) equals ``sanitize_query_for_filename(q)``.
    - The numeric suffix (if any) is the smallest integer ``n >= 2``
      such that ``f`` would not be in ``T`` (minimal suffix).

**Validates: Requirements 8.2, 8.3**
"""

from __future__ import annotations

import re

from hypothesis import given, settings

from ankivn_image_picker.filename import (
    WINDOWS_RESERVED,
    derive_filename,
    sanitize_query_for_filename,
)
from tests.property.strategies import filename_derivation_inputs

# Characters that must never appear in the produced filename. Mirrors
# the disallowed set documented in Property 13.
_FORBIDDEN_CHARS = set('<>:"|?*/\\\x00')


def _parse_suffix(
    filename: str, expected_stem: str, ext: str
) -> int | None:
    """Return the numeric suffix encoded in ``filename`` or ``None``.

    Parses ``filename`` against the *known* expected stem so a stem
    that itself ends in ``_<digits>`` (e.g. ``"foo_5"``) is not
    misinterpreted as a suffixed candidate. Raises
    ``AssertionError`` if the filename does not match either the
    unsuffixed or suffixed shape; that condition is itself a
    Property 13 violation.
    """

    expected_ext = f".{ext.lower()}"
    assert filename.endswith(expected_ext), (
        f"filename {filename!r} does not end in {expected_ext!r}"
    )
    base = filename[: -len(expected_ext)]

    if base == expected_stem:
        return None

    prefix = f"{expected_stem}_"
    assert base.startswith(prefix), (
        f"filename base {base!r} does not start with the expected "
        f"stem prefix {prefix!r}"
    )
    suffix = base[len(prefix) :]
    assert re.fullmatch(r"\d+", suffix), (
        f"suffix {suffix!r} is not a non-empty run of digits"
    )
    return int(suffix)


@given(filename_derivation_inputs())
@settings(max_examples=200)
def test_derive_filename_property_13(inputs: tuple[str, str, set[str]]) -> None:
    query, ext, taken_set = inputs

    f = derive_filename(query, ext, taken=lambda name: name in taken_set)

    # --- Sub-property A: ``f`` is a valid Anki media filename. ---
    assert isinstance(f, str)
    assert f, "derived filename must be non-empty"
    assert _FORBIDDEN_CHARS.isdisjoint(f), (
        f"derived filename {f!r} contains a forbidden character"
    )

    expected_ext = f".{ext.lower()}"
    assert f.endswith(expected_ext), (
        f"derived filename {f!r} does not end in {expected_ext!r}"
    )

    parsed_stem = sanitize_query_for_filename(query)
    parsed_n = _parse_suffix(f, parsed_stem, ext)

    # The stem alone (i.e. without any numeric suffix) must not be a
    # Windows reserved name. The sanitiser guarantees this; we
    # double-check here so a future change cannot regress the
    # invariant silently.
    assert parsed_stem.upper() not in WINDOWS_RESERVED, (
        f"stem {parsed_stem!r} is a Windows reserved name"
    )

    # --- Sub-property B: uniqueness. ---
    assert f not in taken_set, (
        f"derived filename {f!r} collides with a taken name"
    )

    # --- Sub-property C: stem matches sanitize_query_for_filename(q). ---
    # Already enforced by ``_parse_suffix`` (it asserts the filename's
    # base equals or starts with the expected stem). The explicit
    # statement here documents the property for future readers.
    expected_stem = parsed_stem

    # --- Sub-property D: minimal suffix. ---
    unsuffixed = f"{expected_stem}{expected_ext}"
    if unsuffixed not in taken_set:
        # The unsuffixed candidate was free; ``derive_filename`` must
        # have returned it directly.
        assert parsed_n is None, (
            f"unsuffixed candidate {unsuffixed!r} was free but "
            f"derive_filename returned suffixed {f!r}"
        )
        assert f == unsuffixed
    else:
        # The unsuffixed candidate was taken; the chosen suffix must
        # be the smallest ``n >= 2`` whose candidate is free.
        assert parsed_n is not None, (
            f"unsuffixed candidate {unsuffixed!r} was taken but "
            f"derive_filename returned unsuffixed {f!r}"
        )
        assert parsed_n >= 2, (
            f"numeric suffix {parsed_n} must be >= 2"
        )
        for smaller in range(2, parsed_n):
            assert (
                f"{expected_stem}_{smaller}{expected_ext}" in taken_set
            ), (
                f"derive_filename chose suffix {parsed_n} but "
                f"_{smaller} was free"
            )
