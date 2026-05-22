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
_MENU_LABEL = "⚡ Image Picker (Selected Notes)"


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
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QLabel,
            QVBoxLayout,
        )

        field_dialog = QDialog(browser)
        field_dialog.setWindowTitle("⚡ Image Picker · Batch Setup")
        field_dialog.setMinimumWidth(400)
        dlg_layout = QVBoxLayout(field_dialog)

        header = QLabel(
            f"<b>{len(nids)} note(s) selected</b><br>"
            f"Choose which field to use as the search query, "
            f"and which field to insert the image into."
        )
        header.setWordWrap(True)
        dlg_layout.addWidget(header)

        # Per-note-type field-mapping toggle. When the user has saved
        # mappings under Settings → Field Mappings, those win over the
        # per-batch combo boxes below. Box stays enabled in either
        # case so the user can override on the fly.
        from ..config import resolve_fields as _resolve_fields

        mappings_count = len(getattr(deps.config, "field_mappings", ()) or ())
        use_mappings_check = QCheckBox(
            f"Use per-note-type mappings from Settings ({mappings_count} configured)",
            field_dialog,
        )
        use_mappings_check.setChecked(mappings_count > 0)
        use_mappings_check.setToolTip(
            "When checked, each note uses the source/target field\n"
            "configured for its note type in Settings → Field Mappings.\n"
            "Notes whose type has no mapping fall back to the global\n"
            "default (or the dropdowns below if you uncheck this).\n"
            "When unchecked, every note uses the dropdowns below — "
            "useful when the entire selection shares one note type."
        )
        dlg_layout.addWidget(use_mappings_check)

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
        use_mappings = bool(use_mappings_check.isChecked())

        if not use_mappings and batch_source_field == batch_target_field:
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
        # ``note_queries`` carries the per-note effective source/target
        # so that per-note-type mappings can be applied without
        # re-resolving in every downstream code-path.
        note_queries: list[tuple[int, Any, str, str, str]] = []
        # (index, nid, query, src_field, tgt_field)
        # Per-note metadata for the dialog's left-side notes panel.
        # Same order as note_queries; one dict per kept note.
        notes_meta: list[dict] = []
        for index, nid in enumerate(nids, start=1):
            try:
                note = mw.col.get_note(nid)
            except Exception as exc:
                _log.exception("Failed to load note %s: %s", nid, exc)
                errors.append(f"note {nid}: {exc}")
                continue

            field_defs = note.note_type()["flds"]
            field_names = [fld["name"] for fld in field_defs]

            # Resolve per-note effective fields. When the user enabled
            # per-note-type mappings we look them up here; otherwise
            # fall back to whatever the user picked in the dropdowns.
            if use_mappings:
                try:
                    nt_name = note.note_type()["name"]
                except Exception:
                    nt_name = ""
                src_field, tgt_field = _resolve_fields(nt_name, config)
            else:
                src_field, tgt_field = batch_source_field, batch_target_field

            if src_field not in field_names:
                skipped += 1
                continue
            field_index = field_names.index(src_field)
            raw_value = note.fields[field_index]
            query = normalize_query(raw_value)
            if not query:
                skipped += 1
                continue
            note_queries.append((index, nid, query, src_field, tgt_field))

            # Detect whether the target field already contains an
            # image. This is a cheap substring check: anything Anki
            # has rendered will contain an <img> tag.
            has_image = False
            try:
                if tgt_field in field_names:
                    tgt_idx = field_names.index(tgt_field)
                    tgt_value = note.fields[tgt_idx] or ""
                    has_image = "<img" in tgt_value.lower()
            except Exception:
                has_image = False

            notes_meta.append({
                "nid": nid,
                "query": query,
                "label": query,
                "has_image": has_image,
                "status": None,  # set later: "chosen" / "skipped"
            })

        if not note_queries:
            tooltip("No notes with valid source fields found.", parent=browser)
            return

        # --- Prefetch: start searching for the first N notes immediately ---
        # We aim to prefetch every note in the selection, not just a
        # rolling window. The pool itself enforces a small concurrency
        # cap (3 by default) so we don't burst the providers; tasks
        # queue up and run as workers free up. This means the user
        # never lands on a note that hasn't been at least scheduled
        # for prefetching.
        import concurrent.futures

        prefetch_ahead = max(0, config.prefetch_notes_ahead)
        # Concurrent prefetch cap. The user setting now controls how
        # many notes are processed in parallel — total queued work
        # always covers the full selection.
        pool_size = max(1, min(prefetch_ahead, 8)) if prefetch_ahead > 0 else 0

        prefetch_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=pool_size
        )
        _log.info(
            "Batch prefetch: prefetch_ahead=%d, pool_size=%d, total_notes=%d",
            prefetch_ahead, pool_size, len(nids),
        )
        prefetch_cache: dict[str, list] = {}  # query -> list of ImageResult
        prefetch_errors: dict[str, dict[str, str]] = {}  # query -> {provider_id -> error}
        prefetch_translations: dict[str, str] = {}  # original_query -> translated_query

        # Live counters for the dialog status bar + per-query state
        # for the side panel. Each query in ``prefetch_query_state``
        # is one of:
        #   "queued"   — submitted but not started yet
        #   "running"  — worker has started fetching
        #   "done"     — results landed in prefetch_cache
        prefetch_seen: set[str] = set()
        prefetch_state: dict[str, int] = {
            "done": 0,
            "in_flight": 0,
        }
        prefetch_query_state: dict[str, str] = {}
        # Per-query thumbnail progress: query -> (loaded, total).
        # ``total`` is set as soon as search results land; ``loaded``
        # ticks up as each thumbnail finishes. Reading this lets the
        # side panel show "apple (12/30)" while the note is still
        # warming.
        prefetch_thumb_progress: dict[str, tuple[int, int]] = {}
        prefetch_lock = __import__("threading").Lock()

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

            # Initialise thumbnail progress now that we know the total.
            with prefetch_lock:
                prefetch_thumb_progress[query] = (0, len(results))

            # Warm thumbnail cache so the dialog renders instantly
            # when the user reaches this note.
            loaded = 0
            for result in results:
                try:
                    # Skip if already cached — count as already loaded
                    # so the panel reflects "ready" sooner.
                    if deps.cache.get(result.thumbnail_url) is not None:
                        loaded += 1
                        with prefetch_lock:
                            prefetch_thumb_progress[query] = (
                                loaded, len(results)
                            )
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
                # Always advance the counter, even on failure, so the
                # progress display doesn't stall on a flaky URL.
                loaded += 1
                with prefetch_lock:
                    prefetch_thumb_progress[query] = (loaded, len(results))

        # Kick off prefetch for first N notes
        prefetch_futures: list = []

        def _submit_prefetch(q: str) -> None:
            """Dedupe + counter-aware submission."""
            with prefetch_lock:
                if q in prefetch_cache or q in prefetch_seen:
                    return
                prefetch_seen.add(q)
                prefetch_state["in_flight"] += 1
                prefetch_query_state[q] = "queued"

            def _wrapped() -> None:
                with prefetch_lock:
                    prefetch_query_state[q] = "running"
                try:
                    _prefetch_query(q)
                finally:
                    with prefetch_lock:
                        prefetch_state["in_flight"] -= 1
                        prefetch_state["done"] += 1
                        prefetch_query_state[q] = "done"

            prefetch_futures.append(prefetch_pool.submit(_wrapped))

        if prefetch_ahead > 0:
            # Submit ALL notes for prefetch up-front. The pool's
            # ``max_workers`` ensures only ``pool_size`` run at once;
            # the rest sit in the executor's internal queue and start
            # as workers free up. This guarantees every note in the
            # batch is at least scheduled, so the user never lands on
            # a cold note even if they jump around.
            for q in (nq[2] for nq in note_queries):
                _submit_prefetch(q)

        def _prefetch_status_snapshot() -> dict:
            """Snapshot for the dialog status bar + per-query side panel."""
            with prefetch_lock:
                return {
                    "done": prefetch_state["done"],
                    "in_flight": prefetch_state["in_flight"],
                    "total": len(note_queries),
                    "query_states": dict(prefetch_query_state),
                    "thumb_progress": dict(prefetch_thumb_progress),
                }

        # --- Walk notes via a single reused dialog ---
        # Instead of opening N modal dialogs in a row (which makes the
        # screen flicker for 1-2s between notes while the next one
        # spins up), we open ONE dialog and call ``swap_to_query`` on
        # it for each note. The dialog drives the queue itself via
        # ``start_batch`` + a next-note callback below.
        from .picker_dialog import PickerDialog

        # Builds the dict the dialog expects each time it asks for the
        # next note. Returns None when the queue is exhausted so the
        # dialog knows to accept().

        def _build_job_for(seq_idx: int) -> Optional[dict]:
            if seq_idx >= len(note_queries):
                return None
            index, nid, q, src_field, tgt_field = note_queries[seq_idx]

            # All upcoming notes are already queued by the up-front
            # submission loop, so there's nothing extra to schedule
            # here.

            try:
                note = mw.col.get_note(nid)
            except Exception as exc:
                _log.exception("Failed to load note %s: %s", nid, exc)
                errors.append(f"note {nid}: {exc}")
                return None

            shim = _BatchEditorShim(browser=browser, note=note)
            # Stash a skip-tagger on the shim so the dialog can call
            # it without us having to plumb mw/browser into the dialog.
            def _skip_tag_note(_nid: int = nid) -> None:
                try:
                    n = mw.col.get_note(_nid)
                    n.add_tag("AnkiVN_Image_Picker_Skipped")
                    mw.col.update_note(n)
                except Exception as exc:
                    _log.warning(
                        "Failed to add skip tag to note %s: %s", _nid, exc
                    )

            shim._skip_tag_note = _skip_tag_note  # type: ignore[attr-defined]

            prefetched_results = (
                prefetch_cache.pop(q) if q in prefetch_cache else None
            )
            prefetched_errs = (
                prefetch_errors.pop(q) if q in prefetch_errors else None
            )
            prefetched_translation = (
                prefetch_translations.pop(q)
                if q in prefetch_translations else None
            )
            # Mark this query as consumed so the side-panel marker
            # transitions from 📦 (cached) to ▶ (active) cleanly.
            with prefetch_lock:
                prefetch_query_state[q] = "consumed"

            return {
                "editor": shim,
                "query": q,
                "source_field": src_field,
                "target_field": tgt_field,
                "position": (index, len(nids)),
                "prefetched_results": prefetched_results,
                "prefetched_errors": prefetched_errs,
                "prefetched_translation": prefetched_translation,
            }

        def _next_job() -> Optional[dict]:
            # Build the job for the seq following whatever the dialog
            # currently has active. Using the dialog's own pointer
            # means click-to-jump works without confusing the
            # sequential advance logic.
            seq = getattr(dialog, "_batch_current_seq", 0) + 1
            return _build_job_for(seq)

        first_job = _build_job_for(0)
        if first_job is None:
            tooltip("No notes with valid source fields found.", parent=browser)
            prefetch_pool.shutdown(wait=False)
            return

        # Construct the dialog with the first note's data, then enter
        # batch mode and exec(). The dialog stays open until the queue
        # is drained or the user closes it.
        from dataclasses import replace as _replace
        first_config = _replace(
            config,
            source_field=first_job["source_field"],
            target_field=first_job["target_field"],
        )

        dialog = PickerDialog(
            editor=first_job["editor"],
            config=first_config,
            query=first_job["query"],
            providers=deps.providers,
            http=deps.http,
            cache=deps.cache,
            parent=browser,
            prefetched_results=first_job["prefetched_results"],
            prefetched_errors=first_job["prefetched_errors"],
            prefetched_translation=first_job["prefetched_translation"],
        )
        try:
            pos = first_job["position"]
            dialog.setWindowTitle(
                f"⚡ Image Picker · Batch [{pos[0]}/{pos[1]}] · {first_job['query']}"
            )
        except Exception:
            pass

        dialog.start_batch(
            _next_job,
            prefetch_status=_prefetch_status_snapshot,
            notes_meta=notes_meta,
            job_factory=_build_job_for,
        )

        try:
            dialog.exec()
        except Exception as exc:
            _log.exception("Picker dialog raised: %s", exc)

        # Pull aggregated outcomes off the dialog
        outcomes = dialog.batch_outcomes or {}
        chosen = outcomes.get("chosen", 0)
        skipped = outcomes.get("skipped", 0)
        errors.extend(outcomes.get("errors", []))

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
                f"Image Picker batch failed:\n{exc}",
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
            f"⚡ Image Picker · Batch [{position[0]}/{position[1]}] · {query}"
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
