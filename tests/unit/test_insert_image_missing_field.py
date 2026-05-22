"""Unit test for missing target field behavior of
:func:`ankivn_image_picker.editor_bridge.insert_image`.

This file covers Req 9.4: when ``target_field`` does not exist on the
current note's note type, ``insert_image`` must:

1. Raise :class:`~ankivn_image_picker.errors.FieldNotFoundError`.
2. Leave the note's fields completely unmodified (no partial writes).
3. Never call ``editor.loadNoteKeepingFocus()`` or
   ``mw.col.update_note(note)`` (no persistence side effects).

The tests use lightweight fakes for the Anki editor and main window
rather than mocking the entire ``aqt`` package.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ankivn_image_picker.editor_bridge import insert_image
from ankivn_image_picker.errors import FieldNotFoundError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_fake_note(fields: dict[str, str]) -> Any:
    """Build a minimal fake note with the given field names and values.

    The fake mirrors the Anki ``Note`` interface used by
    ``insert_image``:

    * ``note.note_type()["flds"]`` returns a list of dicts with a
      ``"name"`` key.
    * ``note.fields`` is a parallel list of field values.
    """

    fld_defs = [{"name": name} for name in fields]
    field_values = list(fields.values())

    note = MagicMock()
    note.note_type.return_value = {"flds": fld_defs}
    note.fields = field_values
    return note


def _make_fake_editor(note: Any) -> Any:
    """Build a minimal fake editor wrapping the given note."""

    editor = MagicMock()
    editor.note = note
    return editor


def _make_fake_mw() -> Any:
    """Build a minimal fake main window with a col.update_note mock."""

    mw = MagicMock()
    return mw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_insert_image_raises_field_not_found_error_for_missing_field() -> None:
    """``insert_image`` raises ``FieldNotFoundError`` when target_field
    is not present on the note type."""

    note = _make_fake_note({"Front": "hello", "Back": "world"})
    editor = _make_fake_editor(note)
    fake_mw = _make_fake_mw()

    with pytest.raises(FieldNotFoundError):
        insert_image(editor, "image", "test.jpg", mw=fake_mw)


def test_insert_image_does_not_modify_note_fields_on_missing_field() -> None:
    """The note's fields list is byte-identical before and after the
    failed ``insert_image`` call."""

    original_fields = ["hello", "world"]
    note = _make_fake_note({"Front": "hello", "Back": "world"})
    editor = _make_fake_editor(note)
    fake_mw = _make_fake_mw()

    with pytest.raises(FieldNotFoundError):
        insert_image(editor, "nonexistent_field", "photo.png", mw=fake_mw)

    # Fields must be unchanged.
    assert note.fields == original_fields


def test_insert_image_does_not_call_load_note_on_missing_field() -> None:
    """``editor.loadNoteKeepingFocus()`` is never called when the
    target field is missing."""

    note = _make_fake_note({"Front": "hello", "Back": "world"})
    editor = _make_fake_editor(note)
    fake_mw = _make_fake_mw()

    with pytest.raises(FieldNotFoundError):
        insert_image(editor, "missing", "img.jpg", mw=fake_mw)

    editor.loadNoteKeepingFocus.assert_not_called()


def test_insert_image_does_not_call_update_note_on_missing_field() -> None:
    """``mw.col.update_note(note)`` is never called when the target
    field is missing."""

    note = _make_fake_note({"Front": "hello", "Back": "world"})
    editor = _make_fake_editor(note)
    fake_mw = _make_fake_mw()

    with pytest.raises(FieldNotFoundError):
        insert_image(editor, "absent", "pic.webp", mw=fake_mw)

    fake_mw.col.update_note.assert_not_called()


def test_insert_image_error_names_the_missing_field() -> None:
    """The raised ``FieldNotFoundError`` carries the missing field name
    so the caller can surface it in a user-facing message."""

    note = _make_fake_note({"word": "cat", "sentence": "The cat sat."})
    editor = _make_fake_editor(note)
    fake_mw = _make_fake_mw()

    with pytest.raises(FieldNotFoundError) as exc_info:
        insert_image(editor, "image", "cat.jpg", mw=fake_mw)

    # The field name should appear in the exception's string
    # representation so error dialogs can display it.
    assert "image" in str(exc_info.value)
