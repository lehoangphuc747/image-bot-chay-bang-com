"""Shared Hypothesis strategies for the AnkiVN Smart Image Picker test suite.

Each property test task (2.5, 2.6, 2.7, 2.8, 2.9, 3.2, 6.3, 6.4, 6.5, 6.6,
7.2, 7.3, 7.4, 9.4, 9.5, 9.6) appends the strategies it needs here so they
can be reused across files. See the design document, section "Strategy
library", for the planned full set.
"""

from __future__ import annotations

from hypothesis import strategies as st

from ankivn_image_picker.cache import CacheEntry
from ankivn_image_picker.filename import (
    ALLOWED_EXT,
    sanitize_query_for_filename,
)

# ---------------------------------------------------------------------------
# Filename derivation (used by Property 13, task 2.8)
# ---------------------------------------------------------------------------

#: An extension drawn uniformly from the add-on's allowed image extensions.
#: Per task 2.8 we draw exclusively from ``ALLOWED_EXT``; the property
#: itself states "any allowed extension ``ext``", so anything outside the
#: set is out of scope for Property 13.
allowed_extensions = st.sampled_from(sorted(ALLOWED_EXT))


#: Free-form queries the user might paste into the source field. Bounded
#: in length so Hypothesis does not spend the bulk of its budget on
#: pathological inputs; the sanitiser truncates to 80 characters anyway.
filename_queries = st.text(max_size=120)


@st.composite
def filename_derivation_inputs(
    draw: st.DrawFn,
) -> tuple[str, str, set[str]]:
    """Draw a ``(query, ext, taken)`` triple for Property 13.

    The ``taken`` set is built in two layers so the strategy reaches both
    the trivial (no collision) branch and the "minimal suffix" branch of
    ``derive_filename`` reliably:

    1. A *systematic* layer that blocks the first ``k`` consecutive
       candidates — i.e. the unsuffixed candidate plus ``_2`` through
       ``_k`` — forcing the function to return ``_{k+1}``. ``k = 0``
       means nothing is blocked and the unsuffixed candidate wins.
    2. A *noise* layer of unrelated filenames that should never affect
       the chosen candidate. They occasionally collide with the next
       slot, which is fine: the property still holds because
       ``derive_filename`` walks ``n`` upward from 2 and returns the
       first miss.
    """

    query = draw(filename_queries)
    ext = draw(allowed_extensions)

    stem = sanitize_query_for_filename(query)
    norm_ext = ext.lower()

    # Block 0..8 leading candidates. The upper bound is small so each
    # example runs in microseconds while still exercising multi-digit
    # neighbourhoods (e.g. _8 -> _9).
    k = draw(st.integers(min_value=0, max_value=8))

    taken: set[str] = set()
    if k >= 1:
        taken.add(f"{stem}.{norm_ext}")
        for i in range(2, k + 1):
            taken.add(f"{stem}_{i}.{norm_ext}")

    # Unrelated filenames sprinkled in as noise.
    extras = draw(st.sets(st.text(min_size=1, max_size=30), max_size=10))
    taken |= extras

    return query, ext, taken


__all__ = [
    "allowed_extensions",
    "filename_queries",
    "filename_derivation_inputs",
    "valid_anki_media_filenames",
    "cancellation_worker_types",
    "WRAP_TAGS",
    "wrap_tags",
    "whitespace_strings",
    "bracket_free_text",
    "arbitrary_text",
    "cache_entries",
    "cache_eviction_inputs",
]


# ---------------------------------------------------------------------------
# Cancellation effectiveness (used by Property 16, task 6.6)
# ---------------------------------------------------------------------------

#: Worker type strategy for cancellation tests. Draws from the three
#: worker types that emit the signals Property 16 cares about:
#: search (result_ready), thumbnail (thumbnail_ready), and
#: download (download_complete).
cancellation_worker_types = st.sampled_from(["search", "thumbnail", "download"])


# ---------------------------------------------------------------------------
# Img tag construction (used by Property 14, task 7.2)
# ---------------------------------------------------------------------------


@st.composite
def valid_anki_media_filenames(draw: st.DrawFn) -> str:
    """Draw a valid Anki media filename produced by ``derive_filename``.

    Property 14 quantifies over "any valid Anki media filename". In
    practice, filenames reaching ``build_img_tag`` have already passed
    through ``derive_filename``, so they are of the form
    ``<sanitized_stem>[_<n>].<ext>``. This strategy generates filenames
    by running ``sanitize_query_for_filename`` on a random query and
    optionally appending a numeric suffix, then attaching an allowed
    extension — mirroring the exact output shape of ``derive_filename``.

    Additionally, we include filenames containing ``&`` (which is legal
    in filenames but must be HTML-escaped) to exercise the escaping path.
    """

    query = draw(filename_queries)
    ext = draw(allowed_extensions)
    stem = sanitize_query_for_filename(query)

    # Optionally add a numeric suffix (simulating collision resolution).
    add_suffix = draw(st.booleans())
    if add_suffix:
        n = draw(st.integers(min_value=2, max_value=99))
        base = f"{stem}_{n}"
    else:
        base = stem

    return f"{base}.{ext}"


