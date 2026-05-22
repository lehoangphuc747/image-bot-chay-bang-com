"""Property test for provider error partition.

Implements **Property 5** from the design document:

    For any partition of the configured provider list into a failing
    subset ``S`` and a succeeding subset ``T = providers \\ S``, after
    ``SearchOrchestrator.run(query)`` completes the bus has emitted
    exactly one ``provider_failed`` signal for each provider in ``S``
    and at least one ``result_ready`` signal for each provider in ``T``
    whose mock yields at least one result; no ``result_ready`` signal
    is emitted whose ``provider_id`` is in ``S``.

**Validates: Requirements 4.5**

The test uses mock providers that either raise ``ProviderError`` (for
the failing subset) or yield a controlled number of ``ImageResult``
instances (for the succeeding subset). The orchestrator is exercised
with a real ``ThreadPoolExecutor`` and a real ``WorkerBus`` to capture
emitted signals. The ``ThumbnailCache`` is stubbed to always return a
cache hit so thumbnail downloads complete instantly without HTTP.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Iterable, List, Set, Tuple
from unittest.mock import MagicMock

from hypothesis import given, settings, strategies as st

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.config import Config
from ankivn_image_picker.errors import ProviderError
from ankivn_image_picker.orchestrator import SearchOrchestrator
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Strategy for provider IDs. Short printable strings that are non-empty.
_provider_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=12,
)

#: Strategy for the number of results a succeeding provider yields.
_num_results = st.integers(min_value=0, max_value=10)

#: Strategy for max_results_per_provider config value.
_max_results = st.integers(min_value=1, max_value=50)


@st.composite
def _provider_partition(draw: st.DrawFn) -> dict:
    """Draw a partition of providers into failing (S) and succeeding (T) sets.

    Returns a dict with:
      - 'failing': list of (provider_id,) tuples for providers that raise ProviderError
      - 'succeeding': list of (provider_id, num_results) tuples for providers that yield results
      - 'max_results': the max_results_per_provider config value

    We ensure at least one provider total (the property quantifies over
    non-empty provider lists). Provider IDs are made unique by appending
    an index suffix to avoid ambiguity in signal attribution.
    """
    num_failing = draw(st.integers(min_value=0, max_value=4))
    num_succeeding = draw(st.integers(min_value=0, max_value=4))

    # Ensure at least one provider total
    if num_failing == 0 and num_succeeding == 0:
        num_succeeding = 1

    failing = []
    for i in range(num_failing):
        base_id = draw(_provider_id)
        # Make unique by appending index
        failing.append(f"fail_{i}_{base_id}")

    succeeding = []
    for i in range(num_succeeding):
        base_id = draw(_provider_id)
        num = draw(_num_results)
        succeeding.append((f"ok_{i}_{base_id}", num))

    max_results_cfg = draw(_max_results)

    return {
        "failing": failing,
        "succeeding": succeeding,
        "max_results": max_results_cfg,
    }


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------


class _FailingProvider:
    """A mock provider that always raises ProviderError."""

    def __init__(self, provider_id: str) -> None:
        self.id = provider_id
        self.display_name = f"Failing {provider_id}"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: object,
        cancel: object,
    ) -> Iterable[ImageResult]:
        raise ProviderError(f"Simulated failure for {self.id}")


class _SucceedingProvider:
    """A mock provider that yields a controlled number of ImageResult instances."""

    def __init__(self, provider_id: str, num_results: int) -> None:
        self.id = provider_id
        self.display_name = f"Succeeding {provider_id}"
        self._num_results = num_results

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: object,
        cancel: object,
    ) -> Iterable[ImageResult]:
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


@given(partition=_provider_partition())
@settings(max_examples=200, deadline=None)
def test_provider_error_partition(partition: dict) -> None:
    """Property 5: Provider error partition.

    For any partition of the configured provider list into a failing
    subset S and a succeeding subset T = providers \\ S, after
    SearchOrchestrator.run(query) completes:

    1. The bus has emitted exactly one provider_failed signal for each
       provider in S.
    2. At least one result_ready signal for each provider in T whose
       mock yields at least one result.
    3. No result_ready signal is emitted whose provider_id is in S.

    **Validates: Requirements 4.5**
    """
    failing_ids: List[str] = partition["failing"]
    succeeding_specs: List[Tuple[str, int]] = partition["succeeding"]
    max_results_cfg: int = partition["max_results"]

    # Build providers
    providers = []
    for fid in failing_ids:
        providers.append(_FailingProvider(fid))
    for sid, num in succeeding_specs:
        providers.append(_SucceedingProvider(sid, num))

    if not providers:
        return  # Degenerate case; skip

    # Build config
    cfg = Config(
        source_field="word",
        target_field="image",
        providers=tuple(p.id for p in providers),
        max_results_per_provider=max_results_cfg,
        thumbnail_cache_max_mb=64,
    )

    # Stub HTTP (not exercised by this property)
    http = MagicMock()

    # Cache always returns a hit so thumbnail downloads complete instantly
    cache = MagicMock()
    cache.get = MagicMock(return_value=b"fake_thumbnail_bytes")

    # Real bus to capture signals
    bus = WorkerBus()

    results_received: List[ImageResult] = []
    failures_received: List[Tuple[str, str]] = []
    lock = threading.Lock()

    def on_result_ready(result: ImageResult) -> None:
        with lock:
            results_received.append(result)

    def on_provider_failed(provider_id: str, message: str) -> None:
        with lock:
            failures_received.append((provider_id, message))

    bus.result_ready.connect(on_result_ready)
    bus.provider_failed.connect(on_provider_failed)

    # Cancellation token (not cancelled)
    cancel = CancellationToken()

    # Compute expected counts
    expected_results_total = sum(
        min(num, max_results_cfg) for _, num in succeeding_specs
    )
    expected_failures_total = len(failing_ids)

    # Use a thread pool large enough to handle provider tasks and
    # thumbnail download tasks without deadlock.
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(len(providers) * 2 + expected_results_total, 4)
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

        # Run the orchestrator
        orchestrator.run("test query")

        # Wait for all signals to arrive
        expected_total_signals = expected_results_total + expected_failures_total
        deadline = time.monotonic() + 10.0
        while True:
            with lock:
                current_total = len(results_received) + len(failures_received)
                if current_total >= expected_total_signals:
                    break
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)

        # Small grace period for any extra spurious signals
        time.sleep(0.05)

        # --- Assertions ---
        with lock:
            results_snapshot = list(results_received)
            failures_snapshot = list(failures_received)

        failing_id_set: Set[str] = set(failing_ids)

        # 1. Exactly one provider_failed signal for each provider in S
        failed_provider_ids = [pid for pid, _ in failures_snapshot]
        for fid in failing_ids:
            count = failed_provider_ids.count(fid)
            assert count == 1, (
                f"Expected exactly 1 provider_failed signal for {fid!r}, "
                f"got {count}.\n"
                f"  Failing providers: {failing_ids}\n"
                f"  Succeeding providers: {succeeding_specs}\n"
                f"  All failures received: {failures_snapshot}"
            )

        # No provider_failed for providers NOT in S
        for pid, _ in failures_snapshot:
            assert pid in failing_id_set, (
                f"Unexpected provider_failed signal for {pid!r} which is "
                f"not in the failing set S.\n"
                f"  Failing set S: {failing_ids}\n"
                f"  All failures received: {failures_snapshot}"
            )

        # 2. At least one result_ready signal for each provider in T
        #    whose mock yields at least one result
        results_per_provider: dict[str, int] = {}
        for result in results_snapshot:
            results_per_provider[result.provider_id] = (
                results_per_provider.get(result.provider_id, 0) + 1
            )

        for sid, num in succeeding_specs:
            expected_count = min(num, max_results_cfg)
            if expected_count > 0:
                actual_count = results_per_provider.get(sid, 0)
                assert actual_count >= 1, (
                    f"Expected at least 1 result_ready signal for "
                    f"succeeding provider {sid!r} (yields {num}, "
                    f"max_results={max_results_cfg}), got {actual_count}.\n"
                    f"  Succeeding providers: {succeeding_specs}\n"
                    f"  Results per provider: {results_per_provider}"
                )

        # 3. No result_ready signal whose provider_id is in S
        for result in results_snapshot:
            assert result.provider_id not in failing_id_set, (
                f"result_ready signal emitted with provider_id={result.provider_id!r} "
                f"which is in the failing set S. This violates the property: "
                f"no result_ready signal should be emitted for a failing provider.\n"
                f"  Failing set S: {failing_ids}\n"
                f"  Result: {result}"
            )

    finally:
        pool.shutdown(wait=False)
