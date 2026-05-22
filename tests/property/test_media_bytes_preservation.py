"""Property test for :func:`ankivn_image_picker.editor_bridge.save_to_media`.

Implements Property 12 from the design document's "Correctness
Properties" section. The property is:

    For any byte string ``b`` and any valid Anki media filename ``f``,
    after ``save_to_media(f, b)`` the bytes returned by reading ``f``
    from the media manager equal ``b`` byte-for-byte.

**Validates: Requirements 8.1, 8.4**
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.editor_bridge import save_to_media
from ankivn_image_picker.filename import ALLOWED_EXT, sanitize_query_for_filename


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Valid Anki media filenames. We generate filenames that mirror what
#: ``derive_filename`` would produce: a sanitized stem followed by a dot
#: and an allowed extension, optionally with a numeric suffix.
_safe_stem_chars = st.characters(
    whitelist_categories=("L", "N"),
    whitelist_characters="_- ",
    blacklist_characters='<>:"|?*/\\\x00',
    blacklist_categories=("Cs",),
)

valid_media_filenames = st.builds(
    lambda stem, ext: f"{stem}.{ext}",
    stem=st.text(_safe_stem_chars, min_size=1, max_size=40).filter(
        lambda s: s.strip() != ""
    ),
    ext=st.sampled_from(sorted(ALLOWED_EXT)),
)

#: Arbitrary byte strings representing image data. The property states
#: "for any byte string b", so we include empty bytes, small payloads,
#: and larger payloads. The max size is bounded to keep tests fast.
arbitrary_image_bytes = st.binary(max_size=4096)


# ---------------------------------------------------------------------------
# Fake media manager
# ---------------------------------------------------------------------------


class _FakeMediaManager:
    """Minimal fake of ``anki.media.MediaManager``.

    Stores written data in a dict keyed by filename so we can read it
    back to verify byte-for-byte preservation. The ``write_data``
    method returns the filename it was given (no renaming), matching
    the normal Anki behavior when no collision occurs.
    """

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write_data(self, filename: str, data: bytes) -> str:
        self.files[filename] = data
        return filename


class _FakeCol:
    """Minimal fake of ``anki.collection.Collection``."""

    def __init__(self) -> None:
        self.media = _FakeMediaManager()


class _FakeMw:
    """Minimal fake of ``aqt.mw``."""

    def __init__(self) -> None:
        self.col = _FakeCol()


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    filename=valid_media_filenames,
    data=arbitrary_image_bytes,
)
@settings(max_examples=200)
def test_media_bytes_preservation_no_reencoding(
    filename: str,
    data: bytes,
) -> None:
    """Property 12: Media bytes preservation (no re-encoding).

    For any byte string ``b`` and any valid Anki media filename ``f``,
    after ``save_to_media(f, b)`` the bytes returned by reading ``f``
    from the media manager equal ``b`` byte-for-byte.

    **Validates: Requirements 8.1, 8.4**
    """

    fake_mw = _FakeMw()

    # Invoke save_to_media with the fake mw
    used_filename = save_to_media(filename, data, mw=fake_mw)

    # The returned filename should match what we passed in (no rename
    # in our fake, matching normal Anki behavior)
    assert used_filename == filename, (
        f"Expected returned filename to match input.\n"
        f"  Input:    {filename!r}\n"
        f"  Returned: {used_filename!r}"
    )

    # Read back the bytes from the fake media manager
    stored_bytes = fake_mw.col.media.files[used_filename]

    # Post-condition: bytes are preserved byte-for-byte (no re-encoding)
    assert stored_bytes == data, (
        f"Bytes written to media are not identical to input bytes.\n"
        f"  Input length:  {len(data)}\n"
        f"  Stored length: {len(stored_bytes)}\n"
        f"  Input[:50]:    {data[:50]!r}\n"
        f"  Stored[:50]:   {stored_bytes[:50]!r}"
    )
