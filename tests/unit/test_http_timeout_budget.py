"""Unit tests for the HTTP per-request timeout budget.

Req 10.2 mandates that no single network request issued by the add-on
may exceed 15 seconds. ``requests`` accepts the timeout as a
``(connect, read)`` tuple whose two values cumulatively bound the
request: the connect leg covers DNS + TCP handshake, and the read leg
covers each individual socket read while the body streams in. The
add-on's :class:`~ankivn_image_picker.http.HttpClient` enforces this
budget by passing ``(5.0, 10.0)`` -- the value of the module-level
``DEFAULT_TIMEOUT`` constant -- to every ``requests.get`` call.

This module pins both halves of that contract:

* the default-constructed client exposes the documented tuple via its
  public :attr:`HttpClient.timeout` attribute, and the two values sum
  to exactly 15 seconds, and
* the tuple is actually handed to ``requests.get`` at call time -- a
  later refactor cannot quietly pass a longer or unbounded timeout
  without this test failing.

We patch ``requests.get`` inside the :mod:`ankivn_image_picker.http`
namespace (the binding that ``HttpClient`` imports) rather than the
upstream :mod:`requests` package so the patch is hermetic and does not
leak between tests. The fake response satisfies the small contract
``HttpClient.get`` relies on: a ``status_code`` of 200, an
``iter_content`` generator yielding a single empty chunk, a ``headers``
mapping, a ``url`` string, and a ``close()`` no-op.

_Validates: Requirements 10.2_
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List
from unittest.mock import patch

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.http import DEFAULT_TIMEOUT, HttpClient


# ---------------------------------------------------------------------------
# Fake response
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for :class:`requests.Response`.

    Only the four members ``HttpClient.get`` actually touches are
    implemented: ``status_code``, ``headers``, ``url``, ``iter_content``,
    and ``close``. Anything else would be over-fitting to the current
    implementation and a maintenance hazard.
    """

    def __init__(self) -> None:
        self.status_code: int = 200
        self.headers: Dict[str, str] = {"Content-Type": "image/png"}
        self.url: str = "https://example.test/image.png"
        self.closed: bool = False

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        # Yield one empty chunk so the streaming loop completes a
        # single iteration and returns. ``HttpClient.get`` filters out
        # empty chunks before appending to its buffer, so the resulting
        # body is ``b""`` -- which is fine, the test does not assert on
        # body content.
        yield b""

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Default-timeout invariants
# ---------------------------------------------------------------------------


def test_default_timeout_constant_is_five_then_ten_summing_to_fifteen() -> None:
    """The module-level ``DEFAULT_TIMEOUT`` is the documented tuple.

    Pinning the literal here means a future contributor cannot widen
    the budget without updating both the constant and this test.
    """

    assert DEFAULT_TIMEOUT == (5.0, 10.0)
    # Floating-point equality is safe here: both operands are exact
    # integers represented as floats, well within the precision of
    # IEEE-754 doubles.
    assert sum(DEFAULT_TIMEOUT) == 15.0


def test_http_client_default_timeout_attribute_matches_constant() -> None:
    """A default-constructed client exposes ``(5.0, 10.0)`` via ``.timeout``.

    The attribute is the public surface the rest of the add-on (and
    this test) reads when it needs to know the budget without
    monkey-patching :mod:`requests`.
    """

    client = HttpClient()

    assert client.timeout == (5.0, 10.0)
    assert sum(client.timeout) == 15.0
    # Identity is not required; what matters is value equality. The
    # next assertion documents the link: the client default is the
    # same value as the module-level constant.
    assert client.timeout == DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# Timeout is actually passed through to requests.get
# ---------------------------------------------------------------------------


def test_http_client_get_passes_timeout_tuple_to_requests_get() -> None:
    """``HttpClient.get`` forwards ``timeout=(5.0, 10.0)`` to the session.

    Without this assertion a future refactor could drop the keyword,
    pass a single scalar (which collapses both legs into one), or
    pass ``None`` (which means "no timeout") -- any of which would
    silently break Req 10.2.
    """

    captured_kwargs: List[Dict[str, Any]] = []
    captured_args: List[tuple] = []

    def fake_get(self: Any, *args: Any, **kwargs: Any) -> _FakeResponse:
        captured_args.append(args)
        captured_kwargs.append(kwargs)
        return _FakeResponse()

    client = HttpClient()
    cancel = CancellationToken()

    # ``HttpClient`` now keeps a persistent ``requests.Session`` so
    # connections can be reused across calls. Patch ``Session.get``
    # rather than the module-level ``requests.get``.
    with patch("requests.Session.get", new=fake_get):
        response = client.get(
            "https://example.test/image.png", cancel=cancel
        )

    # Sanity-check that the call actually went through our fake.
    assert response.status_code == 200
    assert len(captured_kwargs) == 1, (
        "expected exactly one HTTP call, got "
        f"{len(captured_kwargs)}"
    )

    kwargs = captured_kwargs[0]
    # The whole point of the test: the timeout the client hands to
    # ``Session.get`` must be the documented (connect, read) tuple.
    assert kwargs.get("timeout") == (5.0, 10.0)
    assert sum(kwargs["timeout"]) == 15.0
    # ``stream=True`` is part of the same call contract -- losing it
    # would break per-chunk cancellation polling -- but the timeout
    # assertion is the headline of this test.
    assert kwargs.get("stream") is True


def test_http_client_custom_timeout_is_forwarded_to_requests_get() -> None:
    """A caller-supplied timeout overrides the default and is propagated.

    The constructor accepts a ``timeout=`` keyword for tests and for
    callers that need a tighter budget; this test verifies the override
    path without weakening the default-budget guarantee above.
    """

    captured_kwargs: List[Dict[str, Any]] = []

    def fake_get(self: Any, *args: Any, **kwargs: Any) -> _FakeResponse:
        captured_kwargs.append(kwargs)
        return _FakeResponse()

    client = HttpClient(timeout=(2.0, 3.0))
    cancel = CancellationToken()

    with patch("requests.Session.get", new=fake_get):
        client.get("https://example.test/image.png", cancel=cancel)

    assert client.timeout == (2.0, 3.0)
    assert captured_kwargs[0].get("timeout") == (2.0, 3.0)
