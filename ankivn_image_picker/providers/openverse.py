"""Openverse provider for the AnkiVN Smart Image Picker.

Uses the Openverse API (by WordPress/Creative Commons) to search for
openly-licensed images. **No API key required** for basic usage
(anonymous: 100 requests/day, 5 requests/hr burst).

Endpoint: https://api.openverse.org/v1/images/
All results are Creative Commons licensed.
"""

from __future__ import annotations

import json
from typing import Iterable, List, Optional
from urllib.parse import urlencode, urlsplit

from ..errors import CancelledError, DownloadError, ProviderError
from ..filename import ALLOWED_EXT
from . import ProviderRegistry
from .base import ImageResult

_SEARCH_ENDPOINT: str = "https://api.openverse.org/v1/images/"
_FALLBACK_EXTENSION: str = "jpg"


def _extension_from_url(url: str) -> str:
    """Derive extension from URL path."""
    if not isinstance(url, str) or not url:
        return _FALLBACK_EXTENSION
    path = urlsplit(url).path
    _, dot, ext = path.rpartition(".")
    if not dot:
        return _FALLBACK_EXTENSION
    candidate = ext.strip().lower()
    if candidate not in ALLOWED_EXT:
        return _FALLBACK_EXTENSION
    return candidate


class OpenverseProvider:
    """Concrete ImageProvider using Openverse's public API.

    No API key needed for basic usage.
    """

    id: str = "openverse"
    display_name: str = "Openverse"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http,
        cancel,
        page: int = 1,
    ) -> Iterable[ImageResult]:
        if max_results <= 0:
            return []

        cancel.raise_if_cancelled()

        page_size = min(max_results, 20)  # Openverse max per page is 20
        url = (
            f"{_SEARCH_ENDPOINT}?"
            + urlencode({
                "q": query,
                "page_size": page_size,
                "page": page,
            })
        )

        try:
            response = http.get(url, cancel=cancel)
        except CancelledError:
            raise
        except DownloadError as exc:
            raise ProviderError(f"Openverse search failed: {exc}") from exc

        cancel.raise_if_cancelled()

        try:
            payload = json.loads(response.body)
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                "Openverse returned a malformed JSON response"
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError("Openverse response is not a JSON object")

        results_list = payload.get("results")
        if not isinstance(results_list, list):
            return []

        results: List[ImageResult] = []
        for item in results_list[:max_results]:
            cancel.raise_if_cancelled()
            try:
                normalised = self._normalise_item(item)
                if normalised is not None:
                    results.append(normalised)
            except (KeyError, TypeError, ValueError):
                continue

        return results

    @staticmethod
    def _normalise_item(item: object) -> Optional[ImageResult]:
        if not isinstance(item, dict):
            return None

        # Full image URL
        full_url = item.get("url")
        if not isinstance(full_url, str) or not full_url:
            return None

        # Thumbnail
        thumbnail_url = item.get("thumbnail")
        if not isinstance(thumbnail_url, str) or not thumbnail_url:
            thumbnail_url = full_url

        # Source page
        source_page_url: Optional[str] = None
        foreign_url = item.get("foreign_landing_url")
        if isinstance(foreign_url, str) and foreign_url:
            source_page_url = foreign_url

        # Author / creator info
        author_name: Optional[str] = None
        author_url: Optional[str] = None
        creator = item.get("creator")
        if isinstance(creator, str) and creator:
            author_name = creator
        creator_url = item.get("creator_url")
        if isinstance(creator_url, str) and creator_url:
            author_url = creator_url

        # License info
        license_name: Optional[str] = None
        license_url: Optional[str] = None
        license_field = item.get("license")
        license_version = item.get("license_version", "")
        if isinstance(license_field, str) and license_field:
            license_name = f"CC {license_field.upper()}"
            if isinstance(license_version, str) and license_version:
                license_name = f"{license_name} {license_version}"
        license_url_field = item.get("license_url")
        if isinstance(license_url_field, str) and license_url_field:
            license_url = license_url_field

        ext = _extension_from_url(full_url)

        return ImageResult(
            provider_id="openverse",
            thumbnail_url=thumbnail_url,
            full_url=full_url,
            extension=ext,
            source_page_url=source_page_url,
            author_name=author_name,
            author_url=author_url,
            license_name=license_name,
            license_url=license_url,
        )


ProviderRegistry.register(OpenverseProvider.id, OpenverseProvider)

__all__ = ["OpenverseProvider"]
