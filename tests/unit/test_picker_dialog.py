"""Unit tests for :mod:`ankivn_image_picker.ui.picker_dialog`.

Task 9.7: Cover missing source field, empty query, dialog title
contains query, scrollable grid layout, and hover shows provider id.

Requirements: 3.4, 3.5, 6.1, 6.2, 6.3
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, List
from unittest.mock import MagicMock, patch

import pytest

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.config import Config
from ankivn_image_picker.http import HttpClient
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.picker_dialog import PickerDialog


# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


def _make_config(
    source_field: str = "word",
    target_field: str = "image",
) -> Config:
    """Create a minimal valid Config for testing."""
    return Config(
        source_field=source_field,
        target_field=target_field,
        providers=("fake_provider",),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
    )


def _make_fake_note(fields: dict[str, str]) -> Any:
    """Build a minimal fake note with the given field names and values."""
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


class _NoOpProvider:
    """A provider that returns no results (used when we don't care about search)."""

    def __init__(self, provider_id: str = "fake_provider") -> None:
        self.id = provider_id
        self.display_name = f"Fake {provider_id}"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> Iterable[ImageResult]:
        return []


class _ResultProvider:
    """A provider that returns a fixed list of results."""

    def __init__(self, provider_id: str, results: List[ImageResult]) -> None:
        self.id = provider_id
        self.display_name = f"Provider {provider_id}"
        self._results = results

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> Iterable[ImageResult]:
        return self._results[:max_results]


def _make_result(provider_id: str = "unsplash", index: int = 1) -> ImageResult:
    """Create a minimal valid ImageResult for testing."""
    return ImageResult(
        provider_id=provider_id,
        thumbnail_url=f"https://example.com/{provider_id}/thumb_{index}.jpg",
        full_url=f"https://example.com/{provider_id}/full_{index}.jpg",
        extension="jpg",
    )


# ---------------------------------------------------------------------------
# Tests: Missing source field (Req 3.4)
# ---------------------------------------------------------------------------


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_validate_and_open_missing_source_field_shows_warning(
    mock_show_warning: MagicMock,
) -> None:
    """When source_field does not exist on the note type,
    validate_and_open shows a warning naming the missing field (Req 3.4)."""
    note = _make_fake_note({"Front": "hello", "Back": "world"})
    editor = _make_fake_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        result = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    # Should return None (dialog not opened)
    assert result is None

    # Should have shown a warning
    mock_show_warning.assert_called_once()
    warning_msg = mock_show_warning.call_args[0][0]
    # The warning should name the missing field
    assert "word" in warning_msg


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_validate_and_open_missing_source_field_does_not_open_dialog(
    mock_show_warning: MagicMock,
) -> None:
    """When source_field is missing, no PickerDialog is created (Req 3.4)."""
    note = _make_fake_note({"sentence": "The cat sat."})
    editor = _make_fake_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        result = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    assert result is None


# ---------------------------------------------------------------------------
# Tests: Empty query (Req 3.5)
# ---------------------------------------------------------------------------


@patch("ankivn_image_picker.ui.picker_dialog.tooltip")
@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_validate_and_open_empty_query_shows_tooltip(
    mock_show_warning: MagicMock,
    mock_tooltip: MagicMock,
) -> None:
    """When the source field is empty after stripping, a tooltip is shown
    and the dialog is not opened (Req 3.5)."""
    # Source field exists but is empty
    note = _make_fake_note({"word": "", "image": ""})
    editor = _make_fake_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        result = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    assert result is None
    # tooltip should mention "source field is empty"
    mock_tooltip.assert_called_once()
    tooltip_msg = mock_tooltip.call_args[0][0]
    assert "empty" in tooltip_msg.lower()


@patch("ankivn_image_picker.ui.picker_dialog.tooltip")
@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_validate_and_open_whitespace_only_query_is_empty(
    mock_show_warning: MagicMock,
    mock_tooltip: MagicMock,
) -> None:
    """A source field containing only whitespace is treated as empty (Req 3.5)."""
    note = _make_fake_note({"word": "   \t\n  ", "image": ""})
    editor = _make_fake_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        result = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    assert result is None
    mock_tooltip.assert_called_once()


@patch("ankivn_image_picker.ui.picker_dialog.tooltip")
@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_validate_and_open_html_only_query_is_empty(
    mock_show_warning: MagicMock,
    mock_tooltip: MagicMock,
) -> None:
    """A source field containing only HTML tags (no text) is treated as empty (Req 3.5)."""
    note = _make_fake_note({"word": "<b></b><i></i>", "image": ""})
    editor = _make_fake_editor(note)
    config = _make_config(source_field="word")
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)
        providers = [_NoOpProvider()]

        result = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
        )

    assert result is None
    mock_tooltip.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Dialog title contains query (Req 6.1)
