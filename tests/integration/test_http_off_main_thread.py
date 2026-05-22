"""Integration test: HTTP calls run off the main thread.

Task 10.7: Patch ``HttpClient.get`` to assert
``threading.current_thread() is not main_thread`` during invocation.

This proves that the orchestrator schedules all network I/O on
background threads so the Qt main thread (Anki UI) stays responsive
(Req 10.1).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, List
from unittest.mock import patch

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.http import HttpClient, HttpResponse
from ankivn_image_picker.orchestrator import SearchOrchestrator
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAIN_THREAD = threading.main_thread()


@dataclass(frozen=True)
class _FakeConfig:
    """Minimal config stand-in with only the fields the orchestrator uses."""

    source_field: str = "word"
    target_field: str = "image"
    providers: tuple = ("test_provider",)
    max_results_per_provider: int = 5
    thumbnail_cache_max_mb: int = 64


def _make_result(index: int = 1) -> ImageResult:
    return ImageResult(
        provider_id="test_provider",
        thumbnail_url=f"https://example.com/thumb_{index}.jpg",
        full_url=f"https://example.com/full_{index}.jpg",
        extension="jpg",
    )


class _HttpCallingProvider:
    """A mock provider that actually calls http.get, triggering the patch."""

    def __init__(self, results: List[ImageResult]) -> None:
        self.id = "test_provider"
        self.display_name = "Test Provider"
        self._results = results

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: HttpClient,
        cancel: CancellationToken,
    ) -> Iterable[ImageResult]:
        # Call http.get to trigger the thread assertion in the patch
        http.get(f"https://example.com/search?q={query}", cancel=cancel)
        return self._results[:max_results]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_http_get_runs_off_main_thread() -> None:
    """HttpClient.get is never invoked on the main thread (Req 10.1).

    Patches HttpClient.get to record the calling thread and assert it
    is NOT the main thread. The orchestrator fans out provider search
    tasks to a thread pool, so all HTTP calls should happen on worker
    threads.
    """
    call_threads: list[threading.Thread] = []
    call_event = threading.Event()

    original_get = HttpClient.get

    def _asserting_get(self: HttpClient, url: str, *, cancel: CancellationToken) -> HttpResponse:
        current = threading.current_thread()
        assert current is not _MAIN_THREAD, (
            f"HttpClient.get was called on the main thread! "
            f"Thread: {current.name}, URL: {url}"
        )
        call_threads.append(current)
        call_event.set()
        # Return a fake valid image response so the flow continues
        return HttpResponse(
            body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            content_type="image/png",
            url=url,
            status_code=200,
        )

    results = [_make_result(i) for i in range(3)]
    provider = _HttpCallingProvider(results)

    bus = WorkerBus()
    cancel = CancellationToken()
    cfg = _FakeConfig()
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)

        with patch.object(HttpClient, "get", _asserting_get):
            orch = SearchOrchestrator(
                providers=[provider],
                cfg=cfg,
                http=http,
                cache=cache,
                bus=bus,
                cancel=cancel,
            )

            # This test runs on the main thread; the orchestrator submits
            # tasks to a thread pool. If HttpClient.get were called on
            # the main thread, the assertion inside _asserting_get would
            # fail immediately.
            orch.run("test query")

            # Wait for at least one HTTP call to complete
            assert call_event.wait(timeout=10.0), (
                "HttpClient.get was never called — orchestrator may not "
                "be issuing HTTP requests"
            )

            # Give time for thumbnail downloads to also trigger
            time.sleep(1.0)

    # Verify all calls happened on non-main threads
    assert len(call_threads) > 0, "Expected at least one HttpClient.get call"
    for t in call_threads:
        assert t is not _MAIN_THREAD, (
            f"HttpClient.get was called on the main thread: {t.name}"
        )


def test_thumbnail_downloads_run_off_main_thread() -> None:
    """Thumbnail downloads via HttpClient.get also run off main thread.

    After the orchestrator emits results, it schedules thumbnail
    downloads. These must also happen on worker threads, not the main
    thread.
    """
    thumbnail_call_threads: list[threading.Thread] = []
    thumbnail_event = threading.Event()

    def _tracking_get(self: HttpClient, url: str, *, cancel: CancellationToken) -> HttpResponse:
        current = threading.current_thread()
        assert current is not _MAIN_THREAD, (
            f"HttpClient.get was called on the main thread! "
            f"Thread: {current.name}, URL: {url}"
        )
        # Track thumbnail-specific calls (URLs containing "thumb")
        if "thumb" in url:
            thumbnail_call_threads.append(current)
            thumbnail_event.set()
        # Return a fake valid image response
        return HttpResponse(
            body=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            content_type="image/png",
            url=url,
            status_code=200,
        )

    results = [_make_result(i) for i in range(3)]
    provider = _HttpCallingProvider(results)

    bus = WorkerBus()
    cancel = CancellationToken()
    cfg = _FakeConfig()
    http = HttpClient()

    with TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=64 * 1024 * 1024)

        with patch.object(HttpClient, "get", _tracking_get):
            orch = SearchOrchestrator(
                providers=[provider],
                cfg=cfg,
                http=http,
                cache=cache,
                bus=bus,
                cancel=cancel,
            )

            orch.run("hello")

            # Wait for thumbnail downloads to be triggered
            assert thumbnail_event.wait(timeout=10.0), (
                "No thumbnail download calls were made"
            )

            time.sleep(1.0)

    # All thumbnail downloads should have happened off the main thread
    assert len(thumbnail_call_threads) > 0, (
        "Expected at least one thumbnail HttpClient.get call"
    )
    for t in thumbnail_call_threads:
        assert t is not _MAIN_THREAD, (
            f"Thumbnail download ran on main thread: {t.name}"
        )