# ---------------------------------------------------------------------------
# Query normalization (used by Property 3, task 2.7)
# ---------------------------------------------------------------------------

#: A small fixed set of valid HTML tag names used for the
#: "single-tag-wrap invariance" leg of Property 3. The set is deliberately
#: tiny because the property quantifies over a *small fixed set* of valid
#: tags, not over the whole HTML grammar; tags like ``<script>`` /
#: ``<style>`` are excluded because the standard library ``HTMLParser``
#: treats their bodies as raw CDATA, which is out of scope for the
#: invariant.
WRAP_TAGS: tuple[str, ...] = ("b", "i", "p", "span", "div", "strong", "em")


#: Hypothesis strategy that draws one tag name from :data:`WRAP_TAGS`.
wrap_tags = st.sampled_from(WRAP_TAGS)


#: Whitespace-only strings used as the ``p`` / ``q`` prefix and suffix in
#: Property 3's whitespace-trim invariance leg. The alphabet covers every
#: ASCII whitespace character that ``re`` matches with ``\s`` under the
#: default Unicode flag plus the most common Unicode space (NBSP), which
#: is the typical real-world payload Anki notes carry from copy-paste.
#: Strings may be empty: ``p == q == ""`` is the trivial case where the
#: invariance must still hold.
whitespace_strings = st.text(
    alphabet=" \t\n\r\f\v\u00a0",
    max_size=8,
)


#: Text strategy for the ``s`` value in Property 3. Excludes ``<`` and
#: ``>`` (per the property's quantifier) and Unicode surrogates (which
#: are not valid scalar values and would be rejected by encoding layers
#: outside the scope of this property).
bracket_free_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters=("<", ">"),
    ),
    max_size=80,
)


#: Unrestricted text strategy used by the universal output-shape leg of
#: Property 3 (output never starts/ends with whitespace, never contains
#: ``<`` or ``>``). Surrogates are still excluded; they are not valid
#: ``str`` payload Anki ever produces.
arbitrary_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=120,
)


# ---------------------------------------------------------------------------
# LRU eviction (used by Property 8, task 3.2)
# ---------------------------------------------------------------------------


@st.composite
def cache_entries(draw: st.DrawFn) -> CacheEntry:
    """Draw a single :class:`CacheEntry` with arbitrary size and access time.

    The ``url`` and ``sha256_filename`` fields are filled with cheap
    distinct values so equality / set-membership checks in
    :func:`compute_eviction` behave correctly. Their exact contents are
    irrelevant to Property 8 — the policy only inspects ``size_bytes``
    and ``last_access_ts`` — but they must still be unique so the
    "removed" set is unambiguous when comparing against the input.
    """

    # Use Hypothesis-drawn integers to derive a unique-ish url; the
    # eviction policy never collides on url, but we keep the surface
    # honest.
    nonce = draw(st.integers(min_value=0, max_value=2**31 - 1))
    url = f"https://example.test/img/{nonce}"
    return CacheEntry(
        url=url,
        sha256_filename=f"{nonce:064x}.bin",
        size_bytes=draw(st.integers(min_value=0, max_value=10_000)),
        last_access_ts=draw(
            st.floats(
                min_value=0.0,
                max_value=2_000_000_000.0,
                allow_nan=False,
                allow_infinity=False,
            )
        ),
    )


@st.composite
def cache_eviction_inputs(
    draw: st.DrawFn,
) -> tuple[list[CacheEntry], int]:
    """Draw a ``(entries, max_bytes)`` pair for Property 8.

    ``entries`` is a non-empty list (Property 8 quantifies over
    "non-empty list of ``CacheEntry`` values"). ``max_bytes`` is a
    positive integer. The upper bound on ``max_bytes`` is chosen large
    enough to occasionally exceed the cumulative size of the input so
    the "no eviction" branch is exercised, while small enough that
    aggressive eviction is also reachable in the same budget.
    """

    entries = draw(
        st.lists(cache_entries(), min_size=1, max_size=20)
    )
    max_bytes = draw(st.integers(min_value=1, max_value=200_000))
    return entries, max_bytes
