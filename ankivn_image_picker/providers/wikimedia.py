"""Wikimedia Commons provider for the AnkiVN Smart Image Picker.

Uses the MediaWiki API to search Wikimedia Commons for images.
**No API key required** — completely free and open.

Endpoint: https://commons.wikimedia.org/w/api.php
Action: query with generator=search, namespace=6 (File:)

Returns thumbnails via the imageinfo prop.

Rate limiting strategy
----------------------
- Use a compliant User-Agent with contact info (see HttpClient).
- Strip UTM tracking params that MediaWiki adds to URLs.
- Provide alternate URLs at different thumbnail sizes so the dialog
  can fall back when one size returns 429 / 5xx.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, List, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from ..errors import CancelledError, DownloadError, ProviderError
from ..filename import ALLOWED_EXT
from . import ProviderRegistry
from .base import ImageResult

_API_ENDPOINT: str = "https://commons.wikimedia.org/w/api.php"
_FALLBACK_EXTENSION: str = "jpg"

# Only include actual photos/images, skip SVG/PDF/etc for flashcards
_WANTED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

# Thumbnail sizes to try (in order) when one returns an error.
# Smaller sizes are less likely to hit the upload server's rate limit.
_THUMBNAIL_SIZES = [800, 640, 480, 330]
# Default thumbnail size for the grid preview
_GRID_THUMB_WIDTH = 330

# Regex to strip UTM params that MediaWiki sometimes appends to URLs
# Removes anything matching ?utm_source=... or &utm_*= patterns
_UTM_PARAM_RE = re.compile(r"[?&]utm_[a-z]+=[^&]*")


def _strip_utm(url: str) -> str:
    """Remove UTM tracking params from a URL.

    MediaWiki's imageinfo response sometimes includes UTM params on
    URLs (e.g. ``?utm_source=commons.wikimedia.org``), which causes
    cache misses and confuses some downstream tools. Strip them.
    """
    if not isinstance(url, str) or "utm_" not in url:
        return url
    # Replace all utm params, then clean up leftover ? or & at boundaries
    cleaned = _UTM_PARAM_RE.sub("", url)
    # If the first param was UTM, we now have "?&foo=bar" — fix that
    cleaned = cleaned.replace("?&", "?").rstrip("?").rstrip("&")
    return cleaned


def _make_thumb_url(original_url: str, width: int) -> Optional[str]:
    """Construct a Wikimedia thumbnail URL at the given width.

    Wikimedia thumbnails follow the pattern:
        https://upload.wikimedia.org/wikipedia/commons/thumb/X/XY/Name.jpg/{W}px-Name.jpg

    where the original is at:
        https://upload.wikimedia.org/wikipedia/commons/X/XY/Name.jpg

    Returns None if the URL doesn't match the expected pattern.
    """
    if not isinstance(original_url, str) or not original_url:
        return None

    # Already a thumbnail? Replace the width segment.
    thumb_match = re.match(
        r"(https://upload\.wikimedia\.org/wikipedia/commons/thumb/.+?/)\d+px-(.+)$",
        original_url,
    )
    if thumb_match:
        prefix = thumb_match.group(1)
        suffix = thumb_match.group(2)
        return f"{prefix}{width}px-{suffix}"

    # Original file URL — convert to thumbnail
    orig_match = re.match(
        r"(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2})/(.+)$",
        original_url,
    )
    if orig_match:
        base = orig_match.group(1)
        path = orig_match.group(2)
        filename = orig_match.group(3)
        # MediaWiki always renders thumbnails as the same format,
        # except SVG which becomes PNG. We don't care about that here
        # because we filter SVG out earlier.
        return f"{base}thumb/{path}/{filename}/{width}px-{filename}"

    return None


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


class WikimediaProvider:
    """Concrete ImageProvider using Wikimedia Commons search.

    No API key needed — works out of the box.
    """

    id: str = "wikimedia"
    display_name: str = "Wikimedia Commons"

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

        # Request more than needed because we filter out non-image files
        fetch_limit = min(max_results * 2, 50)
        # Wikimedia: gsroffset for pagination
        offset = (page - 1) * fetch_limit

        url = (
            f"{_API_ENDPOINT}?"
            + urlencode({
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrnamespace": "6",  # File: namespace
                "gsrsearch": query,
                "gsrlimit": fetch_limit,
                "gsroffset": offset,
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": "800",  # Thumbnail used for full image
            })
        )

        try:
            import requests as _requests

            resp = _requests.get(
                url,
                headers={
                    "User-Agent": "AnkiVN-Image-Picker/1.0 (Anki addon; contact: ankivn@example.com)",
                    "Api-User-Agent": "AnkiVN-Image-Picker/1.0",
                },
                timeout=(5, 10),
            )
            if resp.status_code >= 400:
                raise DownloadError(f"Wikimedia returned HTTP {resp.status_code}")
            body = resp.content
        except CancelledError:
            raise
        except DownloadError:
            raise
        except Exception as exc:
            raise ProviderError(f"Wikimedia search failed: {exc}") from exc

        cancel.raise_if_cancelled()

        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                "Wikimedia returned a malformed JSON response"
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError("Wikimedia response is not a JSON object")

        query_obj = payload.get("query")
        if not isinstance(query_obj, dict):
            return []  # No results

        pages = query_obj.get("pages")
        if not isinstance(pages, dict):
            return []

        results: List[ImageResult] = []
        for page_id, page_data in pages.items():
            if len(results) >= max_results:
                break
            cancel.raise_if_cancelled()
            try:
                normalised = self._normalise_page(page_data)
                if normalised is not None:
                    results.append(normalised)
            except (KeyError, TypeError, ValueError):
                continue

        return results

    @staticmethod
    def _normalise_page(page: object) -> Optional[ImageResult]:
        if not isinstance(page, dict):
            return None

        imageinfo = page.get("imageinfo")
        if not isinstance(imageinfo, list) or not imageinfo:
            return None

        info = imageinfo[0]
        if not isinstance(info, dict):
            return None

        # Check mime type — only want actual images
        mime = info.get("mime", "")
        if not isinstance(mime, str) or not mime.startswith("image/"):
            return None

        # Skip SVG and TIFF — they don't render in Anki cards
        if "svg" in mime or "tiff" in mime:
            return None

        # Original full URL (always available)
        original_url = info.get("url")
        if not isinstance(original_url, str) or not original_url:
            return None

        # Strip UTM params that MediaWiki sometimes appends
        original_url = _strip_utm(original_url)

        # Determine the original file's extension
        original_ext = _extension_from_url(original_url)

        # MediaWiki provides a rendered thumbnail at our requested width
        # via thumburl. For raster formats (JPEG/PNG/GIF/WEBP) the
        # original is fine to use directly; for anything weird, prefer
        # the rendered thumbnail (always JPEG).
        thumbnail_full = info.get("thumburl")
        if isinstance(thumbnail_full, str):
            thumbnail_full = _strip_utm(thumbnail_full)

        if original_ext in _WANTED_EXTENSIONS:
            # Standard image format — but the original may be huge or
            # heavily rate-limited. Prefer the rendered thumbnail
            # (800px, fast and small) as the "full" image.
            if isinstance(thumbnail_full, str) and thumbnail_full:
                full_url = thumbnail_full
                ext = _extension_from_url(thumbnail_full)
                if ext not in _WANTED_EXTENSIONS:
                    ext = original_ext  # Keep original ext
            else:
                full_url = original_url
                ext = original_ext

            # Smaller thumbnail for the grid preview (330px)
            grid_thumb = _make_thumb_url(original_url, _GRID_THUMB_WIDTH)
            if grid_thumb:
                thumbnail_url = grid_thumb
            else:
                thumbnail_url = full_url
        else:
            # Non-standard extension — fall back to MediaWiki's rendered
            # JPEG thumbnail if available
            if isinstance(thumbnail_full, str) and thumbnail_full:
                full_url = thumbnail_full
                thumbnail_url = thumbnail_full
                ext = _extension_from_url(thumbnail_full)
                if ext not in _WANTED_EXTENSIONS:
                    ext = "jpg"  # MediaWiki thumbs are JPEG
            else:
                # No usable thumbnail and unsupported format — skip
                return None

        # Build fallback full URLs at progressively smaller sizes.
        # If the primary fails (429, 5xx, etc), the downloader will
        # try these in order.
        fallback_urls: list[str] = []
        if original_ext in _WANTED_EXTENSIONS:
            for size in _THUMBNAIL_SIZES:
                alt = _make_thumb_url(original_url, size)
                if alt and alt != full_url and alt not in fallback_urls:
                    fallback_urls.append(alt)
            # As a last resort, try the original URL itself
            if original_url != full_url and original_url not in fallback_urls:
                fallback_urls.append(original_url)

        # Source page on Commons
        title = page.get("title", "")
        source_page_url: Optional[str] = None
        if isinstance(title, str) and title:
            source_page_url = (
                f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}"
            )

        # Wikimedia attribution: extracted metadata if available
        author_name: Optional[str] = None
        # Most Wikimedia images use various CC licenses; we'll just point
        # to the source page where the user can find specific license info
        return ImageResult(
            provider_id="wikimedia",
            thumbnail_url=thumbnail_url,
            full_url=full_url,
            extension=ext,
            source_page_url=source_page_url,
            author_name=author_name,
            author_url=source_page_url,
            license_name="See source page",
            license_url=source_page_url,
            fallback_full_urls=tuple(fallback_urls),
        )


ProviderRegistry.register(WikimediaProvider.id, WikimediaProvider)

__all__ = ["WikimediaProvider"]
