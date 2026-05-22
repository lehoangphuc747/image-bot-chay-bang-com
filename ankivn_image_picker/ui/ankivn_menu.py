"""Shared "AnkiVN" parent menu used by all add-ons in the AnkiVN family.

Multiple AnkiVN add-ons (Image Picker, JSON Bulk Importer, SuperFreeTTS,
…) all want to live under a single "AnkiVN" menu on the Anki menu bar
rather than scattering entries across the "Tools" menu. This module
exposes :func:`get_or_create_ankivn_menu` which:

* scans the existing menu bar for any menu whose ``objectName`` is
  the agreed sentinel ``sf_ankivn_menu`` or whose visible title is
  exactly ``AnkiVN`` (so older add-ons that didn't tag the object
  still get reused), and
* creates a new ``QMenu`` titled ``AnkiVN`` if no such menu is
  present, inserting it just before the "Help" menu so the menu bar
  ordering matches Anki's own placement of third-party menus.

Calling this function is idempotent: every add-on that imports it
will end up with the same single ``QMenu`` instance.
"""

from __future__ import annotations

from typing import Any, Optional

from ..logging import get_logger

_log = get_logger("ankivn_menu")

#: Object name shared with other AnkiVN add-ons so we can find the
#: existing parent menu instead of creating a duplicate.
ANKIVN_MENU_OBJECT_NAME = "sf_ankivn_menu"
#: Visible label used both for new menus we create and for matching
#: menus that older add-ons may have left untagged.
ANKIVN_MENU_TITLE = "AnkiVN"


def get_or_create_ankivn_menu() -> Optional[Any]:
    """Return the shared "AnkiVN" parent menu, creating it if needed.

    Returns ``None`` when ``aqt`` is unavailable (e.g. in the test
    suite) so callers can fall back to ``mw.form.menuTools`` without
    crashing.
    """
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.qt import QMenu  # type: ignore[import-not-found]
    except ImportError:
        _log.debug(
            "aqt not available; cannot resolve AnkiVN parent menu "
            "(expected in test environments)."
        )
        return None

    try:
        menubar = mw.form.menubar
    except Exception as exc:
        _log.warning("Could not access menu bar: %s", exc)
        return None

    # Walk existing menus first so co-installed AnkiVN add-ons share
    # one parent menu.
    try:
        for action in menubar.actions():
            existing = action.menu()
            if existing is None:
                continue
            if existing.objectName() == ANKIVN_MENU_OBJECT_NAME:
                return existing
            try:
                if existing.title() == ANKIVN_MENU_TITLE:
                    # Tag the existing menu so future calls match by
                    # objectName (which is more robust than title
                    # comparisons after locale changes).
                    if not existing.objectName():
                        existing.setObjectName(ANKIVN_MENU_OBJECT_NAME)
                    return existing
            except Exception:
                # ``title()`` can fail on some Qt builds when the menu
                # is mid-construction; ignore and keep scanning.
                continue
    except Exception as exc:
        _log.warning("Failed scanning menu bar: %s", exc)

    # No existing menu found — create one and insert before "Help".
    try:
        ankivn_menu = QMenu(ANKIVN_MENU_TITLE, mw)
        ankivn_menu.setObjectName(ANKIVN_MENU_OBJECT_NAME)

        # Try to insert just before the Help menu so the bar reads
        # "… Tools  AnkiVN  Help". If we can't find Help we fall back
        # to appending at the end of the bar.
        help_action = None
        try:
            for action in menubar.actions():
                m = action.menu()
                if m is not None and m.objectName() in ("menuHelp",):
                    help_action = action
                    break
                # Some locales translate the title; fall back to
                # comparing the visible label to "Help".
                if m is not None:
                    try:
                        if m.title().replace("&", "") == "Help":
                            help_action = action
                            break
                    except Exception:
                        continue
        except Exception:
            help_action = None

        if help_action is not None:
            menubar.insertMenu(help_action, ankivn_menu)
        else:
            menubar.addMenu(ankivn_menu)

        return ankivn_menu
    except Exception as exc:
        _log.exception("Failed to create AnkiVN parent menu: %s", exc)
        return None


__all__ = [
    "ANKIVN_MENU_OBJECT_NAME",
    "ANKIVN_MENU_TITLE",
    "get_or_create_ankivn_menu",
]
