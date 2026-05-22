"""Property test for thumbnail cache round-trip and skip-on-hit.

Implements **Property 7** from the design document:

    For any URL ``u`` and any byte string ``b``, after ``cache.put(u, b)``:

    - ``cache.get(u) == b``.
    - ``ThumbnailDownloader(cache, http, ...).fetch(result_with_url=u)``
      makes zero ``http.get`` calls and emits a single
      ``thumbnail_ready(u, b)`` signal.

**Validates: Requirements 5.1, 5.2**

The test exercises two complementary aspects of the cache contract:

1. **Round-trip integrity** (Req 5.1): any bytes stored via ``put`` are
   returned byte-for-byte by a subsequent ``get`` on the same URL key.
   This is the fundamental correctness guarantee of the on-disk cache.

2. **Skip-on-hit** (Req 5.2): when the ``ThumbnailDownloader`` is asked
   to fetch a thumbnail whose URL is already in the cache, it must
   serve the cached bytes without issuing any HTTP request. This is
   verified by injecting a spy ``HttpClient`` that records calls and
   asserting zero calls were made, while also asserting that exactly
   one ``thumbnail_ready`` signal was emitted with the correct payload.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import MagicMock

from hypothesis import given, settings, strategies as st

from ankivn_image_picker.cache import ThumbnailCache
from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.orchestrator import ThumbnailDownloader
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: URLs used as cache keys. We use http/https URLs with printable ASCII
#: to stay realistic while keeping shrink output readable. The min_size
#: ensures the URL is non-empty (ImageResult rejects empty strings).
_url_strategy = st.from_regex(
    r"https?://[a-z0-9][a-z0-9._/-]{0,80}", fullmatch=True
)

#: Byte payloads representing thumbnail image data. Non-empty because
#: the cache stores actual image bytes; empty bytes would be rejected
#: by ``is_valid_image_response`` on the miss path, but on the hit path
#: the cache returns whatever was stored. We include non-empty bytes
#: to match realistic usage.
_thumbnail_bytes = st.binary(min_size=1, max_size=512)


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@given(url=_url_strategy, data=_thumbnail_bytes)
@settings(max_examples=200)
def test_cache_roundtrip_preserves_bytes(url: str, data: bytes) -> None:
    """Validates: Requirements 5.1 (Property 7, part 1).

    For any URL ``u`` and any byte string ``b``, after
    ``cache.put(u, b)``, ``cache.get(u) == b``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cache = ThumbnailCache(root=Path(tmp), max_bytes=10 * 1024 * 1024)
        cache.put(url, data)
        retrieved = cache.get(url)
        assert retrieved == data, (
            f"Round-trip failed for url={url!r}: "
            f"put {len(data)} bytes, got back {retrieved!r}"
        )


@given(url=_url_strategy, data=_thumbnail_bytes)
@settings(max_examples=200)
def test_thumbnail_downloader_skips_http_on_cache_hit(
    url: str, data: bytes
) -> None:
    """Validates: Requirements 5.2 (Property 7, part 2).

    For any URL ``u`` and any byte string ``b``, after
    ``cache.put(u, b)``, ``ThumbnailDownloader(...).fetch(result)``
    makes zero ``http.get`` calls and emits a single
    ``thumbnail_ready(u, b)`` signal.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Set up cache with the entry pre-populated
        cache = ThumbnailCache(root=Path(tmp), max_bytes=10 * 1024 * 1024)
        cache.put(url, data)

        # Spy HttpClient that records all calls
        http = MagicMock()
        http.get = MagicMock(side_effect=AssertionError(
            "http.get should not be called on cache hit"
        ))

        # WorkerBus with signal recording
        bus = WorkerBus()
        signals_received: List[Tuple[str, bytes]] = []
        bus.thumbnail_ready.connect(
            lambda u, b: signals_received.append((u, b))
        )

        failed_signals: List[Tuple[str, str]] = []
        bus.thumbnail_failed.connect(
            lambda u, msg: failed_signals.append((u, msg))
        )

        # CancellationToken - not cancelled
        cancel = CancellationToken()

        # Build an ImageResult with the pre-cached thumbnail URL
        result = ImageResult(
            provider_id="test_provider",
            thumbnail_url=url,
            full_url="https://example.com/full.jpg",
            extension="jpg",
        )

        # Execute the downloader
        downloader = ThumbnailDownloader(
            cache=cache, http=http, bus=bus, cancel=cancel
        )
        downloader.fetch(result)

        # Assert: zero HTTP calls
        http.get.assert_not_called()

        # Assert: exactly one thumbnail_ready signal with correct payload
        assert len(signals_received) == 1, (
            f"Expected exactly 1 thumbnail_ready signal, got {len(signals_received)}"
        )
        received_url, received_bytes = signals_received[0]
        assert received_url == url
        assert received_bytes == data

        # Assert: no thumbnail_failed signals
        assert len(failed_signals) == 0, (
            f"Expected 0 thumbnail_failed signals, got {len(failed_signals)}"
        )
