"""Search orchestration and download workers for the AnkiVN Smart Image Picker.

This module implements the background-thread logic that fans out search
requests to multiple providers concurrently, downloads thumbnails into
the cache, and fetches full-resolution images on user selection.

Threading model
---------------
Every public method in this module runs on a Qt thread-pool worker.
Workers communicate results back to the main thread exclusively via
signals on the :class:`~ankivn_image_picker.ui.worker_bus.WorkerBus`.
Workers MUST NOT touch Qt widgets, the Anki collection, or the editor.

Cancellation
------------
Every worker polls a shared :class:`CancellationToken` before issuing
network calls and between processing steps. When the token fires, the
worker raises :class:`CancelledError`, which the outer ``try``/``except``
silently swallows so no signal is emitted after cancellation (Req 10.4,
Property 16).

Concurrency
-----------
``SearchOrchestrator.run(query)`` submits one task per configured
provider to a thread pool. Provider tasks run concurrently so one slow
provider does not block the others (Req 4.3). The orchestrator itself
does not wait for tasks to complete; it returns immediately after
scheduling.
"""

from __future__ import annotations

import concurrent.futures
from typing import TYPE_CHECKING, Callable, List, Optional

from .errors import CancelledError, DownloadError, InvalidImageError, ProviderError
from .http import is_valid_image_response

if TYPE_CHECKING:  # pragma: no cover
    from .cache import ThumbnailCache
    from .cancellation import CancellationToken
    from .config import Config
    from .http import HttpClient
    from .providers.base import ImageProvider, ImageResult
    from .search_cache import SearchCache
    from .ui.worker_bus import WorkerBus


class SearchOrchestrator:
    """Fans out search requests to all configured providers concurrently.

    Each provider runs in its own thread-pool task. Results stream back
    to the UI via the worker bus as they arrive. After each result, a
    thumbnail download is scheduled.

    Parameters
    ----------
    providers:
        The list of provider instances to query.
    cfg:
        Validated configuration (supplies ``max_results_per_provider``).
    http:
        HTTP client instance shared across workers.
    cache:
        Thumbnail cache for skip-on-hit behaviour.
    bus:
        Signal hub for delivering results to the main thread.
    cancel:
        Cooperative cancellation token.
    pool:
        Optional thread pool executor. If not provided, a default
        ``ThreadPoolExecutor`` is created with ``max_workers`` equal to
        the number of providers (capped at 8).
    """

    def __init__(
        self,
        providers: List["ImageProvider"],
        cfg: "Config",
        http: "HttpClient",
        cache: "ThumbnailCache",
        bus: "WorkerBus",
        cancel: "CancellationToken",
        pool: Optional[concurrent.futures.ThreadPoolExecutor] = None,
        search_cache: Optional["SearchCache"] = None,
    ) -> None:
        self._providers = providers
        self._cfg = cfg
        self._http = http
        self._cache = cache
        self._bus = bus
        self._cancel = cancel
        self._search_cache = search_cache
        self._pool = pool or concurrent.futures.ThreadPoolExecutor(
            # Pool serves both provider searches and thumbnail
            # downloads. With persistent connections enabled in
            # HttpClient, raising the cap from 8 to 16 lets thumbnail
            # downloads parallelise without saturating any single
            # provider host (each host has its own pool slot via the
            # adapter's pool_maxsize).
            max_workers=max(len(providers) * 2, 16) if providers else 1
        )

    def run(self, query: str, *, page: int = 1) -> None:
        """Submit one search task per provider to the thread pool.

        This method returns immediately after scheduling; it does NOT
        wait for tasks to complete. Each task runs concurrently so one
        slow provider does not block the others (Req 4.3).

        ``page`` enables pagination: passing page=2, 3, ... fetches
        the next batch of results from each provider.
        """
        for provider in self._providers:
            self._pool.submit(self._run_provider, provider, query, page)

    def _run_provider(
        self, provider: "ImageProvider", query: str, page: int = 1
    ) -> None:
        """Execute a single provider search task on a worker thread."""
        from .provider_info import get_provider_limit

        # Get the effective limit for this specific provider
        max_results = get_provider_limit(provider.id, self._cfg)

        # Cache lookup is page-1 only — pagination always hits the
        # network so users explicitly clicking "Load More" still see
        # fresh results past the initial page.
        if page == 1 and self._search_cache is not None:
            try:
                cached_results = self._search_cache.get(provider.id, query)
            except Exception:
                cached_results = None
            if cached_results:
                try:
                    for result in cached_results:
                        self._cancel.raise_if_cancelled()
                        self._bus.result_ready.emit(result)
                        self._pool.submit(self._download_thumbnail, result)
                    return
                except CancelledError:
                    return

        try:
            self._cancel.raise_if_cancelled()
            # Try with page kwarg; fall back to old signature for
            # providers that don't yet accept it.
            try:
                raw_results = provider.search(
                    query,
                    max_results=max_results,
                    http=self._http,
                    cancel=self._cancel,
                    page=page,
                )
            except TypeError:
                raw_results = provider.search(
                    query,
                    max_results=max_results,
                    http=self._http,
                    cancel=self._cancel,
                )
            # Materialise so we can both emit and cache. ``search`` may
            # return a generator, and we want the same list flowing to
            # the bus and the disk.
            results = list(raw_results)
            for result in results:
                self._cancel.raise_if_cancelled()
                self._bus.result_ready.emit(result)
                # Schedule thumbnail download
                self._pool.submit(self._download_thumbnail, result)

            # Write-through to disk so a later same-query open skips
            # the provider API entirely.
            if page == 1 and self._search_cache is not None and results:
                try:
                    self._search_cache.put(provider.id, query, results)
                except Exception:
                    pass
        except CancelledError:
            # Silently swallow — no signal after cancellation (Property 16)
            return
        except ProviderError as exc:
            self._bus.provider_failed.emit(provider.id, str(exc))
        except Exception as exc:
            # Last-resort: convert unexpected errors to provider_failed
            self._bus.provider_failed.emit(
                provider.id, f"Unexpected error: {exc}"
            )

    def _download_thumbnail(self, result: "ImageResult") -> None:
        """Download a thumbnail, using cache when available."""
        downloader = ThumbnailDownloader(
            cache=self._cache,
            http=self._http,
            bus=self._bus,
            cancel=self._cancel,
        )
        downloader.fetch(result)


