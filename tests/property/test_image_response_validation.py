"""Property test for :func:`ankivn_image_picker.http.is_valid_image_response`.

Implements **Property 11** from the design document:

    For any response body ``b`` (any byte string, possibly empty) and
    any ``content_type`` string, ``is_valid_image_response(b, content_type)``
    returns ``True`` if and only if ``len(b) > 0`` AND
    ``content_type.split(";")[0].strip().lower()`` is in the allowed set
    ``{"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}``.

**Validates: Requirements 7.5**

The test exercises both directions of the bi-conditional:

* The *positive* arm uses a strategy that intentionally biases toward
  cases where the function should return ``True`` (allowed media type +
  non-empty body) so a regression that drops a single allowed type
  surfaces immediately rather than only via shrinking from the
  unstructured arm.
* The *unstructured* arm draws fully arbitrary ``(body, content_type)``
  pairs - including empty bodies, missing headers, lookalike media
  types like ``image/svg+xml``, multi-parameter values such as
  ``image/png; charset=utf-8``, and odd casing/whitespace - and
  cross-checks the function's verdict against an independent
  re-implementation of the specification.

Because the specification *is* a one-line predicate, the oracle is just
a literal restatement of the rule; the value of the test lies in
Hypothesis searching the input space (especially ``content_type``
parsing edge cases) for any divergence between the implementation and
that literal restatement.
"""

from __future__ import annotations

from typing import Final, FrozenSet

from hypothesis import given, strategies as st

from ankivn_image_picker.http import (
    ALLOWED_IMAGE_MEDIA_TYPES,
    is_valid_image_response,
)


# ---------------------------------------------------------------------------
# Oracle - independent restatement of the property
# ---------------------------------------------------------------------------

# Frozen snapshot of the allow-list documented in the design. Hard-coded
# rather than imported from the implementation so that a regression which
# accidentally widens or narrows ``ALLOWED_IMAGE_MEDIA_TYPES`` is caught
# by the assertion below before the property is even exercised.
EXPECTED_ALLOWED: Final[FrozenSet[str]] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
    }
)


def test_allowed_set_matches_design_document() -> None:
    """Guard against silent drift of the allow-list constant.

    Property 11 names the five MIME types verbatim. If the constant in
    ``http.py`` is edited without a corresponding design change, this
    assertion fails before the property test runs and pinpoints the
    drift directly.
    """

    assert ALLOWED_IMAGE_MEDIA_TYPES == EXPECTED_ALLOWED


def _expected(body: bytes, content_type: str) -> bool:
    """Reference implementation of the predicate from Property 11.

    This is intentionally written as a near-literal transcription of
    the design statement rather than a clever rewrite, because its job
    is to be obviously correct, not fast.
    """

    if len(body) == 0:
        return False
    media_type = content_type.split(";")[0].strip().lower()
    return media_type in EXPECTED_ALLOWED


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Whitespace characters Python's ``str.strip`` recognises as leading or
# trailing padding around a media type value (``Content-Type`` header
# parsers are typically lenient about these).
_PADDING_WHITESPACE: Final[str] = " \t\r\n\f\v"


def _case_permutations(media_type: str) -> st.SearchStrategy[str]:
    """Yield casing variants of ``media_type``.

    Property 11 specifies a case-insensitive comparison via
    ``.lower()``, so the implementation must accept ``Image/PNG``,
    ``IMAGE/JPEG``, etc. We sample independently for each character
    rather than enumerating to keep the search space tractable while
    still covering mixed-case payloads.
    """

    return st.lists(
        st.booleans(), min_size=len(media_type), max_size=len(media_type)
    ).map(
        lambda flips: "".join(
            ch.upper() if flip else ch.lower()
            for ch, flip in zip(media_type, flips)
        )
    )


def _media_type_with_optional_padding_and_params(
    base: st.SearchStrategy[str],
) -> st.SearchStrategy[str]:
    """Wrap a media-type strategy with optional whitespace and parameters.

    Real ``Content-Type`` headers often look like
    ``image/png; charset=binary`` or have stray whitespace from a
    sloppy server. The function under test must tolerate all of these
    by virtue of ``split(";")[0].strip().lower()``.
    """

    leading = st.text(alphabet=_PADDING_WHITESPACE, max_size=3)
    trailing = st.text(alphabet=_PADDING_WHITESPACE, max_size=3)

    # Parameter segment is either absent or a ``;``-prefixed suffix
    # whose contents Hypothesis is free to fill with anything printable
    # except a literal ``;`` (so the first segment really is the whole
    # media type).
    param_segment = st.one_of(
        st.just(""),
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Cs",),
                blacklist_characters=";",
            ),
            max_size=20,
        ).map(lambda tail: ";" + tail),
    )

    return st.builds(
        lambda lead, mt, params, trail: lead + mt + params + trail,
        leading,
        base,
        param_segment,
        trailing,
    )


