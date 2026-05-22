"""Unit tests for provider response parsing.

These tests verify that the Unsplash provider correctly handles:
- Successful JSON response parsing into ImageResult instances
- Malformed JSON responses (raises ProviderError)
- HTTP errors propagated from the HttpClient (raises ProviderError)
- Empty result lists (returns no results without error)

All tests use a fake HttpClient that returns predetermined responses,
so no real network calls are made.

_Validates: Requirements 4.5_
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

import pytest

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.errors import DownloadError, ProviderError
from ankivn_image_picker.http import HttpResponse
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.providers.unsplash import UnsplashProvider


# ---------------------------------------------------------------------------
# Fake HttpClient
# ---------------------------------------------------------------------------


@dataclass
class FakeHttpClient:
    """A deterministic HttpClient substitute for unit tests.

    Configured with either a response to return or an exception to raise.
    Tracks calls for assertion purposes.
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


def _make_unsplash_response(results: list) -> HttpResponse:
    """Build a fake HttpResponse with a valid Unsplash JSON payload."""
    payload = {"results": results}
    return HttpResponse(
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        url="https://api.unsplash.com/search/photos?query=test",
        status_code=200,
    )


def _make_unsplash_item(
    *,
    small: str = "https://images.unsplash.com/photo-1?w=400",
    regular: str = "https://images.unsplash.com/photo-1?w=1080",
    html: str = "https://unsplash.com/photos/abc123",
) -> dict:
    """Build a single Unsplash result item matching the expected schema."""
    return {
        "urls": {
            "small": small,
            "regular": regular,
        },
        "links": {
            "html": html,
        },
    }


# ---------------------------------------------------------------------------
# Successful parse
# ---------------------------------------------------------------------------


class TestSuccessfulParse:
    """Verify that well-formed Unsplash responses are parsed into ImageResults."""

    def test_single_result_parsed_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single valid result is parsed into an ImageResult with correct fields."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        item = _make_unsplash_item()
        http = FakeHttpClient(response=_make_unsplash_response([item]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        results = list(provider.search("dog", max_results=5, http=http, cancel=cancel))

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ImageResult)
        assert result.provider_id == "unsplash"
        assert result.thumbnail_url == "https://images.unsplash.com/photo-1?w=400"
        assert result.full_url == "https://images.unsplash.com/photo-1?w=1080"
        assert result.extension == "jpg"
        assert result.source_page_url == "https://unsplash.com/photos/abc123"

    def test_multiple_results_parsed_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple results are returned in the same order as the JSON array."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        items = [
            _make_unsplash_item(small=f"https://img.test/thumb-{i}", regular=f"https://img.test/full-{i}")
            for i in range(3)
        ]
        http = FakeHttpClient(response=_make_unsplash_response(items))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        results = list(provider.search("cat", max_results=10, http=http, cancel=cancel))

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.thumbnail_url == f"https://img.test/thumb-{i}"
            assert result.full_url == f"https://img.test/full-{i}"

    def test_max_results_caps_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Provider yields at most max_results even if the API returns more."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        items = [_make_unsplash_item(small=f"https://img.test/t-{i}", regular=f"https://img.test/f-{i}") for i in range(10)]
        http = FakeHttpClient(response=_make_unsplash_response(items))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        results = list(provider.search("bird", max_results=3, http=http, cancel=cancel))

        assert len(results) == 3

    def test_missing_source_page_url_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When 'links.html' is absent, source_page_url is None."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        item = {
            "urls": {
                "small": "https://img.test/thumb",
                "regular": "https://img.test/full",
            },
            # No "links" key at all
        }
        http = FakeHttpClient(response=_make_unsplash_response([item]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        results = list(provider.search("tree", max_results=5, http=http, cancel=cancel))

        assert len(results) == 1
        assert results[0].source_page_url is None


# ---------------------------------------------------------------------------
# Malformed JSON
# ---------------------------------------------------------------------------


class TestMalformedJson:
    """Verify that malformed or unexpected JSON raises ProviderError."""

    def test_invalid_json_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-JSON response body raises ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        response = HttpResponse(
            body=b"this is not json {{{",
            content_type="application/json",
            url="https://api.unsplash.com/search/photos",
            status_code=200,
        )
        http = FakeHttpClient(response=response)
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="malformed JSON"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

    def test_json_not_object_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A JSON array at the top level (instead of object) raises ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        response = HttpResponse(
            body=json.dumps([1, 2, 3]).encode("utf-8"),
            content_type="application/json",
            url="https://api.unsplash.com/search/photos",
            status_code=200,
        )
        http = FakeHttpClient(response=response)
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="not a JSON object"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

    def test_missing_results_key_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A JSON object without a 'results' list raises ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        response = HttpResponse(
            body=json.dumps({"total": 100}).encode("utf-8"),
            content_type="application/json",
            url="https://api.unsplash.com/search/photos",
            status_code=200,
        )
        http = FakeHttpClient(response=response)
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="missing a 'results' list"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

    def test_result_item_missing_urls_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A result item without 'urls' dict raises ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        item = {"id": "abc123"}  # no "urls" key
        http = FakeHttpClient(response=_make_unsplash_response([item]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="unexpected shape"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

    def test_result_item_missing_small_url_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A result item with urls but missing 'small' raises ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        item = {"urls": {"regular": "https://img.test/full"}}
        http = FakeHttpClient(response=_make_unsplash_response([item]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="unexpected shape"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))


# ---------------------------------------------------------------------------
# HTTP error
# ---------------------------------------------------------------------------


class TestHttpError:
    """Verify that HTTP/network errors from HttpClient become ProviderError."""

    def test_download_error_becomes_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A DownloadError from the HTTP layer is wrapped as ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        http = FakeHttpClient(exception=DownloadError("HTTP 503 fetching https://api.unsplash.com/search/photos"))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="Unsplash search failed"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

    def test_download_error_preserves_cause(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The original DownloadError is chained as __cause__ on the ProviderError."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        original = DownloadError("timeout fetching url")
        http = FakeHttpClient(exception=original)
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError) as exc_info:
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

        assert exc_info.value.__cause__ is original

    def test_missing_access_key_raises_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing UNSPLASH_ACCESS_KEY env var raises ProviderError before any HTTP call."""
        monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)

        http = FakeHttpClient(response=_make_unsplash_response([]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        with pytest.raises(ProviderError, match="access key not configured"):
            list(provider.search("test", max_results=5, http=http, cancel=cancel))

        # No HTTP call should have been made
        assert len(http.calls) == 0


# ---------------------------------------------------------------------------
# Empty result list
# ---------------------------------------------------------------------------


class TestEmptyResults:
    """Verify that empty result lists are handled gracefully."""

    def test_empty_results_array_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty 'results' array yields zero ImageResults without error."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        http = FakeHttpClient(response=_make_unsplash_response([]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        results = list(provider.search("nonexistent", max_results=10, http=http, cancel=cancel))

        assert results == []

    def test_zero_max_results_returns_empty_without_http_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_results=0 short-circuits to empty without issuing an HTTP call."""
        monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "test-key-123")

        http = FakeHttpClient(response=_make_unsplash_response([_make_unsplash_item()]))
        cancel = CancellationToken()
        provider = UnsplashProvider()

        results = list(provider.search("test", max_results=0, http=http, cancel=cancel))

        assert results == []
        assert len(http.calls) == 0
