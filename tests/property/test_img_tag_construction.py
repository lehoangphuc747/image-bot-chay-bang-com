"""Property test for :func:`ankivn_image_picker.filename.build_img_tag`.

Implements Property 14 from the design document's "Correctness
Properties" section. The property is:

    For any valid Anki media filename ``f``, the string
    ``build_img_tag(f)`` parses (via ``html.parser.HTMLParser``) as a
    single ``<img>`` element whose ``src`` attribute equals ``f`` and
    which has no other attributes that would interfere with display
    (no ``style``, no ``onerror``).

**Validates: Requirements 9.1**
"""

from __future__ import annotations

from html.parser import HTMLParser

from hypothesis import given, settings

from ankivn_image_picker.filename import build_img_tag
from tests.property.strategies import valid_anki_media_filenames


# ---------------------------------------------------------------------------
# Helper: strict single-element HTML parser
# ---------------------------------------------------------------------------


class _ImgTagParser(HTMLParser):
    """Parse an HTML fragment and record all start tags encountered.

    After feeding a string, the parser exposes:

    - ``tags``: list of ``(tag_name, attrs_dict)`` tuples for every
      start tag encountered.
    - ``data_segments``: list of non-empty text data segments found
      outside of tags.
    - ``end_tags``: list of end tag names encountered.

    This is intentionally strict: any content beyond a single
    self-contained ``<img>`` element is a violation of Property 14.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.data_segments: list[str] = []
        self.end_tags: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        self.end_tags.append(tag)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.data_segments.append(data)


# Attributes that must never appear on the generated <img> tag because
# they could interfere with display or introduce security risks.
_FORBIDDEN_ATTRS = frozenset({"style", "onerror", "onload", "onclick"})


@given(valid_anki_media_filenames())
@settings(max_examples=200)
def test_build_img_tag_property_14(filename: str) -> None:
    """Property 14: img tag construction is well-formed and identifies the source."""

    result = build_img_tag(filename)

    # --- Parse the output with the standard library HTML parser. ---
    parser = _ImgTagParser()
    parser.feed(result)

    # --- Sub-property A: exactly one start tag, and it is <img>. ---
    assert len(parser.tags) == 1, (
        f"expected exactly 1 start tag, got {len(parser.tags)}: "
        f"{parser.tags!r} from {result!r}"
    )
    tag_name, attrs = parser.tags[0]
    assert tag_name == "img", (
        f"expected <img> tag, got <{tag_name}> from {result!r}"
    )

    # --- Sub-property B: no end tags or stray text data. ---
    assert not parser.end_tags, (
        f"unexpected end tags {parser.end_tags!r} in {result!r}"
    )
    assert not parser.data_segments, (
        f"unexpected text data {parser.data_segments!r} in {result!r}"
    )

    # --- Sub-property C: ``src`` attribute equals the filename. ---
    assert "src" in attrs, (
        f"<img> tag missing 'src' attribute in {result!r}"
    )
    assert attrs["src"] == filename, (
        f"src attribute {attrs['src']!r} != filename {filename!r} "
        f"in {result!r}"
    )

    # --- Sub-property D: no forbidden attributes present. ---
    present_forbidden = _FORBIDDEN_ATTRS & set(attrs.keys())
    assert not present_forbidden, (
        f"<img> tag contains forbidden attributes "
        f"{present_forbidden!r} in {result!r}"
    )
