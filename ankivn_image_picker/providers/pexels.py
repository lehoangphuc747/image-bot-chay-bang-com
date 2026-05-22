"""Pexels image provider for the AnkiVN Smart Image Picker add-on.

Queries Pexels' ``/v1/search`` endpoint. Requires a free API key from
https://www.pexels.com/api/ — the key is read from the add-on config
via the ``PEXELS_API_KEY`` environment variable (injected by the
picker factory from ``config.pexels_api_key``).

Pexels returns JPEG images. The thumbnail uses ``src.medium`` (350px
wide) and the full image uses ``src.large2x`` (highest quality
available via the free API).
"""

from __future__ import annotations

import json
import os
from typing import Iterable, List, Optional
from urllib.parse import urlencode

from ..errors import CancelledError, DownloadError, ProviderError
from . import ProviderRegistry
from .base import ImageResult

_SEARCH_ENDPOINT: str = "https://api.pexels.com/v1/search"
_FIXED_EXTENSION: str = "jpg"
_MAX_PER_PAGE: int = 80  # Pexels max
_API_KEY_ENV_VAR: str = "PEXELS_API_KEY"


class PexelsProvider:
    """Concrete ImageProvider backed by Pexels' search API."""

    id: str = "pexels"
    display_name: str = "Pexels"

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

        api_key = os.environ.get(_API_KEY_ENV_VAR, "").strip()
        if not api_key:
            raise ProviderError(
                "Pexels API key not configured "
                f"(set {_API_KEY_ENV_VAR} env var or add pexels_api_key to config)"
            )

        per_page = min(max_results, _MAX_PER_PAGE)
        url = (
            f"{_SEARCH_ENDPOINT}?"
            + urlencode({"query": query, "per_page": per_page, "page": page})
        )

        # Pexels requires the API key in the Authorization header.
        # Our HttpClient.get doesn't support custom headers directly,
        # so we pass it via the URL query param approach won't work.
        # Instead, we'll use the requests library directly here.
        try:
            import requests

            cancel.raise_if_cancelled()
            resp = requests.get(
                url,
                headers={"Authorization": api_key},
                timeout=(5, 10),
            )
            if resp.status_code >= 400:
                raise DownloadError(
                    f"Pexels returned HTTP {resp.status_code}"
                )
            body = resp.content
        except CancelledError:
            raise
        except DownloadError:
            raise
        except requests.RequestException as exc:
            raise ProviderError(f"Pexels search failed: {exc}") from exc

        cancel.raise_if_cancelled()

        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                "Pexels returned a malformed JSON response"
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError("Pexels response is not a JSON object")

        photos = payload.get("photos")
        if not isinstance(photos, list):
            raise ProviderError("Pexels response is missing a 'photos' list")

        results: List[ImageResult] = []
        for item in photos[:max_results]:
            cancel.raise_if_cancelled()
            try:
                normalised = self._normalise_item(item)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Pexels result has unexpected shape: {exc}"
                ) from exc
            results.append(normalised)

        return results

    @staticmethod
    def _normalise_item(item: object) -> ImageResult:
        if not isinstance(item, dict):
            raise TypeError("photo entry is not a JSON object")

        src = item.get("src")
        if not isinstance(src, dict):
            raise TypeError("'src' field is missing or not an object")

        thumbnail_url = src.get("medium")
        full_url = src.get("large2x") or src.get("large") or src.get("original")

        if not isinstance(thumbnail_url, str) or not thumbnail_url:
            raise ValueError("'src.medium' is missing or not a non-empty string")
        if not isinstance(full_url, str) or not full_url:
            raise ValueError("'src.large2x' is missing or not a non-empty string")

        source_page_url: Optional[str] = None
        page_url = item.get("url")
        if isinstance(page_url, str) and page_url:
            source_page_url = page_url

        # Pexels attribution: photographer name and photographer_url
        author_name: Optional[str] = None
        author_url: Optional[str] = None
        photographer = item.get("photographer")
        if isinstance(photographer, str) and photographer:
            author_name = photographer
        photographer_url = item.get("photographer_url")
        if isinstance(photographer_url, str) and photographer_url:
            author_url = photographer_url

        return ImageResult(
            provider_id="pexels",
            thumbnail_url=thumbnail_url,
            full_url=full_url,
            extension=_FIXED_EXTENSION,
            source_page_url=source_page_url,
            author_name=author_name,
            author_url=author_url,
            license_name="Pexels License",
            license_url="https://www.pexels.com/license/",
        )


ProviderRegistry.register(PexelsProvider.id, PexelsProvider)

__all__ = ["PexelsProvider"]
