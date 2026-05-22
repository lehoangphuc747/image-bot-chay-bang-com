"""AnkiVN Smart Image Picker — Anki entry point.

This module is imported by Anki at startup. It:

1. Registers :func:`_on_editor_init_buttons` on
   ``aqt.gui_hooks.editor_did_init_buttons`` so every Editor instance
   gets a toolbar button (Req 2.1).
2. Registers an entry under the main "Tools" menu via
   ``aqt.gui_hooks.main_window_did_init`` so the add-on is discoverable
   even when no editor is open.
3. Registers a "Notes" menu entry on the card Browser via
   ``aqt.gui_hooks.browser_will_show`` so the user can run a batch
   image-pick across selected notes.
4. Registers :func:`_show_config_help` via
   ``mw.addonManager.setConfigAction`` so the standard "Config" button
   in the add-ons window opens Anki's JSON editor (design lifecycle §1).
5. Imports concrete provider modules so they self-register with
   :class:`~ankivn_image_picker.providers.ProviderRegistry`.

All top-level entry points are wrapped in ``try/except Exception`` that
logs via :func:`logging.exception` and shows a critical dialog
summarising the failure (Req 10.3).

Validates Requirements: 1.1, 1.7, 2.1, 10.3.
"""

from __future__ import annotations

import logging as _logging

from .logging import get_logger

_log = get_logger("init")


# ---------------------------------------------------------------------------
# Provider registration: import concrete providers so they call
# ProviderRegistry.register() at import time.
# ---------------------------------------------------------------------------

def _register_providers() -> None:
    """Import provider modules so they self-register."""
    try:
        from .providers import unsplash as _unsplash  # noqa: F401
    except Exception as exc:
        _log.warning("Failed to import unsplash provider: %s", exc)

    try:
        from .providers import pexels as _pexels  # noqa: F401
    except Exception as exc:
        _log.warning("Failed to import pexels provider: %s", exc)

    try:
        from .providers import wikimedia as _wiki  # noqa: F401
    except Exception as exc:
        _log.warning("Failed to import wikimedia provider: %s", exc)

    try:
        from .providers import openverse as _openverse  # noqa: F401
    except Exception as exc:
        _log.warning("Failed to import openverse provider: %s", exc)


_register_providers()


# ---------------------------------------------------------------------------
# Hook callbacks
# ---------------------------------------------------------------------------


def _on_editor_init_buttons(buttons: list, editor: object) -> None:
    """Hook callback: delegate to toolbar_button module.

    Wraps the call in try/except so a failure in button injection
    never crashes the Editor (Req 10.3).
    """
    try:
        from .ui.toolbar_button import editor_did_init_buttons

        editor_did_init_buttons(buttons, editor)
    except Exception as exc:
        _log.exception(
            "Failed to inject toolbar button: %s", exc
        )
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(
                f"Image Picker failed to add toolbar button:\n{exc}"
            )
        except Exception:
            pass


