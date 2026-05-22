"""Settings dialog for the AnkiVN Smart Image Picker add-on.

Adds an entry under the shared "AnkiVN" menu that opens a minimal
two-tab settings dialog: **Setup** (the things every user touches —
API keys + field mappings + translate toggle) and **More** (provider
on/off + cache + admin actions). Deeper knobs live in the raw JSON
config for power users.
"""

from __future__ import annotations

from typing import Any, Dict

from ..logging import get_logger

_log = get_logger("tools_menu")

#: Label displayed under the AnkiVN parent menu.
_MENU_LABEL = "⚡ Image Picker Settings"


# --- Reusable widgets ------------------------------------------------------


def _make_api_key_row(parent: Any, value: str, placeholder: str) -> tuple:
    """Return (row_widget, line_edit) for a password-style API key field.

    The line edit defaults to ``EchoMode.Password`` so the key is
    masked. A small "show/hide" toggle next to it lets the user
    reveal the value when copy-pasting.
    """
    from aqt.qt import (  # type: ignore[import-not-found]
        QHBoxLayout,
        QLineEdit,
        QPushButton,
        QWidget,
    )

    row = QWidget(parent)
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)

    edit = QLineEdit(row)
    edit.setText(value or "")
    edit.setPlaceholderText(placeholder)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    h.addWidget(edit)

    toggle = QPushButton("👁", row)
    toggle.setCheckable(True)
    toggle.setFixedWidth(32)
    toggle.setToolTip("Show / hide the key")

    def _toggle_echo(checked: bool) -> None:
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    toggle.toggled.connect(_toggle_echo)
    h.addWidget(toggle)

    return row, edit


def _make_link_label(parent: Any, text: str, url: str) -> Any:
    """Tiny clickable link for "Get key →" hints next to API key rows."""
    from aqt.qt import QLabel  # type: ignore[import-not-found]

    lbl = QLabel(parent)
    lbl.setText(f'<a href="{url}">{text}</a>')
    lbl.setOpenExternalLinks(True)
    lbl.setStyleSheet("color: #4a8; font-size: 11px;")
    return lbl


# --- Tab builders ----------------------------------------------------------


