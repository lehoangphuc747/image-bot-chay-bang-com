"""Property tests for :mod:`ankivn_image_picker.query`.

Feature: ankivn-image-picker, Property 3: Query normalization invariants.

The design document (``Correctness Properties`` section) states:

    For any string ``s`` not containing ``<`` or ``>``, and any
    whitespace-only strings ``p`` and ``q``, and any HTML tag name ``t``
    from a small fixed set of valid tags, the following hold:

    - ``normalize_query(p + s + q) == normalize_query(s)``
      (whitespace-trim invariance).
    - ``normalize_query(f"<{t}>{s}</{t}>") == normalize_query(s)``
      (single-tag-wrap invariance).
    - ``normalize_query(normalize_query(s)) == normalize_query(s)``
      (idempotence).
    - The output never starts or ends with whitespace, and never
      contains ``<`` or ``>``.

These four sub-invariants are encoded as a single property test
(``test_normalize_query_invariants``) per the design's
"Property-to-test mapping" table, with the universal output-shape leg
applied to the entire text alphabet (including inputs that *do* contain
brackets) since the design states it holds for the output of every
input, not just the constrained ``s``.

**Validates: Requirements 3.2, 3.3**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.query import normalize_query
from tests.property.strategies import (
    arbitrary_text,
    bracket_free_text,
    whitespace_strings,
    wrap_tags,
)


@settings(max_examples=200, deadline=None)
@given(
    s=bracket_free_text,
    p=whitespace_strings,
    q=whitespace_strings,
    t=wrap_tags,
    extra=arbitrary_text,
)
def test_normalize_query_invariants(
    s: str,
    p: str,
    q: str,
    t: str,
    extra: str,
) -> None:
    """**Validates: Requirements 3.2, 3.3**

    Property 3 from ``design.md``: query normalization invariants.

    Combines all four sub-invariants into one property test as
    prescribed by the design document's "Property-to-test mapping"
    table. Each ``assert`` block corresponds to exactly one bullet of
    the property statement.
    """

    # Pre-conditions on ``s`` per the property's quantifier. The
    # ``bracket_free_text`` strategy already enforces these; the
    # asserts make the contract explicit and act as a tripwire if
    # the strategy is ever loosened.
    assert "<" not in s
    assert ">" not in s

    base = normalize_query(s)

    # ------------------------------------------------------------------
    # (1) Whitespace-trim invariance: prepending and appending
    # whitespace-only strings does not change the result. Covers the
    # "trim leading and trailing whitespace" half of Req 3.3.
    # ------------------------------------------------------------------
    assert normalize_query(p + s + q) == base

    # ------------------------------------------------------------------
    # (2) Single-tag-wrap invariance: wrapping ``s`` in a single
    # matched HTML tag does not change the result. Covers Req 3.2
    # (strip HTML tags before using the value as a search query).
    # ------------------------------------------------------------------
    wrapped = f"<{t}>{s}</{t}>"
    assert normalize_query(wrapped) == base

    # ------------------------------------------------------------------
    # (3) Idempotence: normalising an already-normalised string
    # returns it unchanged. Covers Req 3.3 (the trim+collapse step
    # has nothing left to do on a value that is already trimmed and
    # whitespace-collapsed).
    # ------------------------------------------------------------------
    assert normalize_query(base) == base

    # ------------------------------------------------------------------
    # (4) Output shape: the result never starts or ends with
    # whitespace, and never contains ``<`` or ``>``. Applied to
    # ``base`` (constrained input) *and* to ``normalize_query(extra)``
    # (arbitrary text, possibly containing brackets) so the universal
    # leg of the property is exercised across the full input space.
    # Covers Req 3.2 + 3.3 jointly.
    # ------------------------------------------------------------------
    for output in (base, normalize_query(extra)):
        if output:
            assert not output[0].isspace()
            assert not output[-1].isspace()
        assert "<" not in output
        assert ">" not in output


@given(s=st.just(""))
def test_normalize_query_empty_input_is_empty(s: str) -> None:
    """Trivial example fixed by Hypothesis: ``""`` is the fixed point.

    Hypothesis ordinarily skips degenerate examples; this guard pins
    the empty-string case so a regression that returns ``" "`` or
    ``None`` for ``""`` cannot slip through. **Validates: Requirements
    3.2, 3.3**.
    """

    assert normalize_query(s) == ""
