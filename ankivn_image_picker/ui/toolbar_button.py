"""Toolbar button injection for the AnkiVN Smart Image Picker add-on.

This module provides the :func:`editor_did_init_buttons` callback that
is appended to ``aqt.gui_hooks.editor_did_init_buttons`` by the add-on
entry point (task 10.1). When Anki creates an Editor instance (in either
the Add Card dialog or the Browser's edit pane), the hook fires and this
callback adds a toolbar button whose click handler validates that an
editor is active and opens the :class:`~ankivn_image_picker.ui.picker_dialog.PickerDialog`.

Threading
---------
This module runs exclusively on the Qt main thread. The button handler
constructs the picker dialog synchronously; all background work is
delegated to the orchestrator inside the dialog.

Validates Requirements: 2.1, 2.2, 2.3, 2.4.
"""

from __future__ import annotations

import os
from typing import Any, List

from ..logging import get_logger

_log = get_logger("toolbar_button")

# Icon path: look for an icon file shipped with the add-on. If not
# found, fall back to an empty string (Anki will use a default icon).
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON_PATH = os.path.join(_ADDON_DIR, "icon.png")
if not os.path.isfile(_ICON_PATH):
    _ICON_PATH = ""

# Tooltip shown on hover (Req 2.2)
_TOOLTIP = "Image Picker"

# Command identifier for the button
_CMD = "ankivn_image_picker"


def _on_button_click(editor: Any) -> None:
    """Handle toolbar button click: validate editor and open picker.

    This function is the button's action handler. It:

    1. Validates that the editor has an active note (Req 2.4: while no
       editor instance is active, the picker SHALL NOT open).
    2. Loads and validates the add-on config fresh (Req 1.7).
    3. Constructs the provider list, HTTP client, and thumbnail cache.
    4. Delegates to :meth:`PickerDialog.validate_and_open` which
       performs source-field validation and opens the dialog.

    If any step fails, an appropriate error is shown and the picker
    does not open.
    """
    # Req 2.4: guard against no active editor/note
    if editor is None:
        return
    if not hasattr(editor, "note") or editor.note is None:
        return

    try:
        # Import Anki APIs lazily to avoid import errors in test
        # environments that don't have aqt available.
        try:
            from aqt import mw  # type: ignore[import-not-found]
            from aqt.utils import showCritical  # type: ignore[import-not-found]
        except ImportError:
            _log.error("Cannot open picker: aqt not available")
            return

        # Load config fresh on each open (Req 1.7)
        from ..config import ConfigLoader

        raw_config = mw.addonManager.getConfig(__name__.split(".")[0])
        config = ConfigLoader.load(raw_config, log=_log)

        # Inject API keys from config into environment variables so
        # providers can read them without requiring the user to set
        # env vars manually.
        import os

        if config.unsplash_access_key:
            os.environ["UNSPLASH_ACCESS_KEY"] = config.unsplash_access_key
        if config.pexels_api_key:
            os.environ["PEXELS_API_KEY"] = config.pexels_api_key

        # Build provider list from config
        from ..providers import ProviderRegistry

        providers = []
        for provider_id in config.providers:
            try:
                providers.append(ProviderRegistry.create(provider_id))
            except KeyError:
                _log.warning(
                    "Skipping unknown provider %r", provider_id
                )

        if not providers:
            from aqt.utils import showWarning  # type: ignore[import-not-found]

            showWarning(
                "No valid image providers configured. "
                "Please check your add-on configuration."
            )
            return

        # Construct HTTP client
        from ..http import HttpClient

        http = HttpClient()

        # Construct thumbnail cache
        from pathlib import Path

        from ..cache import ThumbnailCache

        cache_root = (
            Path(mw.addonManager.addonsFolder(__name__.split(".")[0]))
            / "user_files"
            / "thumbnail_cache"
        )
        max_cache_bytes = config.thumbnail_cache_max_mb * 1024 * 1024
        cache = ThumbnailCache(cache_root, max_cache_bytes)

        # Open the picker dialog (validation happens inside)
        from .picker_dialog import PickerDialog

        dialog = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
            parent=editor.parentWindow if hasattr(editor, "parentWindow") else None,
        )

        if dialog is not None:
            dialog.exec()

    except Exception as exc:
        # Req 10.3: unhandled exceptions are logged and surfaced
        _log.exception("Failed to open image picker: %s", exc)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(
                f"Image Picker encountered an error:\n{exc}"
            )
        except Exception:
            pass


def editor_did_init_buttons(buttons: List[str], editor: Any) -> None:
    """Hook callback: add the image picker button to the editor toolbar.

    This function matches the signature expected by
    ``aqt.gui_hooks.editor_did_init_buttons``. It is called once per
    Editor instance creation (Req 2.1).

    Parameters
    ----------
    buttons:
        The mutable list of button HTML strings that Anki is building
        for the editor toolbar. The callback appends to this list.
    editor:
        The Editor instance being initialised.
    """
    # Use editor.addButton to create the button with proper icon,
    # command, handler, and tooltip (Req 2.1, 2.2, 2.3)
    button = editor.addButton(
        icon=_ICON_PATH if _ICON_PATH else None,
        cmd=_CMD,
        func=_on_button_click,
        tip=_TOOLTIP,
    )
    buttons.append(button)


__all__ = ["editor_did_init_buttons"]