def _build_setup_tab(parent: Any, raw_config: dict) -> tuple:
    """Tab 1: API keys + field mappings + translate."""
    from aqt.qt import (  # type: ignore[import-not-found]
        QCheckBox,
        QComboBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    page = QWidget(parent)
    v = QVBoxLayout(page)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(16)

    # --- API keys ---
    keys_section = QLabel("<b>API keys</b>")
    v.addWidget(keys_section)

    keys_form = QFormLayout()
    keys_form.setContentsMargins(0, 0, 0, 0)
    keys_form.setHorizontalSpacing(12)

    unsplash_row, unsplash_edit = _make_api_key_row(
        page,
        raw_config.get("unsplash_access_key", ""),
        "Unsplash access key",
    )
    keys_form.addRow("Unsplash", unsplash_row)
    keys_form.addRow(
        "",
        _make_link_label(page, "Get a free key →", "https://unsplash.com/developers"),
    )

    pexels_row, pexels_edit = _make_api_key_row(
        page,
        raw_config.get("pexels_api_key", ""),
        "Pexels API key",
    )
    keys_form.addRow("Pexels", pexels_row)
    keys_form.addRow(
        "",
        _make_link_label(page, "Get a free key →", "https://www.pexels.com/api/"),
    )

    v.addLayout(keys_form)

    # --- Field mappings ---
    mappings_section = QLabel("<b>Field mappings</b>")
    v.addWidget(mappings_section)

    default_row = QHBoxLayout()
    default_row.setContentsMargins(0, 0, 0, 0)
    default_row.addWidget(QLabel("Default:"))

    src_default = QLineEdit(raw_config.get("source_field", "word"))
    src_default.setPlaceholderText("source")
    src_default.setMaximumWidth(140)
    default_row.addWidget(src_default)

    default_row.addWidget(QLabel("→"))

    tgt_default = QLineEdit(raw_config.get("target_field", "image"))
    tgt_default.setPlaceholderText("target")
    tgt_default.setMaximumWidth(140)
    default_row.addWidget(tgt_default)

    default_row.addWidget(
        QLabel(
            "<span style='color:#888; font-size:11px;'>"
            "(used when a note type has no specific mapping)"
            "</span>"
        )
    )
    default_row.addStretch(1)
    v.addLayout(default_row)

    # Per-note-type list inside a scroll area
    nt_widgets: Dict[str, tuple] = {}
    note_types_info: list = []
    try:
        from aqt import mw  # type: ignore[import-not-found]

        for nt in mw.col.models.all():
            name = nt.get("name", "")
            flds = [f["name"] for f in nt.get("flds", [])]
            if name and flds:
                note_types_info.append((name, flds))
        note_types_info.sort(key=lambda p: p[0].lower())
    except Exception:
        note_types_info = []

    existing: dict = {}
    for entry in raw_config.get("field_mappings", []) or []:
        if isinstance(entry, dict):
            nt_name = entry.get("note_type")
            src = entry.get("source") or entry.get("source_field")
            tgt = entry.get("target") or entry.get("target_field")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
            nt_name, src, tgt = entry[0], entry[1], entry[2]
        else:
            continue
        if isinstance(nt_name, str) and nt_name:
            existing[nt_name] = (src or "", tgt or "")

    if note_types_info:
        list_container = QWidget(page)
        list_v = QVBoxLayout(list_container)
        list_v.setContentsMargins(0, 0, 0, 0)
        list_v.setSpacing(4)

        for nt_name, fields_list in note_types_info:
            row = QWidget(list_container)
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)

            name_lbl = QLabel(nt_name, row)
            name_lbl.setMinimumWidth(160)
            name_lbl.setStyleSheet("color: #555;")
            h.addWidget(name_lbl)

            src_combo = QComboBox(row)
            src_combo.addItem("default", "")
            for fn in fields_list:
                src_combo.addItem(fn, fn)
            src_combo.setMinimumWidth(120)
            h.addWidget(src_combo)

            h.addWidget(QLabel("→", row))

            tgt_combo = QComboBox(row)
            tgt_combo.addItem("default", "")
            for fn in fields_list:
                tgt_combo.addItem(fn, fn)
            tgt_combo.setMinimumWidth(120)
            h.addWidget(tgt_combo)

            h.addStretch(1)

            if nt_name in existing:
                saved_src, saved_tgt = existing[nt_name]
                for i in range(src_combo.count()):
                    if src_combo.itemData(i) == saved_src:
                        src_combo.setCurrentIndex(i)
                        break
                for i in range(tgt_combo.count()):
                    if tgt_combo.itemData(i) == saved_tgt:
                        tgt_combo.setCurrentIndex(i)
                        break

            nt_widgets[nt_name] = (src_combo, tgt_combo)
            list_v.addWidget(row)

        list_v.addStretch(1)

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_container)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(180)
        v.addWidget(scroll, 1)
    else:
        v.addWidget(
            QLabel(
                "<i style='color:#888;'>"
                "No note types yet — mappings appear once you have notes."
                "</i>"
            )
        )

    # --- Translate toggle ---
    translate = QCheckBox("Auto-translate non-English queries to English")
    translate.setChecked(bool(raw_config.get("translate_to_english", True)))
    v.addWidget(translate)

    def _mappings_getter() -> list:
        out: list = []
        for nt_name, (src_c, tgt_c) in nt_widgets.items():
            src = src_c.currentData() or ""
            tgt = tgt_c.currentData() or ""
            if not src or not tgt or src == tgt:
                continue
            out.append({"note_type": nt_name, "source": src, "target": tgt})
        return out

    return page, {
        "unsplash_access_key": lambda: unsplash_edit.text().strip(),
        "pexels_api_key": lambda: pexels_edit.text().strip(),
        "source_field": lambda: src_default.text().strip() or "word",
        "target_field": lambda: tgt_default.text().strip() or "image",
        "field_mappings": _mappings_getter,
        "translate_to_english": lambda: translate.isChecked(),
    }


