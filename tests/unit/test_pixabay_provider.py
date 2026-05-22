"""Unit tests for the Pixabay provider response parsing.

These tests mirror the Unsplash provider tests in
``test_provider_response_parsing.py`` against the Pixabay-specific
response shape. They verify that ``PixabayProvider`` correctly handles:

- Successful JSON parse into :class:`ImageResult` instances with the
  Pixabay-specific field mapping (``previewURL``, ``largeImageURL``,
  ``pageURL``).
- Malformed JSON (raises :class:`ProviderError`).
- HTTP/network errors propagated from the :class:`HttpClient` (raises
  :class:`ProviderError`).
- Empty result lists (returns no results without error).
- Missing ``PIXABAY_API_KEY`` env var (raises :class:`ProviderError`
  before any HTTP call).
- Provider self-registers under id ``"pixabay"``.

Each test uses a deterministic ``FakeHttpClient`` so no real network
calls are made.

_Validates: Requirements 4.1, 4.2, 4.5_
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

import pytest

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.errors import DownloadError, ProviderError
from ankivn_image_picker.http import HttpResponse
from ankivn_image_picker.providers import ProviderRegistry
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.providers.pixabay import PixabayProvider


# ---------------------------------------------------------------------------
# Fake HttpClient
# ---------------------------------------------------------------------------


@dataclass
class FakeHttpClient:
    """A deterministic HttpClient substitute for unit tests.

    Configured with either a response to return or an exception to
    raise. Tracks calls for assertion purposes.
    """

    response: HttpResponse | None = None
    exception: Exception | None = None
    calls: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def get(self, url: str, *, cancel: CancellationToken) -> HttpResponse:
        self.calls.append(url)
        if self.exception is not None:
            raise self.exception
        assert self.response is not None
        return self.response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pixabay_response(hits: list) -> HttpResponse:
    """Build a fake HttpResponse with a valid Pixabay JSON payload."""
    payload = {"total": len(hits), "totalHits": len(hits), "hits": hits}
    return HttpResponse(
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        url="https://pixabay.com/api/?key=k&q=test",
        status_code=200,
    )


def _make_pixabay_hit(
    *,
    preview: str = "https://cdn.pixabay.com/photo/2017/01/01/01/01/foo-1.jpg",
    large: str = "https://cdn.pixabay.com/photo/2017/01/01/01/01/foo-1_1280.jpg",
    page: str = "https://pixabay.com/photos/foo-12345/",
) -> dict:
    """Build a single Pixabay hit matching the documented schema."""
    return {
        "id": 12345,
        "previewURL": preview,
        "largeImageURL": large,
        "pageURL": page,
        "tags": "foo, bar",
    }


# ---------------------------------------------------------------------------
# Successful parse
# ---------------------------------------------------------------------------


class TestSuccessfulParse:
    """Verify that well-formed Pixabay responses parse into ImageResults."""

    def test_single_hit_parsed_correctly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single valid hit is parsed into an ImageResult with correct fields."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hit = _make_pixabay_hit()
        http = FakeHttpClient(response=_make_pixabay_response([hit]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search("dog", max_results=5, http=http, cancel=cancel)
        )

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ImageResult)
        assert result.provider_id == "pixabay"
        assert result.thumbnail_url == hit["previewURL"]
        assert result.full_url == hit["largeImageURL"]
        # Extension derived from the URL path (``..._1280.jpg`` -> "jpg")
        assert result.extension == "jpg"
        assert result.source_page_url == hit["pageURL"]

    def test_multiple_hits_parsed_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple hits are returned in the order Pixabay sent them."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hits = [
            _make_pixabay_hit(
                preview=f"https://cdn.test/preview-{i}.png",
                large=f"https://cdn.test/large-{i}.png",
                page=f"https://pixabay.com/photos/foo-{i}/",
            )
            for i in range(3)
        ]
        http = FakeHttpClient(response=_make_pixabay_response(hits))
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search("cat", max_results=10, http=http, cancel=cancel)
        )

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.thumbnail_url == f"https://cdn.test/preview-{i}.png"
            assert result.full_url == f"https://cdn.test/large-{i}.png"
            assert result.extension == "png"

    def test_max_results_caps_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provider yields at most max_results even if Pixabay returns more."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hits = [
            _make_pixabay_hit(
                preview=f"https://cdn.test/p-{i}.jpg",
                large=f"https://cdn.test/l-{i}.jpg",
            )
            for i in range(10)
        ]
        http = FakeHttpClient(response=_make_pixabay_response(hits))
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search("bird", max_results=3, http=http, cancel=cancel)
        )

        assert len(results) == 3

    def test_missing_page_url_yields_none_source_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hit without ``pageURL`` produces an ImageResult with source_page_url=None."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hit = {
            "previewURL": "https://cdn.test/preview.jpg",
            "largeImageURL": "https://cdn.test/large.jpg",
            # No "pageURL" key
        }
        http = FakeHttpClient(response=_make_pixabay_response([hit]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search("tree", max_results=5, http=http, cancel=cancel)
        )

        assert len(results) == 1
        assert results[0].source_page_url is None

    def test_extension_falls_back_to_jpg_for_unknown_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the URL path has no recognised extension, ``jpg`` is used."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hit = _make_pixabay_hit(
            large="https://cdn.test/large_no_ext"
        )
        http = FakeHttpClient(response=_make_pixabay_response([hit]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search("flower", max_results=5, http=http, cancel=cancel)
        )

        assert len(results) == 1
        assert results[0].extension == "jpg"


# ---------------------------------------------------------------------------
# Malformed JSON
# ---------------------------------------------------------------------------


class TestMalformedJson:
    """Verify that malformed or unexpected JSON raises ProviderError."""

    def test_invalid_json_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-JSON response body raises ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        response = HttpResponse(
            body=b"this is not json {{{",
            content_type="application/json",
            url="https://pixabay.com/api/",
            status_code=200,
        )
        http = FakeHttpClient(response=response)
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="malformed JSON"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

    def test_json_not_object_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A JSON array at the top level raises ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        response = HttpResponse(
            body=json.dumps([1, 2, 3]).encode("utf-8"),
            content_type="application/json",
            url="https://pixabay.com/api/",
            status_code=200,
        )
        http = FakeHttpClient(response=response)
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="not a JSON object"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

    def test_missing_hits_key_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A JSON object without a 'hits' list raises ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        response = HttpResponse(
            body=json.dumps({"total": 100}).encode("utf-8"),
            content_type="application/json",
            url="https://pixabay.com/api/",
            status_code=200,
        )
        http = FakeHttpClient(response=response)
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="missing a 'hits' list"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

    def test_hit_missing_preview_url_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hit without ``previewURL`` raises ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hit = {"largeImageURL": "https://cdn.test/large.jpg"}
        http = FakeHttpClient(response=_make_pixabay_response([hit]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="unexpected shape"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

    def test_hit_missing_large_image_url_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hit without ``largeImageURL`` raises ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        hit = {"previewURL": "https://cdn.test/preview.jpg"}
        http = FakeHttpClient(response=_make_pixabay_response([hit]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="unexpected shape"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )


# ---------------------------------------------------------------------------
# HTTP error
# ---------------------------------------------------------------------------


class TestHttpError:
    """Verify that HTTP/network errors from HttpClient become ProviderError."""

    def test_download_error_becomes_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A DownloadError from the HTTP layer is wrapped as ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        http = FakeHttpClient(
            exception=DownloadError(
                "HTTP 503 fetching https://pixabay.com/api/"
            )
        )
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="Pixabay search failed"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

    def test_download_error_preserves_cause(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The original DownloadError is chained as __cause__ on ProviderError."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        original = DownloadError("timeout fetching url")
        http = FakeHttpClient(exception=original)
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError) as exc_info:
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

        assert exc_info.value.__cause__ is original

    def test_missing_api_key_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing PIXABAY_API_KEY env var raises before any HTTP call."""
        monkeypatch.delenv("PIXABAY_API_KEY", raising=False)

        http = FakeHttpClient(response=_make_pixabay_response([]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        with pytest.raises(ProviderError, match="API key not configured"):
            list(
                provider.search("test", max_results=5, http=http, cancel=cancel)
            )

        assert len(http.calls) == 0


# ---------------------------------------------------------------------------
# Empty result list
# ---------------------------------------------------------------------------


class TestEmptyResults:
    """Verify that empty result lists are handled gracefully."""

    def test_empty_hits_array_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty 'hits' array yields zero ImageResults without error."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        http = FakeHttpClient(response=_make_pixabay_response([]))
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search(
                "nonexistent", max_results=10, http=http, cancel=cancel
            )
        )

        assert results == []

    def test_zero_max_results_returns_empty_without_http_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """max_results=0 short-circuits to empty without issuing an HTTP call."""
        monkeypatch.setenv("PIXABAY_API_KEY", "test-key-123")

        http = FakeHttpClient(
            response=_make_pixabay_response([_make_pixabay_hit()])
        )
        cancel = CancellationToken()
        provider = PixabayProvider()

        results = list(
            provider.search("test", max_results=0, http=http, cancel=cancel)
        )

        assert results == []
        assert len(http.calls) == 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    """Verify that PixabayProvider self-registers under id 'pixabay'."""

    def test_provider_is_registered_under_pixabay_id(self) -> None:
        """Importing the module registers the provider with the registry."""
        # Importing the module above triggered the registration call;
        # ``ProviderRegistry.create`` should now resolve "pixabay" to a
        # fresh PixabayProvider instance.
        provider = ProviderRegistry.create("pixabay")
        assert isinstance(provider, PixabayProvider)
        assert provider.id == "pixabay"
        assert provider.display_name == "Pixabay"