class ThumbnailDownloader:
    """Downloads and caches thumbnails for image results.

    On cache hit, emits ``thumbnail_ready`` immediately with zero HTTP
    calls. On cache miss, downloads via ``HttpClient``, validates the
    response, stores in cache, and emits ``thumbnail_ready``. On any
    failure, emits ``thumbnail_failed``.
    """

    def __init__(
        self,
        cache: "ThumbnailCache",
        http: "HttpClient",
        bus: "WorkerBus",
        cancel: "CancellationToken",
    ) -> None:
        self._cache = cache
        self._http = http
        self._bus = bus
        self._cancel = cancel

    def fetch(self, result: "ImageResult") -> None:
        """Fetch a thumbnail, emitting the appropriate signal."""
        try:
            self._cancel.raise_if_cancelled()

            # Check cache first (Req 5.2, Property 7)
            cached = self._cache.get(result.thumbnail_url)
            if cached is not None:
                self._bus.thumbnail_ready.emit(result.thumbnail_url, cached)
                return

            # Cache miss — download
            self._cancel.raise_if_cancelled()
            response = self._http.get(result.thumbnail_url, cancel=self._cancel)

            if not is_valid_image_response(response.body, response.content_type):
                self._bus.thumbnail_failed.emit(
                    result.thumbnail_url, "Invalid image response"
                )
                return

            # Store in cache and emit
            self._cache.put(result.thumbnail_url, response.body)
            self._bus.thumbnail_ready.emit(result.thumbnail_url, response.body)

        except CancelledError:
            return  # Silently swallow
        except Exception as exc:
            self._bus.thumbnail_failed.emit(result.thumbnail_url, str(exc))


class FullImageDownloader:
    """Downloads the full-resolution image on user selection.

    Streams the image, polls cancellation between chunks, validates the
    response, and emits progress/complete/failed signals.
    """

    def __init__(
        self,
        http: "HttpClient",
        bus: "WorkerBus",
        cancel: "CancellationToken",
    ) -> None:
        self._http = http
        self._bus = bus
        self._cancel = cancel

    def fetch(self, result: "ImageResult") -> None:
        """Download the full image and emit the appropriate signal.

        If ``result.fallback_full_urls`` is non-empty, attempt them in
        order when the primary URL fails (429, 5xx, network error,
        invalid image). The first successful response wins.
        """
        # Build the list of URLs to try: primary first, then fallbacks.
        urls_to_try: list[str] = [result.full_url]
        for u in result.fallback_full_urls:
            if u not in urls_to_try:
                urls_to_try.append(u)

        last_error: Optional[Exception] = None
        report_url = result.full_url  # URL the UI is showing progress for

        try:
            self._cancel.raise_if_cancelled()
            self._bus.download_progress.emit(report_url, 0.0)

            for attempt_idx, attempt_url in enumerate(urls_to_try):
                self._cancel.raise_if_cancelled()
                try:
                    response = self._http.get(
                        attempt_url, cancel=self._cancel
                    )
                    if not is_valid_image_response(
                        response.body, response.content_type
                    ):
                        raise InvalidImageError(
                            f"Invalid image response from {attempt_url}"
                        )
                    # Success — emit progress + complete on the
                    # original URL (so the UI's progress mapping works).
                    self._bus.download_progress.emit(report_url, 1.0)
                    self._bus.download_complete.emit(
                        report_url, response.body, result.extension
                    )
                    return
                except CancelledError:
                    raise
                except (DownloadError, InvalidImageError) as exc:
                    last_error = exc
                    # Try the next fallback URL
                    continue

            # All URLs failed
            if last_error is not None:
                self._bus.download_failed.emit(report_url, str(last_error))
            else:
                self._bus.download_failed.emit(
                    report_url, "All URLs failed"
                )

        except CancelledError:
            return  # Silently swallow
        except Exception as exc:
            self._bus.download_failed.emit(
                report_url, f"Unexpected error: {exc}"
            )


__all__ = [
    "SearchOrchestrator",
    "ThumbnailDownloader",
    "FullImageDownloader",
]
