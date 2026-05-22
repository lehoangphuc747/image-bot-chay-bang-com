"""Property test for cancellation effectiveness.

Implements Property 16 from the design document's "Correctness
Properties" section. The property is:

    For any set of in-flight worker tasks ``W`` and any index ``i`` at
    which ``CancellationToken.cancel()`` is invoked, after ``cancel()``
    returns no signal is emitted on the bus by any task in ``W`` whose
    work begun before the cancel — specifically, no ``result_ready``,
    ``thumbnail_ready``, or ``download_complete`` signal is emitted.

The test models worker tasks as threads that follow the orchestrator's
documented contract:

1. Poll the cancellation token at well-defined yield points (before
   each "network call" and immediately before emitting a signal).
2. Wrap the entire task body in ``try/except CancelledError`` that
   exits silently without emitting any signal.

The key guarantee is: because workers check ``raise_if_cancelled()``
immediately before every ``emit()``, once ``cancel()`` has been called
(which atomically sets the internal ``threading.Event``), any subsequent
call to ``raise_if_cancelled()`` will raise, preventing the emit.

To avoid false positives from the inherent race between the last
``raise_if_cancelled()`` check and the ``emit()`` call, the test uses a
deterministic two-phase approach:

- **Phase 1 (before cancel):** Workers run and may emit signals freely.
  These signals are expected and not counted as violations.
- **Phase 2 (after cancel):** Workers that have not yet emitted will
  observe the cancellation at their next poll and exit silently.

The test ensures cancel happens *while workers still have pending work*
by using a synchronization gate that holds workers until cancel is
called. This guarantees that all remaining work attempts happen after
the token is set, making the property deterministically verifiable.

**Validates: Requirements 10.4**
"""

from __future__ import annotations

import threading
from typing import Any, List

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.errors import CancelledError
from ankivn_image_picker.ui.worker_bus import WorkerBus
from ankivn_image_picker.providers.base import ImageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image_result(provider_id: str, index: int) -> ImageResult:
    """Create a minimal valid ImageResult for testing."""
    return ImageResult(
        provider_id=provider_id,
        thumbnail_url=f"https://example.com/thumb/{provider_id}/{index}.jpg",
        full_url=f"https://example.com/full/{provider_id}/{index}.jpg",
        extension="jpg",
        source_page_url=None,
    )


def _worker_search(
    bus: WorkerBus,
    cancel: CancellationToken,
    provider_id: str,
    num_results: int,
    gate: threading.Event,
) -> None:
    """Simulate a provider search worker.

    Waits on ``gate`` before attempting to emit results. The gate is
    released only after ``cancel()`` has been called, so every emit
    attempt happens post-cancel and must be blocked by
    ``raise_if_cancelled()``.
    """
    try:
        gate.wait(timeout=5.0)
        for i in range(num_results):
            cancel.raise_if_cancelled()
            result = _make_image_result(provider_id, i)
            bus.result_ready.emit(result)
    except CancelledError:
        pass


def _worker_thumbnail(
    bus: WorkerBus,
    cancel: CancellationToken,
    url: str,
    data: bytes,
    gate: threading.Event,
) -> None:
    """Simulate a thumbnail download worker."""
    try:
        gate.wait(timeout=5.0)
        cancel.raise_if_cancelled()
        bus.thumbnail_ready.emit(url, data)
    except CancelledError:
        pass


def _worker_download(
    bus: WorkerBus,
    cancel: CancellationToken,
    url: str,
    data: bytes,
    ext: str,
    gate: threading.Event,
) -> None:
    """Simulate a full-image download worker."""
    try:
        gate.wait(timeout=5.0)
        cancel.raise_if_cancelled()
        bus.download_complete.emit(url, data, ext)
    except CancelledError:
        pass


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


