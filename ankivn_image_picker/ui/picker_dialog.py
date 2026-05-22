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
        QSplitter,
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

    class QSplitter:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def addWidget(self, w: Any) -> None:
            pass

        def setStretchFactor(self, *args: Any) -> None:
            pass

        def setSizes(self, *args: Any) -> None:
            pass

        def setChildrenCollapsible(self, *args: Any) -> None:
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
# Click-note-to-search toggle in batch mode (default off — let the
# user opt in to auto-search when navigating the notes list)
_REMEMBERED_AUTO_SEARCH: bool = False
# Remembered splitter sizes for the batch notes-panel + grid layout.
# None = first run, fall back to a sensible default.
_REMEMBERED_SPLITTER_SIZES: Optional[list] = None
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
        search_cache: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._config = config
        self._query = query
        self._providers = providers
        self._http = http
        self._cache = cache
        self._search_cache = search_cache

        # --- Owned components ---
        self._cancel = CancellationToken()
        self._bus = WorkerBus(self)
        self._grid_model = GridModel()
        self._selection_locked = False
        self._selected_result: Optional["ImageResult"] = None
        self._provider_errors: dict[str, str] = {}
        self._provider_counts: dict[str, int] = {}  # provider_id -> result count
        self._results_received = False

        # --- Batch mode state ---
        # When operating as a single reused dialog across many notes,
        # ``_batch_mode`` is True and the helpers below coordinate
        # advancing through the queue without closing the dialog.
        self._batch_mode: bool = False
        # Provider of upcoming notes; set by ``start_batch``.
        # Each call returns ``(note, query, target_field, position,
        # prefetched_results, prefetched_errors, prefetched_translation)``
        # or ``None`` when the batch is exhausted.
        self._batch_next_provider: Optional[Any] = None
        # Outcomes per nid for the caller to summarise after exec().
        # Keys: "chosen", "skipped", "errors" (list of strings).
        self._batch_outcomes: dict[str, Any] = {
            "chosen": 0,
            "skipped": 0,
            "errors": [],
        }
        # Background pool used in batch mode for fire-and-forget
        # full-image downloads. Lazily created.
        self._batch_download_pool: Optional[Any] = None
        # Track in-flight downloads so we can wait/cancel before close.
        self._batch_download_futures: list = []
        # url -> dict capturing the editor/note/result/etc. at click
        # time. Lets _on_download_complete write to the right note
        # even after the dialog has swapped to a different one.
        self._batch_jobs: dict[str, dict[str, Any]] = {}
        # Optional zero-arg callable returning prefetch progress info.
        # Populated by start_batch when the caller wants the status
        # bar to show how many notes have been prefetched ahead.
        self._batch_prefetch_status: Optional[Any] = None
        self._prefetch_poll_timer: Optional[Any] = None
        # Snapshot of the most recent per-query prefetch state, mirrored
        # from the provider callable on each poll tick. Used by
        # ``_update_batch_list_item`` to render the ⏳/📦 markers.
        self._latest_prefetch_query_states: dict[str, str] = {}
        # Per-query thumbnail (loaded, total) pulled from the same
        # snapshot. Lets the side panel show progress like
        # ``⏳ apple (12/30)`` while the note warms up.
        self._latest_prefetch_thumb_progress: dict[str, tuple] = {}
        # Notes panel state (populated by start_batch)
        self._batch_notes_meta: list = []
        self._batch_job_factory: Optional[Any] = None
        self._batch_current_seq: int = 0

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
        self.setWindowTitle(f"⚡ Image Picker · {self._query}")
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
                        f"⚡ Image Picker · {self._query} → {prefetched_translation}"
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

        # Smooth scrolling: by default QListView in IconMode jumps an
        # entire row per wheel-tick, which feels janky on a grid of
        # ~158px-tall thumbnails. Switch to per-pixel scroll so the
        # view glides instead of snapping. Uniform item sizes lets Qt
        # cache the size hint, avoiding re-layout work on every paint.
        try:
            self._grid_widget.setVerticalScrollMode(
                QListWidget.ScrollMode.ScrollPerPixel
            )
            self._grid_widget.setHorizontalScrollMode(
                QListWidget.ScrollMode.ScrollPerPixel
            )
            self._grid_widget.setUniformItemSizes(True)
            vbar = self._grid_widget.verticalScrollBar()
            if vbar is not None:
                # 20px per wheel-notch ~= ⅛ of a row. Smooth without
                # making the scrollbar feel sluggish.
                vbar.setSingleStep(20)
        except Exception:
            # Test stubs don't expose these methods — ignore.
            pass

        self._grid_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Wrap the grid in a horizontal splitter so batch mode can
        # show a notes-panel on the left without changing the layout
        # for single-note (no-batch) usage. The notes panel widget is
        # created hidden; ``start_batch`` populates and reveals it.
        self._batch_panel = self._build_batch_panel()
        try:
            from aqt.qt import Qt as _Qt  # type: ignore[import-not-found]
            self._main_splitter = QSplitter(_Qt.Orientation.Horizontal, self)
            self._main_splitter.addWidget(self._batch_panel)
            self._main_splitter.addWidget(self._grid_widget)
            # Notes panel narrower by default; grid takes the rest.
            self._main_splitter.setStretchFactor(0, 0)
            self._main_splitter.setStretchFactor(1, 1)
            # Notes panel ~180px is enough for typical word/phrase
            # entries while leaving plenty of room for the grid.
            # Restored from the persisted state in start_batch when
            # available.
            self._main_splitter.setSizes([180, 820])
            self._main_splitter.setChildrenCollapsible(True)
        except Exception:
            self._main_splitter = None

        if self._main_splitter is not None:
            # Stretch=1 makes the splitter (notes panel + grid)
            # absorb all extra vertical space. Without this, Qt
            # divides spare height evenly among siblings, leaving
            # the grid cramped while padding accumulates above and
            # below.
            try:
                layout.addWidget(self._main_splitter, 1)
            except TypeError:
                # Fallback for the test stub layout that doesn't
                # accept a stretch arg.
                layout.addWidget(self._main_splitter)
        else:
            # Test/fallback path: no splitter, just the grid
            try:
                layout.addWidget(self._grid_widget, 1)
            except TypeError:
                layout.addWidget(self._grid_widget)
        # Hide the batch panel until start_batch reveals it.
        try:
            self._batch_panel.setVisible(False)
        except Exception:
            pass

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

            # Skip-with-images button: jumps over every upcoming note
            # whose target field already contains an <img> tag, until
            # the dialog lands on a note that still needs an image
            # (or the queue is exhausted). Hidden until batch mode.
            self._skip_with_images_button = QPushButton(
                "Skip notes with images ⏩", self
            )
            self._skip_with_images_button.setToolTip(
                "Skip every upcoming note whose target field already\n"
                "contains an image. Useful for resuming a batch where\n"
                "you've already processed some cards."
            )
            self._skip_with_images_button.clicked.connect(
                self._on_skip_with_images_clicked
            )
            self._skip_with_images_button.setVisible(False)
            bottom_layout.addWidget(self._skip_with_images_button)

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
            self._skip_with_images_button = None
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

    def _build_batch_panel(self) -> Any:
        """Build (but don't populate) the left-side notes panel.

        The panel is hidden by default; ``start_batch`` reveals it
        when batch mode is engaged. Outside batch mode the dialog
        looks identical to the previous single-note layout.
        """
        try:
            from aqt.qt import (  # type: ignore[import-not-found]
                QCheckBox,
                QLabel,
                QListWidget,
                QVBoxLayout,
                QWidget,
            )
        except Exception:
            return QWidget()  # stub path for tests

        panel = QWidget(self)
        v = QVBoxLayout(panel)
        try:
            v.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass

        self._batch_header_label = QLabel("Notes", panel)
        try:
            self._batch_header_label.setStyleSheet("font-weight: bold;")
        except Exception:
            pass
        v.addWidget(self._batch_header_label)

        # "Click note to search" toggle. Persisted across dialog opens
        # via the module-level _REMEMBERED_AUTO_SEARCH flag.
        self._auto_search_checkbox = QCheckBox("Click note to search", panel)
        try:
            self._auto_search_checkbox.setChecked(_REMEMBERED_AUTO_SEARCH)
            self._auto_search_checkbox.setToolTip(
                "When checked, clicking a note in the list immediately\n"
                "searches for images. When unchecked, clicking only\n"
                "switches the active note (use Search to fetch images)."
            )
            self._auto_search_checkbox.toggled.connect(
                self._on_auto_search_toggled
            )
        except Exception:
            pass
        v.addWidget(self._auto_search_checkbox)

        self._batch_list_widget = QListWidget(panel)
        try:
            self._batch_list_widget.setSelectionMode(
                QListWidget.SelectionMode.SingleSelection
            )
            self._batch_list_widget.itemClicked.connect(
                self._on_batch_note_clicked
            )
            # Smooth pixel-based scroll, same reasoning as the grid.
            self._batch_list_widget.setVerticalScrollMode(
                QListWidget.ScrollMode.ScrollPerPixel
            )
            self._batch_list_widget.setUniformItemSizes(True)
            _vbar = self._batch_list_widget.verticalScrollBar()
            if _vbar is not None:
                _vbar.setSingleStep(16)
        except Exception:
            pass
        v.addWidget(self._batch_list_widget)

        # Help label below the list
        self._batch_help_label = QLabel(
            "▶ active · ✅ done · ⏭ skipped · 📦 ready · ⏳ prefetching",
            panel,
        )
        try:
            self._batch_help_label.setStyleSheet(
                "color: #888; font-size: 10px;"
            )
        except Exception:
            pass
        v.addWidget(self._batch_help_label)

        return panel

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
            search_cache=self._search_cache,
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
                    f"⚡ Image Picker · {original} → {translated}"
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
            # 8 workers keeps the connection pool busy without
            # overwhelming any single provider host. With keep-alive
            # enabled in the HTTP client, latency-bound work scales
            # almost linearly here.
            max_workers=min(len(results), 8) if results else 1
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

    def _compute_include_attribution(self, result: "ImageResult") -> bool:
        """Decide whether attribution should be embedded for ``result``."""
        include_attr = True
        if self._attribution_checkbox is not None:
            try:
                include_attr = self._attribution_checkbox.isChecked()
            except Exception:
                include_attr = True
        # Unsplash mandates attribution per their API guidelines
        if result is not None and result.provider_id == "unsplash":
            include_attr = True
        return include_attr

    def _do_save_and_insert(
        self,
        *,
        editor: Any,
        target_field: str,
        query: str,
        result: Optional["ImageResult"],
        include_attr: bool,
        image_bytes: bytes,
        extension: str,
    ) -> str:
        """Save image bytes to media and insert into the editor's note.

        Returns the actual filename written to the media folder. Raises
        on failure (caller decides how to surface the error). The
        method is intentionally context-free: it never reads
        ``self._editor`` / ``self._config`` so it can run for any
        captured batch job, even after the dialog has swapped to a
        different note.
        """
        from .. import editor_bridge
        from ..attribution import build_attribution_html

        try:
            mw_ref = editor_bridge.mw
            taken_fn = mw_ref.col.media.have
        except Exception:
            taken_fn = lambda name: False  # noqa: E731

        filename = derive_filename(query, extension, taken=taken_fn)
        used_filename = editor_bridge.save_to_media(filename, image_bytes)

        attribution_html: Optional[str] = None
        if include_attr and result is not None:
            attribution_html = build_attribution_html(result)

        editor_bridge.insert_image(
            editor,
            target_field,
            used_filename,
            attribution_html=attribution_html,
        )
        return used_filename

    def _on_download_complete(
        self, url: str, image_bytes: bytes, extension: str
    ) -> None:
        """Save image to media and insert into target field (Req 7.3).

        In single-note mode this closes the dialog on success. In batch
        mode the download is fire-and-forget: the job context was
        captured at click-time so the dialog can have already swapped
        to a different note by the time this runs.
        """
        # Batch path: look up captured context for this URL
        if self._batch_mode and url in self._batch_jobs:
            job = self._batch_jobs.pop(url, None)
            if job is None:
                return
            try:
                self._do_save_and_insert(
                    editor=job["editor"],
                    target_field=job["target_field"],
                    query=job["query"],
                    result=job["result"],
                    include_attr=job["include_attr"],
                    image_bytes=image_bytes,
                    extension=extension,
                )
            except FieldNotFoundError as exc:
                _log.error("Batch insert: target field missing: %s", exc)
                self._batch_outcomes.setdefault("errors", []).append(
                    f"target field missing: {exc}"
                )
            except Exception as exc:
                _log.exception("Batch save/insert failed: %s", exc)
                self._batch_outcomes.setdefault("errors", []).append(str(exc))
            return

        # Single-note path: legacy behaviour — close dialog on success.
        try:
            self._do_save_and_insert(
                editor=self._editor,
                target_field=self._config.target_field,
                query=self._query,
                result=self._selected_result,
                include_attr=self._compute_include_attribution(
                    self._selected_result
                ) if self._selected_result is not None else True,
                image_bytes=image_bytes,
                extension=extension,
            )
            self._remember_state()
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
        # Batch path: log per-job failure, don't unlock selection (no
        # selection lock in batch mode).
        if self._batch_mode and url in self._batch_jobs:
            job = self._batch_jobs.pop(url, None)
            note_q = job["query"] if job else "?"
            _log.warning(
                "Background download failed for note %r: %s", note_q, message
            )
            self._batch_outcomes.setdefault("errors", []).append(
                f"{note_q}: download failed ({message})"
            )
            return

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
            self.setWindowTitle(f"⚡ Image Picker · {self._query}")
            self._translate_label.setVisible(False)
            self._dispatch_search(self._query)

        self._update_grid_display()

    # ------------------------------------------------------------------
    # Image selection handling (Req 7.1)
    # ------------------------------------------------------------------

    def on_image_clicked(self, result: "ImageResult") -> None:
        """Handle user clicking an image in the grid.

        In single-note mode: lock the dialog and download synchronously
        (the download blocks the UI until save+insert completes — see
        ``_on_download_complete`` / ``_on_download_failed``).

        In batch mode: capture the current note context, kick off the
        download fire-and-forget on the shared background pool, and
        immediately advance to the next note. Save+insert happens on
        the main thread when the bus emits ``download_complete``,
        using the captured context.
        """
        if self._selection_locked:
            return

        # Mark the cell as downloading so the user can see something
        # is happening before we swap (batch mode) or while we wait
        # (single-note mode).
        for cell in self._grid_model.rows:
            if cell.result is result:
                cell.state = "downloading"
                cell.progress = 0.0
                break
        self._update_grid_display()

        from ..orchestrator import FullImageDownloader

        if self._batch_mode:
            # Capture per-job context now — by the time the download
            # finishes, the dialog will have swapped to a different
            # note (so self._editor / self._query are stale).
            self._batch_jobs[result.full_url] = {
                "editor": self._editor,
                "target_field": self._config.target_field,
                "query": self._query,
                "result": result,
                "include_attr": self._compute_include_attribution(result),
            }
            # Track this note as "chosen" optimistically; the error
            # handler will move it to the errors bucket if save fails.
            self._batch_outcomes["chosen"] = (
                self._batch_outcomes.get("chosen", 0) + 1
            )
            # Mark the current note in the side panel so the user can
            # see at a glance that this note has been picked.
            self._mark_batch_note(self._batch_current_seq, "chosen")

            downloader = FullImageDownloader(
                http=self._http,
                bus=self._bus,
                # IMPORTANT: never share the per-search cancel token
                # with the fire-and-forget download. Swapping notes
                # cancels search work; we don't want that to abort
                # the user's chosen download.
                cancel=CancellationToken(),
            )
            pool = self._get_batch_download_pool()
            future = pool.submit(downloader.fetch, result)
            self._batch_download_futures.append(future)

            # Advance to the next note immediately so the user can
            # keep going without waiting for the download.
            self._advance_batch()
            return

        # Single-note mode: classic blocking flow.
        self._selection_locked = True
        self._selected_result = result

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

        # Batch-mode prefetch progress (📦 Prefetched X/Y notes · Z in flight)
        prefetch_str = ""
        if self._batch_mode and self._batch_prefetch_status is not None:
            try:
                status = self._batch_prefetch_status() or {}
                done = int(status.get("done", 0))
                total = int(status.get("total", 0))
                in_flight = int(status.get("in_flight", 0))
                if total > 0:
                    if done < total:
                        prefetch_str = (
                            f" | 📦 Prefetched {done}/{total} notes"
                        )
                        if in_flight > 0:
                            prefetch_str += f" · {in_flight} in flight"
                    else:
                        prefetch_str = f" | 📦 Prefetched {done}/{total} ✅"
            except Exception:
                prefetch_str = ""

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
                f"Total: {total} | "
                + " · ".join(parts)
                + progress_str
                + prefetch_str
            )
        else:
            # No provider activity yet — but still show prefetch
            # progress if batch mode is starting up.
            self._status_label.setText(prefetch_str.lstrip(" |").strip())

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

    # ------------------------------------------------------------------
    # Batch mode: reuse a single dialog for many notes
    # ------------------------------------------------------------------

    def start_batch(
        self,
        next_provider: Any,
        prefetch_status: Optional[Any] = None,
        notes_meta: Optional[list] = None,
        job_factory: Optional[Any] = None,
    ) -> None:
        """Enter batch mode driven by ``next_provider``.

        ``next_provider`` is a zero-arg callable returning either a
        dict with the keys ``note``, ``editor``, ``query``,
        ``source_field``, ``target_field``, ``position``,
        ``prefetched_results``, ``prefetched_errors``,
        ``prefetched_translation`` — or ``None`` to signal the queue
        is empty.

        ``prefetch_status`` is an optional zero-arg callable returning
        a dict ``{"done": int, "total": int, "in_flight": int}``. The
        dialog polls it periodically and shows progress in the status
        bar so users can see prefetch coverage in real time.

        ``notes_meta`` is the full list of notes scheduled for this
        batch, each entry a dict with keys ``query``, ``has_image``,
        ``nid`` (and optionally ``label``). Populates the left-side
        notes panel so the user can jump around. ``job_factory`` is a
        callable ``(seq_idx) -> job_dict`` used when the user clicks a
        specific note (instead of advancing sequentially).

        The dialog enables fire-and-forget full-image downloads:
        double-clicking an image kicks off the download on a
        background pool and immediately advances to the next note,
        instead of blocking the UI until the file is saved.
        """
        self._batch_mode = True
        self._batch_next_provider = next_provider
        self._batch_prefetch_status = prefetch_status
        self._batch_notes_meta = list(notes_meta) if notes_meta else []
        self._batch_job_factory = job_factory
        self._batch_current_seq = 0  # first job is already loaded by caller

        # Populate and reveal the notes panel.
        if self._batch_notes_meta:
            self._populate_batch_list()
            try:
                self._batch_panel.setVisible(True)
            except Exception:
                pass

            # Show the batch-only "skip notes with images" shortcut.
            try:
                if self._skip_with_images_button is not None:
                    self._skip_with_images_button.setVisible(True)
            except Exception:
                pass

            # Restore the splitter sizes the user picked last time,
            # otherwise pick a sensible default that leaves the grid
            # plenty of room.
            global _REMEMBERED_SPLITTER_SIZES
            try:
                if _REMEMBERED_SPLITTER_SIZES is not None:
                    self._main_splitter.setSizes(_REMEMBERED_SPLITTER_SIZES)
                else:
                    self._main_splitter.setSizes([180, 820])
            except Exception:
                pass

            # If the dialog is currently smaller than the new
            # batch-friendly default, grow it. Don't shrink down a
            # user-resized window though.
            try:
                MIN_BATCH_W = 1100
                MIN_BATCH_H = 750
                # setMinimumSize is more reliable than self.resize()
                # at this point: Qt enforces the floor whether the
                # dialog is shown yet or not, and will adjust the
                # frame on show.
                self.setMinimumSize(MIN_BATCH_W, MIN_BATCH_H)
                cur_w = self.width()
                cur_h = self.height()
                if cur_w < MIN_BATCH_W or cur_h < MIN_BATCH_H:
                    self.resize(
                        max(cur_w, MIN_BATCH_W),
                        max(cur_h, MIN_BATCH_H),
                    )
                # Defer one more resize once the event loop runs so
                # restoreGeometry has fully applied. This second call
                # is a no-op if the user has already grown the
                # dialog past the minimum.
                try:
                    from aqt.qt import QTimer  # type: ignore[import-not-found]
                    QTimer.singleShot(0, self._enforce_batch_min_size)
                except Exception:
                    pass
            except Exception:
                pass

            # Persist splitter sizes whenever the user resizes the
            # divider so the next batch session restores their layout.
            try:
                self._main_splitter.splitterMoved.connect(
                    self._on_splitter_moved
                )
            except Exception:
                pass

        # Poll prefetch status every 250ms while the dialog is open so
        # the status bar reflects newly completed prefetches without
        # requiring a search/thumbnail event to land.
        if prefetch_status is not None:
            try:
                from aqt.qt import QTimer  # type: ignore[import-not-found]
                self._prefetch_poll_timer = QTimer(self)
                self._prefetch_poll_timer.setInterval(250)
                self._prefetch_poll_timer.timeout.connect(
                    self._on_prefetch_poll_tick
                )
                self._prefetch_poll_timer.start()
            except Exception:
                self._prefetch_poll_timer = None

    @property
    def batch_outcomes(self) -> dict[str, Any]:
        """Aggregated batch results, populated as the dialog runs."""
        return self._batch_outcomes

    def _populate_batch_list(self) -> None:
        """Render the notes panel from ``_batch_notes_meta``."""
        try:
            from aqt.qt import QListWidgetItem  # type: ignore[import-not-found]
            from aqt.qt import Qt as _Qt  # type: ignore[import-not-found]
        except Exception:
            return
        try:
            self._batch_list_widget.clear()
        except Exception:
            return

        for idx, meta in enumerate(self._batch_notes_meta):
            item = QListWidgetItem()
            try:
                item.setData(_Qt.ItemDataRole.UserRole, idx)
            except Exception:
                pass
            self._batch_list_widget.addItem(item)

        self._refresh_batch_list_text()
        try:
            self._batch_header_label.setText(
                f"Notes ({len(self._batch_notes_meta)})"
            )
        except Exception:
            pass

    def _refresh_batch_list_text(self) -> None:
        """Re-render every list item's text based on the current meta."""
        for idx, meta in enumerate(self._batch_notes_meta):
            self._update_batch_list_item(idx)

    def _update_batch_list_item(self, idx: int) -> None:
        """Refresh a single list item's text/style from its meta entry."""
        if idx < 0 or idx >= len(self._batch_notes_meta):
            return
        meta = self._batch_notes_meta[idx]
        try:
            item = self._batch_list_widget.item(idx)
        except Exception:
            return
        if item is None:
            return

        # Marker priority (highest first):
        #   ▶ active     — overrides everything
        #   ⏭ skipped    — final state
        #   ✅ has image — final state (already had OR just chose)
        #   📦 cached    — prefetch finished, ready to swap instantly
        #   ⏳ prefetching — task in flight
        #     (blank)    — not yet prefetched
        marker = " "
        progress_suffix = ""
        if idx == self._batch_current_seq:
            marker = "▶"
        elif meta.get("status") == "skipped":
            marker = "⏭"
        elif meta.get("has_image") or meta.get("status") == "chosen":
            marker = "✅"
        else:
            # Look up live prefetch state for this note's query.
            q = meta.get("query") or ""
            pf_state = self._latest_prefetch_query_states.get(q)
            if pf_state == "done":
                marker = "📦"
            elif pf_state in ("running", "queued"):
                marker = "⏳"

            # Append thumbnail progress so the user sees the cell
            # going from "⏳ apple (0/30)" → "⏳ apple (18/30)" →
            # "📦 apple (30/30)" rather than a static spinner.
            tp = self._latest_prefetch_thumb_progress.get(q)
            if tp is not None:
                loaded, total = tp
                if total > 0:
                    progress_suffix = f"  ({loaded}/{total})"

        label = meta.get("label") or meta.get("query") or "(empty)"
        try:
            item.setText(f"{marker} {label}{progress_suffix}")
        except Exception:
            pass

        # Tooltip with full text
        try:
            item.setToolTip(label)
        except Exception:
            pass

    def _set_batch_current(self, seq_idx: int) -> None:
        """Mark a new active note in the list (updates markers)."""
        prev = self._batch_current_seq
        self._batch_current_seq = seq_idx
        # Refresh the previous and current rows so markers update
        self._update_batch_list_item(prev)
        self._update_batch_list_item(seq_idx)
        # Select the active row in the list widget
        try:
            self._batch_list_widget.setCurrentRow(seq_idx)
        except Exception:
            pass

    def _mark_batch_note(self, seq_idx: int, status: str) -> None:
        """Update meta status for a note (e.g. 'chosen', 'skipped')."""
        if seq_idx < 0 or seq_idx >= len(self._batch_notes_meta):
            return
        meta = self._batch_notes_meta[seq_idx]
        meta["status"] = status
        if status == "chosen":
            meta["has_image"] = True
        self._update_batch_list_item(seq_idx)

    def _on_batch_note_clicked(self, item: Any) -> None:
        """Jump to the clicked note in the batch list."""
        if not self._batch_mode or self._batch_job_factory is None:
            return
        try:
            from aqt.qt import Qt as _Qt  # type: ignore[import-not-found]
            seq_idx = item.data(_Qt.ItemDataRole.UserRole)
        except Exception:
            seq_idx = None
        if seq_idx is None:
            try:
                seq_idx = self._batch_list_widget.row(item)
            except Exception:
                return
        if seq_idx is None or seq_idx == self._batch_current_seq:
            return

        # Build the job for this note
        try:
            job = self._batch_job_factory(seq_idx)
        except Exception as exc:
            _log.exception("Job factory raised for seq %s: %s", seq_idx, exc)
            return
        if job is None:
            return

        auto_search = bool(_REMEMBERED_AUTO_SEARCH)
        try:
            if self._auto_search_checkbox is not None:
                auto_search = self._auto_search_checkbox.isChecked()
        except Exception:
            pass

        # If results are already prefetched, swap into them
        # immediately regardless of the auto-search toggle. The toggle
        # is meant to control whether clicking a *cold* note triggers
        # a fresh network search; for a note that's already 📦 ready,
        # not loading would defeat the point of the marker.
        has_prefetch = bool(job.get("prefetched_results"))

        if auto_search or has_prefetch:
            # Full swap: load prefetched results and search
            self._set_batch_current(seq_idx)
            self.swap_to_query(
                editor=job["editor"],
                query=job["query"],
                source_field=job["source_field"],
                target_field=job["target_field"],
                position=job["position"],
                prefetched_results=job.get("prefetched_results"),
                prefetched_errors=job.get("prefetched_errors"),
                prefetched_translation=job.get("prefetched_translation"),
            )
        else:
            # Lightweight swap: change context only, don't search.
            # User can edit query and click Search when ready.
            self._set_batch_current(seq_idx)
            self._reset_state_for_swap()
            from dataclasses import replace as _replace
            self._editor = job["editor"]
            self._config = _replace(
                self._config,
                source_field=job["source_field"],
                target_field=job["target_field"],
            )
            self._query = job["query"]
            self._effective_query = job["query"]
            try:
                self._source_label.setText(
                    f"<b>{job['source_field']}</b>: <i>{job['query']}</i>"
                )
                self.setWindowTitle(
                    f"⚡ Image Picker · Batch [{job['position'][0]}/"
                    f"{job['position'][1]}] · {job['query']}"
                )
                self._search_input.blockSignals(True)
                self._search_input.setText(job["query"])
                self._search_input.blockSignals(False)
                self._grid_label.setText(
                    "Click Search or press Enter to fetch images."
                )
            except Exception:
                pass

    def _on_prefetch_poll_tick(self) -> None:
        """Handle one tick of the prefetch poll timer.

        Mirrors the provider's per-query state into
        ``_latest_prefetch_query_states`` and refreshes any list rows
        whose marker may have changed (queued/running → done, etc).
        Status bar is updated unconditionally — its costs are
        negligible since it only sets a single label's text.
        """
        if self._batch_prefetch_status is None:
            return
        try:
            status = self._batch_prefetch_status() or {}
        except Exception:
            status = {}

        new_states = status.get("query_states") or {}
        new_thumbs = status.get("thumb_progress") or {}

        # Diff against the previous snapshots so we only re-render rows
        # whose state or thumbnail count actually changed. With 100
        # notes this avoids a 100x setText every 250 ms.
        prev = self._latest_prefetch_query_states
        prev_thumbs = self._latest_prefetch_thumb_progress
        changed_queries: set = set()
        for q, st in new_states.items():
            if prev.get(q) != st:
                changed_queries.add(q)
        for q in prev:
            if q not in new_states:
                changed_queries.add(q)
        for q, count in new_thumbs.items():
            if prev_thumbs.get(q) != count:
                changed_queries.add(q)

        self._latest_prefetch_query_states = new_states
        self._latest_prefetch_thumb_progress = new_thumbs

        if changed_queries:
            for idx, meta in enumerate(self._batch_notes_meta):
                if (meta.get("query") or "") in changed_queries:
                    self._update_batch_list_item(idx)

        self._update_status_bar()

    def _on_auto_search_toggled(self, checked: bool) -> None:
        """Persist the auto-search toggle for the next batch session."""
        global _REMEMBERED_AUTO_SEARCH
        _REMEMBERED_AUTO_SEARCH = bool(checked)

    def _on_skip_with_images_clicked(self) -> None:
        """Skip every upcoming note whose target field already has an image.

        Walks forward through the queue from the current position,
        advancing past any note flagged with ``has_image=True`` in
        the meta list. Stops at the first note that still needs an
        image, or at the end of the queue (in which case the dialog
        accepts itself like a normal batch finish).
        """
        if not self._batch_mode:
            return

        skipped_count = 0
        # Walk the meta list rather than calling _advance_batch in a
        # loop. _advance_batch triggers a full search/swap on every
        # call, which would be wasteful when our intent is just to
        # fast-forward over already-completed notes. We do still need
        # to call it once at the end to actually move the dialog onto
        # the first un-imaged note.
        target_seq = self._batch_current_seq
        for seq in range(self._batch_current_seq + 1, len(self._batch_notes_meta)):
            meta = self._batch_notes_meta[seq]
            if meta.get("has_image") or meta.get("status") == "chosen":
                # Tag in meta so the side panel reflects the skip
                if meta.get("status") is None:
                    meta["status"] = "skipped"
                self._update_batch_list_item(seq)
                skipped_count += 1
                target_seq = seq
            else:
                # Found an unimaged note — advance lands here.
                break
        else:
            # No unimaged note remained: ``target_seq`` will be the
            # last skipped one, and _advance_batch() is responsible
            # for accepting the dialog when the queue is exhausted.
            target_seq = len(self._batch_notes_meta) - 1

        if skipped_count == 0:
            try:
                tooltip("No upcoming notes already have images.")
            except Exception:
                pass
            return

        # Bring the dialog's pointer to the last skipped seq so the
        # next _advance_batch call lands on (target_seq + 1), which
        # is the first un-imaged note (or past-the-end → accept()).
        self._batch_current_seq = target_seq
        try:
            tooltip(f"Skipped {skipped_count} note(s) that already have images.")
        except Exception:
            pass

        # If the next provider has a job ready, swap into it; if not,
        # the queue is exhausted and _advance_batch will accept().
        # We don't update batch_outcomes["skipped"] here because these
        # notes weren't actively skipped by the user — they simply
        # already had images. Counting them would distort the summary.
        self._advance_batch()

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Persist splitter sizes after the user drags the divider."""
        global _REMEMBERED_SPLITTER_SIZES
        try:
            _REMEMBERED_SPLITTER_SIZES = list(self._main_splitter.sizes())
        except Exception:
            pass

    def _enforce_batch_min_size(self) -> None:
        """Re-apply the batch min size after the event loop spins.

        Called via QTimer.singleShot(0, ...) so it runs once Qt has
        finished applying any restoreGeometry from a previous
        single-note session — those geometries can be smaller than
        the batch-mode minimum and would otherwise leave the dialog
        cramped.
        """
        try:
            min_w = self.minimumWidth()
            min_h = self.minimumHeight()
            if self.width() < min_w or self.height() < min_h:
                self.resize(
                    max(self.width(), min_w),
                    max(self.height(), min_h),
                )
        except Exception:
            pass

    def _reset_state_for_swap(self) -> None:
        """Wipe per-note state so the dialog can host a fresh query.

        Mirrors the cleanup paths used in ``_on_requery`` /
        ``_on_translate_clicked`` but without re-running search. The
        caller is responsible for kicking off the new search (or
        loading prefetched data) afterwards.
        """
        # Cancel previous in-flight orchestrator work. Note: we do
        # NOT cancel batch_download_futures here; those are fire-and-
        # forget and need to keep running on the background pool.
        try:
            self._cancel.cancel()
        except Exception:
            pass
        self._cancel = CancellationToken()

        # Clear grid + buffers
        try:
            self._grid_model.clear()
        except Exception:
            pass
        try:
            self._grid_widget.clear()
        except Exception:
            pass
        self._url_to_item.clear()
        self._provider_errors.clear()
        self._provider_counts.clear()
        self._pending_results.clear()
        self._ready_results.clear()

        # Reset progress + selection
        self._thumbnails_total = 0
        self._thumbnails_loaded = 0
        self._thumbnails_failed = 0
        self._current_page = 1
        self._results_received = False
        self._selection_locked = False
        self._selected_result = None
        self._skipped = False

        # Hide translation banner; it will be re-set if the new note
        # has a prefetched translation or runs translate again.
        try:
            self._translate_label.setVisible(False)
        except Exception:
            pass

    def swap_to_query(
        self,
        *,
        editor: Any,
        query: str,
        source_field: str,
        target_field: str,
        position: tuple[int, int],
        prefetched_results: Optional[list] = None,
        prefetched_errors: Optional[dict] = None,
        prefetched_translation: Optional[str] = None,
    ) -> None:
        """Repurpose this dialog instance for the next batch note.

        Replaces the editor/config/query without closing or rebuilding
        the dialog. Avoids the visible flicker users see when the
        existing implementation closes one dialog and opens another
        for each note.

        ``editor`` is typically a ``_BatchEditorShim`` bound to the
        new note; ``source_field`` / ``target_field`` override the
        config so we don't have to mutate the shared Config object.
        """
        from dataclasses import replace as _replace

        self._reset_state_for_swap()

        # Swap context
        self._editor = editor
        self._config = _replace(
            self._config,
            source_field=source_field,
            target_field=target_field,
        )
        self._query = query
        self._effective_query = query

        # Refresh source field label + window title + search input
        try:
            self._source_label.setText(
                f"<b>{source_field}</b>: <i>{query}</i>"
            )
            self._source_label.setToolTip(
                f"Original text from the '{source_field}' field."
            )
        except Exception:
            pass
        try:
            self.setWindowTitle(
                f"⚡ Image Picker · Batch [{position[0]}/{position[1]}] · {query}"
            )
        except Exception:
            pass
        try:
            self._search_input.blockSignals(True)
            self._search_input.setText(query)
            self._search_input.blockSignals(False)
        except Exception:
            pass

        # Restart the flush timer if it was stopped
        try:
            if self._flush_timer is not None and not self._flush_timer.isActive():
                self._flush_timer.start()
        except Exception:
            pass

        # --- Use prefetched results or start fresh search ---
        if prefetched_results:
            if prefetched_errors:
                for pid, msg in prefetched_errors.items():
                    self._on_provider_failed(pid, msg)

            if prefetched_translation and prefetched_translation != query:
                self._effective_query = prefetched_translation
                try:
                    self.setWindowTitle(
                        f"⚡ Image Picker · Batch [{position[0]}/{position[1]}] · "
                        f"{query} → {prefetched_translation}"
                    )
                    self._search_input.blockSignals(True)
                    self._search_input.setText(prefetched_translation)
                    self._search_input.blockSignals(False)
                    self._translate_label.setText(
                        f"🌐 Translated: {query} → {prefetched_translation}"
                    )
                    self._translate_label.setVisible(True)
                except Exception:
                    pass

            self._load_prefetched_synchronously(prefetched_results)
            self._flush_all_ready()

            uncached = []
            for result in prefetched_results:
                pending = self._pending_results.get(result.provider_id, {})
                if result.thumbnail_url in pending:
                    uncached.append(result)
            if uncached:
                self._start_thumbnail_downloads(uncached)
        else:
            self._start_search(query)

        self._update_grid_display()
        self._update_status_bar()

    def _advance_batch(self) -> bool:
        """Pull the next note from the batch provider and swap into it.

        Returns True if the dialog moved on to a new note. Returns
        False (and ``accept()`` is called) if the queue is exhausted.
        """
        if not self._batch_mode or self._batch_next_provider is None:
            return False

        try:
            nxt = self._batch_next_provider()
        except Exception as exc:
            _log.exception("Batch provider raised: %s", exc)
            self._batch_outcomes.setdefault("errors", []).append(str(exc))
            nxt = None

        if not nxt:
            # Queue exhausted: wait for any in-flight downloads to
            # finish so insertions complete before we close, then
            # accept the dialog.
            self._wait_for_batch_downloads()
            self._remember_state()
            self.accept()
            return False

        # Bump the current-seq pointer so the notes list highlights
        # the newly active row. The notes_meta list is in the same
        # order as the queue, so seq_idx == current_seq + 1 unless
        # the user clicked around to jump.
        next_seq = self._batch_current_seq + 1
        if next_seq >= len(self._batch_notes_meta):
            # Defensive: if meta list was shorter than the queue
            # (shouldn't happen) just stop bumping.
            next_seq = self._batch_current_seq
        self._set_batch_current(next_seq)

        self.swap_to_query(
            editor=nxt["editor"],
            query=nxt["query"],
            source_field=nxt["source_field"],
            target_field=nxt["target_field"],
            position=nxt["position"],
            prefetched_results=nxt.get("prefetched_results"),
            prefetched_errors=nxt.get("prefetched_errors"),
            prefetched_translation=nxt.get("prefetched_translation"),
        )
        return True

    def _get_batch_download_pool(self) -> Any:
        """Return the (lazily created) background pool for full downloads."""
        import concurrent.futures

        if self._batch_download_pool is None:
            # 4 concurrent downloads is a safe balance: enough to keep
            # the network busy, low enough to avoid rate-limits.
            self._batch_download_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="ankivn-batch-dl",
            )
        return self._batch_download_pool

    def _wait_for_batch_downloads(self) -> None:
        """Block briefly until queued background downloads finish.

        Called when the batch finishes to avoid losing in-flight
        inserts. Bounded by a generous timeout so a stuck request
        never hangs the dialog forever.
        """
        if not self._batch_download_futures:
            return

        try:
            from aqt.utils import tooltip as _tooltip
        except Exception:
            _tooltip = None

        pending = [f for f in self._batch_download_futures if not f.done()]
        if not pending:
            return

        if _tooltip is not None:
            try:
                _tooltip(
                    f"Finishing {len(pending)} background download(s)…",
                    parent=self,
                )
            except Exception:
                pass

        import concurrent.futures
        try:
            concurrent.futures.wait(pending, timeout=30)
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
        # Stop the prefetch poll timer
        try:
            if self._prefetch_poll_timer is not None:
                self._prefetch_poll_timer.stop()
        except Exception:
            pass
        self._cancel.cancel()

        # In batch mode, wait for queued downloads so already-chosen
        # images still land in their notes even if the user closes
        # the dialog before the queue drains.
        if self._batch_mode:
            self._wait_for_batch_downloads()
            try:
                if self._batch_download_pool is not None:
                    self._batch_download_pool.shutdown(wait=False)
            except Exception:
                pass

        super().closeEvent(event)

    def _on_skip_clicked(self) -> None:
        """Handle Skip button.

        In single-note mode this rejects the dialog (legacy behaviour).
        In batch mode it records the skip and advances to the next
        note, keeping the dialog open.
        """
        if self._batch_mode:
            self._batch_outcomes["skipped"] = (
                self._batch_outcomes.get("skipped", 0) + 1
            )
            # Tag this note as skipped so the user can find it later.
            try:
                if hasattr(self._editor, "_skip_tag_note"):
                    self._editor._skip_tag_note()
            except Exception:
                pass
            self._mark_batch_note(self._batch_current_seq, "skipped")
            self._advance_batch()
            return

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
        search_cache: Optional[Any] = None,
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

        # Resolve the effective source/target field for THIS note's
        # note type. Per-note-type field_mappings win over the global
        # source_field / target_field fall-back.
        from ..config import resolve_fields
        from dataclasses import replace as _replace

        try:
            note_type_name = note.note_type()["name"]
        except Exception:
            note_type_name = ""
        src_field, tgt_field = resolve_fields(note_type_name, config)

        # Validate source field exists (Req 3.4)
        field_defs = note.note_type()["flds"]
        field_names = [fld["name"] for fld in field_defs]

        if src_field not in field_names:
            showWarning(
                f"Source field '{src_field}' not found on note type "
                f"'{note_type_name}'.\n\n"
                f"Available fields: {', '.join(field_names)}\n\n"
                f"Configure a mapping under "
                f"AnkiVN → ⚡ Image Picker Settings → Field Mappings."
            )
            return None

        # Read the source field value (Req 3.1)
        field_index = field_names.index(src_field)
        raw_value = note.fields[field_index]

        # Normalise the query (Req 3.2, 3.3)
        query = normalize_query(raw_value)

        # Check for empty query (Req 3.5)
        if not query:
            tooltip(f"Source field '{src_field}' is empty.")
            return None

        # Bake the resolved fields into the config that the dialog will
        # use, so every downstream code-path (insert, status label,
        # etc.) sees the per-note-type values without having to re-
        # resolve.
        effective_config = _replace(
            config,
            source_field=src_field,
            target_field=tgt_field,
        )

        # All validation passed — create and return the dialog
        dialog = PickerDialog(
            editor=editor,
            config=effective_config,
            query=query,
            providers=providers,
            http=http,
            cache=cache,
            parent=parent,
            search_cache=search_cache,
        )
        return dialog


__all__ = ["PickerDialog"]
