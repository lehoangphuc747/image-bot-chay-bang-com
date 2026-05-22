"""Thumbnail grid view for the AnkiVN Smart Image Picker add-on.

This module defines:

* :class:`GridCell` -- a mutable row model holding an
  :class:`~ankivn_image_picker.providers.base.ImageResult`, its current
  display state, and optional thumbnail bytes.
* :class:`GridModel` -- a list-based model whose rows are
  :class:`GridCell` instances. The model exposes slots that mutate rows
  in response to :class:`~ankivn_image_picker.ui.worker_bus.WorkerBus`
  signals (append on ``result_ready``, update bytes on
  ``thumbnail_ready``, mark placeholder on ``thumbnail_failed``, set
  progress on ``download_progress``).

The model preserves insertion order: rows appear in the exact order
their ``result_ready`` events arrived (Req 4.4). Thumbnail events
arriving out of order update the matching row in-place without
reordering (Req 5.5, 5.6).

Threading
---------
The model is designed to be mutated exclusively on the Qt main thread.
Bus signals are delivered on the main thread via Qt's queued-connection
semantics, so no internal locking is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from ..providers.base import ImageResult


@dataclass
class GridCell:
    """A single row in the thumbnail grid.

    Attributes
    ----------
    result:
        The :class:`ImageResult` that produced this row. Immutable once
        set; the cell's identity is tied to this result.
    state:
        Current display state of the cell:
        - ``"pending"``: thumbnail download is in flight.
        - ``"ready"``: thumbnail bytes are available.
        - ``"placeholder"``: thumbnail download failed; a neutral
          placeholder is shown but the full-image URL remains valid.
        - ``"downloading"``: the user selected this cell and the
          full-image download is in progress.
    pixmap_bytes:
        Raw thumbnail bytes when ``state == "ready"``; ``None``
        otherwise. In the full UI these would be decoded into a
        ``QPixmap``; for the model layer we store raw bytes so the
        model can be exercised without a running Qt event loop.
    progress:
        Download progress fraction in ``[0.0, 1.0]``. Only meaningful
        when ``state == "downloading"``.
    """

    result: ImageResult
    state: Literal["pending", "ready", "placeholder", "downloading"] = "pending"
    pixmap_bytes: Optional[bytes] = None
    progress: float = 0.0


class GridModel:
    """List-based model backing the thumbnail grid view.

    Rows are appended via :meth:`on_result_ready` and updated in-place
    by :meth:`on_thumbnail_ready` and :meth:`on_thumbnail_failed`. The
    model maintains a URL-to-index lookup for O(1) thumbnail event
    dispatch.
    """

    def __init__(self) -> None:
        self._rows: List[GridCell] = []
        # Maps thumbnail_url -> row index for fast lookup when
        # thumbnail events arrive out of order.
        self._thumb_url_index: Dict[str, int] = {}

    @property
    def rows(self) -> List[GridCell]:
        """Return the list of grid cells in insertion order."""
        return self._rows

    def row_count(self) -> int:
        """Return the number of rows currently in the model."""
        return len(self._rows)

    def clear(self) -> None:
        """Remove all rows and reset the index."""
        self._rows.clear()
        self._thumb_url_index.clear()

    # ------------------------------------------------------------------
    # Slots connected to WorkerBus signals
    # ------------------------------------------------------------------

    def on_result_ready(self, result: ImageResult) -> None:
        """Append a new row for the given result.

        Called when the bus emits ``result_ready(result)``. The row is
        appended at the end, preserving arrival order (Req 4.4).
        """
        idx = len(self._rows)
        cell = GridCell(result=result)
        self._rows.append(cell)
        self._thumb_url_index[result.thumbnail_url] = idx

    def on_thumbnail_ready(self, url: str, data: bytes) -> None:
        """Update the matching row with thumbnail bytes.

        Called when the bus emits ``thumbnail_ready(url, bytes)``. If
        no row matches ``url`` (e.g. the grid was cleared between the
        download starting and completing), the event is silently
        discarded.
        """
        idx = self._thumb_url_index.get(url)
        if idx is None:
            return
        cell = self._rows[idx]
        cell.state = "ready"
        cell.pixmap_bytes = data

    def on_thumbnail_failed(self, url: str, message: str) -> None:
        """Mark the matching row as placeholder.

        Called when the bus emits ``thumbnail_failed(url, message)``.
        The row's state becomes ``"placeholder"`` but its
        ``result.full_url`` is preserved so the user can still select
        the image for full download (Req 5.5).
        """
        idx = self._thumb_url_index.get(url)
        if idx is None:
            return
        cell = self._rows[idx]
        cell.state = "placeholder"

    def on_download_progress(self, url: str, fraction: float) -> None:
        """Update progress on the row whose full_url matches.

        Called when the bus emits ``download_progress(url, fraction)``.
        """
        for cell in self._rows:
            if cell.result.full_url == url:
                cell.state = "downloading"
                cell.progress = fraction
                break


__all__ = ["GridCell", "GridModel"]
