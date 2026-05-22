"""Integration test: config reloaded on each picker open.

Task 10.3: Mutate the underlying config dict between two picker opens
and assert the second open observes the new values without an Anki restart.

Requirements: 1.7
"""

from __future__ import annotations

import sys
from typing import Any, Iterable, List
from unittest.mock import MagicMock, patch

from ankivn_image_picker.config import Config
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.toolbar_button import _on_button_click


# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


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


class TestConfigReloadedOnEachPickerOpen:
    """Integration tests verifying that config is re-read from Anki's
    config system on every picker open, so edits take effect without
    an Anki restart.

    Requirement 1.7: WHEN the user edits Config through Anki's add-on
    configuration UI, THE Add_On SHALL apply the new values on the next
    Picker_Grid open without requiring an Anki restart.
    """

    def _setup_mocks(self, config_dict: dict) -> tuple:
        """Set up the aqt mock module with a given config dict."""
        mock_mw = MagicMock()
        mock_mw.addonManager.getConfig.return_value = config_dict
        mock_mw.addonManager.addonsFolder.return_value = "/tmp/fake_addons"

        mock_aqt = MagicMock()
        mock_aqt.mw = mock_mw

        mock_aqt_utils = MagicMock()
        mock_aqt_utils.showCritical = MagicMock()
        mock_aqt_utils.showWarning = MagicMock()

        return mock_mw, mock_aqt, mock_aqt_utils

    def test_second_open_observes_mutated_config_values(self) -> None:
        """Mutate the underlying config dict between two picker opens
        and assert the second open observes the new values.

        This simulates the user editing config via Anki's config UI
        between two toolbar button clicks. The add-on must re-read
        config on each open (Req 1.7).
        """
        note = _make_fake_note({"word": "chó", "image": ""})
        editor = _make_fake_editor(note)

        # First config: default values
        config_v1 = {
            "source_field": "word",
            "target_field": "image",
            "providers": ["unsplash"],
            "max_results_per_provider": 12,
            "thumbnail_cache_max_mb": 64,
        }

        # Second config: changed values (simulates user editing config)
        config_v2 = {
            "source_field": "vocabulary",
            "target_field": "picture",
            "providers": ["unsplash"],
            "max_results_per_provider": 25,
            "thumbnail_cache_max_mb": 128,
        }

        # Track configs observed by PickerDialog.validate_and_open
        observed_configs: List[Config] = []

        def _capture_config(**kwargs: Any) -> None:
            """Capture the config passed to validate_and_open."""
            config = kwargs.get("config")
            if config is not None:
                observed_configs.append(config)
            return None  # Simulate validation failure to keep test simple

        # The mutable config reference that getConfig returns
        current_config = [config_v1]

        mock_mw = MagicMock()
        mock_mw.addonManager.getConfig.side_effect = lambda _: current_config[0]
        mock_mw.addonManager.addonsFolder.return_value = "/tmp/fake_addons"

        mock_aqt = MagicMock()
        mock_aqt.mw = mock_mw

        mock_aqt_utils = MagicMock()

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
                side_effect=_capture_config,
            ),
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            # First open: should use config_v1
            _on_button_click(editor)

            # Mutate the config (simulates user editing via Anki's UI)
            current_config[0] = config_v2

            # Second open: should use config_v2 (fresh read)
            _on_button_click(editor)

        # Assert: two opens occurred
        assert len(observed_configs) == 2

        # Assert: first open used config_v1 values
        first_config = observed_configs[0]
        assert first_config.source_field == "word"
        assert first_config.target_field == "image"
        assert first_config.max_results_per_provider == 12
        assert first_config.thumbnail_cache_max_mb == 64

        # Assert: second open used config_v2 values (Req 1.7)
        second_config = observed_configs[1]
        assert second_config.source_field == "vocabulary"
        assert second_config.target_field == "picture"
        assert second_config.max_results_per_provider == 25
        assert second_config.thumbnail_cache_max_mb == 128

    def test_provider_list_change_observed_on_second_open(self) -> None:
        """Changing the providers list in config between opens is
        observed on the second open without restart (Req 1.7)."""
        note = _make_fake_note({"word": "hello", "image": ""})
        editor = _make_fake_editor(note)

        config_v1 = {
            "source_field": "word",
            "target_field": "image",
            "providers": ["unsplash"],
            "max_results_per_provider": 12,
            "thumbnail_cache_max_mb": 64,
        }

        config_v2 = {
            "source_field": "word",
            "target_field": "image",
            "providers": ["unsplash", "pixabay"],
            "max_results_per_provider": 12,
            "thumbnail_cache_max_mb": 64,
        }

        observed_configs: List[Config] = []

        def _capture_config(**kwargs: Any) -> None:
            config = kwargs.get("config")
            if config is not None:
                observed_configs.append(config)
            return None

        current_config = [config_v1]

        mock_mw = MagicMock()
        mock_mw.addonManager.getConfig.side_effect = lambda _: current_config[0]
        mock_mw.addonManager.addonsFolder.return_value = "/tmp/fake_addons"

        mock_aqt = MagicMock()
        mock_aqt.mw = mock_mw

        mock_aqt_utils = MagicMock()

        # Create providers for both IDs
        def _create_provider(provider_id: str) -> _NoOpProvider:
            return _NoOpProvider(provider_id)

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
                side_effect=_capture_config,
            ),
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                side_effect=_create_provider,
            ),
        ):
            # First open: providers = ["unsplash"]
            _on_button_click(editor)

            # Mutate config: add pixabay provider
            current_config[0] = config_v2

            # Second open: providers = ["unsplash", "pixabay"]
            _on_button_click(editor)

        assert len(observed_configs) == 2

        # First open: only unsplash
        assert observed_configs[0].providers == ("unsplash",)

        # Second open: unsplash + pixabay (Req 1.7)
        assert observed_configs[1].providers == ("unsplash", "pixabay")

    def test_getconfig_called_on_every_open(self) -> None:
        """Verify that mw.addonManager.getConfig is called on every
        picker open, not cached from a previous call (Req 1.7)."""
        note = _make_fake_note({"word": "test", "image": ""})
        editor = _make_fake_editor(note)

        config_dict = {
            "source_field": "word",
            "target_field": "image",
            "providers": ["unsplash"],
            "max_results_per_provider": 12,
            "thumbnail_cache_max_mb": 64,
        }

        mock_mw = MagicMock()
        mock_mw.addonManager.getConfig.return_value = config_dict
        mock_mw.addonManager.addonsFolder.return_value = "/tmp/fake_addons"

        mock_aqt = MagicMock()
        mock_aqt.mw = mock_mw

        mock_aqt_utils = MagicMock()

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
            ),
            patch(
                "ankivn_image_picker.providers.ProviderRegistry.create",
                return_value=_NoOpProvider("unsplash"),
            ),
        ):
            # Open picker three times
            _on_button_click(editor)
            _on_button_click(editor)
            _on_button_click(editor)

        # Assert: getConfig was called exactly 3 times (once per open)
        assert mock_mw.addonManager.getConfig.call_count == 3
