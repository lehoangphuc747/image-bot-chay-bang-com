"""Tools-menu entry for the AnkiVN Smart Image Picker add-on.

Adds an entry under Anki's main "Tools" menu that opens a settings
dialog where the user can configure API keys and other options
directly from the GUI — no need to edit JSON manually.
"""

from __future__ import annotations

from typing import Any

from ..logging import get_logger

_log = get_logger("tools_menu")

#: Label displayed under the AnkiVN parent menu.
_MENU_LABEL = "⚡ Image Picker Settings"


def _on_tools_menu_clicked() -> None:
    """Open the settings dialog."""
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.qt import (  # type: ignore[import-not-found]
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLabel,
            QLineEdit,
            QSpinBox,
            QVBoxLayout,
        )
    except ImportError:
        _log.error("Cannot show settings: aqt not available")
        return

    try:
        from ._picker_factory import ADDON_PACKAGE

        # Load current config
        raw_config = mw.addonManager.getConfig(ADDON_PACKAGE) or {}

        # --- Build the dialog ---
        dialog = QDialog(mw)
        dialog.setWindowTitle("Image Picker — Settings")
        dialog.setMinimumWidth(500)

        layout = QVBoxLayout(dialog)

        # Header
        header = QLabel(
            "<h3>Image Picker Settings</h3>"
            "<p>Configure your API keys and field mappings below. "
            "Changes take effect the next time you open the picker.</p>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Form
        form = QFormLayout()

        # Source field
        source_field_input = QLineEdit()
        source_field_input.setText(raw_config.get("source_field", "word"))
        source_field_input.setToolTip(
            "Name of the note field used as the search query"
        )
        form.addRow("Source field:", source_field_input)

        # Target field
        target_field_input = QLineEdit()
        target_field_input.setText(raw_config.get("target_field", "image"))
        target_field_input.setToolTip(
            "Name of the note field where the image will be inserted"
        )
        form.addRow("Target field:", target_field_input)

        # Max results
        max_results_input = QSpinBox()
        max_results_input.setRange(1, 200)
        max_results_input.setValue(raw_config.get("max_results_per_provider", 30))
        max_results_input.setToolTip(
            "Fallback limit when a provider doesn't have a specific\n"
            "limit set (per-provider limits below override this).\n"
            "Each provider clamps to its own API hard cap."
        )
        form.addRow("Max results (fallback):", max_results_input)

        # Per-provider limits section
        from ..provider_info import PROVIDER_INFO

        per_provider_inputs: dict[str, Any] = {}

        # Total label - updates when any spinbox changes
        total_label = QLabel()

        def _update_total() -> None:
            total = 0
            fallback = max_results_input.value()
            for p, s in per_provider_inputs.items():
                v = s.value()
                if v == 0:
                    info = PROVIDER_INFO[p]
                    v = min(fallback, info.max_per_request)
                total += v
            total_label.setText(
                f"<b>Total per request: ~{total} images</b> "
                f"(across all enabled providers)"
            )

        for pid in ["unsplash", "pexels", "wikimedia", "openverse"]:
            info = PROVIDER_INFO[pid]
            spin = QSpinBox()
            spin.setRange(0, info.max_per_request)
            spin.setSpecialValueText("(use fallback)")  # When 0 is shown
            spin.setValue(raw_config.get(f"{pid}_max_results", info.default_limit))
            spin.setToolTip(
                f"{info.display_name} API max: {info.max_per_request} per request.\n"
                f"{info.free_tier_note}\n"
                f"Set to 0 to use the fallback limit above."
            )
            spin.valueChanged.connect(_update_total)
            per_provider_inputs[pid] = spin
            form.addRow(
                f"  • {info.display_name} (max {info.max_per_request}):",
                spin,
            )

        # Hook fallback spinbox changes to total update too
        max_results_input.valueChanged.connect(_update_total)
        _update_total()  # Initial calculation
        form.addRow("", total_label)

        # Prefetch notes ahead (batch mode performance tweak)
        prefetch_input = QSpinBox()
        prefetch_input.setRange(0, 20)
        prefetch_input.setValue(raw_config.get("prefetch_notes_ahead", 8))
        prefetch_input.setSpecialValueText("Disabled")  # Shown when 0
        prefetch_input.setToolTip(
            "Number of upcoming notes to search ahead in batch mode (0-20).\n"
            "Higher = less waiting between notes, more API requests upfront.\n"
            "Set to 0 to disable prefetching.\n"
            "Recommended: 5"
        )
        form.addRow("Prefetch notes ahead (batch):", prefetch_input)

        # Unsplash key
        unsplash_input = QLineEdit()
        unsplash_input.setText(raw_config.get("unsplash_access_key", ""))
        unsplash_input.setPlaceholderText("Paste your Unsplash Access Key here")
        unsplash_input.setToolTip(
            "Get a free key at https://unsplash.com/developers"
        )
        form.addRow("Unsplash Access Key:", unsplash_input)

        # Pexels key
        pexels_input = QLineEdit()
        pexels_input.setText(raw_config.get("pexels_api_key", ""))
        pexels_input.setPlaceholderText("Paste your Pexels API Key here")
        pexels_input.setToolTip(
            "Get a free key at https://www.pexels.com/api/"
        )
        form.addRow("Pexels API Key:", pexels_input)

        layout.addLayout(form)

        # Help text
        help_label = QLabel(
            "<hr><b>How to use:</b><br>"
            "• <b>Single note:</b> Open editor → click the image-picker "
            "toolbar button.<br>"
            "• <b>Batch:</b> Browser → select notes → "
            "<i>Notes → Image Picker (Selected Notes)</i>"
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        # --- Show and save ---
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Update config
            raw_config["source_field"] = source_field_input.text().strip() or "word"
            raw_config["target_field"] = target_field_input.text().strip() or "image"
            raw_config["max_results_per_provider"] = max_results_input.value()
            raw_config["unsplash_access_key"] = unsplash_input.text().strip()
            raw_config["pexels_api_key"] = pexels_input.text().strip()

            # Per-provider limits
            for pid, spin in per_provider_inputs.items():
                raw_config[f"{pid}_max_results"] = spin.value()

            # Prefetch
            raw_config["prefetch_notes_ahead"] = prefetch_input.value()

            # Save
            mw.addonManager.writeConfig(ADDON_PACKAGE, raw_config)

            from aqt.utils import tooltip  # type: ignore[import-not-found]

            tooltip("Settings saved. Changes apply on next picker open.")

    except Exception as exc:
        _log.exception("Failed to show settings dialog: %s", exc)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(f"Image Picker settings error:\n{exc}")
        except Exception:
            pass


def install_tools_menu() -> None:
    """Add the add-on entry to the shared "AnkiVN" menu.

    Falls back to the standard "Tools" menu when the AnkiVN parent
    menu can't be created (e.g. running headless during tests).

    Idempotent: re-running this function will not produce duplicate
    menu entries because we tag the QAction with a sentinel
    ``objectName`` and skip insertion if a tagged action is already
    present.
    """
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.qt import QAction  # type: ignore[import-not-found]
    except ImportError:
        _log.debug(
            "aqt not available; skipping menu install "
            "(expected in test environments)."
        )
        return

    try:
        from .ankivn_menu import get_or_create_ankivn_menu

        menu = get_or_create_ankivn_menu()
        if menu is None:
            # Fall back to Tools menu so the entry is still reachable.
            menu = mw.form.menuTools

        # Idempotency guard
        sentinel = "ankivn_image_picker_tools_action"
        for existing in menu.actions():
            if existing.objectName() == sentinel:
                return

        action = QAction(_MENU_LABEL, mw)
        action.setObjectName(sentinel)
        action.triggered.connect(_on_tools_menu_clicked)
        menu.addAction(action)

        _log.info(
            "Menu entry installed under %r.", menu.title() if menu else "?"
        )
    except Exception as exc:
        _log.exception("Failed to install menu entry: %s", exc)


__all__ = ["install_tools_menu"]
