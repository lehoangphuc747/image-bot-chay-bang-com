"""Browser-menu integration for the AnkiVN Smart Image Picker add-on.

Adds a "AnkiVN Image Picker (Selected Notes)" entry to the Browser's
``Notes`` menu. When the user selects one or more notes in the
Browser and triggers the action, this module walks the selection
sequentially and opens the picker dialog once per note, letting the
user choose an image for each note in turn.

Threading
---------
This module runs exclusively on the Qt main thread. Each picker
dialog is shown modally, so the loop blocks until the user picks an
image (or closes the dialog) before advancing to the next note. This
keeps the implementation simple and avoids contention with the
collection (the picker writes to ``mw.col.media`` and updates the
note via ``mw.col.update_note``, both of which are main-thread-only).

Cancellation
------------
The user can abort the batch at any time by closing the picker
dialog without selecting an image; the loop interprets a non-accepted
dialog result as "skip this note and continue" by default. Pressing
Cancel in the confirmation prompt at the start aborts the batch
entirely before any work is done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

from ..errors import FieldNotFoundError
from ..logging import get_logger
from ..query import normalize_query
from ._picker_factory import build_picker_deps

if TYPE_CHECKING:  # pragma: no cover
    from anki.notes import Note, NoteId  # type: ignore[import-not-found]


_log = get_logger("browser_menu")

#: Label shown in the Browser's Notes menu.
_MENU_LABEL = "AnkiVN Image Picker (Selected Notes)"


def _on_batch_action(browser: Any) -> None:
    """Handle the menu click: iterate selected notes, open picker per note.

    Wrapped in a top-level try/except so unexpected failures surface a
    user-visible critical dialog instead of an unhandled traceback
    (Req 10.3).
    """
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.utils import (  # type: ignore[import-not-found]
            askUser,
            showCritical,
            showInfo,
            tooltip,
        )
    except ImportError:
        _log.error("Cannot run batch action: aqt not available")
        return

    try:
        nids: List["NoteId"] = list(browser.selected_notes())
        if not nids:
            showInfo("Please select one or more notes in the Browser first.")
            return

        deps = build_picker_deps()
        if deps is None:
            # build_picker_deps already showed a warning to the user
            return

        # --- Collect all field names from selected notes ---
        # Different note types may have different fields, so we gather
        # the union of all field names across the selection.
        all_field_names: list[str] = []
        seen_fields: set[str] = set()
        for nid in nids:
            try:
                note = mw.col.get_note(nid)
                for fld in note.note_type()["flds"]:
                    name = fld["name"]
                    if name not in seen_fields:
                        seen_fields.add(name)
                        all_field_names.append(name)
            except Exception:
                continue

        if not all_field_names:
            showInfo("Could not read fields from selected notes.")
            return

        # --- Show field selection dialog ---
        from aqt.qt import (  # type: ignore[import-not-found]
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLabel,
            QVBoxLayout,
        )

        field_dialog = QDialog(browser)
        field_dialog.setWindowTitle("AnkiVN Image Picker — Batch Setup")
        field_dialog.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(field_dialog)

        header = QLabel(
            f"<b>{len(nids)} note(s) selected</b><br>"
            f"Choose which field to use as the search query, "
            f"and which field to insert the image into."
        )
        header.setWordWrap(True)
        dlg_layout.addWidget(header)

        form = QFormLayout()

        source_combo = QComboBox()
        source_combo.addItems(all_field_names)
        # Pre-select the config default if it exists
        config_source = deps.config.source_field
        if config_source in all_field_names:
            source_combo.setCurrentText(config_source)
        form.addRow("Source field (search query):", source_combo)

        target_combo = QComboBox()
        target_combo.addItems(all_field_names)
        config_target = deps.config.target_field
        if config_target in all_field_names:
            target_combo.setCurrentText(config_target)
        form.addRow("Target field (insert image):", target_combo)

        dlg_layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(field_dialog.accept)
        buttons.rejected.connect(field_dialog.reject)
        dlg_layout.addWidget(buttons)

        if field_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Use the user-chosen fields for this batch run
        batch_source_field = source_combo.currentText()
        batch_target_field = target_combo.currentText()

        if batch_source_field == batch_target_field:
            showInfo("Source and target fields cannot be the same.")
            return

        # Walk the selection. We do not start a progress bar because
        # each picker dialog is itself a modal that occupies the
        # foreground; a separate progress dialog would compete with it.
        chosen = 0
        skipped = 0
        errors: list[str] = []

        # Pre-compute queries for all notes so we can prefetch.
        config = deps.config
        note_queries: list[tuple[int, Any, str]] = []  # (index, nid, query)
        for index, nid in enumerate(nids, start=1):
            try:
                note = mw.col.get_note(nid)
            except Exception as exc:
                _log.exception("Failed to load note %s: %s", nid, exc)
                errors.append(f"note {nid}: {exc}")
                continue

            field_defs = note.note_type()["flds"]
            field_names = [fld["name"] for fld in field_defs]
            if batch_source_field not in field_names:
                skipped += 1
                continue
            field_index = field_names.index(batch_source_field)
            raw_value = note.fields[field_index]
            query = normalize_query(raw_value)
            if not query:
                skipped += 1
                continue
            note_queries.append((index, nid, query))

        if not note_queries:
            tooltip("No notes with valid source fields found.", parent=browser)
            return

        # --- Prefetch: start searching for the first N notes immediately ---
        import concurrent.futures

        prefetch_ahead = max(0, config.prefetch_notes_ahead)
        # Pool sized to handle concurrent metadata fetches AND thumbnail
        # downloads. Each note prefetch does 1 metadata call per
        # provider plus N thumbnail downloads, so we need plenty of
        # workers to avoid serialising thumbnails behind metadata.
        pool_size = max(2, min(prefetch_ahead * 2, 16))

        prefetch_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=pool_size
        )
        prefetch_cache: dict[str, list] = {}  # query -> list of ImageResult
        prefetch_errors: dict[str, dict[str, str]] = {}  # query -> {provider_id -> error}
        prefetch_translations: dict[str, str] = {}  # original_query -> translated_query

        def _prefetch_query(query: str) -> None:
            """Run search for a query AND warm the thumbnail cache."""
            from ..cancellation import CancellationToken
            from ..errors import ProviderError
            from ..http import is_valid_image_response
            from ..provider_info import get_provider_limit
            from ..translator import looks_like_english, translate_to_english

            cancel = CancellationToken()

            # Translate non-English queries up-front so prefetched
            # results match what the dialog will show
            search_query = query
            if (
                getattr(config, "translate_to_english", True)
                and not looks_like_english(query)
            ):
                try:
                    search_query = translate_to_english(
                        query, http=deps.http, cancel=cancel
                    )
                except Exception:
                    search_query = query

            # Record translation so the dialog can show it in the
            # search box (only if it actually changed)
            if search_query and search_query != query:
                prefetch_translations[query] = search_query

            results: list = []
            errors: dict[str, str] = {}  # provider_id -> error message
            for provider in deps.providers:
                try:
                    max_n = get_provider_limit(provider.id, config)
                    for result in provider.search(
                        search_query,
                        max_results=max_n,
                        http=deps.http,
                        cancel=cancel,
                    ):
                        results.append(result)
                except ProviderError as exc:
                    errors[provider.id] = str(exc)
                except Exception as exc:
                    errors[provider.id] = f"Unexpected error: {exc}"
            prefetch_cache[query] = results
            # Stash errors so the dialog can surface them in the status bar
            prefetch_errors[query] = errors

            # Warm thumbnail cache so the dialog renders instantly
            # when the user reaches this note.
            for result in results:
                try:
                    # Skip if already cached
                    if deps.cache.get(result.thumbnail_url) is not None:
                        continue
                    response = deps.http.get(
                        result.thumbnail_url, cancel=cancel
                    )
                    if is_valid_image_response(
                        response.body, response.content_type
                    ):
                        deps.cache.put(result.thumbnail_url, response.body)
                except Exception:
                    # Failed thumbnail downloads are non-fatal — the
                    # dialog will retry when it opens.
                    pass

        # Kick off prefetch for first N notes
        prefetch_futures: list = []
        if prefetch_ahead > 0:
            for i in range(min(prefetch_ahead, len(note_queries))):
                q = note_queries[i][2]
                if q not in prefetch_cache:
                    prefetch_futures.append(
                        prefetch_pool.submit(_prefetch_query, q)
                    )

        # --- Walk notes sequentially ---
        for seq_idx, (index, nid, query) in enumerate(note_queries):
            # Kick off prefetch for upcoming notes (look ahead N)
            if prefetch_ahead > 0:
                for ahead in range(1, prefetch_ahead + 1):
                    future_idx = seq_idx + ahead
                    if future_idx < len(note_queries):
                        future_q = note_queries[future_idx][2]
                        if future_q not in prefetch_cache:
                            prefetch_pool.submit(_prefetch_query, future_q)

            try:
                note = mw.col.get_note(nid)
            except Exception as exc:
                _log.exception("Failed to load note %s: %s", nid, exc)
                errors.append(f"note {nid}: {exc}")
                continue

            outcome = _run_picker_for_note(
                browser=browser,
                note=note,
                deps=deps,
                position=(index, len(nids)),
                prefetch_cache=prefetch_cache,
                prefetch_errors=prefetch_errors,
                prefetch_translations=prefetch_translations,
                source_field=batch_source_field,
                target_field=batch_target_field,
            )

            if outcome == "chosen":
                chosen += 1
            elif outcome == "skipped":
                skipped += 1
                # Tag the note so user can filter skipped notes later.
                # Use underscores instead of spaces because Anki splits
                # tags on whitespace.
                try:
                    note.add_tag("AnkiVN_Image_Picker_Skipped")
                    mw.col.update_note(note)
                except Exception as exc:
                    _log.warning("Failed to add skip tag to note %s: %s", nid, exc)
            elif outcome == "abort":
                break
            else:
                errors.append(f"note {nid}")

        # Cleanup prefetch pool
        prefetch_pool.shutdown(wait=False)

        # Refresh the browser so any newly inserted images become
        # visible in the preview pane.
        try:
            browser.model.reset()
        except Exception:
            pass

        summary_parts = [f"{chosen} note(s) updated"]
        if skipped:
            summary_parts.append(f"{skipped} skipped")
        if errors:
            summary_parts.append(f"{len(errors)} error(s)")
        tooltip(", ".join(summary_parts), parent=browser)

    except Exception as exc:
        _log.exception("Batch picker action failed: %s", exc)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]

            showCritical(
                f"AnkiVN Image Picker batch failed:\n{exc}",
                parent=browser,
            )
        except Exception:
            pass


def _run_picker_for_note(
    *,
    browser: Any,
    note: "Note",
    deps: Any,
    position: tuple[int, int],
    prefetch_cache: Optional[dict] = None,
    prefetch_errors: Optional[dict] = None,
    prefetch_translations: Optional[dict] = None,
    source_field: Optional[str] = None,
    target_field: Optional[str] = None,
) -> str:
    """Open the picker for a single note and return the outcome string.

    Returns one of:

    * ``"chosen"`` — the user selected an image and it was inserted.
    * ``"skipped"`` — the user closed the dialog without choosing.
    * ``"abort"`` — the batch should stop entirely.
    * ``"error"`` — an unexpected error occurred; logged but the batch
      continues.
    """
    try:
        from aqt.utils import tooltip  # type: ignore[import-not-found]
    except ImportError:
        return "error"

    config = deps.config
    # Use batch-specified fields if provided, otherwise fall back to config
    src_field = source_field or config.source_field
    tgt_field = target_field or config.target_field

    # Validate the source field exists on this specific note's note type.
    field_defs = note.note_type()["flds"]
    field_names = [fld["name"] for fld in field_defs]

    if src_field not in field_names:
        _log.info(
            "Skipping note (source field %r missing on note type)",
            src_field,
        )
        tooltip(
            f"Skipping note {position[0]}/{position[1]}: "
            f"source field '{src_field}' not found.",
            parent=browser,
        )
        return "skipped"

    # Read and normalise the source field.
    field_index = field_names.index(src_field)
    raw_value = note.fields[field_index]
    query = normalize_query(raw_value)

    if not query:
        _log.info("Skipping note: source field empty")
        tooltip(
            f"Skipping note {position[0]}/{position[1]}: "
            f"source field is empty.",
            parent=browser,
        )
        return "skipped"

    # Build a lightweight editor-shim that the picker uses to insert
    # the image into the right field of the right note. The shim
    # exposes the attributes the picker reaches for: ``note``,
    # ``parentWindow``, and ``loadNoteKeepingFocus``.
    shim = _BatchEditorShim(browser=browser, note=note)

    # Open the dialog modally so the loop waits for the user.
    from .picker_dialog import PickerDialog

    # Check if we have prefetched results for this query
    prefetched_results = None
    if prefetch_cache and query in prefetch_cache:
        prefetched_results = prefetch_cache.pop(query)

    # Pull any prefetch errors so the dialog can show them in status bar
    prefetched_errors = None
    if prefetch_errors and query in prefetch_errors:
        prefetched_errors = prefetch_errors.pop(query)

    # Pull any prefetched translation so the search box shows it
    prefetched_translation = None
    if prefetch_translations and query in prefetch_translations:
        prefetched_translation = prefetch_translations.pop(query)

    # Create a config override with the batch-specified target field
    from dataclasses import replace as _replace
    batch_config = _replace(config, source_field=src_field, target_field=tgt_field)

    dialog = PickerDialog(
        editor=shim,
        config=batch_config,
        query=query,
        providers=deps.providers,
        http=deps.http,
        cache=deps.cache,
        parent=browser,
        prefetched_results=prefetched_results,
        prefetched_errors=prefetched_errors,
        prefetched_translation=prefetched_translation,
    )

    # Decorate the title so the user knows where they are in the batch.
    try:
        dialog.setWindowTitle(
            f"Image Picker [{position[0]}/{position[1]}] — {query}"
        )
    except Exception:
        pass

    try:
        result = dialog.exec()
    except Exception as exc:
        _log.exception("Picker dialog raised: %s", exc)
        return "error"

    # ``QDialog.Accepted`` (1) = user selected an image.
    # ``QDialog.Rejected`` (0) = user closed or skipped.
    #   - was_skipped = True → skip this note, continue batch
    #   - was_skipped = False → user closed (X button) → abort batch
    if result == 1:
        return "chosen"
    if hasattr(dialog, 'was_skipped') and dialog.was_skipped:
        return "skipped"
    return "abort"


class _BatchEditorShim:
    """Minimal editor-like object used during batch processing.

    The picker dialog and editor_bridge expect an Anki ``Editor`` with
    ``note``, ``parentWindow``, and ``loadNoteKeepingFocus``. In the
    batch flow there is no live editor for each note, so this shim
    supplies just those three attributes. ``loadNoteKeepingFocus`` is
    a no-op because the note is not currently being displayed in any
    editor; the change is persisted by ``mw.col.update_note`` inside
    :func:`editor_bridge.insert_image`, which is what makes it
    durable.
    """

    def __init__(self, *, browser: Any, note: "Note") -> None:
        self.note = note
        self.parentWindow = browser

    def loadNoteKeepingFocus(self) -> None:
        """No-op: the note is not currently displayed in an editor."""
        return None


def _on_browser_will_show(browser: Any) -> None:
    """Hook callback: install the batch action under ``Notes``."""
    try:
        from aqt.qt import QAction  # type: ignore[import-not-found]
    except ImportError:
        return

    try:
        menu = browser.form.menu_Notes

        # Idempotency: skip if already installed for this Browser.
        sentinel = "ankivn_image_picker_browser_action"
        for existing in menu.actions():
            if existing.objectName() == sentinel:
                return

        action = QAction(_MENU_LABEL, browser)
        action.setObjectName(sentinel)
        action.triggered.connect(lambda _checked=False, b=browser: _on_batch_action(b))
        menu.addSeparator()
        menu.addAction(action)
    except Exception as exc:
        _log.exception("Failed to install Browser menu entry: %s", exc)


def install_browser_hook() -> None:
    """Register the ``browser_will_show`` hook with Anki."""
    try:
        from aqt import gui_hooks  # type: ignore[import-not-found]
    except ImportError:
        _log.debug(
            "aqt not available; skipping Browser hook registration "
            "(expected in test environments)."
        )
        return

    gui_hooks.browser_will_show.append(_on_browser_will_show)
    _log.info("Browser menu hook registered.")


__all__ = ["install_browser_hook"]