# ---------------------------------------------------------------------------


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_dialog_title_contains_query(mock_show_warning: MagicMock) -> None:
    """The dialog title/header contains the search query text (Req 6.1)."""
    note = _make_fake_note({"word": "chó", "image": ""})
    editor = _make_fake_editor(note)
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
    # The window title should contain the query
    assert "chó" in dialog._window_title


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_dialog_title_updates_on_requery(mock_show_warning: MagicMock) -> None:
    """When the user re-queries, the dialog title updates to the new query (Req 6.1)."""
    note = _make_fake_note({"word": "cat", "image": ""})
    editor = _make_fake_editor(note)
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
    assert "cat" in dialog._window_title

    # Simulate re-query by setting the search input and triggering returnPressed
    dialog._search_input.setText("dog")
    dialog._on_requery()

    assert "dog" in dialog._window_title


# ---------------------------------------------------------------------------
# Tests: Scrollable grid layout (Req 6.2)
# ---------------------------------------------------------------------------


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_grid_model_arranges_results_in_arrival_order(
    mock_show_warning: MagicMock,
) -> None:
    """Image results are arranged in the grid in arrival order (Req 6.2).

    The grid model preserves insertion order, which is the foundation
    for a scrollable grid layout. Results appear in the order they
    arrive from providers.
    """
    note = _make_fake_note({"word": "flower", "image": ""})
    editor = _make_fake_editor(note)
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

    # Simulate results arriving in a specific order
    results = [
        _make_result("unsplash", 1),
        _make_result("pixabay", 2),
        _make_result("unsplash", 3),
    ]

    for r in results:
        dialog._on_result_ready(r)

    # Grid model should have 3 rows in arrival order
    assert dialog._grid_model.row_count() == 3
    assert dialog._grid_model.rows[0].result.provider_id == "unsplash"
    assert dialog._grid_model.rows[1].result.provider_id == "pixabay"
    assert dialog._grid_model.rows[2].result.provider_id == "unsplash"


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_grid_model_supports_multiple_results(
    mock_show_warning: MagicMock,
) -> None:
    """The grid model can hold many results, supporting scrollable display (Req 6.2)."""
    note = _make_fake_note({"word": "tree", "image": ""})
    editor = _make_fake_editor(note)
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

    # Add many results to simulate a full grid that would need scrolling
    for i in range(24):
        dialog._on_result_ready(_make_result("unsplash", i))

    assert dialog._grid_model.row_count() == 24


# ---------------------------------------------------------------------------
# Tests: Hover shows provider id (Req 6.3)
# ---------------------------------------------------------------------------


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_grid_cell_carries_provider_id_for_hover(
    mock_show_warning: MagicMock,
) -> None:
    """Each grid cell carries the provider_id so hover can display it (Req 6.3).

    The grid model stores the full ImageResult on each cell, which
    includes provider_id. The UI layer (delegate) uses this to render
    a tooltip on hover showing the source provider.
    """
    note = _make_fake_note({"word": "mountain", "image": ""})
    editor = _make_fake_editor(note)
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

    # Add results from different providers
    result_unsplash = _make_result("unsplash", 1)
    result_pixabay = _make_result("pixabay", 2)

    dialog._on_result_ready(result_unsplash)
    dialog._on_result_ready(result_pixabay)

    # Each cell should carry the provider_id for hover tooltip display
    assert dialog._grid_model.rows[0].result.provider_id == "unsplash"
    assert dialog._grid_model.rows[1].result.provider_id == "pixabay"


@patch("ankivn_image_picker.ui.picker_dialog.showWarning")
def test_grid_cell_provider_id_matches_result_source(
    mock_show_warning: MagicMock,
) -> None:
    """The provider_id on each grid cell matches the provider that produced
    the result, ensuring hover tooltip accuracy (Req 6.3)."""
    note = _make_fake_note({"word": "ocean", "image": ""})
    editor = _make_fake_editor(note)
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

    # Simulate results from multiple providers arriving interleaved
    results = [
        _make_result("pexels", 1),
        _make_result("unsplash", 2),
        _make_result("pexels", 3),
        _make_result("pixabay", 4),
    ]

    for r in results:
        dialog._on_result_ready(r)

    # Verify each cell's provider_id matches the result that produced it
    for i, r in enumerate(results):
        assert dialog._grid_model.rows[i].result.provider_id == r.provider_id
        # The full result is preserved so hover can access any field
        assert dialog._grid_model.rows[i].result is r
