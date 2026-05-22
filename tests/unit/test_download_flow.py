"""Unit tests for the download flow in the picker dialog.

Task 9.8: Cover progress indicator while downloading, dialog closes on
``download_complete``, HTTP error keeps dialog open.

Requirements: 7.2, 7.3, 7.4
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, List, Optional
from unittest.mock import MagicMock, patch

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.config import Config
from ankivn_image_picker.http import HttpClient, HttpResponse
from ankivn_image_picker.orchestrator import FullImageDownloader
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.grid_view import GridModel
from ankivn_image_picker.ui.picker_dialog import PickerDialog
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(provider_id: str = "unsplash", index: int = 1) -> ImageResult:
    """Create a minimal valid ImageResult for testing."""
    return ImageResult(
        provider_id=provider_id,
        thumbnail_url=f"https://example.com/{provider_id}/thumb_{index}.jpg",
        full_url=f"https://example.com/{provider_id}/full_{index}.jpg",
        extension="jpg",
    )


class _NoOpProvider:
    """A provider that yields no results (search is never called in these tests)."""

    def __init__(self, provider_id: str = "test_provider") -> None:
        self.id = provider_id
        self.display_name = f"Test {provider_id}"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> Iterable[ImageResult]:
        return []


def _make_config() -> Config:
    """Create a minimal valid Config for testing."""
    return Config(
        source_field="word",
        target_field="image",
        providers=("test_provider",),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
    )


def _make_editor(source_value: str = "test", target_field: str = "image") -> MagicMock:
    """Create a mock editor with a note that has source and target fields."""
    editor = MagicMock()
    note = MagicMock()
    note.fields = [source_value, ""]
    note.note_type.return_value = {
        "flds": [
            {"name": "word"},
            {"name": "image"},
        ]
    }
    editor.note = note
    editor.loadNoteKeepingFocus = MagicMock()
    return editor


def _make_dialog(
    tmp_dir: str,
    editor: Optional[MagicMock] = None,
) -> PickerDialog:
    """Create a PickerDialog with minimal dependencies for testing."""
    if editor is None:
        editor = _make_editor()

    config = _make_config()
    providers = [_NoOpProvider()]
    http = HttpClient()
    cache = ThumbnailCache(root=Path(tmp_dir), max_bytes=64 * 1024 * 1024)

    dialog = PickerDialog(
        editor=editor,
        config=config,
        query="test",
        providers=providers,
        http=http,
        cache=cache,
    )
    return dialog


# ---------------------------------------------------------------------------
# Test: Progress indicator while downloading (Req 7.2)
# ---------------------------------------------------------------------------


def test_download_progress_updates_grid_cell_state() -> None:
    """When download_progress is emitted, the grid cell state becomes
    'downloading' with the reported fraction (Req 7.2).

    The picker dialog connects the bus's download_progress signal to the
    grid model's on_download_progress slot, which updates the matching
    cell's state and progress fraction.
    """
    with TemporaryDirectory() as tmp:
        dialog = _make_dialog(tmp)

        # Simulate a result arriving in the grid
        result = _make_result()
        dialog._bus.result_ready.emit(result)

        # Verify the cell is in 'pending' state initially
        assert dialog._grid_model.row_count() == 1
        cell = dialog._grid_model.rows[0]
        assert cell.state == "pending"
        assert cell.progress == 0.0

        # Simulate download_progress signal (Req 7.2)
        dialog._bus.download_progress.emit(result.full_url, 0.5)

        # The cell should now be in 'downloading' state with progress
        cell = dialog._grid_model.rows[0]
        assert cell.state == "downloading"
        assert cell.progress == 0.5


def test_download_progress_updates_to_full() -> None:
    """Progress indicator reaches 1.0 before download_complete (Req 7.2)."""
    with TemporaryDirectory() as tmp:
        dialog = _make_dialog(tmp)

        result = _make_result()
        dialog._bus.result_ready.emit(result)

        # Simulate progress updates from 0 to 1
        dialog._bus.download_progress.emit(result.full_url, 0.0)
        cell = dialog._grid_model.rows[0]
        assert cell.state == "downloading"
        assert cell.progress == 0.0

        dialog._bus.download_progress.emit(result.full_url, 0.5)
        assert cell.progress == 0.5

        dialog._bus.download_progress.emit(result.full_url, 1.0)
        assert cell.progress == 1.0


# ---------------------------------------------------------------------------
# Test: Dialog closes on download_complete (Req 7.3)
# ---------------------------------------------------------------------------


def test_dialog_closes_on_download_complete() -> None:
    """When download_complete is emitted, the dialog calls accept() (Req 7.3).

    A successful full-image download triggers the dialog to save the
    image to media, insert it into the target field, and close via
    accept().
    """
    import ankivn_image_picker.editor_bridge as eb_module

    with TemporaryDirectory() as tmp:
        editor = _make_editor()
        dialog = _make_dialog(tmp, editor=editor)

        result = _make_result()
        dialog._bus.result_ready.emit(result)

        # Mock the editor_bridge functions so we don't need real Anki
        fake_mw = MagicMock()
        fake_mw.col.media.write_data.return_value = "test.jpg"
        fake_mw.col.media.have.return_value = False
        fake_mw.col.update_note = MagicMock()

        with patch.object(
            eb_module, "save_to_media", return_value="test.jpg"
        ) as mock_save, patch.object(
            eb_module, "insert_image"
        ) as mock_insert, patch.object(
            eb_module, "mw", fake_mw
        ):
            # Track whether accept() was called
            accept_called = []
            dialog.accept = lambda: accept_called.append(True)

            # Emit download_complete (Req 7.3)
            image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            dialog._bus.download_complete.emit(
                result.full_url, image_bytes, "jpg"
            )

            # Dialog should have called accept()
            assert len(accept_called) == 1, (
                "Dialog should close (accept) on successful download"
            )

            # editor_bridge.save_to_media should have been called
            mock_save.assert_called_once()

            # editor_bridge.insert_image should have been called
            mock_insert.assert_called_once()


def test_download_complete_saves_and_inserts_image() -> None:
    """download_complete triggers save_to_media then insert_image (Req 7.3).

    Verifies the correct sequence: derive filename, save bytes to media,
    insert img tag into target field.
    """
    import ankivn_image_picker.editor_bridge as eb_module

    with TemporaryDirectory() as tmp:
        editor = _make_editor()
        dialog = _make_dialog(tmp, editor=editor)

        result = _make_result()
        dialog._bus.result_ready.emit(result)

        fake_mw = MagicMock()
        fake_mw.col.media.write_data.return_value = "test.jpg"
        fake_mw.col.media.have.return_value = False
        fake_mw.col.update_note = MagicMock()

        with patch.object(
            eb_module, "save_to_media", return_value="test.jpg"
        ) as mock_save, patch.object(
            eb_module, "insert_image"
        ) as mock_insert, patch.object(
            eb_module, "mw", fake_mw
        ):
            # Suppress accept() to avoid side effects
            dialog.accept = lambda: None

            image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 50
            dialog._bus.download_complete.emit(
                result.full_url, image_bytes, "jpg"
            )

            # save_to_media called with a filename and the image bytes
            call_args = mock_save.call_args
            assert call_args is not None
            saved_filename = call_args[0][0]
            saved_bytes = call_args[0][1]
            assert saved_filename.endswith(".jpg")
            assert saved_bytes == image_bytes

            # insert_image called with editor, target field, and filename
            insert_args = mock_insert.call_args
            assert insert_args is not None
            assert insert_args[0][0] is editor
            assert insert_args[0][1] == "image"  # target_field
            assert insert_args[0][2] == "test.jpg"  # used filename


# ---------------------------------------------------------------------------
# Test: HTTP error keeps dialog open (Req 7.4)
# ---------------------------------------------------------------------------


def test_download_failed_keeps_dialog_open() -> None:
    """When download_failed is emitted, the dialog stays open (Req 7.4).

    If the Full_Image download fails with a network or HTTP error, the
    add-on displays an error message and keeps the Picker_Grid open for
    another selection.
    """
    with TemporaryDirectory() as tmp:
        dialog = _make_dialog(tmp)

        result = _make_result()
        dialog._bus.result_ready.emit(result)

        # Simulate user clicking the image (locks selection)
        dialog._selection_locked = True
        dialog._selected_result = result

        # Track whether accept() is called (it should NOT be)
        accept_called = []
        dialog.accept = lambda: accept_called.append(True)

        # Emit download_failed (Req 7.4)
        dialog._bus.download_failed.emit(
            result.full_url, "HTTP 503 fetching https://example.com/full_1.jpg"
        )

        # Dialog should NOT have called accept()
        assert len(accept_called) == 0, (
            "Dialog should remain open when download fails"
        )


def test_download_failed_unlocks_selection() -> None:
    """After download failure, the user can select another image (Req 7.4).

    The dialog unlocks selection so the user can try a different image
    without closing and reopening the picker.
    """
    with TemporaryDirectory() as tmp:
        dialog = _make_dialog(tmp)

        result = _make_result()
        dialog._bus.result_ready.emit(result)

        # Lock selection as if user clicked an image
        dialog._selection_locked = True

        # Emit download_failed
        dialog._bus.download_failed.emit(
            result.full_url, "Connection timeout"
        )

        # Selection should be unlocked for another attempt
        assert dialog._selection_locked is False, (
            "Selection should be unlocked after download failure so "
            "user can pick another image"
        )


def test_download_failed_does_not_modify_note() -> None:
    """A failed download does not touch the editor's note (Req 7.4).

    The note should remain unchanged when the download fails — no
    save_to_media or insert_image calls should occur.
    """
    import ankivn_image_picker.editor_bridge as eb_module

    with TemporaryDirectory() as tmp:
        editor = _make_editor()
        dialog = _make_dialog(tmp, editor=editor)

        result = _make_result()
        dialog._bus.result_ready.emit(result)

        with patch.object(
            eb_module, "save_to_media"
        ) as mock_save, patch.object(
            eb_module, "insert_image"
        ) as mock_insert:
            dialog._bus.download_failed.emit(
                result.full_url, "HTTP 500 Server Error"
            )

            # Neither save nor insert should have been called
            mock_save.assert_not_called()
            mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# Test: FullImageDownloader emits correct signals
# ---------------------------------------------------------------------------


def test_full_image_downloader_emits_progress_and_complete() -> None:
    """FullImageDownloader emits download_progress then download_complete
    on a successful download (Req 7.2, 7.3).
    """
    bus = WorkerBus()
    cancel = CancellationToken()

    # Create a fake HTTP client that returns a valid image response
    fake_http = MagicMock(spec=HttpClient)
    fake_http.get.return_value = HttpResponse(
        body=b"\x89PNG" + b"\x00" * 100,
        content_type="image/png",
        url="https://example.com/full.png",
        status_code=200,
    )

    progress_events: list = []
    complete_events: list = []
    failed_events: list = []

    bus.download_progress.connect(
        lambda url, frac: progress_events.append((url, frac))
    )
    bus.download_complete.connect(
        lambda url, data, ext: complete_events.append((url, data, ext))
    )
    bus.download_failed.connect(
        lambda url, msg: failed_events.append((url, msg))
    )

    result = _make_result()
    downloader = FullImageDownloader(http=fake_http, bus=bus, cancel=cancel)
    downloader.fetch(result)

    # Should have emitted progress at 0.0 and 1.0
    assert len(progress_events) == 2
    assert progress_events[0] == (result.full_url, 0.0)
    assert progress_events[1] == (result.full_url, 1.0)

    # Should have emitted download_complete
    assert len(complete_events) == 1
    assert complete_events[0][0] == result.full_url
    assert complete_events[0][2] == "jpg"

    # Should NOT have emitted download_failed
    assert len(failed_events) == 0


def test_full_image_downloader_emits_failed_on_http_error() -> None:
    """FullImageDownloader emits download_failed on HTTP error (Req 7.4).

    When the HTTP client raises a DownloadError, the downloader emits
    download_failed with the URL and error message.
    """
    from ankivn_image_picker.errors import DownloadError

    bus = WorkerBus()
    cancel = CancellationToken()

    fake_http = MagicMock(spec=HttpClient)
    fake_http.get.side_effect = DownloadError("HTTP 503 fetching url")

    progress_events: list = []
    complete_events: list = []
    failed_events: list = []

    bus.download_progress.connect(
        lambda url, frac: progress_events.append((url, frac))
    )
    bus.download_complete.connect(
        lambda url, data, ext: complete_events.append((url, data, ext))
    )
    bus.download_failed.connect(
        lambda url, msg: failed_events.append((url, msg))
    )

    result = _make_result()
    downloader = FullImageDownloader(http=fake_http, bus=bus, cancel=cancel)
    downloader.fetch(result)

    # Should have emitted initial progress at 0.0
    assert len(progress_events) == 1
    assert progress_events[0] == (result.full_url, 0.0)

    # Should NOT have emitted download_complete
    assert len(complete_events) == 0

    # Should have emitted download_failed
    assert len(failed_events) == 1
    assert failed_events[0][0] == result.full_url
    assert "503" in failed_events[0][1]


def test_full_image_downloader_emits_failed_on_invalid_image() -> None:
    """FullImageDownloader emits download_failed when response is not a
    valid image (empty body or wrong content type) (Req 7.5).
    """
    bus = WorkerBus()
    cancel = CancellationToken()

    # Return a response with non-image content type
    fake_http = MagicMock(spec=HttpClient)
    fake_http.get.return_value = HttpResponse(
        body=b"<html>error page</html>",
        content_type="text/html",
        url="https://example.com/full.jpg",
        status_code=200,
    )

    complete_events: list = []
    failed_events: list = []

    bus.download_complete.connect(
        lambda url, data, ext: complete_events.append((url, data, ext))
    )
    bus.download_failed.connect(
        lambda url, msg: failed_events.append((url, msg))
    )

    result = _make_result()
    downloader = FullImageDownloader(http=fake_http, bus=bus, cancel=cancel)
    downloader.fetch(result)

    # Should NOT have emitted download_complete
    assert len(complete_events) == 0

    # Should have emitted download_failed
    assert len(failed_events) == 1
    assert failed_events[0][0] == result.full_url
