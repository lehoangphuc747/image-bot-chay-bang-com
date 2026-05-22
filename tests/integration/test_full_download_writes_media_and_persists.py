"""Integration test: full download writes to media and persists note.

Task 10.6: Verify a successful selection calls ``mw.col.media.write_data``,
appends an ``<img>`` to the target field, and calls ``mw.col.update_note``.

Requirements: 7.1, 9.2, 9.5
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable
from unittest.mock import MagicMock, call, patch

import pytest

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.config import Config
from ankivn_image_picker.http import HttpClient
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.picker_dialog import PickerDialog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> Config:
    return Config(
        source_field="word",
        target_field="image",
        providers=("test_provider",),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
    )


def _make_result() -> ImageResult:
    return ImageResult(
        provider_id="unsplash",
        thumbnail_url="https://example.com/thumb.jpg",
        full_url="https://example.com/full.jpg",
        extension="jpg",
    )


class _NoOpProvider:
    """Provider that yields no results (search is not exercised)."""

    def __init__(self) -> None:
        self.id = "test_provider"
        self.display_name = "Test Provider"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> Iterable[ImageResult]:
        return []


def _make_editor_and_mw(
    source_value: str = "cat",
    target_value: str = "",
) -> tuple[MagicMock, MagicMock]:
    """Create a mock editor and mw that behave like real Anki objects.

    The note has two fields: "word" (source) and "image" (target).
    The mw mock provides col.media.write_data and col.update_note.
    """
    # Build a note with mutable fields list
    note = MagicMock()
    note.fields = [source_value, target_value]
    note.note_type.return_value = {
        "flds": [
            {"name": "word"},
            {"name": "image"},
        ]
    }

    editor = MagicMock()
    editor.note = note
    editor.loadNoteKeepingFocus = MagicMock()

    # Build mw with col.media and col.update_note
    mw = MagicMock()
    # write_data returns the filename it was given (no rename)
    mw.col.media.write_data = MagicMock(side_effect=lambda fn, data: fn)
    mw.col.media.have = MagicMock(return_value=False)
    mw.col.update_note = MagicMock()

    return editor, mw


def _make_dialog(tmp_dir: str, editor: MagicMock) -> PickerDialog:
    """Create a PickerDialog wired to the given editor."""
    config = _make_config()
    providers = [_NoOpProvider()]
    http = HttpClient()
    cache = ThumbnailCache(root=Path(tmp_dir), max_bytes=64 * 1024 * 1024)

    dialog = PickerDialog(
        editor=editor,
        config=config,
        query="cat",
        providers=providers,
        http=http,
        cache=cache,
    )
    return dialog


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestFullDownloadWritesMediaAndPersistsNote:
    """Integration: a successful image selection writes to media and persists
    the note through the full signal → editor_bridge → Anki API chain."""

    def test_write_data_called_with_image_bytes(self) -> None:
        """A successful selection calls mw.col.media.write_data with the
        downloaded image bytes (Req 7.1)."""
        import ankivn_image_picker.editor_bridge as eb_module

        editor, fake_mw = _make_editor_and_mw()
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

        with TemporaryDirectory() as tmp:
            dialog = _make_dialog(tmp, editor)
            result = _make_result()
            dialog._bus.result_ready.emit(result)

            # Patch the module-level mw so editor_bridge uses our fake
            original_mw = eb_module.mw
            eb_module.mw = fake_mw
            try:
                # Suppress accept() to avoid side effects
                dialog.accept = MagicMock()

                # Emit download_complete — this triggers the full flow
                dialog._bus.download_complete.emit(
                    result.full_url, image_bytes, "png"
                )

                # mw.col.media.write_data must have been called
                fake_mw.col.media.write_data.assert_called_once()
                call_args = fake_mw.col.media.write_data.call_args
                saved_filename = call_args[0][0]
                saved_bytes = call_args[0][1]

                # The bytes written are the exact image bytes (no re-encoding)
                assert saved_bytes == image_bytes
                # The filename should end with the correct extension
                assert saved_filename.endswith(".png")
            finally:
                eb_module.mw = original_mw

    def test_img_tag_appended_to_target_field(self) -> None:
        """A successful selection appends an <img> tag to the target field
        content (Req 9.2)."""
        import ankivn_image_picker.editor_bridge as eb_module

        editor, fake_mw = _make_editor_and_mw(target_value="existing content")
        image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100

        with TemporaryDirectory() as tmp:
            dialog = _make_dialog(tmp, editor)
            result = _make_result()
            dialog._bus.result_ready.emit(result)

            original_mw = eb_module.mw
            eb_module.mw = fake_mw
            try:
                dialog.accept = MagicMock()

                dialog._bus.download_complete.emit(
                    result.full_url, image_bytes, "jpg"
                )

                # The target field (index 1) should now contain the
                # original content plus an <img> tag
                target_field_value = editor.note.fields[1]
                assert target_field_value.startswith("existing content")
                assert "<img " in target_field_value
                assert 'src="' in target_field_value
                # The filename derived from query "cat" with ext "jpg"
                assert ".jpg" in target_field_value
            finally:
                eb_module.mw = original_mw

    def test_update_note_called_after_insertion(self) -> None:
        """A successful selection calls mw.col.update_note to persist the
        change (Req 9.5)."""
        import ankivn_image_picker.editor_bridge as eb_module

        editor, fake_mw = _make_editor_and_mw()
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with TemporaryDirectory() as tmp:
            dialog = _make_dialog(tmp, editor)
            result = _make_result()
            dialog._bus.result_ready.emit(result)

            original_mw = eb_module.mw
            eb_module.mw = fake_mw
            try:
                dialog.accept = MagicMock()

                dialog._bus.download_complete.emit(
                    result.full_url, image_bytes, "png"
                )

                # mw.col.update_note must have been called with the note
                fake_mw.col.update_note.assert_called_once_with(editor.note)
            finally:
                eb_module.mw = original_mw

    def test_full_flow_sequence(self) -> None:
        """The full flow calls write_data, appends img, and update_note in
        the correct sequence (Req 7.1, 9.2, 9.5).

        This test verifies the end-to-end integration: the
        download_complete signal triggers save_to_media (which calls
        write_data), then insert_image (which appends the tag and calls
        update_note), and finally the dialog closes via accept().
        """
        import ankivn_image_picker.editor_bridge as eb_module

        editor, fake_mw = _make_editor_and_mw(target_value="<b>hello</b>")
        image_bytes = b"GIF89a" + b"\x00" * 150

        # Track call order
        call_order: list[str] = []
        fake_mw.col.media.write_data.side_effect = (
            lambda fn, data: (call_order.append("write_data"), fn)[1]
        )
        fake_mw.col.update_note.side_effect = (
            lambda note: call_order.append("update_note")
        )

        with TemporaryDirectory() as tmp:
            dialog = _make_dialog(tmp, editor)
            result = _make_result()
            dialog._bus.result_ready.emit(result)

            original_mw = eb_module.mw
            eb_module.mw = fake_mw
            try:
                accept_called = []
                dialog.accept = lambda: (
                    call_order.append("accept"),
                    accept_called.append(True),
                )

                dialog._bus.download_complete.emit(
                    result.full_url, image_bytes, "gif"
                )

                # Verify the sequence: write_data → update_note → accept
                assert call_order == ["write_data", "update_note", "accept"]

                # Verify the target field was modified
                target_value = editor.note.fields[1]
                assert target_value.startswith("<b>hello</b>")
                assert "<img " in target_value
                assert ".gif" in target_value

                # Verify editor was refreshed (Req 9.2)
                editor.loadNoteKeepingFocus.assert_called_once()
            finally:
                eb_module.mw = original_mw

    def test_write_data_receives_derived_filename(self) -> None:
        """write_data receives a filename derived from the search query and
        the image extension, not an arbitrary name (Req 8.2)."""
        import ankivn_image_picker.editor_bridge as eb_module

        editor, fake_mw = _make_editor_and_mw()
        image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 80

        with TemporaryDirectory() as tmp:
            dialog = _make_dialog(tmp, editor)
            result = _make_result()
            dialog._bus.result_ready.emit(result)

            original_mw = eb_module.mw
            eb_module.mw = fake_mw
            try:
                dialog.accept = MagicMock()

                dialog._bus.download_complete.emit(
                    result.full_url, image_bytes, "jpg"
                )

                call_args = fake_mw.col.media.write_data.call_args
                filename = call_args[0][0]

                # The filename should be derived from the query "cat"
                assert "cat" in filename
                assert filename.endswith(".jpg")
            finally:
                eb_module.mw = original_mw
