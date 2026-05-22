"""Integration test: source field read via editor.note.

Task 10.5: Verify the dialog reads the ``source_field`` value from
``editor.note`` rather than the raw editor HTML.

Requirements: 3.1
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.config import Config
from ankivn_image_picker.http import HttpClient
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.picker_dialog import PickerDialog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(source_field: str = "word") -> Config:
    return Config(
        source_field=source_field,
        target_field="image",
        providers=("fake",),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
    )


class _NoOpProvider:
    """Provider that returns no results."""

    def __init__(self) -> None:
        self.id = "fake"
        self.display_name = "Fake"

    def search(self, query: str, *, max_results: int, http: Any, cancel: Any):
        return []


def _make_note(fields: dict[str, str]) -> MagicMock:
    """Build a fake note with the given field names and values."""
    fld_defs = [{"name": name} for name in fields]
    note = MagicMock()
    note.note_type.return_value = {"flds": fld_defs}
    note.fields = list(fields.values())
    return note


def _make_editor(note: MagicMock, *, raw_html: str | None = None) -> MagicMock:
    """Build a fake editor with a note and optional raw HTML content.

    The ``raw_html`` parameter simulates the raw HTML that might be
    present in the editor's web view (e.g. via ``editor.note.fields``
    vs the live DOM). The test verifies that the dialog uses
    ``editor.note`` and NOT any raw HTML accessor.
    """
    editor = MagicMock()
    editor.note = note
    # Simulate a raw HTML property that differs from the note's field
    # value. If the dialog incorrectly reads from this, the test will
    # detect the discrepancy.
    editor.currentField = 0
    editor.web = MagicMock()
    if raw_html is not None:
        editor.web.page.return_value.toHtml = MagicMock(return_value=raw_html)
    return editor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_source_field_read_from_note_not_raw_html(
    mock_show_warning: MagicMock,
) -> None:
    """The dialog reads source_field from editor.note.fields, not raw HTML.

    This test sets up a scenario where the note's field value differs
    from what might be in the editor's raw HTML. The dialog should use
    the note object's value (Req 3.1).
    """
    # The note has "chó" in the source field
    note = _make_note({"word": "chó", "image": ""})

    # The editor's raw HTML contains different content (simulating
    # unsaved edits in the web view or HTML formatting differences)
    editor = _make_editor(
        note,
        raw_html='<div class="field">mèo</div>',
    )

    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        dialog = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    # Dialog should open with the note's field value, not the raw HTML
    assert dialog is not None
    assert dialog._query == "chó"


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_source_field_uses_note_fields_index(
    mock_show_warning: MagicMock,
) -> None:
    """The dialog accesses the correct field index from editor.note.fields.

    Verifies that the dialog looks up the source_field by name in the
    note type's field definitions and reads the corresponding index
    from editor.note.fields (Req 3.1).
    """
    # Note with multiple fields; source_field is not the first one
    note = _make_note({
        "sentence": "The dog runs.",
        "word": "hund",
        "image": "",
    })
    editor = _make_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        dialog = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    # Should read "hund" from the second field, not "The dog runs."
    assert dialog is not None
    assert dialog._query == "hund"


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_source_field_html_in_note_is_stripped(
    mock_show_warning: MagicMock,
) -> None:
    """HTML tags in the note's field value are stripped before use as query.

    The dialog reads from editor.note.fields (which may contain HTML
    formatting from Anki's rich text editor) and normalises it
    (Req 3.1, 3.2, 3.3).
    """
    # The note's field contains HTML-formatted content
    note = _make_note({"word": "<b>café</b>", "image": ""})
    editor = _make_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        dialog = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    # HTML should be stripped; the query should be plain text
    assert dialog is not None
    assert dialog._query == "café"


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_editor_note_accessed_not_editor_web(
    mock_show_warning: MagicMock,
) -> None:
    """The dialog accesses editor.note, not editor.web or other HTML sources.

    This verifies the integration contract: the dialog reads the
    source field value through the note model object, ensuring it gets
    the persisted/committed value rather than any transient DOM state.
    """
    note = _make_note({"word": "baum", "image": ""})
    editor = _make_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        dialog = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    assert dialog is not None
    assert dialog._query == "baum"

    # Verify that editor.note was accessed (the note property was read)
    # The mock tracks attribute access; editor.note should have been used
    # but editor.web should NOT have been called for field reading
    assert not editor.web.page.called
    # The note's note_type() and fields were accessed
    note.note_type.assert_called()