# Allowed media types, in mixed casing, optionally surrounded by
# whitespace and optionally followed by a parameter segment. Combined
# with non-empty bodies, this is the strategy that drives the
# ``True``-arm of the bi-conditional.
_allowed_media_type = st.sampled_from(sorted(EXPECTED_ALLOWED)).flatmap(
    _case_permutations
)
_allowed_content_type = _media_type_with_optional_padding_and_params(
    _allowed_media_type
)

# A handful of media types that are deliberately excluded from the
# allow-list (``image/svg+xml`` because Anki cannot render it inline,
# plus a sample of unrelated MIME types). Combined with non-empty
# bodies, this is the strategy that exercises the close-miss
# ``False``-arm without relying on Hypothesis's free-text search
# happening to land near the boundary.
_disallowed_known_media_types = st.sampled_from(
    [
        "image/svg+xml",
        "image/tiff",
        "image/heic",
        "image/avif",
        "image/x-icon",
        "application/octet-stream",
        "application/json",
        "text/html",
        "text/plain",
        "video/mp4",
        "",  # missing header
    ]
).flatmap(_case_permutations)
_disallowed_known_content_type = _media_type_with_optional_padding_and_params(
    _disallowed_known_media_types
)

# Fully unstructured content-type string. Hypothesis is allowed to
# produce anything from the empty string to a multi-line blob with
# semicolons and unicode, which stresses the parsing pipeline (split,
# strip, lower) much more aggressively than the curated strategies.
_arbitrary_content_type = st.text(max_size=80)

# Combine the three so the property runs on a mix of likely-positive,
# likely-negative-but-near-the-boundary, and fully arbitrary inputs.
content_type_strategy: Final = st.one_of(
    _allowed_content_type,
    _disallowed_known_content_type,
    _arbitrary_content_type,
)

# Body strategy: empty bytes appear with non-trivial frequency so the
# ``len(b) == 0`` branch is well-covered, and longer payloads exercise
# the "non-empty" branch without dominating shrink time.
body_strategy: Final = st.one_of(
    st.just(b""),
    st.binary(min_size=1, max_size=64),
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(body=body_strategy, content_type=content_type_strategy)
def test_is_valid_image_response_matches_specification(
    body: bytes, content_type: str
) -> None:
    """Validates: Requirements 7.5 (Property 11).

    For every ``(body, content_type)`` pair drawn by Hypothesis, the
    implementation's verdict must agree with the reference predicate
    from the design document. Both directions of the bi-conditional
    are checked because ``assertEqual`` on booleans is symmetric.
    """

    assert is_valid_image_response(body, content_type) == _expected(
        body, content_type
    )


@given(content_type=content_type_strategy)
def test_empty_body_is_always_rejected(content_type: str) -> None:
    """An empty body is rejected regardless of the content type.

    Singled out as its own property so a regression that accidentally
    accepts ``b""`` for an otherwise-valid header is reported with a
    minimal counterexample (``b""`` plus the offending header) instead
    of being shrunk from the joint strategy.
    """

    assert is_valid_image_response(b"", content_type) is False


@given(body=st.binary(min_size=1, max_size=32))
def test_allowed_media_type_with_nonempty_body_is_accepted(
    body: bytes,
) -> None:
    """Each allowed media type, paired with any non-empty body, is accepted.

    Iterates the allow-list explicitly so a regression that drops one
    entry (for example, removing ``image/bmp``) fails on that specific
    type rather than being masked by Hypothesis's bias toward simpler
    examples.
    """

    for media_type in sorted(EXPECTED_ALLOWED):
        assert is_valid_image_response(body, media_type) is True
        # Same media type with a parameter segment must still pass,
        # because the implementation strips everything after ``;``.
        assert (
            is_valid_image_response(body, f"{media_type}; charset=binary")
            is True
        )
        # And uppercased, because the implementation lower-cases.
        assert is_valid_image_response(body, media_type.upper()) is True
