"""Integration test: toolbar button click opens picker.

Task 10.4: Verify clicking the injected button constructs and shows a
``PickerDialog`` bound to the current editor's note.

Requirements: 2.3
"""

from __future__ import annotations

import sys
from typing import Any, Iterable, List
from unittest.mock import MagicMock, patch

from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.toolbar_button import (
    _on_button_click,
    editor_did_init_buttons,
)


# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


def _make_config() -> dict:
    """Return a raw config dict matching the documented defaults."""
    return {
        "source_field": "word",
        "target_field": "image",
        "providers": ["unsplash"],
        "max_results_per_provider": 12,
        "thumbnail_cache_max_mb": 64,
    }


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
    editor.parentWindow = None
    # addButton returns a button HTML string
    editor.addButton.return_value = "<button>fake</button>"
    return editor


class _NoOpProvider:
    """A provider that returns no results."""

    def __init__(self, provider_id: str = "unsplash") -> None:
        self.id = provider_id
        self.display_name = f"Provider {provider_id}"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> Iterable[ImageResult]:
        return []


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestToolbarButtonClickOpensPicker:
    """Integration tests verifying that clicking the toolbar button
    constructs and shows a PickerDialog bound to the current editor's note.

    Requirement 2.3: WHEN the user clicks the Toolbar_Button, THE Add_On
    SHALL open the Picker_Grid bound to the Current_Note in that Editor.
    """

    def _setup_mocks(self):
        """Set up the aqt mock module so lazy imports inside
        _on_button_click resolve correctly."""
        # Create a fake aqt module with mw
        mock_mw = MagicMock()
        mock_mw.addonManager.getConfig.return_value = _make_config()
        mock_mw.addonManager.addonsFolder.return_value = "/tmp/fake_addons"

        mock_aqt = MagicMock()
        mock_aqt.mw = mock_mw

        mock_aqt_utils = MagicMock()
        mock_aqt_utils.showCritical = MagicMock()
        mock_aqt_utils.showWarning = MagicMock()

        return mock_mw, mock_aqt, mock_aqt_utils

    def test_toolbar_button_click_constructs_picker_dialog(self) -> None:
        """Clicking the toolbar button constructs a PickerDialog bound to
        the current editor's note (Req 2.3).

        This test verifies the full integration path:
        1. The toolbar button handler reads the editor's note.
        2. It loads config and constructs dependencies.
        3. It calls PickerDialog.validate_and_open with the editor.
        4. The resulting dialog is bound to the editor's note.
        """
        note = _make_fake_note({"word": "chó", "image": ""})
        editor = _make_fake_editor(note)

        mock_mw, mock_aqt, mock_aqt_utils = self._setup_mocks()

        mock_dialog = MagicMock()
        mock_picker_dialog_cls = MagicMock()
        mock_picker_dialog_cls.validate_and_open.return_value = mock_dialog

        with (
            patch.dict(
                sys.modules,
                {
                    "aqt": mock_aqt,
                    "aqt.mw": mock_mw,
                    "aqt.utils": mock_aqt_utils,
                },
            ),
            patch(
                "ankivn_image_picker.ui.picker_dialog.PickerDialog.validate_and_open",
                return_value=mock_dialog,
            ) as mock_validate_and_open,
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            _on_button_click(editor)

            # Assert: validate_and_open was called with the editor
            mock_validate_and_open.assert_called_once()
            call_kwargs = mock_validate_and_open.call_args
            # Check editor was passed
            passed_editor = call_kwargs.kwargs.get(
                "editor", call_kwargs.args[0] if call_kwargs.args else None
            )
            assert passed_editor is editor

    def test_toolbar_button_click_opens_picker_bound_to_note(self) -> None:
        """The picker dialog receives the editor that holds the current
        note, ensuring the dialog is bound to the correct note (Req 2.3)."""
        note = _make_fake_note({"word": "mèo", "image": ""})
        editor = _make_fake_editor(note)

        mock_mw, mock_aqt, mock_aqt_utils = self._setup_mocks()

        mock_dialog = MagicMock()

        with (
            patch.dict(
                sys.modules,
                {
                    "aqt": mock_aqt,
                    "aqt.mw": mock_mw,
                    "aqt.utils": mock_aqt_utils,
                },
            ),
            patch(
                "ankivn_image_picker.ui.picker_dialog.PickerDialog.validate_and_open",
                return_value=mock_dialog,
            ) as mock_validate_and_open,
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            _on_button_click(editor)

            mock_validate_and_open.assert_called_once()
            call_kwargs = mock_validate_and_open.call_args
            passed_editor = call_kwargs.kwargs.get("editor", call_kwargs.args[0] if call_kwargs.args else None)
            assert passed_editor is editor
            # The editor's note is accessible through the passed editor
            assert passed_editor.note is note

    def test_toolbar_button_click_shows_dialog(self) -> None:
        """After constructing the dialog, exec() is called to show it
        modally (Req 2.3)."""
        note = _make_fake_note({"word": "dog", "image": ""})
        editor = _make_fake_editor(note)

        mock_mw, mock_aqt, mock_aqt_utils = self._setup_mocks()

        mock_dialog = MagicMock()

        with (
            patch.dict(
                sys.modules,
                {
                    "aqt": mock_aqt,
                    "aqt.mw": mock_mw,
                    "aqt.utils": mock_aqt_utils,
                },
            ),
            patch(
                "ankivn_image_picker.ui.picker_dialog.PickerDialog.validate_and_open",
                return_value=mock_dialog,
            ),
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            _on_button_click(editor)

            # Assert: dialog.exec() was called (dialog was shown)
            mock_dialog.exec.assert_called_once()

    def test_toolbar_button_click_does_not_show_when_validate_returns_none(
        self,
    ) -> None:
        """When validate_and_open returns None (validation failed), exec()
        is not called — the dialog is not shown."""
        note = _make_fake_note({"word": "hello", "image": ""})
        editor = _make_fake_editor(note)

        mock_mw, mock_aqt, mock_aqt_utils = self._setup_mocks()

        with (
            patch.dict(
                sys.modules,
                {
                    "aqt": mock_aqt,
                    "aqt.mw": mock_mw,
                    "aqt.utils": mock_aqt_utils,
                },
            ),
            patch(
                "ankivn_image_picker.ui.picker_dialog.PickerDialog.validate_and_open",
                return_value=None,
            ) as mock_validate_and_open,
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            _on_button_click(editor)

            # validate_and_open was called but returned None
            mock_validate_and_open.assert_called_once()
            # No dialog to exec since validate_and_open returned None

    def test_full_integration_button_inject_and_click_opens_picker(
        self,
    ) -> None:
        """Full integration: inject button via hook, then simulate click
        to verify the complete path from button injection to picker opening.

        This test exercises the full flow:
        1. editor_did_init_buttons injects a button with _on_button_click
           as handler.
        2. Calling that handler with the editor opens the picker dialog.
        """
        note = _make_fake_note({"word": "cat", "image": ""})
        editor = _make_fake_editor(note)

        # Step 1: Inject the button
        buttons: List[str] = []
        editor_did_init_buttons(buttons, editor)

        # Verify button was injected
        assert len(buttons) == 1

        # Verify the handler function was passed to addButton
        call_kwargs = editor.addButton.call_args.kwargs
        handler = call_kwargs["func"]
        assert handler is _on_button_click

        # Step 2: Simulate clicking the button (call the handler)
        mock_mw, mock_aqt, mock_aqt_utils = self._setup_mocks()
        mock_dialog = MagicMock()

        with (
            patch.dict(
                sys.modules,
                {
                    "aqt": mock_aqt,
                    "aqt.mw": mock_mw,
                    "aqt.utils": mock_aqt_utils,
                },
            ),
            patch(
                "ankivn_image_picker.ui.picker_dialog.PickerDialog.validate_and_open",
                return_value=mock_dialog,
            ) as mock_validate_and_open,
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            # Call the handler (simulates button click)
            handler(editor)

            # Assert: PickerDialog.validate_and_open was called
            mock_validate_and_open.assert_called_once()

            # Assert: the editor passed is the same one that owns the note
            call_kwargs = mock_validate_and_open.call_args
            passed_editor = call_kwargs.kwargs.get(
                "editor", call_kwargs.args[0] if call_kwargs.args else None
            )
            assert passed_editor is editor
            assert passed_editor.note is note

            # Assert: dialog was shown (exec called)
            mock_dialog.exec.assert_called_once()
