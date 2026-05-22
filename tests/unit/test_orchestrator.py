"""Unit tests for :mod:`ankivn_image_picker.orchestrator`.

Task 6.7: Cover the concurrent-providers case (a barrier-based mock
proves no provider blocks the others) and the all-providers-fail
empty-state case.

Requirements: 4.3, 4.6
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, List

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.errors import ProviderError
from ankivn_image_picker.http import HttpClient
from ankivn_image_picker.orchestrator import SearchOrchestrator
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeConfig:
    """Minimal config stand-in with only the fields the orchestrator uses."""

    source_field: str = "word"
    target_field: str = "image"
    providers: tuple = ("fake_a", "fake_b")
    max_results_per_provider: int = 12
    thumbnail_cache_max_mb: int = 64


def _make_result(provider_id: str, index: int = 1) -> ImageResult:
    """Create a minimal valid ImageResult for testing."""
    return ImageResult(
        provider_id=provider_id,
        thumbnail_url=f"https://example.com/{provider_id}/thumb_{index}.jpg",
        full_url=f"https://example.com/{provider_id}/full_{index}.jpg",
        extension="jpg",
    )


class _BarrierProvider:
    """A mock provider that waits on a barrier before returning results.

    This proves that providers run concurrently: if N providers all wait
    on a barrier with parties=N, they can only proceed if all N threads
    are running simultaneously. If the orchestrator ran them sequentially,
    the barrier would deadlock and the test would time out.
    """

    def __init__(self, provider_id: str, barrier: threading.Barrier, results: List[ImageResult]) -> None:
        self.id = provider_id
        self.display_name = f"Fake {provider_id}"
        self._barrier = barrier
        self._results = results
        self.search_called = threading.Event()

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: HttpClient,
        cancel: CancellationToken,
    ) -> Iterable[ImageResult]:
        # Signal that search was called
        self.search_called.set()
        # Wait for all providers to reach this point — proves concurrency
        self._barrier.wait(timeout=5.0)
        return self._results[:max_results]


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
        http: HttpClient,
        cancel: CancellationToken,
    ) -> Iterable[ImageResult]:
        raise ProviderError(f"Provider {self.id} failed: HTTP 503")


# ---------------------------------------------------------------------------
# Test: Concurrent providers (Req 4.3)
# ---------------------------------------------------------------------------


def test_concurrent_providers_do_not_block_each_other() -> None:
    """Barrier-based mock proves no provider blocks the others (Req 4.3).

    Three providers share a barrier with parties=3. Each provider's
    search method waits on the barrier before returning results. If the
    orchestrator ran providers sequentially, the barrier would never
    reach 3 parties and would deadlock (timeout). The test passing
    proves all three providers execute concurrently.
    """
    num_providers = 3
    barrier = threading.Barrier(num_providers)

    providers = []
    for i in range(num_providers):
        pid = f"provider_{i}"
        results = [_make_result(pid, j) for j in range(2)]
        providers.append(_BarrierProvider(pid, barrier, results))

    bus = WorkerBus()
    cancel = CancellationToken()
    cfg = _FakeConfig(max_results_per_provider=5)
    http = HttpClient()  # Not actually used by barrier providers

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)

        received_results: list = []
        bus.result_ready.connect(lambda r: received_results.append(r))

        failed: list = []
        bus.provider_failed.connect(lambda pid, msg: failed.append((pid, msg)))

        orch = SearchOrchestrator(
            providers=providers,
            cfg=cfg,
            http=http,
            cache=cache,
            bus=bus,
            cancel=cancel,
        )

        orch.run("test query")

        # Wait for all providers to have been called (barrier passed)
        for p in providers:
            assert p.search_called.wait(timeout=10.0), (
                f"Provider {p.id} was never called — orchestrator may not "
                f"be running providers concurrently"
            )

        # Give a moment for signals to be emitted after barrier passes
        time.sleep(0.5)

    # All providers should have returned results, none should have failed
    assert len(failed) == 0, f"Unexpected failures: {failed}"
    # Each provider yields 2 results, 3 providers = 6 total
    assert len(received_results) == 6, (
        f"Expected 6 results from 3 concurrent providers, got {len(received_results)}"
    )

    # Verify results came from all providers
    provider_ids = {r.provider_id for r in received_results}
    assert provider_ids == {"provider_0", "provider_1", "provider_2"}


def test_concurrent_providers_two_providers_barrier() -> None:
    """Simpler two-provider barrier test confirming concurrency (Req 4.3).

    Two providers share a barrier with parties=2. If they don't run
    concurrently, the barrier deadlocks.
    """
    barrier = threading.Barrier(2)

    provider_a = _BarrierProvider(
        "alpha", barrier, [_make_result("alpha", 1)]
    )
    provider_b = _BarrierProvider(
        "beta", barrier, [_make_result("beta", 1)]
    )

    bus = WorkerBus()
    cancel = CancellationToken()
    cfg = _FakeConfig(max_results_per_provider=10)
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)

        received_results: list = []
        bus.result_ready.connect(lambda r: received_results.append(r))

        orch = SearchOrchestrator(
            providers=[provider_a, provider_b],
            cfg=cfg,
            http=http,
            cache=cache,
            bus=bus,
            cancel=cancel,
        )

        orch.run("hello")

        # Both must pass the barrier within timeout
        assert provider_a.search_called.wait(timeout=10.0)
        assert provider_b.search_called.wait(timeout=10.0)

        time.sleep(0.5)

    assert len(received_results) == 2
    provider_ids = {r.provider_id for r in received_results}
    assert provider_ids == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# Test: All providers fail → empty state (Req 4.6)
# ---------------------------------------------------------------------------


def test_all_providers_fail_emits_provider_failed_for_each() -> None:
    """When every provider fails, bus emits provider_failed for each (Req 4.6).

    If every Image_Provider fails for a given Search_Query, the add-on
    should display a message stating that no results were retrieved.
    The orchestrator's role is to emit provider_failed for each failing
    provider and emit zero result_ready signals, which the UI layer
    interprets as the "no results" empty state.
    """
    providers = [
        _FailingProvider("unsplash"),
        _FailingProvider("pixabay"),
        _FailingProvider("pexels"),
    ]

    bus = WorkerBus()
    cancel = CancellationToken()
    cfg = _FakeConfig(max_results_per_provider=12)
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)

        received_results: list = []
        bus.result_ready.connect(lambda r: received_results.append(r))

        failures: list = []
        bus.provider_failed.connect(lambda pid, msg: failures.append((pid, msg)))

        orch = SearchOrchestrator(
            providers=providers,
            cfg=cfg,
            http=http,
            cache=cache,
            bus=bus,
            cancel=cancel,
        )

        orch.run("test query")

        # Wait for all provider tasks to complete
        time.sleep(1.0)

    # No results should have been emitted
    assert len(received_results) == 0, (
        f"Expected zero results when all providers fail, got {len(received_results)}"
    )

    # Each provider should have emitted exactly one provider_failed signal
    assert len(failures) == 3, (
        f"Expected 3 provider_failed signals, got {len(failures)}"
    )

    failed_ids = {pid for pid, _ in failures}
    assert failed_ids == {"unsplash", "pixabay", "pexels"}

    # Each failure message should mention the provider
    for pid, msg in failures:
        assert pid in msg, (
            f"Failure message for {pid} should mention the provider id"
        )


def test_single_provider_fails_emits_provider_failed() -> None:
    """A single failing provider emits exactly one provider_failed signal."""
    providers = [_FailingProvider("broken")]

    bus = WorkerBus()
    cancel = CancellationToken()
    cfg = _FakeConfig(max_results_per_provider=12)
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)

        received_results: list = []
        bus.result_ready.connect(lambda r: received_results.append(r))

        failures: list = []
        bus.provider_failed.connect(lambda pid, msg: failures.append((pid, msg)))

        orch = SearchOrchestrator(
            providers=providers,
            cfg=cfg,
            http=http,
            cache=cache,
            bus=bus,
            cancel=cancel,
        )

        orch.run("query")
        time.sleep(0.5)

    assert len(received_results) == 0
    assert len(failures) == 1
    assert failures[0][0] == "broken"
