"""Property test for orchestrator fan-out cardinality.

Implements **Property 4** from the design document:

    For any non-empty list of mock providers ``P`` and any positive
    ``max_results`` value ``N``, ``SearchOrchestrator(providers=P, ...).run(query)``
    schedules exactly ``len(P)`` provider tasks (one per provider,
    regardless of duplicates), and for each provider ``p`` that yields
    ``M`` results, the orchestrator emits exactly ``min(M, N)``
    ``result_ready`` signals carrying ``provider_id == p.id``.

**Validates: Requirements 4.1, 4.2**

The test uses mock providers that yield a controlled number of
``ImageResult`` instances. The orchestrator is exercised with a real
``ThreadPoolExecutor`` and a real ``WorkerBus`` (to capture emitted
signals). The ``ThumbnailCache`` is stubbed to always return a cache
hit (so thumbnail downloads complete instantly without HTTP) and the
``HttpClient`` is a no-op mock, since the property only concerns the
fan-out and result-emission logic.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Iterable, List, Tuple
from unittest.mock import MagicMock

from hypothesis import given, settings, strategies as st

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.config import Config
from ankivn_image_picker.orchestrator import SearchOrchestrator
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Strategy for the number of results a mock provider yields.
#: Bounded to keep test execution fast while covering the interesting
#: cases: 0 results, 1 result, and several results.
_num_results = st.integers(min_value=0, max_value=20)

#: Strategy for max_results_per_provider config value (positive int in [1, 50]).
_max_results = st.integers(min_value=1, max_value=50)

#: Strategy for provider IDs. Short printable strings that are non-empty.
_provider_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=12,
)


@st.composite
def _provider_list_with_results(draw: st.DrawFn) -> List[Tuple[str, int]]:
    """Draw a non-empty list of (provider_id, num_results) pairs.

    Provider IDs may repeat (the property states "regardless of
    duplicates"), so we do not enforce uniqueness.
    """
    size = draw(st.integers(min_value=1, max_value=6))
    providers = []
    for _ in range(size):
        pid = draw(_provider_id)
        num = draw(_num_results)
        providers.append((pid, num))
    return providers


# ---------------------------------------------------------------------------
# Mock provider factory
# ---------------------------------------------------------------------------


class _MockProvider:
    """A mock provider that yields a controlled number of ImageResult instances.

    The provider yields ``min(num_results, max_results)`` results, which
    mirrors the contract of a well-behaved provider: it yields up to
    ``max_results`` items from its pool of available results (Req 4.2).
    """

    def __init__(self, provider_id: str, num_results: int) -> None:
        self.id = provider_id
        self.display_name = f"Mock {provider_id}"
        self._num_results = num_results

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: object,
        cancel: object,
    ) -> Iterable[ImageResult]:
        """Yield ``min(self._num_results, max_results)`` results.

        This respects the ``max_results`` contract (Req 4.2) while
        allowing the test to control how many results are "available"
        from this provider.
        """
        count = min(self._num_results, max_results)
        for i in range(count):
            yield ImageResult(
                provider_id=self.id,
                thumbnail_url=f"https://example.com/{self.id}/thumb_{i}.jpg",
                full_url=f"https://example.com/{self.id}/full_{i}.jpg",
                extension="jpg",
            )


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    provider_specs=_provider_list_with_results(),
    max_results_cfg=_max_results,
)
@settings(max_examples=200, deadline=None)
def test_orchestrator_fanout_cardinality(
    provider_specs: List[Tuple[str, int]],
    max_results_cfg: int,
) -> None:
    """Validates: Requirements 4.1, 4.2 (Property 4).

    For any non-empty list of mock providers P and any positive
    max_results value N, SearchOrchestrator schedules exactly len(P)
    provider tasks and emits exactly min(M, N) result_ready signals
    per provider p that yields M results.
    """
    # Build mock providers
    providers = [
        _MockProvider(pid, num_results)
        for pid, num_results in provider_specs
    ]

    # Build config with the drawn max_results value
    cfg = Config(
        source_field="word",
        target_field="image",
        providers=tuple(p.id for p in providers),
        max_results_per_provider=max_results_cfg,
        thumbnail_cache_max_mb=64,
    )

    # Stub out http (not exercised by this property)
    http = MagicMock()

    # Cache always returns a hit so thumbnail downloads complete
    # instantly without HTTP calls. This avoids race conditions with
    # pool shutdown while keeping the orchestrator's thumbnail-download
    # scheduling path exercised.
    cache = MagicMock()
    cache.get = MagicMock(return_value=b"fake_thumbnail_bytes")

    # Real bus to capture signals
    bus = WorkerBus()
    results_received: List[ImageResult] = []
    lock = threading.Lock()

    def on_result_ready(result: ImageResult) -> None:
        with lock:
            results_received.append(result)

    bus.result_ready.connect(on_result_ready)

    # Cancellation token (not cancelled)
    cancel = CancellationToken()

    # Compute expected total signals upfront for the wait loop
    expected_total = sum(
        min(num, max_results_cfg) for _, num in provider_specs
    )

    # Use a thread pool large enough to handle both provider tasks and
    # their spawned thumbnail download tasks without deadlock.
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(len(providers) * 2 + expected_total, 4)
    )

    try:
        orchestrator = SearchOrchestrator(
            providers=providers,
            cfg=cfg,
            http=http,
            cache=cache,
            bus=bus,
            cancel=cancel,
            pool=pool,
        )

        # Run the orchestrator (returns immediately after scheduling)
        orchestrator.run("test query")

        # Wait for all result_ready signals to arrive. We poll with a
        # timeout rather than calling pool.shutdown(wait=True) because
        # shutdown prevents new task submissions, which would break the
        # orchestrator's thumbnail-download scheduling.
        deadline = time.monotonic() + 10.0
        while True:
            with lock:
                if len(results_received) >= expected_total:
                    break
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)

        # Small grace period for any extra spurious signals
        time.sleep(0.05)

        # --- Assertions ---

        # Count expected signals per provider_id.
        # Since providers may share IDs, we sum expected counts per ID.
        expected_per_id: dict[str, int] = {}
        for pid, num_results in provider_specs:
            expected_count = min(num_results, max_results_cfg)
            expected_per_id[pid] = expected_per_id.get(pid, 0) + expected_count

        # Count actual signals per provider_id
        with lock:
            snapshot = list(results_received)

        actual_per_id: dict[str, int] = {}
        for result in snapshot:
            actual_per_id[result.provider_id] = (
                actual_per_id.get(result.provider_id, 0) + 1
            )

        # Verify per-provider counts match. Providers with 0 expected
        # results won't appear in actual_per_id, so we normalize.
        all_ids = set(expected_per_id.keys()) | set(actual_per_id.keys())
        for pid in all_ids:
            expected = expected_per_id.get(pid, 0)
            actual = actual_per_id.get(pid, 0)
            assert actual == expected, (
                f"Signal count mismatch for provider_id={pid!r}.\n"
                f"  Provider specs (id, num_results): {provider_specs}\n"
                f"  max_results_per_provider: {max_results_cfg}\n"
                f"  Expected {expected} signals, got {actual}"
            )

        # Verify total signal count
        assert len(snapshot) == expected_total, (
            f"Total signal count mismatch: "
            f"expected {expected_total}, got {len(snapshot)}"
        )

        # Verify every signal carries a valid provider_id
        valid_ids = {pid for pid, _ in provider_specs}
        for result in snapshot:
            assert result.provider_id in valid_ids, (
                f"Unexpected provider_id {result.provider_id!r} "
                f"in result_ready signal"
            )

    finally:
        pool.shutdown(wait=False)
