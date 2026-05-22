"""Property tests for :mod:`ankivn_image_picker.ui.grid_view`.

Feature: ankivn-image-picker, Property 6: Grid streaming integrity.

The design document (``Correctness Properties`` section) states:

    For any sequence of bus events ``E`` (a stream of
    ``result_ready(r)`` followed by ``thumbnail_ready(url, bytes)`` or
    ``thumbnail_failed(url)`` events in arbitrary order), after the grid
    model has applied all events in ``E`` in arrival order, the
    following hold:

    - The grid's row order equals the order of ``result_ready`` events
      in ``E``.
    - Every row's ``result.provider_id`` equals the ``provider_id`` of
      the ``result_ready`` event that produced it.
    - For every row whose corresponding ``thumbnail_failed`` event
      arrived, the row state is ``"placeholder"`` and
      ``row.result.full_url`` equals the original full URL from the
      ``result_ready`` event (the failed thumbnail does not destroy the
      full-image URL).

**Validates: Requirements 4.4, 5.5, 5.6**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Union

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.grid_view import GridModel


# ---------------------------------------------------------------------------
# Event types for the property test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultReadyEvent:
    """Represents a ``result_ready(result)`` bus event."""

    result: ImageResult


@dataclass(frozen=True)
class ThumbnailReadyEvent:
    """Represents a ``thumbnail_ready(url, bytes)`` bus event."""

    url: str
    data: bytes


@dataclass(frozen=True)
class ThumbnailFailedEvent:
    """Represents a ``thumbnail_failed(url, message)`` bus event."""

    url: str
    message: str


BusEvent = Union[ResultReadyEvent, ThumbnailReadyEvent, ThumbnailFailedEvent]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Provider IDs drawn from a small pool to get collisions in provider_id
# (multiple results from the same provider) while keeping shrinking fast.
_provider_ids = st.sampled_from(["unsplash", "pixabay", "pexels", "flickr"])

# Extensions from the allowed set.
_extensions = st.sampled_from(["jpg", "png", "gif", "webp", "bmp"])


@st.composite
def _image_results(draw: st.DrawFn) -> ImageResult:
    """Generate a valid ImageResult with unique URLs."""
    provider_id = draw(_provider_ids)
    # Use a unique suffix to ensure thumbnail_url uniqueness across results.
    uid = draw(st.uuids())
    thumbnail_url = f"https://example.com/thumb/{uid}.jpg"
    full_url = f"https://example.com/full/{uid}.jpg"
    extension = draw(_extensions)
    return ImageResult(
        provider_id=provider_id,
        thumbnail_url=thumbnail_url,
        full_url=full_url,
        extension=extension,
    )


@st.composite
def bus_event_sequences(draw: st.DrawFn) -> List[BusEvent]:
    """Generate a sequence of bus events.

    The sequence always starts with 1..N ``result_ready`` events (since
    thumbnail events only make sense after a result has been registered),
    followed by an arbitrary interleaving of ``thumbnail_ready`` and
    ``thumbnail_failed`` events for the URLs introduced by the results.

    The strategy ensures:
    - At least one result_ready event exists.
    - Thumbnail events reference URLs from the result_ready events.
    - A given URL gets at most one thumbnail outcome (ready OR failed).
    """
    # Generate 1..10 results
    results = draw(st.lists(_image_results(), min_size=1, max_size=10))

    # Build the result_ready events (these always come first in the
    # conceptual "arrival order" — the grid appends rows as results
    # arrive).
    result_events: List[BusEvent] = [ResultReadyEvent(result=r) for r in results]

    # For each result, optionally generate a thumbnail outcome.
    # The thumbnail events can arrive in any order relative to each
    # other (but always after the result_ready that introduced the URL).
    thumb_events: List[BusEvent] = []
    for r in results:
        outcome = draw(st.sampled_from(["ready", "failed", "pending"]))
        if outcome == "ready":
            data = draw(st.binary(min_size=1, max_size=64))
            thumb_events.append(ThumbnailReadyEvent(url=r.thumbnail_url, data=data))
        elif outcome == "failed":
            thumb_events.append(
                ThumbnailFailedEvent(url=r.thumbnail_url, message="timeout")
            )
        # "pending" means no thumbnail event arrives — row stays pending.

    # Shuffle the thumbnail events to simulate arbitrary arrival order.
    shuffled_thumbs = draw(st.permutations(thumb_events))

    # The full event sequence: all result_ready first, then shuffled
    # thumbnail events. This models the real scenario where results
    # stream in and thumbnails arrive asynchronously afterward.
    return result_events + list(shuffled_thumbs)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(events=bus_event_sequences())
def test_grid_streaming_integrity(events: List[BusEvent]) -> None:
    """**Validates: Requirements 4.4, 5.5, 5.6**

    Property 6 from ``design.md``: grid streaming integrity.

    Applies all events in arrival order to a fresh GridModel and then
    asserts the three sub-invariants from the property statement.
    """
    model = GridModel()

    # Track the result_ready events in order for verification.
    result_ready_order: List[ImageResult] = []

    # Track which URLs had thumbnail_failed events.
    failed_urls: set = set()

    # Apply all events in sequence.
    for event in events:
        if isinstance(event, ResultReadyEvent):
            model.on_result_ready(event.result)
            result_ready_order.append(event.result)
        elif isinstance(event, ThumbnailReadyEvent):
            model.on_thumbnail_ready(event.url, event.data)
        elif isinstance(event, ThumbnailFailedEvent):
            model.on_thumbnail_failed(event.url, event.message)
            failed_urls.add(event.url)

    # ------------------------------------------------------------------
    # Invariant 1: The grid's row order equals the order of
    # result_ready events in E.
    # ------------------------------------------------------------------
    assert model.row_count() == len(result_ready_order)
    for i, expected_result in enumerate(result_ready_order):
        assert model.rows[i].result is expected_result

    # ------------------------------------------------------------------
    # Invariant 2: Every row's result.provider_id equals the provider_id
    # of the result_ready event that produced it.
    # ------------------------------------------------------------------
    for i, expected_result in enumerate(result_ready_order):
        assert model.rows[i].result.provider_id == expected_result.provider_id

    # ------------------------------------------------------------------
    # Invariant 3: For every row whose corresponding thumbnail_failed
    # event arrived, the row state is "placeholder" and
    # row.result.full_url equals the original full URL from the
    # result_ready event (the failed thumbnail does not destroy the
    # full-image URL).
    # ------------------------------------------------------------------
    for i, expected_result in enumerate(result_ready_order):
        row = model.rows[i]
        if expected_result.thumbnail_url in failed_urls:
            assert row.state == "placeholder", (
                f"Row {i} should be 'placeholder' after thumbnail_failed, "
                f"got '{row.state}'"
            )
            assert row.result.full_url == expected_result.full_url, (
                f"Row {i} full_url was corrupted: expected "
                f"{expected_result.full_url!r}, got {row.result.full_url!r}"
            )
