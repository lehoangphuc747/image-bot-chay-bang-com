"""Modal picker dialog for the AnkiVN Smart Image Picker add-on.

The :class:`PickerDialog` is the top-level UI component that the user
interacts with after clicking the toolbar button. It owns:

* A :class:`~ankivn_image_picker.ui.worker_bus.WorkerBus` for
  cross-thread signal delivery.
* A :class:`~ankivn_image_picker.orchestrator.SearchOrchestrator` that
  fans out search requests to all configured providers.
* A :class:`~ankivn_image_picker.cancellation.CancellationToken` that
  aborts in-flight work when the dialog closes or a new query is
  submitted.
* A :class:`~ankivn_image_picker.ui.grid_view.GridModel` backing the
  thumbnail grid.
* A ``QLineEdit`` for re-querying (Req 6.5).
* A status bar that surfaces per-provider error indicators (Req 4.5).

Threading
---------
The dialog and all its slots run exclusively on the Qt main thread.
Workers communicate via the bus; the dialog never touches worker-thread
objects directly.

Validates Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 4.6, 6.1, 6.2, 6.3,
6.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5, 10.3, 10.4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ..cancellation import CancellationToken
from ..errors import FieldNotFoundError
from ..filename import derive_filename
from ..logging import get_logger
from ..query import normalize_query
from .grid_view import GridModel
from .worker_bus import WorkerBus

if TYPE_CHECKING:  # pragma: no cover
    from ..cache import ThumbnailCache
    from ..config import Config
    from ..http import HttpClient
    from ..orchestrator import FullImageDownloader, SearchOrchestrator
    from ..providers.base import ImageProvider, ImageResult

# Attempt to import Qt widgets. Inside Anki these come from aqt.qt;
# outside Anki (tests) we fall back gracefully.
try:
    from aqt.qt import (  # type: ignore[import-not-found]
        QCloseEvent,
        QDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListView,
        QListWidget,
        QListWidgetItem,
        QPixmap,
        QSize,
        QVBoxLayout,
        QWidget,
        Qt,
    )
    from aqt.utils import showWarning, tooltip  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - shim for tests without Qt
    # Minimal stubs so the module can be imported for type-checking and
    # basic unit tests that mock the Qt layer.
    class QDialog:  # type: ignore[no-redef]
        def __init__(self, parent: Any = None) -> None:
            self._parent = parent
            self._result = 0

        def setWindowTitle(self, title: str) -> None:
            self._window_title = title

        def accept(self) -> None:
            self._result = 1

        def reject(self) -> None:
            self._result = 0

        def show(self) -> None:
            pass

        def exec(self) -> int:
            return self._result

        def close(self) -> None:
            pass

        def closeEvent(self, event: Any) -> None:
            pass

    class QWidget:  # type: ignore[no-redef]
        pass

    class QVBoxLayout:  # type: ignore[no-redef]
        def __init__(self, parent: Any = None) -> None:
            pass

        def addWidget(self, w: Any) -> None:
            pass

        def addLayout(self, l: Any) -> None:
            pass

    class QHBoxLayout:  # type: ignore[no-redef]
        def __init__(self) -> None:
            pass

        def addWidget(self, w: Any) -> None:
            pass

    class _StubSignal:
        """Minimal signal stub for test environments."""

        def __init__(self) -> None:
            self._slots: list[Any] = []

        def connect(self, slot: Any) -> None:
            self._slots.append(slot)

        def disconnect(self, slot: Any = None) -> None:
            if slot is None:
                self._slots.clear()
            else:
                self._slots = [s for s in self._slots if s is not slot]

        def emit(self, *args: Any) -> None:
            for slot in list(self._slots):
                slot(*args)

    class QLineEdit:  # type: ignore[no-redef]
        def __init__(self, parent: Any = None) -> None:
            self._text = ""
            self.returnPressed = _StubSignal()

        def text(self) -> str:
            return self._text

        def setText(self, t: str) -> None:
            self._text = t

        def setPlaceholderText(self, t: str) -> None:
            pass

        def setToolTip(self, t: str) -> None:
            pass

    class QLabel:  # type: ignore[no-redef]
        def __init__(self, text: str = "", parent: Any = None) -> None:
            self._text = text

        def setText(self, t: str) -> None:
            self._text = t

        def text(self) -> str:
            return self._text

    class QCloseEvent:  # type: ignore[no-redef]
        pass

    class QSize:  # type: ignore[no-redef]
        def __init__(self, w: int = 0, h: int = 0) -> None:
            self._w = w
            self._h = h

    class QPixmap:  # type: ignore[no-redef]
        def __init__(self) -> None:
            pass

        def loadFromData(self, data: Any) -> bool:
            return True

        def isNull(self) -> bool:
            return False

        def scaled(self, *args: Any, **kwargs: Any) -> "QPixmap":
            return self

    class QListWidgetItem:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._text = ""
            self._data: dict[Any, Any] = {}

        def setText(self, t: str) -> None:
            self._text = t

        def setToolTip(self, t: str) -> None:
            pass

        def setSizeHint(self, size: Any) -> None:
            pass

        def setData(self, role: Any, value: Any) -> None:
            self._data[role] = value

        def data(self, role: Any) -> Any:
            return self._data.get(role)

        def setIcon(self, icon: Any) -> None:
            pass

    class QListWidget:  # type: ignore[no-redef]
        class ViewMode:
            IconMode = 1

        class ResizeMode:
            Adjust = 1

        class Movement:
            Static = 0

        class SelectionMode:
            SingleSelection = 1

        def __init__(self, parent: Any = None) -> None:
            self._items: list[Any] = []
            self.itemDoubleClicked = _StubSignal()

        def setViewMode(self, mode: Any) -> None:
            pass

        def setIconSize(self, size: Any) -> None:
            pass

        def setResizeMode(self, mode: Any) -> None:
            pass

        def setWrapping(self, wrap: bool) -> None:
            pass

        def setSpacing(self, spacing: int) -> None:
            pass

        def setMovement(self, movement: Any) -> None:
            pass

        def setSelectionMode(self, mode: Any) -> None:
            pass

        def addItem(self, item: Any) -> None:
            self._items.append(item)

        def item(self, idx: int) -> Any:
            if 0 <= idx < len(self._items):
                return self._items[idx]
            return None

        def count(self) -> int:
            return len(self._items)

        def clear(self) -> None:
            self._items.clear()

    class Qt:  # type: ignore[no-redef]
        class ItemDataRole:
            UserRole = 256

        class AspectRatioMode:
            KeepAspectRatio = 1

        class TransformationMode:
            SmoothTransformation = 1

        class TextFormat:
            RichText = 1
            PlainText = 0
            AutoText = 2

    def showWarning(msg: str, **kwargs: Any) -> None:  # type: ignore[no-redef]
        pass

    def tooltip(msg: str, **kwargs: Any) -> None:  # type: ignore[no-redef]
        pass


_log = get_logger("picker_dialog")


# Module-level state to persist dialog size/maximized between batch iterations
_REMEMBERED_GEOMETRY: Optional[bytes] = None  # QByteArray from saveGeometry()
_REMEMBERED_MAXIMIZED: bool = False
# Persisted checkbox states (default to True the first time the dialog opens)
_REMEMBERED_AUTO_SCROLL: bool = True
_REMEMBERED_INCLUDE_ATTRIBUTION: bool = True
_REMEMBERED_TRANSLATE: Optional[bool] = None  # None = use config default
# Persisted sort mode ("mixed" or "grouped")
_REMEMBERED_SORT_MODE: str = "mixed"


class PickerDialog(QDialog):  # type: ignore[misc]
    """Modal dialog that displays image search results in a grid.

    The dialog is constructed by the toolbar button handler with a
    validated config, the active editor, and the pre-normalised query.
    It owns the full lifecycle of a search: orchestrator fan-out,
    thumbnail streaming, user selection, full-image download, media
    save, and field insertion.

    Parameters
    ----------
    editor:
        The active Anki editor instance. Used to read the source field
        and to insert the image into the target field on selection.
    config:
        Validated add-on configuration.
    query:
        The normalised search query (already stripped and trimmed).
    providers:
        List of provider instances to query.
    http:
        HTTP client for network requests.
    cache:
        Thumbnail cache instance.
    parent:
        Optional parent widget for the dialog.
    """

    def __init__(
        self,
        editor: Any,
        config: "Config",
        query: str,
        providers: list["ImageProvider"],
        http: "HttpClient",
        cache: "ThumbnailCache",
        parent: Any = None,
        prefetched_results: Optional[list] = None,
        prefetched_errors: Optional[dict] = None,
        prefetched_translation: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._config = config
        self._query = query
        self._providers = providers
        self._http = http
        self._cache = cache

        # --- Owned components ---
        self._cancel = CancellationToken()
        self._bus = WorkerBus(self)
        self._grid_model = GridModel()
        self._selection_locked = False
        self._selected_result: Optional["ImageResult"] = None
        self._provider_errors: dict[str, str] = {}
        self._provider_counts: dict[str, int] = {}  # provider_id -> result count
        self._results_received = False

        # Round-robin buffers: per-provider queues for interleaved display.
        # Two-stage pipeline:
        #  1) _pending_results: result metadata received but thumbnail
        #     not yet downloaded. Maps provider_id -> {url: result}.
        #  2) _ready_results: thumbnail data has arrived AND decoded
        #     successfully. Maps provider_id -> [(result, pixmap)].
        # Only stage-2 entries are flushed into the grid widget. Items
        # whose thumbnails fail never enter the grid, so there is no
        # reflow / takeItem cost when providers like Wikimedia return
        # rate-limit errors for individual files.
        self._pending_results: dict[str, dict[str, Any]] = {}
        self._ready_results: dict[str, list] = {}
        # Sort mode: "mixed" (round-robin) or "grouped" (by provider).
        # Initialised from the persisted module-level state so the user's
        # preference carries across batch notes.
        self._sort_mode: str = _REMEMBERED_SORT_MODE

        # Thumbnail loading progress tracking
        self._thumbnails_total: int = 0  # incremented on each result_ready
        self._thumbnails_loaded: int = 0  # success count
        self._thumbnails_failed: int = 0  # failed/hidden count

        # Map thumbnail URL -> QListWidgetItem so we can find/remove
        # items even after the widget reflows (which would invalidate
        # row indices). This decouples item lookup from row position.
        self._url_to_item: dict[str, Any] = {}

        # --- UI setup ---
        self._setup_ui()
        self._connect_signals()

        # --- Show query in title (Req 6.1) ---
        self.setWindowTitle(f"Image Picker — {self._query}")
        try:
            # Allow maximize button on the dialog
            from aqt.qt import Qt as QtCore  # type: ignore[import-not-found]
            self.setWindowFlags(
                self.windowFlags()
                | QtCore.WindowType.WindowMaximizeButtonHint
                | QtCore.WindowType.WindowMinimizeButtonHint
            )

            # Apply remembered geometry from previous dialog instance.
            # Using saveGeometry/restoreGeometry preserves both size and
            # maximized state in one shot, avoiding the flicker caused
            # by resize-then-maximize.
            global _REMEMBERED_GEOMETRY, _REMEMBERED_MAXIMIZED
            if _REMEMBERED_GEOMETRY is not None:
                self.restoreGeometry(_REMEMBERED_GEOMETRY)
            else:
                self.resize(900, 650)
        except Exception:
            pass

        # Track whether user explicitly skipped (vs closing/aborting)
        self._skipped = False

        # Track current pagination page for "Load More"
        self._current_page = 1

        # Effective query (translated if applicable). Updated by
        # _on_translation_done; defaults to the original query.
        self._effective_query: str = self._query

        # Setup round-robin flush timer (drains pending buffers every 100ms)
        try:
            from aqt.qt import QTimer  # type: ignore[import-not-found]
            self._flush_timer = QTimer(self)
            self._flush_timer.setInterval(100)  # 100ms
            self._flush_timer.timeout.connect(self._flush_pending_buffers)
            self._flush_timer.start()
        except Exception:
            self._flush_timer = None

        # --- Use prefetched results or start fresh search ---
        if prefetched_results:
            # Replay any prefetch-time provider errors so the status bar
            # shows them (e.g. Unsplash rate-limit during background search).
            if prefetched_errors:
                for pid, msg in prefetched_errors.items():
                    self._on_provider_failed(pid, msg)

            # If prefetch translated the query, reflect that in the
            # search box and translation label immediately.
            if prefetched_translation and prefetched_translation != self._query:
                self._effective_query = prefetched_translation
                try:
                    self.setWindowTitle(
                        f"Image Picker — {self._query} → {prefetched_translation}"
                    )
                    self._search_input.blockSignals(True)
                    self._search_input.setText(prefetched_translation)
                    self._search_input.blockSignals(False)
                    self._translate_label.setText(
                        f"🌐 Translated: {self._query} → {prefetched_translation}"
                    )
                    self._translate_label.setVisible(True)
                except Exception:
                    pass

            # Fast-path: load all prefetched results synchronously.
            # Cached thumbnails are read straight from disk and pushed
            # into _ready_results, bypassing the worker-thread round-trip.
            self._load_prefetched_synchronously(prefetched_results)

            # Drain the entire ready buffer at once so cached items are
            # rendered immediately when the dialog opens (no flush-tick
            # delay). _flush_pending_buffers normally batches 40/tick;
            # here we want everything visible right away.
            self._flush_all_ready()

            # Kick off background downloads only for results whose
            # thumbnails weren't already cached. _load_prefetched_synchronously
            # leaves cache misses in _pending_results so we can detect them.
            uncached = []
            for result in prefetched_results:
                pending = self._pending_results.get(result.provider_id, {})
                if result.thumbnail_url in pending:
                    uncached.append(result)
            if uncached:
                self._start_thumbnail_downloads(uncached)
        else:
            self._start_search(self._query)

    def _setup_ui(self) -> None:
        """Build the dialog layout: search bar, grid, status bar."""
        layout = QVBoxLayout(self)

        # Source field label: shows the original field name + value
        self._source_label = QLabel(
            f"<b>{self._config.source_field}</b>: "
            f"<i>{self._query}</i>",
            self,
        )
        self._source_label.setToolTip(
            f"Original text from the '{self._config.source_field}' field."
        )
        try:
            self._source_label.setTextFormat(Qt.TextFormat.RichText)
        except Exception:
            pass
        layout.addWidget(self._source_label)

        # Search bar row
        search_layout = QHBoxLayout()

        self._search_input = QLineEdit(self)
        self._search_input.setText(self._query)
        self._search_input.setPlaceholderText(
            "Edit and press Enter or click Search..."
        )
        search_layout.addWidget(self._search_input)

        # Explicit Search button
        try:
            from aqt.qt import QPushButton  # type: ignore[import-not-found]
            self._search_button = QPushButton("🔍 Search", self)
            self._search_button.setToolTip("Search with the text in the box")
            self._search_button.clicked.connect(self._on_requery)
            search_layout.addWidget(self._search_button)

            # Translate button: manual translate regardless of checkbox
            self._translate_button = QPushButton("🌐 Translate", self)
            self._translate_button.setToolTip(
                "Translate the text in the search box to English,\n"
                "then search with the translated text.\n"
                "Works even if the auto-translate checkbox is off."
            )
            self._translate_button.clicked.connect(self._on_translate_clicked)
            search_layout.addWidget(self._translate_button)
        except Exception:
            self._search_button = None
            self._translate_button = None

        # Sort mode toggle
        try:
            from aqt.qt import QComboBox  # type: ignore[import-not-found]
            self._sort_combo = QComboBox(self)
            self._sort_combo.addItem("Mixed 🔀", "mixed")
            self._sort_combo.addItem("Grouped 📁", "grouped")
            self._sort_combo.setToolTip(
                "Mixed: interleave results from all providers\n"
                "Grouped: show all results from one provider before the next"
            )
            for i in range(self._sort_combo.count()):
                if self._sort_combo.itemData(i) == _REMEMBERED_SORT_MODE:
                    self._sort_combo.setCurrentIndex(i)
                    break
            self._sort_combo.currentIndexChanged.connect(self._on_sort_mode_changed)
            search_layout.addWidget(self._sort_combo)
        except Exception:
            self._sort_combo = None

        layout.addLayout(search_layout)

        # Translation info label (only visible when translate is active)
        self._translate_label = QLabel("", self)
        try:
            self._translate_label.setStyleSheet(
                "color: #666; font-size: 11px; padding-left: 4px;"
            )
        except Exception:
            pass
        self._translate_label.setVisible(False)
        layout.addWidget(self._translate_label)

        # Thumbnail grid using QListWidget in icon mode
        self._grid_widget = QListWidget(self)
        self._grid_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid_widget.setIconSize(QSize(150, 150))
        self._grid_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid_widget.setWrapping(True)
        self._grid_widget.setSpacing(8)
        self._grid_widget.setMovement(QListWidget.Movement.Static)
        self._grid_widget.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self._grid_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._grid_widget)

        # Status label (shows "Searching..." / result count / errors)
        self._grid_label = QLabel("Searching...", self)
        layout.addWidget(self._grid_label)

        # Bottom bar: skip button + status
        bottom_layout = QHBoxLayout()

        # Skip button for batch mode
        try:
            from aqt.qt import QPushButton, QCheckBox  # type: ignore[import-not-found]
            self._skip_button = QPushButton("Skip ⏭", self)
            self._skip_button.setToolTip("Skip this note and move to the next one")
            self._skip_button.clicked.connect(self._on_skip_clicked)
            bottom_layout.addWidget(self._skip_button)

            # Load More button to fetch next page from all providers
            self._load_more_button = QPushButton("Load More 🔄", self)
            self._load_more_button.setToolTip(
                "Load more images from all providers"
            )
            self._load_more_button.clicked.connect(self._on_load_more_clicked)
            bottom_layout.addWidget(self._load_more_button)

            # Auto-scroll checkbox
            self._auto_scroll_checkbox = QCheckBox("Auto load on scroll", self)
            self._auto_scroll_checkbox.setChecked(_REMEMBERED_AUTO_SCROLL)
            self._auto_scroll_checkbox.setToolTip(
                "When checked, scrolling near the bottom of the grid\n"
                "automatically fetches more images.\n"
                "Uncheck if you prefer to use the Load More button only."
            )
            self._auto_scroll_checkbox.toggled.connect(
                self._on_auto_scroll_toggled
            )
            bottom_layout.addWidget(self._auto_scroll_checkbox)

            # Translate checkbox
            self._translate_checkbox = QCheckBox(
                "Translate keyword to English", self
            )
            # Initial state: persisted choice, or config default
            if _REMEMBERED_TRANSLATE is not None:
                init_translate = _REMEMBERED_TRANSLATE
            else:
                init_translate = bool(
                    getattr(self._config, "translate_to_english", True)
                )
            self._translate_checkbox.setChecked(init_translate)
            self._translate_checkbox.setToolTip(
                "Auto-translate non-English queries (Vietnamese,\n"
                "Korean, Japanese, etc) to English before searching.\n"
                "Greatly improves results from Unsplash and Pexels.\n"
                "Uses Google Translate's free endpoint."
            )
            self._translate_checkbox.toggled.connect(
                self._on_translate_toggled
            )
            bottom_layout.addWidget(self._translate_checkbox)

            # Attribution checkbox (required for Unsplash compliance)
            self._attribution_checkbox = QCheckBox("Include attribution", self)
            self._attribution_checkbox.setChecked(_REMEMBERED_INCLUDE_ATTRIBUTION)
            self._attribution_checkbox.setToolTip(
                "Include 'Photo by [Author] on [Provider]' attribution.\n"
                "Required by Unsplash API guidelines."
            )
            self._attribution_checkbox.toggled.connect(
                self._on_attribution_toggled
            )
            bottom_layout.addWidget(self._attribution_checkbox)
        except Exception:
            self._attribution_checkbox = None
            self._auto_scroll_checkbox = None
            self._translate_checkbox = None
            pass  # Test environment without aqt

        # Provider status bar (Req 4.5 error indicators)
        self._status_label = QLabel("", self)
        bottom_layout.addWidget(self._status_label)

        layout.addLayout(bottom_layout)

        # Auto-scroll: detect when user scrolls near the bottom and load more
        try:
            scroll_bar = self._grid_widget.verticalScrollBar()
            if scroll_bar is not None:
                scroll_bar.valueChanged.connect(self._on_scroll)
        except Exception:
            pass

    def _connect_signals(self) -> None:
        """Wire bus signals to dialog/grid slots."""
        # Search results -> grid model
        self._bus.result_ready.connect(self._on_result_ready)
        self._bus.provider_failed.connect(self._on_provider_failed)

        # Thumbnails -> grid model + UI refresh
        self._bus.thumbnail_ready.connect(self._on_thumbnail_ready_ui)
        self._bus.thumbnail_failed.connect(self._on_thumbnail_failed_ui)

        # Full-image download
        self._bus.download_progress.connect(self._on_download_progress)
        self._bus.download_complete.connect(self._on_download_complete)
        self._bus.download_failed.connect(self._on_download_failed)

        # Translation completion (marshaled from worker thread)
        self._bus.translation_ready.connect(self._on_translation_done)

        # Unhandled errors (Req 10.3)
        self._bus.unhandled_error.connect(self._on_unhandled_error)

        # Re-query on Enter in search bar (Req 6.5)
        self._search_input.returnPressed.connect(self._on_requery)

    def _start_search(self, query: str) -> None:
        """Create a fresh orchestrator and fan out to all providers.

        If ``config.translate_to_english`` is True and the query is
        not English-looking, translate it before dispatching. The
        translation runs on a worker thread to avoid blocking the UI.
        """
        from ..orchestrator import SearchOrchestrator

        # Decide whether to translate based on config + the persisted
        # checkbox state (if available).
        should_translate = bool(
            getattr(self._config, "translate_to_english", True)
        )
        try:
            if self._translate_checkbox is not None:
                should_translate = self._translate_checkbox.isChecked()
        except Exception:
            pass

        # Run translation off the UI thread, then start the orchestrator.
        if should_translate:
            self._dispatch_search_with_translation(query)
        else:
            self._dispatch_search(query)

    def _dispatch_search(self, query: str, page: int = 1) -> None:
        """Run the orchestrator with the (already-prepared) query."""
        from ..orchestrator import SearchOrchestrator

        self._orchestrator = SearchOrchestrator(
            providers=self._providers,
            cfg=self._config,
            http=self._http,
            cache=self._cache,
            bus=self._bus,
            cancel=self._cancel,
        )
        self._orchestrator.run(query, page=page)

    def _dispatch_search_with_translation(self, query: str) -> None:
        """Translate the query in a worker thread, then dispatch search.

        Translation runs on a thread pool worker. The result is
        marshaled back to the UI thread via the bus's
        ``translation_ready`` signal (Qt's queued connection
        delivers it on the main thread automatically).
        """
        from ..translator import looks_like_english, translate_to_english

        # Skip the worker hop if the query is already plain ASCII —
        # most likely English already.
        if looks_like_english(query):
            self._dispatch_search(query)
            return

        # Run translation on a one-shot worker. When done, emit the
        # bus signal which is handled on the main thread.
        import concurrent.futures

        def _do_translate() -> None:
            try:
                translated = translate_to_english(
                    query, http=self._http, cancel=self._cancel
                )
            except Exception as exc:
                _log.debug("Translate failed, using original: %s", exc)
                translated = query
            try:
                self._bus.translation_ready.emit(query, translated)
            except Exception:
                pass

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pool.submit(_do_translate)

    def _on_translation_done(self, original: str, translated: str) -> None:
        """Main-thread callback after translation completes.

        Updates the search bar to show the translated text, and shows
        a small label below the search bar indicating the translation.
        Then dispatches the actual search with the translated query.
        """
        self._effective_query = translated or original

        if translated and translated != original:
            try:
                self.setWindowTitle(
                    f"Image Picker — {original} → {translated}"
                )
                # Update search bar to show what's actually being searched
                self._search_input.blockSignals(True)
                self._search_input.setText(translated)
                self._search_input.blockSignals(False)
                # Show translation label
                self._translate_label.setText(
                    f"🌐 Translated: {original} → {translated}"
                )
                self._translate_label.setVisible(True)
            except Exception:
                pass
            self._dispatch_search(translated)
        else:
            # No translation happened (already English or same text)
            try:
                self._translate_label.setVisible(False)
            except Exception:
                pass
            self._dispatch_search(original)

    def _start_thumbnail_downloads(self, results: list) -> None:
        """Kick off thumbnail downloads for prefetched results.

        When results come from the prefetch cache, the orchestrator
        didn't run so thumbnails weren't downloaded. We schedule them
        here using the same mechanism the orchestrator uses.
        """
        import concurrent.futures

        from ..orchestrator import ThumbnailDownloader

        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(results), 4) if results else 1
        )
        for result in results:
            downloader = ThumbnailDownloader(
                cache=self._cache,
                http=self._http,
                bus=self._bus,
                cancel=self._cancel,
            )
            pool.submit(downloader.fetch, result)

    def _load_prefetched_synchronously(self, results: list) -> None:
        """Fast-path: load prefetched results, decode cached thumbnails inline.

        For results whose thumbnails are already in the on-disk cache
        (the common case after batch-mode prefetching), decode the
        bytes synchronously on the main thread and push directly into
        ``_ready_results``. This bypasses the worker-thread round-trip
        and the thumbnail signal/slot bus, eliminating the visible lag
        when navigating between batch notes.

        Results without a cached thumbnail are left in
        ``_pending_results`` so the caller can schedule background
        downloads for them.

        Decode budget: only inline-decode the first ~30 cached items
        (enough to fill what the user sees on screen). The rest are
        emitted via a worker so we don't block the main thread for
        seconds when there are 180 prefetched items to decode.
        """
        # Hard cap on how many pixmaps we decode synchronously to keep
        # dialog opening snappy. Roughly the number of cells visible
        # in a 900x650 dialog.
        INLINE_DECODE_CAP = 30

        decoded_inline = 0
        late_decode: list = []

        for result in results:
            self._results_received = True
            pid = result.provider_id
            self._provider_counts[pid] = self._provider_counts.get(pid, 0) + 1
            self._thumbnails_total += 1

            cached = self._cache.get(result.thumbnail_url)
            if cached is None:
                # Cache miss — record as pending; background download
                # will populate via the signal bus as usual.
                self._pending_results.setdefault(pid, {})[
                    result.thumbnail_url
                ] = result
                continue

            if decoded_inline < INLINE_DECODE_CAP:
                # Cache hit AND within the inline budget — decode now
                try:
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(cached) or pixmap.isNull():
                        self._thumbnails_failed += 1
                        continue
                    self._ready_results.setdefault(pid, []).append(
                        (result, pixmap)
                    )
                    self._thumbnails_loaded += 1
                    decoded_inline += 1
                except Exception as exc:
                    _log.debug("Inline thumbnail decode failed: %s", exc)
                    self._thumbnails_failed += 1
            else:
                # Over the inline cap — decode on worker thread shortly
                self._pending_results.setdefault(pid, {})[
                    result.thumbnail_url
                ] = result
                late_decode.append(result)

        self._update_status_bar()

        # Schedule a background pass to emit cached-but-not-yet-decoded
        # thumbnails through the bus. This keeps decoding off the main
        # thread for items the user can't see yet.
        if late_decode:
            self._schedule_late_decode(late_decode)

    def _schedule_late_decode(self, results: list) -> None:
        """Background-decode cached thumbnails for items beyond the inline cap.

        Reads bytes from cache and emits ``thumbnail_ready`` via the bus
        so the existing main-thread slot picks them up at the normal
        flush-tick cadence. Decoding the actual QPixmap still happens
        on the main thread (Qt requirement) but it's interleaved with
        the flush timer rather than blocking dialog opening.
        """
        import concurrent.futures

        def _emit_cached(result: "ImageResult") -> None:
            try:
                cached = self._cache.get(result.thumbnail_url)
                if cached is None:
                    return  # shouldn't happen — was a cache hit a moment ago
                self._bus.thumbnail_ready.emit(result.thumbnail_url, cached)
            except Exception:
                pass

        # Tiny pool — we're only doing disk reads, not network IO
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        for r in results:
            pool.submit(_emit_cached, r)

    # ------------------------------------------------------------------
    # Bus signal handlers
    # ------------------------------------------------------------------

    def _on_result_ready(self, result: "ImageResult") -> None:
        """Receive a search result. Queue it pending thumbnail download.

        The result does NOT enter the grid yet. We wait for its
        thumbnail to arrive (and decode successfully) before adding
        the item. Items whose thumbnails fail are silently dropped,
        keeping the grid free of empty slots and avoiding reflow
        churn from `takeItem` calls.
        """
        self._results_received = True

        # Track per-provider count
        pid = result.provider_id
        self._provider_counts[pid] = self._provider_counts.get(pid, 0) + 1

        # Track thumbnail loading total (one per result we expect)
        self._thumbnails_total += 1

        # Stash the metadata for use when the thumbnail arrives. We
        # key on thumbnail_url so _on_thumbnail_ready can find the
        # matching ImageResult quickly.
        self._pending_results.setdefault(pid, {})[result.thumbnail_url] = result

        self._update_status_bar()

    def _flush_pending_buffers(self) -> None:
        """Drain ready buffers (post-thumbnail) into the grid widget.

        Called periodically by the flush timer. The pending buffer is
        populated by `_on_thumbnail_ready_ui` only after a thumbnail
        decodes successfully. Round-robin or grouped order is applied
        here at flush time.
        """
        if not self._ready_results:
            return

        if self._sort_mode == "grouped":
            self._flush_grouped()
        else:
            self._flush_roundrobin()

    def _flush_all_ready(self) -> None:
        """Drain every ready item right now, ignoring the per-tick cap.

        Used when opening a dialog from prefetched cache: all the
        cached thumbnails are decoded synchronously, and we want them
        to appear in the grid before the dialog becomes visible.
        Calling _flush_pending_buffers in a loop until empty is the
        simplest way to apply round-robin / grouped ordering without
        duplicating logic.
        """
        # Bound the loop to avoid pathological infinite cycles if a
        # flush helper stops draining for some reason.
        for _ in range(50):
            if not self._ready_results or not any(
                self._ready_results.values()
            ):
                return
            if self._sort_mode == "grouped":
                self._flush_grouped(items_per_tick=1000)
            else:
                self._flush_roundrobin(items_per_tick=1000)

    def _flush_roundrobin(self, items_per_tick: int = 40) -> None:
        """Drain in round-robin: 1 item from each provider per pass."""
        added = 0
        max_per_tick = items_per_tick

        while added < max_per_tick:
            any_added = False
            for pid in list(self._ready_results.keys()):
                buf = self._ready_results.get(pid)
                if buf:
                    result, pixmap = buf.pop(0)
                    self._add_ready_item(result, pixmap)
                    added += 1
                    any_added = True
                    if added >= max_per_tick:
                        break
            if not any_added:
                break

        if added > 0:
            self._update_grid_display()

    def _flush_grouped(self, items_per_tick: int = 40) -> None:
        """Drain grouped: empty one provider's buffer fully before next."""
        added = 0
        for pid in list(self._ready_results.keys()):
            buf = self._ready_results.get(pid)
            if not buf:
                continue
            while buf and added < items_per_tick:
                result, pixmap = buf.pop(0)
                self._add_ready_item(result, pixmap)
                added += 1
            if added >= items_per_tick:
                break

        if added > 0:
            self._update_grid_display()

    def _add_ready_item(self, result: "ImageResult", pixmap: Any) -> None:
        """Add a result whose thumbnail is already loaded to the grid."""
        from ..attribution import build_attribution_text

        attribution = build_attribution_text(result)

        try:
            from aqt.qt import QIcon  # type: ignore[import-not-found]

            item = QListWidgetItem()
            tooltip_text = f"{attribution}\n{result.full_url}"
            item.setToolTip(tooltip_text)
            item.setSizeHint(QSize(160, 160))
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setIcon(QIcon(pixmap.scaled(
                150, 150,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )))
            self._grid_widget.addItem(item)
            # Register so we can find the item later if needed
            self._url_to_item[result.thumbnail_url] = item
        except Exception:
            pass

    def _on_thumbnail_ready_ui(self, url: str, data: bytes) -> None:
        """Thumbnail arrived. Decode and queue for grid insertion."""
        self._grid_model.on_thumbnail_ready(url, data)

        # Find the matching pending result
        result = self._take_pending_by_url(url)
        if result is None:
            self._thumbnails_loaded += 1
            self._update_status_bar()
            return

        try:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data) or pixmap.isNull():
                # Bytes weren't a decodable image — drop silently
                self._thumbnails_failed += 1
                self._update_status_bar()
                return

            # Queue the (result, pixmap) pair for the next flush tick
            self._ready_results.setdefault(
                result.provider_id, []
            ).append((result, pixmap))
            self._thumbnails_loaded += 1
        except Exception as exc:
            _log.debug("Failed to decode thumbnail: %s", exc)
            self._thumbnails_failed += 1

        self._update_status_bar()

    def _on_thumbnail_failed_ui(self, url: str, message: str) -> None:
        """Thumbnail download failed. Drop the result entirely."""
        self._grid_model.on_thumbnail_failed(url, message)
        self._thumbnails_failed += 1
        self._update_status_bar()

        # Remove from pending so it never enters the grid
        self._take_pending_by_url(url)

    def _take_pending_by_url(self, url: str) -> Optional["ImageResult"]:
        """Pop a pending result matching ``url`` (no matter which provider)."""
        for pid, mapping in self._pending_results.items():
            if url in mapping:
                return mapping.pop(url)
        return None

    def _on_item_double_clicked(self, item: Any) -> None:
        """Handle double-click on a grid item to select the image."""
        if self._selection_locked:
            return

        try:
            # ImageResult is stored directly on the item via UserRole.
            # This is robust against row reflows caused by removing
            # failed items.
            result = item.data(Qt.ItemDataRole.UserRole)
            if result is not None:
                self.on_image_clicked(result)
        except Exception as exc:
            _log.debug("Failed to handle item click: %s", exc)

    def _on_provider_failed(self, provider_id: str, message: str) -> None:
        """Record a provider error and update the status bar (Req 4.5)."""
        self._provider_errors[provider_id] = message
        self._update_status_bar()

        # Check if all providers have failed (Req 4.6)
        if (
            len(self._provider_errors) == len(self._providers)
            and not self._results_received
        ):
            self._show_empty_state(
                "No results retrieved — all providers failed."
            )

    def _on_download_progress(self, url: str, fraction: float) -> None:
        """Update progress on the selected cell (Req 7.2)."""
        self._grid_model.on_download_progress(url, fraction)
        self._update_grid_display()

    def _on_download_complete(
        self, url: str, image_bytes: bytes, extension: str
    ) -> None:
        """Save image to media and insert into target field (Req 7.3).

        On success, closes the dialog via accept().
        """
        from .. import editor_bridge
        from ..attribution import build_attribution_html

        try:
            # Derive a unique filename (Req 8.2, 8.3)
            try:
                mw_ref = editor_bridge.mw
                taken_fn = mw_ref.col.media.have
            except Exception:
                # Fallback for environments where mw is not fully wired
                taken_fn = lambda name: False  # noqa: E731

            filename = derive_filename(
                self._query, extension, taken=taken_fn
            )

            # Save to media (Req 8.1, 8.4)
            used_filename = editor_bridge.save_to_media(filename, image_bytes)

            # Build attribution HTML (required for Unsplash compliance)
            attribution_html: Optional[str] = None
            if self._selected_result is not None:
                # Check if user wants to include attribution
                include_attr = True
                if self._attribution_checkbox is not None:
                    try:
                        include_attr = self._attribution_checkbox.isChecked()
                    except Exception:
                        include_attr = True
                # Always include for Unsplash (required by their guidelines)
                if self._selected_result.provider_id == "unsplash":
                    include_attr = True
                if include_attr:
                    attribution_html = build_attribution_html(self._selected_result)

            # Insert into target field (Req 9.1, 9.2, 9.3, 9.5)
            editor_bridge.insert_image(
                self._editor,
                self._config.target_field,
                used_filename,
                attribution_html=attribution_html,
            )

            # Remember size/maximized for next dialog instance
            self._remember_state()

            # Close the dialog on success (Req 7.3)
            self.accept()

        except FieldNotFoundError as exc:
            _log.error("Target field not found: %s", exc)
            tooltip(
                f"Target field '{self._config.target_field}' not found on "
                f"this note type."
            )
        except Exception as exc:
            _log.exception("Failed to save/insert image: %s", exc)
            tooltip(f"Failed to save image: {exc}")

    def _on_download_failed(self, url: str, message: str) -> None:
        """Show error and keep dialog open for another selection (Req 7.4)."""
        self._selection_locked = False
        tooltip(f"Download failed: {message}")
        _log.warning("Download failed for %s: %s", url, message)

    def _on_unhandled_error(self, message: str) -> None:
        """Log and surface unhandled errors (Req 10.3)."""
        _log.error("Unhandled error: %s", message)
        try:
            from aqt.utils import showCritical  # type: ignore[import-not-found]
            showCritical(f"Image Picker error: {message}")
        except Exception:
            tooltip(f"Error: {message}")

    # ------------------------------------------------------------------
    # Re-query handling (Req 6.5)
    # ------------------------------------------------------------------

    def _on_requery(self) -> None:
        """Handle re-query: clear grid and re-run with new query.

        If "Translate keyword to English" is checked, the text in the
        search box is translated first. The search bar is then updated
        to show the translated text, and the translation label shows
        the mapping. If unchecked, the text is used as-is.
        """
        raw_text = self._search_input.text()
        new_query = normalize_query(raw_text)

        if not new_query:
            self._search_input.setToolTip("source field is empty")
            tooltip("Search query is empty.")
            return

        # Cancel in-flight work from the previous query
        self._cancel.cancel()

        # Create a fresh cancellation token for the new search
        self._cancel = CancellationToken()

        # Clear the grid model (Req 6.5 / Property 10)
        self._grid_model.clear()
        self._grid_widget.clear()
        self._url_to_item.clear()
        self._provider_errors.clear()
        self._provider_counts.clear()
        self._pending_results.clear()
        self._ready_results.clear()
        self._thumbnails_total = 0
        self._thumbnails_loaded = 0
        self._thumbnails_failed = 0
        self._current_page = 1
        self._results_received = False
        self._selection_locked = False
        self._selected_result = None

        # Check if translate is enabled
        should_translate = False
        try:
            if self._translate_checkbox is not None:
                should_translate = self._translate_checkbox.isChecked()
        except Exception:
            pass

        if should_translate:
            # Translate the user's input, then search with the result.
            # _on_translation_done will update the search bar and label.
            self._query = new_query  # remember original for label
            self._start_search(new_query)
        else:
            # Search directly with what the user typed
            self._query = new_query
            self._effective_query = new_query
            self.setWindowTitle(f"Image Picker — {self._query}")
            self._translate_label.setVisible(False)
            self._dispatch_search(self._query)

        self._update_grid_display()

    # ------------------------------------------------------------------
    # Image selection handling (Req 7.1)
    # ------------------------------------------------------------------

    def on_image_clicked(self, result: "ImageResult") -> None:
        """Handle user clicking an image in the grid.

        Disables further selection, shows progress on the chosen cell,
        and kicks off the full-image download (Req 7.1, 7.2).
        """
        if self._selection_locked:
            return

        self._selection_locked = True
        self._selected_result = result

        # Mark the cell as downloading
        for cell in self._grid_model.rows:
            if cell.result is result:
                cell.state = "downloading"
                cell.progress = 0.0
                break

        self._update_grid_display()

        # Kick off full-image download
        from ..orchestrator import FullImageDownloader

        downloader = FullImageDownloader(
            http=self._http,
            bus=self._bus,
            cancel=self._cancel,
        )

        # Run the download on a background thread
        import concurrent.futures

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pool.submit(downloader.fetch, result)

    # ------------------------------------------------------------------
    # UI update helpers
    # ------------------------------------------------------------------

    def _update_grid_display(self) -> None:
        """Update the status label with current state."""
        count = self._grid_model.row_count()
        if count == 0:
            self._grid_label.setText("Searching...")
        else:
            self._grid_label.setText(
                f"{count} results — double-click an image to select"
            )

    def _update_status_bar(self) -> None:
        """Update the status bar with per-provider counts, errors, and progress."""
        parts = []

        # Show counts for providers that returned results
        for pid, count in self._provider_counts.items():
            parts.append(f"{pid}: {count}")

        # Show errors for providers that failed
        for pid, msg in self._provider_errors.items():
            if pid not in self._provider_counts:
                parts.append(f"{pid}: ❌")

        if parts:
            total = sum(self._provider_counts.values())

            # Thumbnail loading progress
            done = self._thumbnails_loaded + self._thumbnails_failed
            visible = self._thumbnails_loaded
            if self._thumbnails_total > 0:
                pct = int(100 * done / self._thumbnails_total)
                if done < self._thumbnails_total:
                    progress_str = (
                        f" | 🖼 {visible} shown · "
                        f"{done}/{self._thumbnails_total} ({pct}%)"
                    )
                    if self._thumbnails_failed > 0:
                        progress_str += f" · {self._thumbnails_failed} hidden"
                else:
                    progress_str = f" | 🖼 {visible} shown ✅"
                    if self._thumbnails_failed > 0:
                        progress_str += f" · {self._thumbnails_failed} hidden"
            else:
                progress_str = ""

            self._status_label.setText(
                f"Total: {total} | " + " · ".join(parts) + progress_str
            )
        else:
            self._status_label.setText("")

    def _show_empty_state(self, message: str) -> None:
        """Show an empty-state message when no results are available."""
        self._grid_label.setText(message)
        tooltip(message)

    # ------------------------------------------------------------------
    # Dialog lifecycle
    # ------------------------------------------------------------------

    def _remember_state(self) -> None:
        """Save current geometry (size + maximized) for the next dialog instance."""
        global _REMEMBERED_GEOMETRY, _REMEMBERED_MAXIMIZED
        try:
            # saveGeometry returns a QByteArray that captures size,
            # position, and maximized/normal state in one blob.
            geom = self.saveGeometry()
            if geom is not None:
                _REMEMBERED_GEOMETRY = bytes(geom)
            _REMEMBERED_MAXIMIZED = bool(self.isMaximized())
        except Exception:
            pass

    def closeEvent(self, event: Any) -> None:
        """Cancel in-flight operations before closing (Req 10.4).

        Closing the dialog (X button) means ABORT the batch, not skip.
        """
        # Remember size/maximized for next dialog instance
        self._remember_state()
        # Stop the flush timer
        try:
            if self._flush_timer is not None:
                self._flush_timer.stop()
        except Exception:
            pass
        self._cancel.cancel()
        super().closeEvent(event)

    def _on_skip_clicked(self) -> None:
        """Handle Skip button: mark as skipped and close dialog."""
        self._remember_state()
        self._skipped = True
        self.reject()

    def _on_load_more_clicked(self) -> None:
        """Load the next page of results from all providers."""
        self._load_more()

    def _on_sort_mode_changed(self, index: int) -> None:
        """Handle sort mode change between Mixed and Grouped."""
        global _REMEMBERED_SORT_MODE
        try:
            mode = self._sort_combo.itemData(index)
            if mode in ("mixed", "grouped"):
                self._sort_mode = mode
                _REMEMBERED_SORT_MODE = mode
                # Re-flush remaining buffers under new mode
                # (already-displayed items stay in place; buffer order
                # changes for upcoming items only)
        except Exception:
            pass

    def _on_translate_clicked(self) -> None:
        """Manual translate button: translate search box text and search.

        Works regardless of the auto-translate checkbox state. This
        lets the user translate on-demand without enabling auto for
        every note.
        """
        raw_text = self._search_input.text()
        new_query = normalize_query(raw_text)

        if not new_query:
            tooltip("Search query is empty.")
            return

        # Cancel in-flight work
        self._cancel.cancel()
        self._cancel = CancellationToken()

        # Clear grid
        self._grid_model.clear()
        self._grid_widget.clear()
        self._url_to_item.clear()
        self._provider_errors.clear()
        self._provider_counts.clear()
        self._pending_results.clear()
        self._ready_results.clear()
        self._thumbnails_total = 0
        self._thumbnails_loaded = 0
        self._thumbnails_failed = 0
        self._current_page = 1
        self._results_received = False
        self._selection_locked = False
        self._selected_result = None

        # Force translate (bypass checkbox check) then search
        self._query = new_query
        self._dispatch_search_with_translation(new_query)
        self._update_grid_display()

    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        """Persist the auto-scroll preference for next dialog instance."""
        global _REMEMBERED_AUTO_SCROLL
        _REMEMBERED_AUTO_SCROLL = bool(checked)

    def _on_attribution_toggled(self, checked: bool) -> None:
        """Persist the attribution preference for next dialog instance."""
        global _REMEMBERED_INCLUDE_ATTRIBUTION
        _REMEMBERED_INCLUDE_ATTRIBUTION = bool(checked)

    def _on_translate_toggled(self, checked: bool) -> None:
        """Persist the translation preference for next dialog instance."""
        global _REMEMBERED_TRANSLATE
        _REMEMBERED_TRANSLATE = bool(checked)

    def _load_more(self) -> None:
        """Internal: fetch next page from all providers."""
        self._current_page += 1
        try:
            # Use the effective query (translated if applicable) so
            # Load More fetches more of the same kind of results.
            query = getattr(self, "_effective_query", self._query)
            self._dispatch_search(query, page=self._current_page)
            self._grid_label.setText(
                f"Loading page {self._current_page}... "
                f"({self._grid_model.row_count()} results so far)"
            )
        except Exception as exc:
            _log.exception("Failed to load more: %s", exc)

    def _on_scroll(self, value: int) -> None:
        """Auto-load more when user scrolls near the bottom."""
        # Respect the auto-scroll checkbox
        try:
            if self._auto_scroll_checkbox is not None:
                if not self._auto_scroll_checkbox.isChecked():
                    return
        except Exception:
            pass

        try:
            scroll_bar = self._grid_widget.verticalScrollBar()
            if scroll_bar is None:
                return
            maximum = scroll_bar.maximum()
            # Trigger when within 80% of bottom
            if maximum > 0 and value >= maximum * 0.8:
                # Throttle: only auto-load if we haven't loaded recently
                if not getattr(self, "_loading_more", False):
                    self._loading_more = True
                    self._load_more()
                    # Reset the flag after a short delay so subsequent
                    # scrolls can trigger another load
                    try:
                        from aqt.qt import QTimer  # type: ignore[import-not-found]
                        QTimer.singleShot(2000, lambda: setattr(self, "_loading_more", False))
                    except Exception:
                        self._loading_more = False
        except Exception:
            pass

    @property
    def was_skipped(self) -> bool:
        """Return True if the user clicked Skip (vs closing/aborting)."""
        return self._skipped

    # ------------------------------------------------------------------
    # Class methods for validation at open time
    # ------------------------------------------------------------------

    @staticmethod
    def validate_and_open(
        editor: Any,
        config: "Config",
        providers: list["ImageProvider"],
        http: "HttpClient",
        cache: "ThumbnailCache",
        parent: Any = None,
    ) -> Optional["PickerDialog"]:
        """Validate the source field and open the picker if valid.

        This is the recommended entry point for opening the dialog. It
        performs all pre-open validation:

        1. Checks that the source field exists on the note type
           (Req 3.4).
        2. Reads and normalises the query from the source field
           (Req 3.1, 3.2, 3.3).
        3. Checks that the normalised query is non-empty (Req 3.5).

        Returns the dialog instance if validation passes, or ``None``
        if the dialog should not be opened.
        """
        note = editor.note
        if note is None:
            showWarning("No active note in the editor.")
            return None

        # Validate source field exists (Req 3.4)
        field_defs = note.note_type()["flds"]
        field_names = [fld["name"] for fld in field_defs]

        if config.source_field not in field_names:
            showWarning(
                f"Source field '{config.source_field}' not found on this "
                f"note type. Available fields: {', '.join(field_names)}"
            )
            return None

        # Read the source field value (Req 3.1)
        field_index = field_names.index(config.source_field)
        raw_value = note.fields[field_index]

        # Normalise the query (Req 3.2, 3.3)
        query = normalize_query(raw_value)

        # Check for empty query (Req 3.5)
        if not query:
            tooltip("source field is empty")
            return None

        # All validation passed — create and return the dialog
        dialog = PickerDialog(
            editor=editor,
            config=config,
            query=query,
            providers=providers,
            http=http,
            cache=cache,
            parent=parent,
        )
        return dialog


__all__ = ["PickerDialog"]
