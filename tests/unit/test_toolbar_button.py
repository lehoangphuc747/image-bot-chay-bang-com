"""Unit tests for :mod:`ankivn_image_picker.ui.toolbar_button`.

Task 9.9: Cover icon and tooltip presence, and that clicking with no
active editor is a no-op.

Requirements: 2.2, 2.4
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from ankivn_image_picker.ui.toolbar_button import (
    _CMD,
    _TOOLTIP,
    _on_button_click,
    editor_did_init_buttons,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_editor(note: Any = None) -> MagicMock:
    """Build a minimal fake editor with an addButton method."""
    editor = MagicMock()
    editor.note = note
    # addButton returns a button HTML string
    editor.addButton.return_value = "<button>fake</button>"
    return editor


# ---------------------------------------------------------------------------
# Tests: Icon and tooltip presence (Req 2.2)
# ---------------------------------------------------------------------------


def test_editor_did_init_buttons_appends_button() -> None:
    """The hook callback appends a button to the buttons list (Req 2.1)."""
    editor = _make_fake_editor(note=MagicMock())
    buttons: List[str] = []

    editor_did_init_buttons(buttons, editor)

    assert len(buttons) == 1


def test_editor_did_init_buttons_calls_addButton_with_tooltip() -> None:
    """The button is created with the correct tooltip (Req 2.2)."""
    editor = _make_fake_editor(note=MagicMock())
    buttons: List[str] = []

    editor_did_init_buttons(buttons, editor)

    editor.addButton.assert_called_once()
    call_kwargs = editor.addButton.call_args
    # Check tip keyword argument
    assert call_kwargs.kwargs.get("tip") == _TOOLTIP or (
        "tip" not in call_kwargs.kwargs
        and any(arg == _TOOLTIP for arg in call_kwargs.args)
    )


def test_editor_did_init_buttons_passes_icon() -> None:
    """The button is created with an icon argument (Req 2.2).

    The icon may be a path string or None (if the icon file is not found),
    but the argument must be passed to addButton.
    """
    editor = _make_fake_editor(note=MagicMock())
    buttons: List[str] = []

    editor_did_init_buttons(buttons, editor)

    editor.addButton.assert_called_once()
    call_kwargs = editor.addButton.call_args
    # icon is passed as a keyword argument
    assert "icon" in call_kwargs.kwargs


def test_editor_did_init_buttons_passes_cmd() -> None:
    """The button is created with a command identifier."""
    editor = _make_fake_editor(note=MagicMock())
    buttons: List[str] = []

    editor_did_init_buttons(buttons, editor)

    editor.addButton.assert_called_once()
    call_kwargs = editor.addButton.call_args
    assert call_kwargs.kwargs.get("cmd") == _CMD


def test_editor_did_init_buttons_passes_func() -> None:
    """The button is created with a click handler function."""
    editor = _make_fake_editor(note=MagicMock())
    buttons: List[str] = []

    editor_did_init_buttons(buttons, editor)

    editor.addButton.assert_called_once()
    call_kwargs = editor.addButton.call_args
    assert call_kwargs.kwargs.get("func") is _on_button_click


def test_tooltip_identifies_addon() -> None:
    """The tooltip text identifies the add-on (Req 2.2)."""
    assert "AnkiVN" in _TOOLTIP or "Image" in _TOOLTIP


# ---------------------------------------------------------------------------
# Tests: Clicking with no active editor is a no-op (Req 2.4)
# ---------------------------------------------------------------------------


def test_on_button_click_with_none_editor_is_noop() -> None:
    """When editor is None, clicking the button does nothing (Req 2.4)."""
    # Should not raise any exception
    _on_button_click(None)


def test_on_button_click_with_editor_without_note_attr_is_noop() -> None:
    """When editor has no 'note' attribute, clicking is a no-op (Req 2.4)."""
    editor = MagicMock(spec=[])  # spec=[] means no attributes
    _on_button_click(editor)


def test_on_button_click_with_editor_note_none_is_noop() -> None:
    """When editor.note is None, clicking is a no-op (Req 2.4)."""
    editor = MagicMock()
    editor.note = None
    _on_button_click(editor)


@patch("ankivn_image_picker.ui.toolbar_button._log")
def test_on_button_click_none_editor_does_not_log_error(
    mock_log: MagicMock,
) -> None:
    """When editor is None, no error is logged (it's an expected no-op)."""
    _on_button_click(None)
    mock_log.error.assert_not_called()
    mock_log.exception.assert_not_called()


@patch("ankivn_image_picker.ui.toolbar_button._log")
def test_on_button_click_note_none_does_not_log_error(
    mock_log: MagicMock,
) -> None:
    """When editor.note is None, no error is logged (it's an expected no-op)."""
    editor = MagicMock()
    editor.note = None
    _on_button_click(editor)
    mock_log.error.assert_not_called()
    mock_log.exception.assert_not_called()
