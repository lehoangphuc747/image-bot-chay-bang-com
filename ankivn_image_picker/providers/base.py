"""Provider abstraction for the AnkiVN Smart Image Picker add-on.

This module defines the two types every concrete provider plugs into:

* :class:`ImageResult` -- a frozen, hashable value object that carries a
  single search hit from a provider back to the orchestrator and the UI
  grid (Req 4.4: "tagging each Image_Result with the provider
  identifier").
* :class:`ImageProvider` -- a :class:`typing.Protocol` describing the
  duck-typed contract a provider module must satisfy. Providers are not
  required to inherit from this class; satisfying the protocol's shape
  is enough, which keeps concrete provider modules free of base-class
  boilerplate.

The provider registry that resolves a provider id back into a concrete
instance lives in :mod:`ankivn_image_picker.providers.__init__` so that
``from ankivn_image_picker.providers import ProviderRegistry`` is the
public entry point and this file remains a pure type-definitions module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional, Protocol, runtime_checkable

from ..filename import ALLOWED_EXT

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from ..cancellation import CancellationToken
    from ..http import HttpClient


@dataclass(frozen=True)
class ImageResult:
    """A single search result returned by an :class:`ImageProvider`.

    Instances are immutable so they can be safely passed across the
    Qt-signal boundary between worker threads and the main thread
    (workers must not share mutable state with the UI).

    Attributes
    ----------
    provider_id:
        The id of the provider that produced this result. Surfaced to
        the user in the picker grid as a hover tooltip (Req 6.3) and
        used by the orchestrator to tag every result with its source
        (Req 4.4).
    thumbnail_url:
        URL of the small preview image used by the picker grid.
    full_url:
        URL of the full-resolution image downloaded on selection.
    extension:
        Lowercase file extension without a leading dot, e.g. ``"jpg"``.
        Must be one of :data:`ankivn_image_picker.filename.ALLOWED_EXT`;
        providers are responsible for normalising whatever the upstream
        response says into a value in that set.
    source_page_url:
        Optional URL of the human-facing page that hosts the image
        (useful for attribution). May be ``None`` when the upstream
        provider does not expose one.
    """

    provider_id: str
    thumbnail_url: str
    full_url: str
    extension: str
    source_page_url: Optional[str] = None
    # Attribution fields (required by some providers like Unsplash, Pexels)
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    license_name: Optional[str] = None
    license_url: Optional[str] = None
    # Optional fallback URLs to try if full_url fails (in order)
    # Tuple makes the dataclass hashable and immutable.
    fallback_full_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # The dataclass is frozen, but ``__post_init__`` runs before the
        # instance is handed back to the caller, so simple shape checks
        # here keep malformed results out of the orchestrator and the
        # grid. The checks are intentionally cheap and structural; full
        # validation of the upstream response happens inside each
        # provider module.
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("provider_id must be a non-empty string")
        if not isinstance(self.thumbnail_url, str) or not self.thumbnail_url:
            raise ValueError("thumbnail_url must be a non-empty string")
        if not isinstance(self.full_url, str) or not self.full_url:
            raise ValueError("full_url must be a non-empty string")
        if not isinstance(self.extension, str) or not self.extension:
            raise ValueError("extension must be a non-empty string")
        if self.extension != self.extension.lower() or self.extension.startswith("."):
            raise ValueError(
                "extension must be lowercase and without a leading dot, "
                f"got {self.extension!r}"
            )
        if self.extension not in ALLOWED_EXT:
            raise ValueError(
                f"extension {self.extension!r} is not in ALLOWED_EXT"
            )
        if self.source_page_url is not None and not isinstance(
            self.source_page_url, str
        ):
            raise ValueError("source_page_url must be a string or None")
        if self.author_name is not None and not isinstance(self.author_name, str):
            raise ValueError("author_name must be a string or None")
        if self.author_url is not None and not isinstance(self.author_url, str):
            raise ValueError("author_url must be a string or None")
        if self.license_name is not None and not isinstance(self.license_name, str):
            raise ValueError("license_name must be a string or None")
        if self.license_url is not None and not isinstance(self.license_url, str):
            raise ValueError("license_url must be a string or None")
        if not isinstance(self.fallback_full_urls, tuple):
            raise ValueError("fallback_full_urls must be a tuple")
        for u in self.fallback_full_urls:
            if not isinstance(u, str) or not u:
                raise ValueError(
                    "fallback_full_urls entries must be non-empty strings"
                )


@runtime_checkable
class ImageProvider(Protocol):
    """Duck-typed contract for an image search provider.

    A provider is anything with two string attributes (``id``,
    ``display_name``) and a ``search`` method that yields
    :class:`ImageResult` instances. Concrete providers live in their
    own modules under :mod:`ankivn_image_picker.providers` and register
    themselves with :class:`ProviderRegistry` at import time.

    Implementation contract
    -----------------------
    * ``search`` MUST yield at most ``max_results`` results (Req 4.2).
    * ``search`` MUST poll ``cancel`` between network calls so the
      caller can abort an in-flight search by setting the cancellation
      token (Req 10.4).
    * ``search`` MUST raise
      :class:`ankivn_image_picker.errors.ProviderError` on HTTP error,
      network error, or malformed response. The orchestrator converts
      that into a single ``provider_failed`` signal on the worker bus
      (Req 4.5). It MUST NOT raise generic exceptions for those cases
      because the orchestrator only special-cases ``ProviderError`` and
      :class:`ankivn_image_picker.errors.CancelledError`.
    * ``search`` is invoked on a Qt thread-pool worker; it MUST NOT
      touch any Qt widget or the Anki collection.

    The protocol is :func:`runtime_checkable` so the registry can
    perform a cheap ``isinstance`` shape check before storing a factory
    -- this catches typos and missing methods at registration time
    rather than at the first user-triggered search.
    """

    id: str
    display_name: str

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: "HttpClient",
        cancel: "CancellationToken",
    ) -> Iterable[ImageResult]:
        ...


__all__ = ["ImageResult", "ImageProvider"]
