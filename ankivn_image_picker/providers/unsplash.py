"""Unsplash image provider for the AnkiVN Smart Image Picker add-on.

This module is a concrete :class:`ImageProvider` (Req 4.1, 4.2, 4.5) that
queries Unsplash's official ``/search/photos`` endpoint and normalises the
JSON response into :class:`~ankivn_image_picker.providers.base.ImageResult`
instances. The orchestrator runs ``search`` on a Qt thread-pool worker,
streams results into the picker grid via the worker bus, and converts
any :class:`~ankivn_image_picker.errors.ProviderError` raised here into a
``provider_failed`` signal.

Authentication
--------------
Unsplash requires a per-application access key. The add-on does not
expose this in :mod:`config.json` (the design's config schema is fixed
and a key would not be safely shippable in user-editable JSON), so the
provider reads it from the ``UNSPLASH_ACCESS_KEY`` environment variable
at call time. If the variable is unset or empty the provider raises
:class:`ProviderError` with a message that tells the user how to fix it;
the orchestrator surfaces that message in the per-provider error
indicator on the picker's status bar (Req 4.5).

Reading the key inside :meth:`search` rather than at module import time
matters because:

* :class:`ProviderRegistry` stores the *class* as a zero-argument factory.
  We do not want a ``KeyError`` raised at registration to abort the
  add-on's startup.
* The user can set the env var, restart Anki, and the provider works
  on the *next* picker open without an add-on reinstall (Req 1.7's
  spirit applied to provider configuration).

Network shape
-------------
The endpoint is `<https://api.unsplash.com/search/photos>`_. We issue a
single GET per search and trust :class:`HttpClient` to enforce the
15-second per-request budget (Req 10.2). Pagination is intentionally not
used: ``per_page`` is capped at the smaller of ``max_results`` and
Unsplash's documented maximum of 30 per page, and the picker is happy
with whatever subset arrives.

Failure modes mapped to :class:`ProviderError`:

* missing access key (env var unset or empty);
* :class:`~ankivn_image_picker.errors.DownloadError` raised by the HTTP
  layer (timeout, DNS, connect/read failure, HTTP >= 400);
* a body that is not valid JSON;
* a body whose top-level shape is not the expected
  ``{"results": [...]}`` mapping;
* a result entry whose ``urls.small`` / ``urls.regular`` keys are
  missing or non-string.

:class:`~ankivn_image_picker.errors.CancelledError` is *not* caught: the
orchestrator's outer try/except swallows it silently so no signal is
emitted after cancellation (Req 10.4, Property 16).
"""

from __future__ import annotations

import json
import os
from typing import Iterable, List, Optional
from urllib.parse import urlencode

from ..errors import CancelledError, DownloadError, ProviderError
from . import ProviderRegistry
from .base import ImageResult

# Unsplash's documented per-page maximum. Asking for more than this
# silently returns 30 anyway, so clamping client-side keeps the URL
# honest and avoids surprising the user.
_UNSPLASH_MAX_PER_PAGE: int = 30

# We always request the "regular" size for the full image and the
# "small" size for the thumbnail. Unsplash returns JPEG bytes for both
# regardless of the original asset's format, so the extension is fixed.
_SEARCH_ENDPOINT: str = "https://api.unsplash.com/search/photos"
_FIXED_EXTENSION: str = "jpg"

# Env var name kept module-level so tests (and the user-facing error
# message) reference exactly one literal.
_ACCESS_KEY_ENV_VAR: str = "UNSPLASH_ACCESS_KEY"

# UTM parameters required by Unsplash API guidelines for attribution links
# https://help.unsplash.com/en/articles/2511315-guideline-attribution
_UTM_PARAMS: str = "?utm_source=anki_image_picker&utm_medium=referral"


def _add_utm(url: str) -> str:
    """Append UTM tracking parameters to an Unsplash URL.

    Unsplash requires UTM parameters on attribution links per their API
    guidelines. If the URL already has query params, append with '&'.
    """
    if not url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}utm_source=anki_image_picker&utm_medium=referral"


