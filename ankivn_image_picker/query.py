"""Query normalization for the Smart Image Picker.

The picker reads its search query from the configured source field of
the current note. Anki stores field values as HTML, so before issuing a
search request the raw value is reduced to a plain-text query: tags are
stripped, character and entity references are decoded, runs of
whitespace are collapsed to a single space, and the ends are trimmed.

The implementation is intentionally dependency-free and deterministic so
it can be exhaustively covered by property-based tests (see Property 3
in ``design.md``). Specifically, for every input ``s`` that contains
neither ``<`` nor ``>``:

- prepending or appending whitespace-only strings does not change the
  result (whitespace-trim invariance),
- wrapping ``s`` in a single matched HTML tag does not change the
  result (single-tag-wrap invariance),
- :func:`normalize_query` is idempotent,

and for *every* input the output never starts or ends with whitespace
and never contains ``<`` or ``>``.

The module satisfies the ``query.py`` interface in ``design.md`` and
implements the behaviour required by Requirements 3.2 and 3.3.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# A run of one or more Unicode whitespace characters.
_WHITESPACE_RUN = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    """:class:`html.parser.HTMLParser` subclass that keeps only data.

    Start, end, and self-closing tags are silently discarded along with
    any tag attributes, comments, processing instructions, declarations,
    and unknown markup. Character and entity references are decoded by
    the base parser before being delivered to :meth:`handle_data`
    because the parser is constructed with ``convert_charrefs=True``
    (the default since Python 3.5).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    # `HTMLParser` invokes this for every block of plain text data,
    # including data that originated as a character or entity reference.
    def handle_data(self, data: str) -> None:  # noqa: D401 - HTMLParser hook
        self._chunks.append(data)

    # Treat malformed/unknown declarations as if they were absent.
    def handle_decl(self, decl: str) -> None:  # noqa: D401 - HTMLParser hook
        return None

    def get_text(self) -> str:
        """Return the concatenation of all data chunks seen so far."""
        return "".join(self._chunks)


def normalize_query(raw: str) -> str:
    """Reduce a raw source-field value to a plain-text search query.

    The transformation is, in order:

    1. Strip HTML tags via a custom :class:`HTMLParser` subclass. This
       removes start tags, end tags, self-closing tags, attributes,
       comments, processing instructions, and unknown declarations.
    2. Decode HTML character and entity references (``&amp;`` becomes
       ``&``, ``&#x1f600;`` becomes ``😀``). The parser delivers
       decoded data to the extractor automatically.
    3. Drop any residual ``<`` or ``>`` characters that survived steps
       1 and 2 (for example, ``&lt;`` decodes to a literal ``<``). This
       keeps the output free of HTML angle brackets so the result is
       safe to embed in tooltips and dialog titles, and it preserves
       the "no ``<`` or ``>`` in output" invariant of Property 3.
    4. Collapse every run of Unicode whitespace into a single ASCII
       space.
    5. Trim leading and trailing whitespace.

    The function is total: it never raises for any ``str`` input,
    including the empty string and strings containing malformed HTML.
    The empty string normalises to the empty string.

    Parameters
    ----------
    raw:
        The raw source-field value as stored by Anki. May contain HTML
        tags, character or entity references, and arbitrary whitespace.

    Returns
    -------
    str
        The normalised plain-text search query.

    Examples
    --------
    >>> normalize_query("")
    ''
    >>> normalize_query("  hello   world  ")
    'hello world'
    >>> normalize_query("<b>chó &amp; mèo</b>")
    'chó & mèo'
    >>> normalize_query("<p>foo</p>  <p>bar</p>")
    'foo bar'
    """

    if not raw:
        return ""

    parser = _TextExtractor()
    # `HTMLParser.feed` accepts arbitrary partial input; `close` flushes
    # any buffered data so trailing text is not lost.
    parser.feed(raw)
    parser.close()
    text = parser.get_text()

    # Strip residual angle brackets revealed by entity decoding; this
    # keeps the output invariant required by Property 3 even when the
    # input contains escaped brackets like `&lt;` or `&gt;`.
    if "<" in text or ">" in text:
        text = text.replace("<", "").replace(">", "")

    collapsed = _WHITESPACE_RUN.sub(" ", text)
    return collapsed.strip()


__all__ = ["normalize_query"]
