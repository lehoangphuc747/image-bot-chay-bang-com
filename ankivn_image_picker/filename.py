"""Pure helpers for deriving Anki-media-safe filenames and ``<img>`` tags.

This module is referenced by the design's "Components and Interfaces"
section under ``filename.py``. Three responsibilities live here:

1. :func:`sanitize_query_for_filename` turns an arbitrary search query
   into a stem that is safe to write into Anki's media folder on any
   platform we target (Windows, macOS, Linux).
2. :func:`derive_filename` extends that stem with an extension and a
   minimal numeric suffix so the resulting filename is unique against a
   caller-supplied ``taken`` predicate (Req 8.2, 8.3).
3. :func:`build_img_tag` produces the minimal ``<img src="...">``
   string that the editor bridge writes into the target field
   (Req 9.1).

The module is deliberately pure: no imports from :mod:`aqt`, no I/O,
and no module-level state. Property test 13 (filename derivation) and
Property test 14 (img tag construction) live close to this module and
exercise these helpers across a wide input space.
"""

from __future__ import annotations

import html
import re
from typing import Callable

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Lowercase, no-dot extensions that the add-on accepts as image media.
#: Anything outside this set is normalised to ``"jpg"`` inside
#: :func:`derive_filename` because the providers only emit one of these
#: extensions per the design's :class:`ImageResult` contract.
ALLOWED_EXT: frozenset[str] = frozenset(
    {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
)

#: Stem names that Windows reserves regardless of file extension.
#: A stem matching one of these (case-insensitively) is replaced with
#: :data:`_FALLBACK_STEM` so the resulting filename can be created on
#: Windows. Required by Property 13.
WINDOWS_RESERVED: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

# Characters that are forbidden in cross-platform filenames:
#   - path separators ``/`` and ``\``
#   - the NUL byte plus the rest of the C0 control range (0x00-0x1F)
#   - the Windows-reserved set ``<>:"|?*``
# Each match is replaced with a single ``_`` before runs are collapsed.
_UNSAFE_PATTERN = re.compile(r'[<>:"|?*/\\\x00-\x1f]')

# Used to collapse ``"___"`` -> ``"_"`` after substitution.
_REPEAT_UNDERSCORE = re.compile(r"_+")

# Maximum stem length. Anki itself does not impose a hard cap, but most
# filesystems start to misbehave above ~255 bytes for the full path,
# and very long filenames are unwieldy in the UI. 80 chars leaves
# plenty of room for the ``"_<n>.<ext>"`` suffix.
_MAX_STEM_LEN = 80

# Stem to use when the sanitised query would otherwise be empty or
# match a Windows reserved name. ``"image"`` is itself a safe stem on
# every supported platform.
_FALLBACK_STEM = "image"

# Characters stripped from the ends of the sanitised stem. Trailing
# spaces and dots are illegal on Windows, and trailing underscores
# look ugly after collapse.
_TRIM_CHARS = " ._"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_query_for_filename(query: str) -> str:
    """Return an Anki-media-safe stem derived from ``query``.

    The function performs four passes:

    1. Replace every character matching :data:`_UNSAFE_PATTERN` with
       ``"_"``.
    2. Collapse runs of ``"_"`` to a single underscore.
    3. Strip leading and trailing whitespace, dots, and underscores.
    4. Truncate to :data:`_MAX_STEM_LEN` characters and re-strip the
       new tail so the result never ends in a stripped character.

    If the result would be empty or matches a name in
    :data:`WINDOWS_RESERVED` (case-insensitively), the function
    returns :data:`_FALLBACK_STEM` instead. This guarantees the stem
    is non-empty, is not a Windows reserved name, and contains no
    path-unsafe characters, satisfying the precondition of
    Property 13.
    """

    if not isinstance(query, str):
        # Defensive: keep the function total even if a caller hands in
        # something that has not yet been normalised by ``query.py``.
        return _FALLBACK_STEM

    cleaned = _UNSAFE_PATTERN.sub("_", query)
    cleaned = _REPEAT_UNDERSCORE.sub("_", cleaned)
    cleaned = cleaned.strip(_TRIM_CHARS)

    if len(cleaned) > _MAX_STEM_LEN:
        cleaned = cleaned[:_MAX_STEM_LEN].rstrip(_TRIM_CHARS)

    if not cleaned or cleaned.upper() in WINDOWS_RESERVED:
        return _FALLBACK_STEM

    return cleaned


def _normalise_extension(ext: str) -> str:
    """Lowercase and strip a leading dot from ``ext``.

    Returns one of :data:`ALLOWED_EXT` or ``"jpg"`` if the input is
    not in the allowed set. The fallback exists so that a faulty
    provider (one that returns ``ext="jpeg2000"`` or similar) still
    produces a writable filename rather than crashing at the call
    site; the providers themselves are expected to produce only
    members of :data:`ALLOWED_EXT`.
    """

    if not isinstance(ext, str):
        return "jpg"
    candidate = ext.lstrip(".").strip().lower()
    if candidate not in ALLOWED_EXT:
        return "jpg"
    return candidate


def derive_filename(
    query: str,
    ext: str,
    *,
    taken: Callable[[str], bool],
) -> str:
    """Return a unique filename for ``query`` with extension ``ext``.

    The returned filename has the form ``f"{stem}.{ext}"`` if that
    name is not in ``taken``, otherwise ``f"{stem}_{n}.{ext}"`` where
    ``n`` is the smallest integer ``>= 2`` such that ``taken(name)``
    returns ``False``. ``stem`` is exactly
    ``sanitize_query_for_filename(query)`` and ``ext`` is normalised
    via :func:`_normalise_extension`.

    Parameters
    ----------
    query:
        The search query that produced the image. Used as the source
        of the stem.
    ext:
        File extension to attach. Case-insensitive; a leading dot is
        accepted and stripped.
    taken:
        Predicate that returns ``True`` when a candidate filename is
        already in use. In production this is bound to
        ``mw.col.media.have``; in tests it is typically ``set.__contains__``.

    Notes
    -----
    Property 13 asserts that the chosen ``n`` is the *smallest*
    integer ``>= 2`` not in ``taken`` when the unsuffixed candidate
    is taken. The loop below produces that minimum by construction
    because it walks ``n`` upward from ``2``.
    """

    normalised_ext = _normalise_extension(ext)
    stem = sanitize_query_for_filename(query)

    candidate = f"{stem}.{normalised_ext}"
    if not taken(candidate):
        return candidate

    n = 2
    while True:
        candidate = f"{stem}_{n}.{normalised_ext}"
        if not taken(candidate):
            return candidate
        n += 1


def build_img_tag(filename: str) -> str:
    """Return ``<img src="filename">`` with ``filename`` HTML-escaped.

    The filename has already been sanitised by
    :func:`sanitize_query_for_filename` so it cannot contain ``<``,
    ``>``, or ``"``; calling :func:`html.escape` is therefore
    cosmetic for ``"`` but does protect against the ``&`` character,
    which is allowed in filenames but must be encoded in HTML to
    survive Anki's editor round-tripping.

    The tag is intentionally minimal: no ``alt``, no ``style``, no
    event handlers. Property 14 asserts that the output parses to a
    single ``<img>`` element whose only attribute is ``src``.
    """

    escaped = html.escape(filename, quote=True)
    return f'<img src="{escaped}">'


__all__ = [
    "ALLOWED_EXT",
    "WINDOWS_RESERVED",
    "sanitize_query_for_filename",
    "derive_filename",
    "build_img_tag",
]
