"""Attribution helpers for image providers.

Some providers (notably Unsplash) require attribution links in a specific
format with UTM tracking. This module generates compliant HTML snippets.

Unsplash guidelines:
https://help.unsplash.com/en/articles/2511315-guideline-attribution

Format: Photo by [Photographer Name] on [Unsplash]
- Photographer name MUST link to their Unsplash profile with UTM
- "Unsplash" MUST link to https://unsplash.com with UTM
- UTM params: ?utm_source=anki_image_picker&utm_medium=referral
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .providers.base import ImageResult


_UTM_PARAMS = "utm_source=anki_image_picker&utm_medium=referral"


def _add_utm(url: str) -> str:
    """Append UTM tracking parameters to a URL."""
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{_UTM_PARAMS}"


# Provider display info: (display_name, base_url, requires_utm)
_PROVIDER_INFO = {
    "unsplash": ("Unsplash", "https://unsplash.com", True),
    "pexels": ("Pexels", "https://www.pexels.com", False),
    "wikimedia": ("Wikimedia Commons", "https://commons.wikimedia.org", False),
    "openverse": ("Openverse", "https://openverse.org", False),
}


def build_attribution_html(result: "ImageResult") -> Optional[str]:
    """Build an HTML attribution snippet for an image result.

    Returns an HTML string formatted as:
        <div class="image-attribution">
          Photo by <a href="...">Author</a> on <a href="...">Provider</a>
        </div>

    For providers that don't require attribution, returns None.
    For Unsplash, this is REQUIRED by their API guidelines.

    Returns None if the result has no author info (some Wikimedia results).
    """
    provider_id = result.provider_id
    info = _PROVIDER_INFO.get(provider_id)
    if info is None:
        return None

    provider_display, provider_url, requires_utm = info

    # Apply UTM if required (Unsplash) and not already present
    if requires_utm:
        provider_url = _add_utm(provider_url)

    author_name = result.author_name
    author_url = result.author_url

    # If no author info, just link to provider
    if not author_name:
        return (
            f'<div class="image-attribution" style="font-size: 11px; '
            f'color: #888; margin-top: 4px;">'
            f'Image from <a href="{escape(provider_url)}" target="_blank">'
            f'{escape(provider_display)}</a>'
            f'</div>'
        )

    # Full attribution with author
    if author_url:
        author_link = (
            f'<a href="{escape(author_url)}" target="_blank">'
            f'{escape(author_name)}</a>'
        )
    else:
        author_link = escape(author_name)

    provider_link = (
        f'<a href="{escape(provider_url)}" target="_blank">'
        f'{escape(provider_display)}</a>'
    )

    return (
        f'<div class="image-attribution" style="font-size: 11px; '
        f'color: #888; margin-top: 4px;">'
        f'Photo by {author_link} on {provider_link}'
        f'</div>'
    )


def build_attribution_text(result: "ImageResult") -> str:
    """Build a plain-text attribution string for tooltips.

    Format: "Photo by Author on Provider"
    """
    provider_id = result.provider_id
    info = _PROVIDER_INFO.get(provider_id)
    provider_display = info[0] if info else provider_id

    if result.author_name:
        return f"Photo by {result.author_name} on {provider_display}"
    return f"Image from {provider_display}"


__all__ = [
    "build_attribution_html",
    "build_attribution_text",
]
