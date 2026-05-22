"""HTTP foundation for the AnkiVN Smart Image Picker add-on.

This module is the single point through which every worker thread talks
to the network. It exposes two pieces:

* :class:`HttpClient`, a thin wrapper over :func:`requests.get` that
  enforces the 15 second per-request budget (Req 10.2) by setting
  ``timeout=(5, 10)`` on the ``requests`` call, and that polls a
  :class:`~ankivn_image_picker.cancellation.CancellationToken` between
  every streamed chunk so a closed picker cannot leave a download
  running (Req 10.4).
* :func:`is_valid_image_response`, a pure predicate that rejects any
  response which is empty or which advertises a media type outside the
  allowed image set (Req 7.5, Property 11).

Every transport failure - connect timeout, read timeout, DNS failure,
connection reset, HTTP status ``>= 400`` - is translated into
:class:`~ankivn_image_picker.errors.DownloadError` so that callers do
not have to import or pattern-match on :mod:`requests`'s exception
hierarchy. Providers that need a :class:`ProviderError` for their
``provider_failed`` signal are expected to catch :class:`DownloadError`
and re-raise it as :class:`ProviderError` at their own boundary; the
HttpClient itself stays context-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, FrozenSet, Tuple

import requests

from .errors import DownloadError

if TYPE_CHECKING:  # pragma: no cover - import for type-checking only.
    from .cancellation import CancellationToken


# --- Timeout configuration -----------------------------------------------
#
# Req 10.2 mandates that no single network request exceeds 15 seconds.
# ``requests`` accepts a ``(connect, read)`` tuple; the two values sum to
# the cumulative budget. The connect leg covers DNS resolution and TCP
# handshake; the read leg covers each individual socket read while the
# body streams in. Streaming downloads therefore observe 10 seconds of
# inactivity before the request is aborted, which the test in task 2.11
# verifies.
_CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0
_READ_TIMEOUT_SECONDS: Final[float] = 10.0
DEFAULT_TIMEOUT: Final[Tuple[float, float]] = (
    _CONNECT_TIMEOUT_SECONDS,
    _READ_TIMEOUT_SECONDS,
)


# --- Image media type allow-list -----------------------------------------
#
# Property 11 (Req 7.5) frames image-response validation as a membership
# check against this exact set. Keep the constant module-level so tests
# can import and parametrise over it without duplicating the literal.
ALLOWED_IMAGE_MEDIA_TYPES: Final[FrozenSet[str]] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
    }
)


# Chunk size used while streaming response bodies. 16 KiB is a good
# trade-off: small enough that cancellation becomes effective within a
# few milliseconds on a typical broadband connection, large enough that
# we are not making per-byte syscalls for multi-megabyte images.
_CHUNK_SIZE_BYTES: Final[int] = 16 * 1024


@dataclass(frozen=True)
class HttpResponse:
    """A successful HTTP response materialised in memory.

    Returned by :meth:`HttpClient.get` once the body has been fully
    read. ``content_type`` is the raw ``Content-Type`` header value the
    server sent (or an empty string when the header is absent); callers
    should hand it directly to :func:`is_valid_image_response` rather
    than parsing it themselves so that the parsing rule lives in one
    place.
    """

    body: bytes
    content_type: str
    url: str
    status_code: int


class HttpClient:
    """Thin :mod:`requests` wrapper with cancellation and a fixed budget.

    Workers receive an instance via dependency injection so tests can
    substitute an in-memory fake (see ``tests/property/strategies.py``).
    The instance is intentionally cheap: it stores only the timeout
    tuple and creates no session, because each call uses a fresh
    request and the underlying connection pool is managed by
    ``requests`` itself.
    """

    def __init__(
        self, *, timeout: Tuple[float, float] = DEFAULT_TIMEOUT
    ) -> None:
        # Exposed as a public attribute so the timeout-budget unit test
        # (task 2.11, Req 10.2) can assert the tuple without monkey-
        # patching ``requests.get``.
        self.timeout: Tuple[float, float] = timeout
        # User-Agent compliant with Wikimedia's policy:
        # https://meta.wikimedia.org/wiki/User-Agent_policy
        # Includes contact info to avoid 429s from upload.wikimedia.org.
        self._headers = {
            "User-Agent": (
                "AnkiVN-Image-Picker/1.0 "
                "(https://github.com/ankivn/image-picker; ankivn@protonmail.com) "
                "python-requests"
            ),
        }

        # Persistent session with a generously sized connection pool.
        # Each thumbnail/full-image fetch is a separate request, but
        # they go to a small set of hosts (one per provider). Without
        # a session every request pays for a fresh TCP+TLS handshake
        # — typically 300-800 ms of round-trip latency. The session
        # below keeps connections alive so subsequent requests to the
        # same host complete in roughly one RTT.
        try:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry  # type: ignore[import-untyped]
        except Exception:  # pragma: no cover - urllib3 always ships with requests
            HTTPAdapter = None  # type: ignore[assignment]
            Retry = None  # type: ignore[assignment]

        self._session: requests.Session = requests.Session()
        if HTTPAdapter is not None:
            # pool_connections = number of distinct hosts to keep
            # connections for; pool_maxsize = max sockets per host.
            # Image providers we hit live on a handful of hosts and
            # may serve dozens of parallel thumbnails, so keep both
            # numbers comfortably above worker count.
            adapter_kwargs: dict = {
                "pool_connections": 16,
                "pool_maxsize": 32,
                "pool_block": False,
            }
            if Retry is not None:
                # No retries: providers handle their own back-off and
                # we don't want a stalled host to delay every request.
                adapter_kwargs["max_retries"] = Retry(total=0)
            adapter = HTTPAdapter(**adapter_kwargs)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        # Apply default headers once so we don't re-send the dict
        # per call.
        self._session.headers.update(self._headers)

    def get(
        self, url: str, *, cancel: "CancellationToken"
    ) -> HttpResponse:
        """Fetch ``url`` and return the body, raising on any failure.

        The request opens with ``stream=True`` so the body can be read
        chunk-by-chunk; the cancellation token is polled before the
        socket is even opened and again between every chunk. Any
        :mod:`requests` exception, plus any HTTP status of 400 or
        above, surfaces as :class:`DownloadError` with a message that
        names the URL so logs are useful.
        """
        # Poll once before issuing the request. If the dialog cancelled
        # while this task was queued in the thread pool, exit early
        # without opening a socket at all.
        cancel.raise_if_cancelled()

        try:
            response = self._session.get(
                url, timeout=self.timeout, stream=True,
            )
        except requests.Timeout as exc:
            raise DownloadError(f"timeout fetching {url}") from exc
        except requests.ConnectionError as exc:
            raise DownloadError(
                f"connection error fetching {url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            # Catch-all for the rest of the requests hierarchy
            # (invalid URL, too many redirects, SSL error, ...). All
            # of them are transport failures from the caller's point
            # of view, so collapsing them into DownloadError keeps the
            # caller's error matrix small.
            raise DownloadError(
                f"network error fetching {url}: {exc}"
            ) from exc

        try:
            # HTTP-level failure: anything 4xx/5xx is treated as a
            # transport failure (Req 4.5 / 7.4). The body is discarded
            # because the server's error payload is not useful for the
            # downstream image pipeline.
            if response.status_code >= 400:
                raise DownloadError(
                    f"HTTP {response.status_code} fetching {url}"
                )

            chunks: list[bytes] = []
            try:
                for chunk in response.iter_content(
                    chunk_size=_CHUNK_SIZE_BYTES
                ):
                    # Per-chunk cancellation poll: if the dialog
                    # cancelled mid-stream, stop reading immediately.
                    # The CancelledError propagates out of `get` and
                    # is silently swallowed by the worker's outer
                    # try/except (Property 16).
                    cancel.raise_if_cancelled()
                    if chunk:
                        chunks.append(chunk)
            except requests.Timeout as exc:
                raise DownloadError(
                    f"read timeout fetching {url}"
                ) from exc
            except requests.ConnectionError as exc:
                # Includes ChunkedEncodingError, which is a subclass.
                raise DownloadError(
                    f"connection error reading {url}: {exc}"
                ) from exc
            except requests.RequestException as exc:
                raise DownloadError(
                    f"network error reading {url}: {exc}"
                ) from exc

            body = b"".join(chunks)
            content_type = response.headers.get("Content-Type", "") or ""
            return HttpResponse(
                body=body,
                content_type=content_type,
                url=response.url or url,
                status_code=response.status_code,
            )
        finally:
            # Always release the connection back to the pool, even if
            # we raised after `iter_content` started.
            response.close()


def is_valid_image_response(body: bytes, content_type: str) -> bool:
    """Return ``True`` iff a response body is a usable image.

    Implements Property 11 / Req 7.5 verbatim: the body must be
    non-empty AND ``content_type.split(";")[0].strip().lower()`` must
    be in :data:`ALLOWED_IMAGE_MEDIA_TYPES`. Any deviation - empty body,
    missing header, ``application/json``, ``text/html`` error page,
    ``image/svg+xml`` (intentionally excluded because Anki's editor
    does not render inline SVG reliably) - causes a ``False`` return.
    """
    if len(body) == 0:
        return False
    # ``split(";")[0]`` always yields at least one element, so the
    # subsequent ``.strip().lower()`` is total. Parameter handling
    # (charset=, boundary=, ...) is intentionally discarded; only the
    # media type itself participates in the membership check.
    media_type = content_type.split(";")[0].strip().lower()
    return media_type in ALLOWED_IMAGE_MEDIA_TYPES


__all__ = [
    "ALLOWED_IMAGE_MEDIA_TYPES",
    "DEFAULT_TIMEOUT",
    "HttpClient",
    "HttpResponse",
    "is_valid_image_response",
]
