"""Settings dialog for the AnkiVN Smart Image Picker add-on.

Adds an entry under the shared "AnkiVN" menu that opens a polished
settings dialog with three tabs: General, Providers, and Advanced.
The dialog reads + writes Anki's standard JSON config so existing
config.md keys keep working.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    toggle.setFixedWidth(36)
    toggle.setToolTip("Show / hide the key")

    def _toggle_echo(checked: bool) -> None:
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    toggle.toggled.connect(_toggle_echo)
    h.addWidget(toggle)

    return row, edit


def _make_link_label(parent: Any, text: str, url: str) -> Any:
    """A tiny clickable link label for "Get free key →" style hints."""
    from aqt.qt import QLabel  # type: ignore[import-not-found]

    lbl = QLabel(parent)
    lbl.setText(f'<a href="{url}">{text}</a>')
    lbl.setOpenExternalLinks(True)
    lbl.setStyleSheet("color: #4a8; font-size: 11px;")
    return lbl


def _provider_status_text(pid: str, has_key: bool) -> str:
    """Pretty status badge for a provider row."""
    from ..provider_info import PROVIDER_INFO

    info = PROVIDER_INFO.get(pid)
    if info is None:
        return ""
    if not info.requires_api_key:
        return f'<span style="color:#4a8;">✅ Free, no key needed</span>'
    if has_key:
        return f'<span style="color:#4a8;">✅ Key set</span>'
    return f'<span style="color:#c66;">⚠ Key required, missing</span>'


# --- Tab builders ----------------------------------------------------------


def _build_general_tab(parent: Any, raw_config: dict) -> tuple:
    """Return (widget, getters_dict) for the General tab.

    ``getters_dict`` maps a config key to a zero-arg callable that
    returns the current widget value, so the save path doesn't need
    to know about widget identities.
    """
    from aqt.qt import (  # type: ignore[import-not-found]
        QCheckBox,
        QFormLayout,
        QLabel,
        QVBoxLayout,
        QWidget,
    )

    page = QWidget(parent)
    v = QVBoxLayout(page)

    intro = QLabel(
        "<b>Translation & general behaviour.</b><br>"
        "<span style='color:#888; font-size:11px;'>"
        "Field mappings live under <i>Field Mappings</i>. Prefetch is "
        "automatic — see the side panel during batch mode for live status."
        "</span>"
    )
    intro.setWordWrap(True)
    v.addWidget(intro)

    form = QFormLayout()

    # Translate-to-English
    translate = QCheckBox("Auto-translate non-English queries to English")
    translate.setChecked(bool(raw_config.get("translate_to_english", True)))
    translate.setToolTip(
        "Improves results from Unsplash and Pexels for Vietnamese,\n"
        "Korean, Japanese, etc. Uses Google Translate's free endpoint."
    )
    form.addRow("", translate)

    v.addLayout(form)
    v.addStretch(1)

    return page, {
        "translate_to_english": lambda: translate.isChecked(),
    }


def _build_field_mappings_tab(parent: Any, raw_config: dict) -> tuple:
    """Return (widget, getters_dict) for the Field Mappings tab.

    Lists every note type currently in the user's collection, lets
    them choose source/target fields per type. A "global default"
    section at the top covers note types not yet assigned a mapping.
    """
    from aqt.qt import (  # type: ignore[import-not-found]
        QComboBox,
        QFormLayout,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    page = QWidget(parent)
    v = QVBoxLayout(page)

    intro = QLabel(
        "<b>Per-note-type field mappings.</b><br>"
        "<span style='color:#888; font-size:11px;'>"
        "Each note type can use different source/target fields. The picker "
        "looks up the active note's type here first, then falls back to the "
        "global default below."
        "</span>"
    )
    intro.setWordWrap(True)
    v.addWidget(intro)

    # --- Global fallback ---
    fallback_box = QGroupBox("Global default (fallback)", page)
    fb_form = QFormLayout(fallback_box)

    src_default = QLineEdit(raw_config.get("source_field", "word"))
    src_default.setToolTip(
        "Used when a note's note type has no specific mapping below."
    )
    fb_form.addRow("Source field:", src_default)

    tgt_default = QLineEdit(raw_config.get("target_field", "image"))
    tgt_default.setToolTip(
        "Used when a note's note type has no specific mapping below."
    )
    fb_form.addRow("Target field:", tgt_default)

    v.addWidget(fallback_box)

    # --- Per-note-type rows ---
    per_type_box = QGroupBox("Per note type", page)
    per_type_layout = QVBoxLayout(per_type_box)

    # Discover note types + their fields. Outside Anki this returns
    # an empty list and the tab degrades to "no note types found".
    note_types_info: list[tuple[str, list[str]]] = []
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

    # Existing mappings keyed by note type name for fast lookup
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

    if not note_types_info:
        per_type_layout.addWidget(
            QLabel(
                "<i style='color:#888;'>"
                "No note types found in this collection. "
                "Mappings will be available once notes exist."
                "</i>",
                per_type_box,
            )
        )

    # nt_name -> (source_combo, target_combo)
    nt_widgets: dict[str, tuple] = {}

    for nt_name, fields_list in note_types_info:
        row = QWidget(per_type_box)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)

        label = QLabel(f"<b>{nt_name}</b>", row)
        label.setMinimumWidth(160)
        h.addWidget(label)

        src_combo = QComboBox(row)
        src_combo.addItem("(use global default)", "")
        for fn in fields_list:
            src_combo.addItem(fn, fn)
        h.addWidget(QLabel("→ source:", row))
        h.addWidget(src_combo)

        tgt_combo = QComboBox(row)
        tgt_combo.addItem("(use global default)", "")
        for fn in fields_list:
            tgt_combo.addItem(fn, fn)
        h.addWidget(QLabel("target:", row))
        h.addWidget(tgt_combo)
        h.addStretch(1)

        # Pre-select existing mapping if any
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
        else:
            # Auto-pick if a field with the global-default name exists
            global_src = raw_config.get("source_field", "word")
            global_tgt = raw_config.get("target_field", "image")
            for i in range(src_combo.count()):
                if src_combo.itemData(i) == global_src:
                    src_combo.setCurrentIndex(i)
                    break
            for i in range(tgt_combo.count()):
                if tgt_combo.itemData(i) == global_tgt:
                    tgt_combo.setCurrentIndex(i)
                    break

        nt_widgets[nt_name] = (src_combo, tgt_combo)
        per_type_layout.addWidget(row)

    # Wrap per-type rows in a scroll area in case the user has many
    # note types.
    scroll = QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setWidget(per_type_box)
    scroll.setMinimumHeight(220)
    v.addWidget(scroll, 1)

    def _mappings_getter() -> list:
        out: list = []
        for nt_name, (src_c, tgt_c) in nt_widgets.items():
            src = src_c.currentData() or ""
            tgt = tgt_c.currentData() or ""
            # Both must be set + both must be non-empty + they must
            # differ from each other to be a useful mapping. Fall
            # back to global default otherwise.
            if not src or not tgt or src == tgt:
                continue
            out.append({
                "note_type": nt_name,
                "source": src,
                "target": tgt,
            })
        return out

    return page, {
        "source_field": lambda: src_default.text().strip() or "word",
        "target_field": lambda: tgt_default.text().strip() or "image",
        "field_mappings": _mappings_getter,
    }


def _build_providers_tab(parent: Any, raw_config: dict) -> tuple:
    """Return (widget, getters_dict) for the Providers tab."""
    from aqt.qt import (  # type: ignore[import-not-found]
        QCheckBox,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QSpinBox,
        QVBoxLayout,
        QWidget,
        Qt,
    )

    from ..provider_info import PROVIDER_INFO

    page = QWidget(parent)
    v = QVBoxLayout(page)

    intro = QLabel(
        "<b>Image providers.</b><br>"
        "<span style='color:#888; font-size:11px;'>"
        "Toggle providers on/off and tune how many images each one returns. "
        "API keys are masked; click 👁 to reveal."
        "</span>"
    )
    intro.setWordWrap(True)
    v.addWidget(intro)

    enabled_providers = list(raw_config.get("providers") or [])

    enable_checks: Dict[str, Any] = {}
    key_edits: Dict[str, Any] = {}
    limit_spins: Dict[str, Any] = {}
    status_labels: Dict[str, Any] = {}

    def _refresh_status(pid: str) -> None:
        """Refresh the status badge for a provider row."""
        info = PROVIDER_INFO[pid]
        has_key = True
        if info.requires_api_key:
            edit = key_edits.get(pid)
            has_key = bool(edit and edit.text().strip())
        if pid in status_labels:
            status_labels[pid].setText(_provider_status_text(pid, has_key))

    # Total label updates as user changes per-provider limits
    total_label = QLabel()

    def _update_total() -> None:
        total = 0
        for pid, spin in limit_spins.items():
            if not enable_checks[pid].isChecked():
                continue
            v_ = spin.value()
            if v_ == 0:
                v_ = PROVIDER_INFO[pid].default_limit
            total += v_
        total_label.setText(
            f"<b>Total per request: ~{total} images</b> "
            f"<span style='color:#888;'>(across enabled providers)</span>"
        )

    # One QGroupBox per provider — easier to scan than a dense form.
    for pid in ("unsplash", "pexels", "wikimedia", "openverse"):
        info = PROVIDER_INFO[pid]

        box = QGroupBox(info.display_name, page)
        box_layout = QVBoxLayout(box)

        # Top row: enable checkbox + status badge + free-tier note
        top = QHBoxLayout()
        enable = QCheckBox("Enable", box)
        enable.setChecked(pid in enabled_providers if enabled_providers else True)
        enable_checks[pid] = enable
        top.addWidget(enable)

        status = QLabel(box)
        status_labels[pid] = status
        status.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(status)
        top.addStretch(1)

        tier = QLabel(
            f"<span style='color:#888; font-size:11px;'>"
            f"{info.free_tier_note}</span>",
            box,
        )
        top.addWidget(tier)
        box_layout.addLayout(top)

        # API key row (only for providers that need one)
        if info.requires_api_key:
            key_row, edit = _make_api_key_row(
                box,
                raw_config.get(f"{info.id}_access_key")
                or raw_config.get(f"{info.id}_api_key")
                or "",
                placeholder=f"Paste your {info.display_name} API key here",
            )
            key_edits[pid] = edit
            edit.textChanged.connect(lambda _t, p=pid: _refresh_status(p))
            box_layout.addWidget(key_row)
            if info.signup_url:
                box_layout.addWidget(
                    _make_link_label(
                        box, f"Get a free key at {info.display_name} →",
                        info.signup_url,
                    )
                )

        # Per-provider limit row
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Max results per search:", box))
        spin = QSpinBox(box)
        spin.setRange(0, info.max_per_request)
        spin.setSpecialValueText("(use fallback)")
        spin.setValue(int(raw_config.get(f"{pid}_max_results", info.default_limit)))
        spin.setToolTip(
            f"API hard cap: {info.max_per_request}\n"
            f"Set to 0 to use the global fallback (Advanced tab)."
        )
        spin.valueChanged.connect(_update_total)
        limit_spins[pid] = spin
        limit_row.addWidget(spin)
        limit_row.addWidget(
            QLabel(
                f"<span style='color:#888;'>(max {info.max_per_request})</span>",
                box,
            )
        )
        limit_row.addStretch(1)
        box_layout.addLayout(limit_row)

        # Re-flow total when enable toggles
        enable.toggled.connect(lambda _c, p=pid: (_refresh_status(p), _update_total()))

        v.addWidget(box)
        _refresh_status(pid)

    # Total
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    v.addWidget(sep)
    v.addWidget(total_label)
    _update_total()
    v.addStretch(1)

    def _enabled_providers_getter() -> list:
        return [pid for pid, c in enable_checks.items() if c.isChecked()]

    getters: Dict[str, Any] = {
        "providers": _enabled_providers_getter,
        "unsplash_access_key": lambda: key_edits["unsplash"].text().strip()
            if "unsplash" in key_edits else "",
        "pexels_api_key": lambda: key_edits["pexels"].text().strip()
            if "pexels" in key_edits else "",
    }
    for pid, spin in limit_spins.items():
        getters[f"{pid}_max_results"] = (lambda s=spin: s.value())

    return page, getters


def _build_advanced_tab(parent: Any, raw_config: dict) -> tuple:
    """Return (widget, getters_dict) for the Advanced tab."""
    from aqt.qt import (  # type: ignore[import-not-found]
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )

    page = QWidget(parent)
    v = QVBoxLayout(page)

    intro = QLabel(
        "<b>Caching & advanced options.</b><br>"
        "<span style='color:#888; font-size:11px;'>"
        "Power-user knobs. Defaults are fine for most users."
        "</span>"
    )
    intro.setWordWrap(True)
    v.addWidget(intro)

    form = QFormLayout()

    # Fallback max results
    fallback = QSpinBox()
    fallback.setRange(1, 200)
    fallback.setValue(int(raw_config.get("max_results_per_provider", 30)))
    fallback.setSuffix(" results")
    fallback.setToolTip(
        "Used when a provider has no per-provider limit set.\n"
        "Each provider still clamps to its own API cap."
    )
    form.addRow("Fallback max per provider:", fallback)

    # Cache size
    cache_size = QSpinBox()
    cache_size.setRange(10, 5000)
    cache_size.setValue(int(raw_config.get("thumbnail_cache_max_mb", 200)))
    cache_size.setSuffix(" MB")
    cache_size.setToolTip(
        "Disk size limit for cached thumbnail images. When the cache\n"
        "fills up, the least-recently-used items are evicted."
    )
    form.addRow("Thumbnail cache size:", cache_size)

    v.addLayout(form)

    # Action buttons
    btn_row = QHBoxLayout()

    open_json_btn = QPushButton("Open raw config.json…")
    open_json_btn.setToolTip(
        "Open the underlying JSON in Anki's built-in editor for\n"
        "options not exposed in this dialog."
    )

    def _open_raw_config() -> None:
        try:
            from aqt import mw  # type: ignore[import-not-found]
            from ._picker_factory import ADDON_PACKAGE

            mw.addonManager.onConfig(ADDON_PACKAGE)
        except Exception as exc:
            _log.warning("Could not open raw config: %s", exc)

    open_json_btn.clicked.connect(_open_raw_config)
    btn_row.addWidget(open_json_btn)

    reset_btn = QPushButton("Reset to defaults…")
    reset_btn.setToolTip(
        "Restore every setting to its built-in default. Your API\n"
        "keys are preserved."
    )
    btn_row.addWidget(reset_btn)
    btn_row.addStretch(1)

    v.addLayout(btn_row)
    v.addStretch(1)

    getters = {
        "max_results_per_provider": lambda: fallback.value(),
        "thumbnail_cache_max_mb": lambda: cache_size.value(),
    }

    return page, getters, reset_btn


# --- Main dialog -----------------------------------------------------------


def _on_tools_menu_clicked() -> None:
    """Open the settings dialog."""
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.qt import (  # type: ignore[import-not-found]
            QDialog,
            QDialogButtonBox,
            QLabel,
            QMessageBox,
            QTabWidget,
            QVBoxLayout,
        )
    except ImportError:
        _log.error("Cannot show settings: aqt not available")
        return

    try:
        from ._picker_factory import ADDON_PACKAGE

        # Load current config
        raw_config = mw.addonManager.getConfig(ADDON_PACKAGE) or {}

        dialog = QDialog(mw)
        dialog.setWindowTitle("⚡ Image Picker — Settings")
        dialog.setMinimumWidth(620)
        dialog.setMinimumHeight(560)

        outer = QVBoxLayout(dialog)

        # Hero header
        hero = QLabel(
            "<h2 style='margin:0;'>⚡ Image Picker</h2>"
            "<div style='color:#888; font-size:12px;'>"
            "Fastest Image Search & Insert — by AnkiVN"
            "</div>"
        )
        outer.addWidget(hero)

        # Tabs
        tabs = QTabWidget(dialog)
        outer.addWidget(tabs, 1)

        general_page, general_getters = _build_general_tab(dialog, raw_config)
        mappings_page, mappings_getters = _build_field_mappings_tab(
            dialog, raw_config
        )
        providers_page, providers_getters = _build_providers_tab(dialog, raw_config)
        advanced_page, advanced_getters, reset_btn = _build_advanced_tab(
            dialog, raw_config
        )

        tabs.addTab(general_page, "General")
        tabs.addTab(mappings_page, "Field Mappings")
        tabs.addTab(providers_page, "Providers")
        tabs.addTab(advanced_page, "Advanced")

        # Reset-to-defaults handler (close + reopen so widgets reload)
        def _on_reset() -> None:
            confirm = QMessageBox.question(
                dialog,
                "Reset to defaults",
                "Restore every setting to its built-in default?\n"
                "(API keys will be preserved.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                from dataclasses import asdict

                from ..config import ConfigLoader

                preserved_keys = {
                    "unsplash_access_key": raw_config.get(
                        "unsplash_access_key", ""
                    ),
                    "pexels_api_key": raw_config.get("pexels_api_key", ""),
                }
                new_raw = asdict(ConfigLoader.DEFAULTS)
                new_raw.update(preserved_keys)
                mw.addonManager.writeConfig(ADDON_PACKAGE, new_raw)
                dialog.accept()
                # Re-open so the form reflects the reset values
                _on_tools_menu_clicked()
            except Exception as exc:
                _log.exception("Reset failed: %s", exc)
                from aqt.utils import showCritical  # type: ignore[import-not-found]
                showCritical(f"Reset failed: {exc}", parent=dialog)

        reset_btn.clicked.connect(_on_reset)

        # Save / cancel
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

        # Merge getters from every tab into the existing config dict so
        # we don't drop keys we didn't expose.
        merged = dict(raw_config)
        for getters in (
            general_getters,
            mappings_getters,
            providers_getters,
            advanced_getters,
        ):
            for k, fn in getters.items():
                try:
                    merged[k] = fn()
                except Exception as exc:
                    _log.warning("Could not read %s: %s", k, exc)

        # Light validation: empty source/target field falls back to a
        # safe default rather than letting the user save a blank string
        # that the picker can't act on.
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