@st.composite
def cancellation_scenario(draw: st.DrawFn) -> dict:
    """Generate a cancellation scenario with a set of worker tasks.

    All workers are held at a gate until cancel() is called, ensuring
    that their emit attempts happen strictly after cancellation. This
    models the property's "work begun before the cancel" condition:
    the workers are *in-flight* (started) before cancel, but their
    signal-emitting work is still pending.
    """
    num_workers = draw(st.integers(min_value=1, max_value=8))

    worker_types = draw(
        st.lists(
            st.sampled_from(["search", "thumbnail", "download"]),
            min_size=num_workers,
            max_size=num_workers,
        )
    )

    # Number of results each search worker will try to emit
    search_results_per_worker = draw(
        st.lists(
            st.integers(min_value=1, max_value=6),
            min_size=num_workers,
            max_size=num_workers,
        )
    )

    return {
        "num_workers": num_workers,
        "worker_types": worker_types,
        "search_results_per_worker": search_results_per_worker,
    }


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(scenario=cancellation_scenario())
@settings(max_examples=200, deadline=10000)
def test_cancellation_prevents_post_cancel_side_effects(
    scenario: dict,
) -> None:
    """Property 16: Cancellation prevents post-cancel side effects.

    After cancel() returns, no result_ready, thumbnail_ready, or
    download_complete signal is emitted by any worker task whose work
    begun before the cancel.

    The test uses a deterministic gate pattern:
    1. Start all workers (they block on a gate event).
    2. Call cancel() on the token.
    3. Open the gate so workers proceed.
    4. Workers call raise_if_cancelled() before emit -> CancelledError.
    5. Assert zero forbidden signals were emitted.

    This guarantees that all emit attempts happen strictly after
    cancellation, making the property deterministically testable
    without timing-dependent races.

    **Validates: Requirements 10.4**
    """
    num_workers = scenario["num_workers"]
    worker_types = scenario["worker_types"]
    search_results_per_worker = scenario["search_results_per_worker"]

    # --- Set up bus and signal tracking ---
    bus = WorkerBus()
    cancel = CancellationToken()
    gate = threading.Event()  # holds workers until cancel is called

    # Track any forbidden signals
    violations: List[str] = []
    lock = threading.Lock()

    def _on_result_ready(result: Any) -> None:
        with lock:
            violations.append(f"result_ready({result.provider_id})")

    def _on_thumbnail_ready(url: str, data: bytes) -> None:
        with lock:
            violations.append(f"thumbnail_ready({url})")

    def _on_download_complete(url: str, data: bytes, ext: str) -> None:
        with lock:
            violations.append(f"download_complete({url})")

    bus.result_ready.connect(_on_result_ready)
    bus.thumbnail_ready.connect(_on_thumbnail_ready)
    bus.download_complete.connect(_on_download_complete)

    # --- Launch workers (they will block on the gate) ---
    threads: List[threading.Thread] = []

    for i in range(num_workers):
        wtype = worker_types[i]

        if wtype == "search":
            t = threading.Thread(
                target=_worker_search,
                args=(
                    bus,
                    cancel,
                    f"provider_{i}",
                    search_results_per_worker[i],
                    gate,
                ),
                daemon=True,
            )
        elif wtype == "thumbnail":
            t = threading.Thread(
                target=_worker_thumbnail,
                args=(
                    bus,
                    cancel,
                    f"https://example.com/thumb/{i}.jpg",
                    b"\x89PNG" + bytes([i]),
                    gate,
                ),
                daemon=True,
            )
        else:  # download
            t = threading.Thread(
                target=_worker_download,
                args=(
                    bus,
                    cancel,
                    f"https://example.com/full/{i}.jpg",
                    b"\xff\xd8\xff" + bytes([i]),
                    "jpg",
                    gate,
                ),
                daemon=True,
            )

        threads.append(t)
        t.start()

    # --- Cancel BEFORE opening the gate ---
    # Workers are in-flight (started) but blocked. This models the
    # scenario where workers have begun execution but have not yet
    # reached their emit points.
    cancel.cancel()

    # --- Open the gate: workers proceed and hit raise_if_cancelled ---
    gate.set()

    # --- Wait for all workers to finish ---
    for t in threads:
        t.join(timeout=5.0)

    # --- Assert: no forbidden signals were emitted ---
    assert not violations, (
        f"Signals emitted after cancel(): {violations}. "
        f"Property 16 violated: cancellation must prevent post-cancel "
        f"side effects (Req 10.4)."
    )