def _build_more_tab(parent: Any, raw_config: dict) -> tuple:
    """Tab 2: provider on/off, cache size, admin actions."""
    from aqt.qt import (  # type: ignore[import-not-found]
        QCheckBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )

    from ..provider_info import PROVIDER_INFO

    page = QWidget(parent)
    v = QVBoxLayout(page)
    v.setContentsMargins(12, 12, 12, 12)
    v.setSpacing(16)

    # --- Providers ---
    v.addWidget(QLabel("<b>Providers</b>"))

    enabled = list(raw_config.get("providers") or [])
    enable_checks: Dict[str, Any] = {}

    prov_row = QHBoxLayout()
    for pid in ("unsplash", "pexels", "wikimedia", "openverse"):
        info = PROVIDER_INFO[pid]
        cb = QCheckBox(info.display_name, page)
        cb.setChecked(pid in enabled if enabled else True)
        cb.setToolTip(info.free_tier_note)
        enable_checks[pid] = cb
        prov_row.addWidget(cb)
    prov_row.addStretch(1)
    v.addLayout(prov_row)

    # --- Cache size ---
    v.addWidget(QLabel("<b>Thumbnail cache</b>"))

    cache_row = QHBoxLayout()
    cache_row.addWidget(QLabel("Size:"))
    cache_size = QSpinBox()
    cache_size.setRange(10, 5000)
    cache_size.setValue(int(raw_config.get("thumbnail_cache_max_mb", 200)))
    cache_size.setSuffix(" MB")
    cache_size.setMaximumWidth(120)
    cache_row.addWidget(cache_size)
    cache_row.addStretch(1)
    v.addLayout(cache_row)

    v.addStretch(1)

    # --- Admin actions ---
    actions_row = QHBoxLayout()
    open_json_btn = QPushButton("Open raw config")

    def _open_raw_config() -> None:
        try:
            from aqt import mw  # type: ignore[import-not-found]
            from ._picker_factory import ADDON_PACKAGE
            mw.addonManager.onConfig(ADDON_PACKAGE)
        except Exception as exc:
            _log.warning("Could not open raw config: %s", exc)

    open_json_btn.clicked.connect(_open_raw_config)
    actions_row.addWidget(open_json_btn)

    reset_btn = QPushButton("Reset to defaults…")
    actions_row.addWidget(reset_btn)
    actions_row.addStretch(1)
    v.addLayout(actions_row)

    def _enabled_providers_getter() -> list:
        return [pid for pid, c in enable_checks.items() if c.isChecked()]

    return page, {
        "providers": _enabled_providers_getter,
        "thumbnail_cache_max_mb": lambda: cache_size.value(),
    }, reset_btn


# --- Main dialog -----------------------------------------------------------


def _on_tools_menu_clicked() -> None:
    """Open the settings dialog."""
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.qt import (  # type: ignore[import-not-found]
            QDialog,
            QDialogButtonBox,
            QMessageBox,
            QTabWidget,
            QVBoxLayout,
        )
    except ImportError:
        _log.error("Cannot show settings: aqt not available")
        return

    try:
        from ._picker_factory import ADDON_PACKAGE

        raw_config = mw.addonManager.getConfig(ADDON_PACKAGE) or {}

        dialog = QDialog(mw)
        dialog.setWindowTitle("⚡ Image Picker — Settings")
        dialog.setMinimumWidth(560)
        dialog.setMinimumHeight(520)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(8)

        tabs = QTabWidget(dialog)
        outer.addWidget(tabs, 1)

        setup_page, setup_getters = _build_setup_tab(dialog, raw_config)
        more_page, more_getters, reset_btn = _build_more_tab(dialog, raw_config)

        tabs.addTab(setup_page, "Setup")
        tabs.addTab(more_page, "More")

        # Reset-to-defaults handler
        def _on_reset() -> None:
            confirm = QMessageBox.question(
                dialog,
                "Reset to defaults",
                "Restore every setting to its built-in default?\n"
                "(API keys are preserved.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                from dataclasses import asdict

                from ..config import ConfigLoader

                preserved = {
                    "unsplash_access_key": raw_config.get(
                        "unsplash_access_key", ""
                    ),
                    "pexels_api_key": raw_config.get("pexels_api_key", ""),
                }
                new_raw = asdict(ConfigLoader.DEFAULTS)
                new_raw.update(preserved)
                mw.addonManager.writeConfig(ADDON_PACKAGE, new_raw)
                dialog.accept()
                _on_tools_menu_clicked()
            except Exception as exc:
                _log.exception("Reset failed: %s", exc)
                from aqt.utils import showCritical  # type: ignore[import-not-found]
                showCritical(f"Reset failed: {exc}", parent=dialog)

        reset_btn.clicked.connect(_on_reset)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        merged = dict(raw_config)
        for getters in (setup_getters, more_getters):
            for k, fn in getters.items():
                try:
                    merged[k] = fn()
                except Exception as exc:
                    _log.warning("Could not read %s: %s", k, exc)

        # Empty source/target field falls back to a safe default.
        for key in ("source_field", "target_field"):
            v = merged.get(key, "")
            if not isinstance(v, str) or not v.strip():
                merged[key] = "word" if key == "source_field" else "image"

        mw.addonManager.writeConfig(ADDON_PACKAGE, merged)

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
            menu = mw.form.menuTools

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
