"""Cross-thread signal hub for the AnkiVN Smart Image Picker add-on.

The :class:`WorkerBus` :class:`QObject` is the single channel by which
background worker tasks (provider searches, thumbnail downloads,
full-image downloads) deliver progress, results, and failures back to
the Qt main thread.

Threading rules
---------------
The :class:`WorkerBus` instance MUST be constructed and owned by the Qt
main thread (Anki's UI thread). The :class:`QObject` itself lives on
that thread, which is what makes Qt's queued-connection delivery
semantics work: signals emitted from a worker thread are automatically
queued for delivery on the bus's owning thread, so connected slots run
on the main thread without any additional synchronization.

Workers MAY emit any of the bus's signals from any thread. Workers MUST
NOT call methods on widgets, the Anki collection (``mw.col``), or the
editor directly; they communicate only through the bus.

Signal payloads
---------------
The signal signatures below are chosen so every payload is a primitive
Qt-marshallable type or a frozen Python value:

``result_ready(object)``
    Carries an ``ImageResult`` instance. Declared as ``object`` because
    ``ImageResult`` is a frozen dataclass defined in
    :mod:`ankivn_image_picker.providers.base`; ``object`` is the safest
    portable signature for arbitrary Python values across Qt bindings.
    Emitted once per result yielded by a provider (Req 4.4).

``provider_failed(str, str)``
    ``(provider_id, message)``. Emitted at most once per provider that
    raises :class:`~ankivn_image_picker.errors.ProviderError`
    (Req 4.5).

``thumbnail_ready(str, bytes)``
    ``(thumbnail_url, thumbnail_bytes)``. Emitted when a thumbnail is
    available, whether served from the on-disk cache or freshly
    downloaded. The grid view replaces the row's placeholder pixmap
    with these bytes (Req 5.6).

``thumbnail_failed(str, str)``
    ``(thumbnail_url, message)``. Emitted when a thumbnail download
    fails. The grid view renders a neutral placeholder pixmap for the
    affected row but retains the row's full-image URL for selection
    (Req 5.5).

``download_progress(str, float)``
    ``(full_url, fraction)``. ``fraction`` is in the closed interval
    ``[0.0, 1.0]``. Emitted periodically while a full-image download is
    in flight so the picker can render a progress overlay on the
    selected cell (Req 7.2).

``download_complete(str, bytes, str)``
    ``(full_url, image_bytes, extension)``. Emitted once when a
    full-image download finishes successfully and the response is a
    valid image. The picker dialog hands these bytes to the editor
    bridge and then closes (Req 7.3).

``download_failed(str, str)``
    ``(full_url, message)``. Emitted when a full-image download fails
    for any reason, including HTTP/network errors (Req 7.4) and a
    response that is empty or whose content type is not an image
    (Req 7.5). The dialog displays a toast and stays open.

``unhandled_error(str)``
    ``(message)``. Last-resort signal emitted by a worker's outer
    ``try``/``except`` when an exception escapes the typed-error
    conversion path. The picker dialog logs and surfaces this through
    ``aqt.utils.showCritical`` (Req 10.3).

Qt binding compatibility
------------------------
The module imports Qt symbols via Anki's ``aqt.qt`` shim, which
re-exports the active binding (PyQt6 on modern Anki). When the test
environment does not have ``aqt`` or PyQt available (for example, on a
CI image used to run only the pure-module tests), the imports fall
back step-by-step through PyQt6, PyQt5, and PySide6, and finally to a
minimal in-process shim that implements just enough of ``QObject`` and
``pyqtSignal`` for unit tests to construct and exercise the bus.
The shim is never used inside Anki itself.
"""

from __future__ import annotations

from typing import Any

from .._qt_compat import QObject, pyqtSignal


class WorkerBus(QObject):
    """Cross-thread signal hub owned by the Qt main thread.

    The bus is constructed by :class:`PickerDialog` (or by tests that
    exercise the orchestrator in isolation) on the main thread and
    passed to every worker that needs to report back. Workers emit
    signals; the dialog and grid view connect slots on the main thread.
    """

    # --- Search / provider results ----------------------------------
    result_ready = pyqtSignal(object)  # ImageResult
    provider_failed = pyqtSignal(str, str)  # provider_id, message

    # --- Thumbnails -------------------------------------------------
    thumbnail_ready = pyqtSignal(str, bytes)  # url, bytes
    thumbnail_failed = pyqtSignal(str, str)  # url, message

    # --- Full-image download ---------------------------------------
    download_progress = pyqtSignal(str, float)  # url, fraction in [0, 1]
    download_complete = pyqtSignal(str, bytes, str)  # url, bytes, extension
    download_failed = pyqtSignal(str, str)  # url, message

    # --- Last-resort -----------------------------------------------
    unhandled_error = pyqtSignal(str)  # message

    # --- Translation (worker -> main thread marshaling) -------------
    translation_ready = pyqtSignal(str, str)  # original, translated

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)


__all__ = ["WorkerBus"]