def _open_picker(editor: object) -> None:
    """Open the image picker for the given editor.

    Loads config fresh (Req 1.7), constructs all dependencies, and
    shows the :class:`~ankivn_image_picker.ui.picker_dialog.PickerDialog`
    modally. Wrapped in try/except for Req 10.3.
    """
    try:
        from pathlib import Path

        from aqt import mw  # type: ignore[import-not-found]
        from aqt.utils import showCritical, showWarning  # type: ignore[import-not-found]

        from .cache import ThumbnailCache
        from .config import ConfigLoader
        from .http import HttpClient
        from .providers import ProviderRegistry

        # Req 2.4: guard against no active editor/note
        if editor is None:
            return
        if not hasattr(editor, "note") or editor.note is None:  # type: ignore[union-attr]
            return

        # Load config fresh on each open (Req 1.7)
        addon_name = mw.addonManager.addonFromModule(__name__)
        raw_config = mw.addonManager.getConfig(addon_name)
        config = ConfigLoader.load(raw_config, log=_log)

        # Inject API keys from config into environment variables
        import os

        if config.unsplash_access_key:
            os.environ["UNSPLASH_ACCESS_KEY"] = config.unsplash_access_key
        if config.pexels_api_key:
            os.environ["PEXELS_API_KEY"] = config.pexels_api_key

        # Build provider list from config
        providers = []
        for provider_id in config.providers:
            try:
                providers.append(ProviderRegistry.create(provider_id))
            except KeyError:
                _log.warning("Skipping unknown provider %r", provider_id)

        if not providers:
            showWarning(
                "No valid image providers configured. "
                "Please check your add-on configuration."
            )
            return

        # Construct HTTP client
        http = HttpClient()

        # Construct thumbnail cache (Req 5.3)
        addon_folder = mw.addonManager.addonsFolder(addon_name)
        cache_root = Path(addon_folder) / "user_files" / "thumbnail_cache"
        max_cache_bytes = config.thumbnail_cache_max_mb * 1024 * 1024
        cache = ThumbnailCache(cache_root, max_cache_bytes)

        # Search-result metadata cache (sibling to thumbnail cache).
        # Lets a same-query re-open skip the provider API entirely.
        from .search_cache import SearchCache

        search_cache = SearchCache(
            Path(addon_folder) / "user_files" / "search_cache"
        )
        try:
            search_cache.prune_expired()
        except Exception:
            pass

        # Open the picker dialog (validation happens inside)
        from .ui.picker_dialog import PickerDialog

        dialog = PickerDialog.validate_and_open(
            editor=editor,
            config=config,
            providers=providers,
            http=http,
            cache=cache,
            parent=getattr(editor, "parentWindow", None),
            search_cache=search_cache,
        )

        if dialog is not None:
            dialog.exec()

    except Exception as exc:
        _log.exception("Failed to open image picker: %s", exc)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(
                f"Image Picker encountered an error:\n{exc}"
            )
        except Exception:
            pass


def _show_config_help() -> None:
    """Config action callback: open Anki's built-in JSON config editor.

    Registered via ``mw.addonManager.setConfigAction`` so the standard
    "Config" button in the add-ons window works. The add-on does not
    ship a custom config GUI; it relies on Anki's JSON editor plus the
    ``config.md`` documentation file.
    """
    try:
        from aqt import mw  # type: ignore[import-not-found]

        addon_name = mw.addonManager.addonFromModule(__name__)
        mw.addonManager.onConfig(addon_name)
    except Exception as exc:
        _log.exception("Failed to open config editor: %s", exc)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(
                f"Image Picker failed to open config:\n{exc}"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Anki hook registration — runs at import time (add-on load).
# ---------------------------------------------------------------------------


def _setup_hooks() -> None:
    """Register all hooks with Anki. Called once at module load."""
    try:
        from aqt import gui_hooks, mw  # type: ignore[import-not-found]

        # Req 2.1: add toolbar button to every Editor instance
        gui_hooks.editor_did_init_buttons.append(_on_editor_init_buttons)

        # Tools menu: install once the main window is fully initialised.
        # ``main_window_did_init`` fires after profile load, so
        # ``mw.form.menuTools`` exists by the time the callback runs.
        from .ui.tools_menu import install_tools_menu

        gui_hooks.main_window_did_init.append(install_tools_menu)

        # Browser menu: install per-Browser via ``browser_will_show``.
        from .ui.browser_menu import install_browser_hook

        install_browser_hook()

        # Config action: standard "Config" button opens Anki's JSON editor
        addon_name = mw.addonManager.addonFromModule(__name__)
        mw.addonManager.setConfigAction(addon_name, _show_config_help)

        _log.info("Image Picker hooks registered successfully.")

    except ImportError:
        # Running outside Anki (e.g. in the test suite). Hooks cannot
        # be registered but the package is still importable for testing.
        _log.debug(
            "aqt not available; skipping hook registration "
            "(expected in test environments)."
        )
    except Exception as exc:
        _log.exception("Failed to register hooks: %s", exc)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(
                f"Image Picker failed to initialise:\n{exc}"
            )
        except Exception:
            pass


_setup_hooks()
