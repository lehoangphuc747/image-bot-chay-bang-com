"""Main-thread-only bridge between the picker and the Anki editor.

Every function in this module touches one or more of:

* ``mw.col`` (the Anki collection) - via
  :meth:`anki.collection.Collection.update_note` and
  :meth:`anki.media.MediaManager.write_data`.
* ``editor.note`` (an :class:`anki.notes.Note`) - to read and mutate
  field values.
* :meth:`aqt.editor.Editor.loadNoteKeepingFocus` - to refresh the
  visible editor without losing the user's caret position.

Per the design document's "Concurrency rules" table, anything touching
``mw.col``, the editor, or any Qt widget must run on the main (Qt)
thread. **Every function in this module is therefore documented as
main-thread-only**; calling it from a worker thread is a programming
error and may corrupt the collection.

Worker threads must instead emit a Qt signal carrying the bytes
(``download_complete``) and let a slot on the main thread invoke
:func:`save_to_media` followed by :func:`insert_image`.

The ``mw`` reference is imported once at module load and stored on the
module so tests can monkey-patch it (``editor_bridge.mw = fake_mw``)
without faking the entire ``aqt`` package. Each function also accepts
an optional ``mw`` keyword to make explicit injection ergonomic in
tests; when omitted the module-global is used.

Validates Requirements 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 9.4, 9.5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .errors import FieldNotFoundError
from .filename import build_img_tag
from .logging import get_logger

# ``aqt`` is only available inside a running Anki process. Import it
# defensively so the module can be imported by tests in plain CPython
# (they then assign ``editor_bridge.mw`` to a fake before invoking any
# function). The ``# type: ignore`` keeps mypy/pyright happy in either
# environment.
try:  # pragma: no cover - exercised inside Anki, not in unit tests
    from aqt import mw as _aqt_mw  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - same
    _aqt_mw = None

#: Module-global Anki main window reference. Tests overwrite this
#: attribute (``editor_bridge.mw = fake_mw``) to inject a fake without
#: monkey-patching ``aqt``. Production code sets it once at import time
#: from :mod:`aqt`.
mw: Any = _aqt_mw


if TYPE_CHECKING:  # pragma: no cover - import-time only
    from aqt.editor import Editor  # noqa: F401


_log = get_logger("editor_bridge")


__all__ = ["save_to_media", "insert_image"]


def _resolve_mw(mw_arg: Optional[Any]) -> Any:
    """Return the explicit ``mw_arg`` if given, else the module global.

    Centralised so both public functions share the same rule and the
    same error message when no main window is wired up (which only
    happens in misconfigured tests; production always has ``aqt.mw``).
    """

    candidate = mw_arg if mw_arg is not None else mw
    if candidate is None:
        raise RuntimeError(
            "ankivn_image_picker.editor_bridge: no Anki main window is "
            "available. The module must be imported inside a running "
            "Anki process, or tests must assign editor_bridge.mw to a "
            "fake before calling save_to_media/insert_image."
        )
    return candidate


def save_to_media(
    filename: str,
    data: bytes,
    *,
    mw: Optional[Any] = None,
) -> str:
    """Write ``data`` to Anki's media folder under ``filename``.

    **Main-thread only.** This function calls into ``mw.col.media``,
    which is part of the Anki collection and must not be touched from
    a worker thread (design "Concurrency rules" table; Req 10.1).

    Parameters
    ----------
    filename:
        The desired media filename. The caller is expected to have
        derived this from
        :func:`ankivn_image_picker.filename.derive_filename`, which
        already sanitises the stem and resolves collisions against
        ``mw.col.media.have``. Anki's
        :meth:`~anki.media.MediaManager.write_data` may still rename
        the file if a same-name file appeared between
        :func:`derive_filename` and this call; the actually-used name
        is what this function returns.
    data:
        Raw image bytes to write verbatim. The function does **not**
        re-encode (Req 8.4 / Property 12); the bytes returned by
        reading the file back from the media manager are byte-for-byte
        identical to ``data``.
    mw:
        Optional explicit ``mw`` reference. Defaults to the module
        global, which is set from :mod:`aqt` at import time but may be
        overridden in tests.

    Returns
    -------
    str
        The filename Anki actually used. Anki's API contract is to
        return the (possibly renamed) name; we forward that value
        unchanged so the caller can build the ``<img>`` tag against
        the *real* filename rather than the requested one.
    """

    main_window = _resolve_mw(mw)
    used_filename = main_window.col.media.write_data(filename, data)
    # Anki documents ``write_data`` as returning the filename it used
    # (which may differ from the requested one when a same-name file
    # appears mid-flight). Fall back to the requested name if some
    # alternate Anki version returns ``None``.
    if not isinstance(used_filename, str) or not used_filename:
        used_filename = filename
    _log.debug(
        "save_to_media: requested=%r used=%r bytes=%d",
        filename,
        used_filename,
        len(data),
    )
    return used_filename


def insert_image(
    editor: Any,
    target_field: str,
    filename: str,
    *,
    mw: Optional[Any] = None,
    attribution_html: Optional[str] = None,
) -> None:
    """Append an ``<img src="filename">`` tag to ``target_field``.

    **Main-thread only.** This function reads and mutates
    ``editor.note`` (an :class:`anki.notes.Note`) and calls
    :meth:`aqt.editor.Editor.loadNoteKeepingFocus` plus
    :meth:`anki.collection.Collection.update_note`, all of which must
    run on the Qt main thread.

    Parameters
    ----------
    editor:
        The active Anki :class:`~aqt.editor.Editor` whose current note
        will receive the image. The note is read via ``editor.note``
        and refreshed via ``editor.loadNoteKeepingFocus()`` once the
        field has been mutated, so the change is visible without a
        manual reload (Req 9.2).
    target_field:
        The name of the field to append the image to. Looked up
        case-sensitively in ``editor.note.note_type()["flds"]``;
        :class:`~ankivn_image_picker.errors.FieldNotFoundError` is
        raised if no field with this name exists on the note type.
        On that error path the note is **not** modified
        (Req 9.4 / Property 15 precondition: a failed lookup is a
        no-op against the note state).
    filename:
        The media filename, as returned by :func:`save_to_media`. The
        function builds the tag via
        :func:`~ankivn_image_picker.filename.build_img_tag` so the
        ``src`` attribute is properly HTML-escaped (Req 9.1 /
        Property 14).
    mw:
        Optional explicit ``mw`` reference. Defaults to the module
        global; see :func:`save_to_media` for the rationale.
    attribution_html:
        Optional HTML snippet for image attribution (e.g. "Photo by X
        on Unsplash" with proper UTM links). When provided, it is
        appended after the image tag inside a small styled wrapper.

    Raises
    ------
    FieldNotFoundError
        If ``target_field`` is not a field on the current note type.

    Notes
    -----
    The new tag is **appended** to the existing field content rather
    than overwriting it (Req 9.3 / Property 15). The post-condition
    field value is exactly ``original_content + tag``: the original
    content is preserved as a strict prefix, and the tag appears as a
    strict suffix.
    """

    main_window = _resolve_mw(mw)

    note = editor.note
    if note is None:
        # Defensive: the picker should only open when an editor is
        # active and bound to a note, but a misuse from elsewhere in
        # the package shouldn't corrupt anything.
        raise RuntimeError(
            "editor_bridge.insert_image: editor has no active note"
        )

    # Locate the field index by name. Anki stores fields in the order
    # they appear on the note type, and ``note.fields`` is parallel to
    # ``note_type()["flds"]``. The lookup is case-sensitive to match
    # how config validation in :mod:`config` records the configured
    # field names.
    field_defs = note.note_type()["flds"]
    field_index: Optional[int] = None
    for idx, fld in enumerate(field_defs):
        if fld["name"] == target_field:
            field_index = idx
            break

    if field_index is None:
        # Never mutate the note when the field is missing. Property 15
        # only describes the success path; the failure path leaves
        # note state untouched (Req 9.4).
        raise FieldNotFoundError(target_field)

    tag = build_img_tag(filename)
    # Append attribution HTML if provided (e.g. for Unsplash compliance)
    if attribution_html:
        tag = tag + attribution_html

    existing = note.fields[field_index]
    # Append, do not overwrite (Req 9.3 / Property 15). String
    # concatenation is used instead of ``+=`` to make it obvious that
    # the original content is preserved as a strict prefix.
    note.fields[field_index] = existing + tag

    # Refresh the editor view first so the user sees the change
    # immediately, then persist via the collection so the change is
    # durable on the next save (Req 9.2, 9.5).
    editor.loadNoteKeepingFocus()
    main_window.col.update_note(note)

    _log.debug(
        "insert_image: target_field=%r index=%d filename=%r appended=%d bytes",
        target_field,
        field_index,
        filename,
        len(tag),
    )
