"""Property test for :func:`ankivn_image_picker.editor_bridge.insert_image`.

Implements Property 15 from the design document's "Correctness
Properties" section. The property is:

    For any existing field content ``c`` (any string) and any valid
    ``<img>`` tag ``t``, after ``insert_image(editor, target_field,
    filename)`` is invoked with the field initially set to ``c``, the
    post-condition field value equals ``c + t`` (the original content
    is preserved as a strict prefix of the new value, and the tag
    appears as a strict suffix).

**Validates: Requirements 9.3**
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.editor_bridge import insert_image
from ankivn_image_picker.filename import ALLOWED_EXT, build_img_tag


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Arbitrary existing field content. This can be any string — HTML,
#: plain text, empty, or already containing img tags. Surrogates are
#: excluded because they are not valid scalar values in Python strings
#: that Anki would produce.
existing_field_content = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=200,
)

#: Valid Anki media filenames. We generate filenames that are realistic:
#: a non-empty stem of safe characters followed by a dot and an allowed
#: extension. This mirrors what ``derive_filename`` would produce.
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


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeNote:
    """Minimal fake of ``anki.notes.Note`` for testing insert_image."""

    def __init__(self, fields: list[str], field_names: list[str]) -> None:
        self.fields = fields
        self._field_names = field_names

    def note_type(self) -> dict[str, Any]:
        return {
            "flds": [{"name": name} for name in self._field_names]
        }


class _FakeEditor:
    """Minimal fake of ``aqt.editor.Editor``."""

    def __init__(self, note: _FakeNote) -> None:
        self.note = note
        self.load_called = False

    def loadNoteKeepingFocus(self) -> None:
        self.load_called = True


class _FakeMw:
    """Minimal fake of ``aqt.mw``."""

    def __init__(self) -> None:
        self.col = _FakeCol()


class _FakeCol:
    """Minimal fake of ``anki.collection.Collection``."""

    def __init__(self) -> None:
        pass

    def update_note(self, note: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    existing_content=existing_field_content,
    filename=valid_media_filenames,
)
@settings(max_examples=200)
def test_insert_image_appends_rather_than_overwrites(
    existing_content: str,
    filename: str,
) -> None:
    """Property 15: Target-field write appends rather than overwrites.

    For any existing field content ``c`` and any valid media filename,
    after ``insert_image`` is invoked the field value equals
    ``c + build_img_tag(filename)``: the original content is preserved
    as a strict prefix and the tag appears as a strict suffix.
    """

    target_field = "image"
    field_names = ["word", target_field, "extra"]
    # Set up the note with the existing content in the target field
    fields = ["some word", existing_content, "extra content"]
    note = _FakeNote(fields=fields, field_names=field_names)
    editor = _FakeEditor(note)
    fake_mw = _FakeMw()

    # Compute the expected tag
    expected_tag = build_img_tag(filename)

    # Invoke insert_image
    insert_image(editor, target_field, filename, mw=fake_mw)

    # Post-condition: field value == original content + tag
    actual_value = note.fields[1]
    assert actual_value == existing_content + expected_tag, (
        f"Expected field to be original content + tag.\n"
        f"  Original: {existing_content!r}\n"
        f"  Tag:      {expected_tag!r}\n"
        f"  Expected: {(existing_content + expected_tag)!r}\n"
        f"  Actual:   {actual_value!r}"
    )

    # The original content is a strict prefix
    assert actual_value.startswith(existing_content), (
        f"Original content is not preserved as a prefix.\n"
        f"  Original: {existing_content!r}\n"
        f"  Actual:   {actual_value!r}"
    )

    # The tag is a strict suffix
    assert actual_value.endswith(expected_tag), (
        f"Tag is not a suffix of the field value.\n"
        f"  Tag:    {expected_tag!r}\n"
        f"  Actual: {actual_value!r}"
    )

    # Other fields are untouched
    assert note.fields[0] == "some word"
    assert note.fields[2] == "extra content"

    # Editor was refreshed
    assert editor.load_called