class UnsplashProvider:
    """Concrete :class:`ImageProvider` backed by Unsplash's search API.

    The class is registered as a zero-argument factory at import time
    (see the bottom of this module). Each picker open calls
    ``UnsplashProvider()`` to get a fresh, stateless instance, so
    holding any per-search state on ``self`` would be unsafe and is
    avoided.
    """

    # Public identity used by the registry, the config validator, and
    # the picker's hover tooltip / status bar (Req 4.4, Req 6.3).
    id: str = "unsplash"
    display_name: str = "Unsplash"

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

        access_key = os.environ.get(_ACCESS_KEY_ENV_VAR, "").strip()
        if not access_key:
            raise ProviderError(
                "Unsplash access key not configured "
                f"(set {_ACCESS_KEY_ENV_VAR} env var)"
            )

        per_page = min(max_results, _UNSPLASH_MAX_PER_PAGE)
        url = (
            f"{_SEARCH_ENDPOINT}?"
            + urlencode(
                {
                    "query": query,
                    "per_page": per_page,
                    "page": page,
                    "client_id": access_key,
                }
            )
        )

        # The HTTP layer raises DownloadError for any transport or
        # status failure; convert that into ProviderError so the
        # orchestrator only has to handle one provider-side error
        # class. CancelledError flows through unmodified.
        try:
            response = http.get(url, cancel=cancel)
        except CancelledError:
            raise
        except DownloadError as exc:
            raise ProviderError(
                f"Unsplash search failed: {exc}"
            ) from exc

        # Cancellation poll #2: if the dialog closed between issuing
        # the request and decoding its body, drop the response on the
        # floor.
        cancel.raise_if_cancelled()

        try:
            payload = json.loads(response.body)
        except (ValueError, TypeError) as exc:
            # ``ValueError`` covers ``json.JSONDecodeError``; ``TypeError``
            # covers the (rare) case where ``response.body`` is not a
            # bytes/str instance, which would indicate a fake
            # HttpClient is misbehaving in a test.
            raise ProviderError(
                "Unsplash returned a malformed JSON response"
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError(
                "Unsplash response is not a JSON object"
            )

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ProviderError(
                "Unsplash response is missing a 'results' list"
            )

        results: List[ImageResult] = []
        for item in raw_results[:max_results]:
            # Cancellation poll #3: between every parsed item so a
            # late cancel does not pay the full normalisation cost.
            cancel.raise_if_cancelled()

            try:
                normalised = self._normalise_item(item)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Unsplash result has unexpected shape: {exc}"
                ) from exc
            results.append(normalised)

        return results

    @staticmethod
    def _normalise_item(item: object) -> ImageResult:
        """Convert one Unsplash search hit into an :class:`ImageResult`.

        The Unsplash schema we rely on is documented at
        `<https://unsplash.com/documentation#search-photos>`_; this
        helper extracts only the fields the picker needs. Any
        deviation from the expected shape raises ``KeyError``,
        ``TypeError``, or ``ValueError``, which the caller wraps as
        :class:`ProviderError`.
        """
        if not isinstance(item, dict):
            raise TypeError("result entry is not a JSON object")

        urls = item.get("urls")
        if not isinstance(urls, dict):
            raise TypeError("'urls' field is missing or not an object")

        thumbnail_url = urls.get("small")
        full_url = urls.get("regular")
        if not isinstance(thumbnail_url, str) or not thumbnail_url:
            raise ValueError(
                "'urls.small' is missing or not a non-empty string"
            )
        if not isinstance(full_url, str) or not full_url:
            raise ValueError(
                "'urls.regular' is missing or not a non-empty string"
            )

        # ``links.html`` is the human-facing photo page on
        # unsplash.com; surfacing it lets the picker offer attribution
        # in a future task without re-fetching the result. It is
        # optional per the schema (and per the ImageResult contract).
        source_page_url: Optional[str] = None
        links = item.get("links")
        if isinstance(links, dict):
            html = links.get("html")
            if isinstance(html, str) and html:
                source_page_url = _add_utm(html)

        # Extract author info for attribution (required by Unsplash guidelines)
        author_name: Optional[str] = None
        author_url: Optional[str] = None
        user = item.get("user")
        if isinstance(user, dict):
            name = user.get("name")
            if isinstance(name, str) and name:
                author_name = name
            user_links = user.get("links")
            if isinstance(user_links, dict):
                html_link = user_links.get("html")
                if isinstance(html_link, str) and html_link:
                    author_url = _add_utm(html_link)

        return ImageResult(
            provider_id="unsplash",
            thumbnail_url=thumbnail_url,
            full_url=full_url,
            extension=_FIXED_EXTENSION,
            source_page_url=source_page_url,
            author_name=author_name,
            author_url=author_url,
            license_name="Unsplash License",
            license_url="https://unsplash.com/license",
        )


# Register at module load so that ``import ankivn_image_picker.providers.unsplash``
# is the only step needed to make the provider available to the
# orchestrator. Re-importing is a no-op because :meth:`ProviderRegistry.register`
# treats "same factory under same id" as idempotent.
ProviderRegistry.register(UnsplashProvider.id, UnsplashProvider)


__all__ = ["UnsplashProvider"]
