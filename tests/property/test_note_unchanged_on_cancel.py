"""Property test for note-unchanged-on-cancel.

Implements Property 9 from the design document's "Correctness
Properties" section. The property is:

    For any ``Note`` ``n`` and any sequence of search and re-query
    events that does not include a "user clicks an image" event, after
    the picker is closed ``note_state(n)`` (a tuple of all field values
    plus tags) is byte-identical to the state captured before the picker
    was opened, and ``mw.col.update_note`` was never called for ``n``.

The test models the picker lifecycle without user selection:

1. Construct a note with arbitrary field values and tags.
2. Open the picker dialog (which starts a search).
3. Optionally perform re-query events (submitting new search terms).
4. Close the picker without clicking any image.
5. Assert that the note's fields and tags are byte-identical to the
   state captured before the picker was opened.
6. Assert that ``mw.col.update_note`` was never called.

The key insight is that the picker dialog only modifies the note via
``editor_bridge.insert_image`` (which calls ``mw.col.update_note``),
and that path is only triggered by ``download_complete`` — which
requires a user image click. Without a click, the note must remain
pristine.

**Validates: Requirements 6.4**
"""

from __future__ import annotations

from typing import Any, List, Optional
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.config import Config
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.picker_dialog import PickerDialog
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Arbitrary field content that a note might contain. Excludes surrogates
#: which are not valid in Python strings Anki would produce.
_field_content = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=100,
)

#: Arbitrary tag strings (Anki tags are space-separated strings).
_tag_content = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="_-::",
        blacklist_categories=("Cs",),
    ),
    min_size=1,
    max_size=20,
)


@st.composite
def note_state_scenario(draw: st.DrawFn) -> dict:
    """Generate a scenario with a note and a sequence of events.

    The scenario includes:
    - A note with 2-5 fields of arbitrary content
    - A list of tags
    - A sequence of 0-3 re-query events (new search terms submitted
      without clicking any image)
    """
    # Generate field names and values
    num_fields = draw(st.integers(min_value=2, max_value=5))
    field_names = [f"field_{i}" for i in range(num_fields)]
    field_values = draw(
        st.lists(_field_content, min_size=num_fields, max_size=num_fields)
    )

    # Ensure source_field and target_field are among the field names
    field_names[0] = "word"
    field_names[1] = "image"

    # Put a non-empty query in the source field so the picker opens
    source_value = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
                blacklist_categories=("Cs",),
            ),
            min_size=1,
            max_size=30,
        )
    )
    field_values[0] = source_value

    # Generate tags
    tags = draw(st.lists(_tag_content, min_size=0, max_size=5))

    # Generate re-query events (search terms submitted without clicking)
    num_requeries = draw(st.integers(min_value=0, max_value=3))
    requery_terms = draw(
        st.lists(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("L", "N"),
                    blacklist_categories=("Cs",),
                ),
                min_size=1,
                max_size=20,
            ),
            min_size=num_requeries,
            max_size=num_requeries,
        )
    )

    # Generate some search results that arrive (but are never clicked)
    num_results = draw(st.integers(min_value=0, max_value=6))

    return {
        "field_names": field_names,
        "field_values": field_values,
        "tags": tags,
        "requery_terms": requery_terms,
        "num_results": num_results,
    }


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeNote:
    """Minimal fake of ``anki.notes.Note``."""

    def __init__(
        self, fields: list[str], field_names: list[str], tags: list[str]
    ) -> None:
        self.fields = list(fields)  # mutable copy
        self._field_names = field_names
        self.tags = list(tags)  # mutable copy

    def note_type(self) -> dict[str, Any]:
        return {"flds": [{"name": name} for name in self._field_names]}


class _FakeEditor:
    """Minimal fake of ``aqt.editor.Editor``."""

    def __init__(self, note: _FakeNote) -> None:
        self.note = note

    def loadNoteKeepingFocus(self) -> None:
        pass


class _FakeCol:
    """Minimal fake of ``anki.collection.Collection``.

    Tracks calls to ``update_note`` so we can assert it was never
    called during a cancel-without-selection scenario.
    """

    def __init__(self) -> None:
        self.update_note_calls: list[Any] = []
        self.media = _FakeMedia()

    def update_note(self, note: Any) -> None:
        self.update_note_calls.append(note)


class _FakeMedia:
    """Minimal fake of ``anki.media.MediaManager``."""

    def write_data(self, filename: str, data: bytes) -> str:
        return filename

    def have(self, filename: str) -> bool:
        return False


class _FakeMw:
    """Minimal fake of ``aqt.mw``."""

    def __init__(self) -> None:
        self.col = _FakeCol()


class _FakeProvider:
    """A provider that yields a fixed number of results."""

    def __init__(self, provider_id: str, num_results: int) -> None:
        self.id = provider_id
        self.display_name = provider_id.title()
        self._num_results = num_results

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> list[ImageResult]:
        results = []
        for i in range(min(self._num_results, max_results)):
            results.append(
                ImageResult(
                    provider_id=self.id,
                    thumbnail_url=f"https://example.com/thumb/{self.id}/{i}.jpg",
                    full_url=f"https://example.com/full/{self.id}/{i}.jpg",
                    extension="jpg",
                    source_page_url=None,
                )
            )
        return results


class _FakeHttpClient:
    """HTTP client that never actually makes requests."""

    def get(self, url: str, *, cancel: Any = None) -> Any:
        # Return a minimal response object
        resp = MagicMock()
        resp.body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp.content_type = "image/png"
        return resp


class _FakeCache:
    """Cache that always misses (returns None on get)."""

    def get(self, url: str) -> Optional[bytes]:
        return None

    def put(self, url: str, data: bytes) -> None:
        pass

    def size_bytes(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_note_state(note: _FakeNote) -> tuple:
    """Capture the full state of a note as an immutable tuple.

    Returns a tuple of (tuple_of_field_values, tuple_of_tags) that can
    be compared for byte-identity after the picker lifecycle.
    """
    return (tuple(note.fields), tuple(note.tags))


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(scenario=note_state_scenario())
@settings(max_examples=200, deadline=10000)
def test_note_unchanged_on_cancel(scenario: dict) -> None:
    """Property 9: Closing the picker without selecting leaves the note unchanged.

    For any Note n and any sequence of search and re-query events that
    does not include a "user clicks an image" event, after the picker
    is closed note_state(n) is byte-identical to the state captured
    before the picker was opened, and mw.col.update_note was never
    called for n.

    **Validates: Requirements 6.4**
    """
    field_names = scenario["field_names"]
    field_values = scenario["field_values"]
    tags = scenario["tags"]
    requery_terms = scenario["requery_terms"]
    num_results = scenario["num_results"]

    # --- Set up the note and editor ---
    note = _FakeNote(
        fields=field_values, field_names=field_names, tags=tags
    )
    editor = _FakeEditor(note)
    fake_mw = _FakeMw()

    # Capture note state BEFORE the picker is opened
    state_before = _capture_note_state(note)

    # --- Set up the picker dialog ---
    config = Config(
        source_field="word",
        target_field="image",
        providers=("test_provider",),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
    )

    providers = [_FakeProvider("test_provider", num_results)]
    http = _FakeHttpClient()
    cache = _FakeCache()

    # Patch editor_bridge.mw so any accidental call would be tracked
    import ankivn_image_picker.editor_bridge as eb
    original_mw = eb.mw
    eb.mw = fake_mw

    try:
        # Create the picker dialog
        dialog = PickerDialog(
            editor=editor,
            config=config,
            query=field_values[0],  # source field value as query
            providers=providers,
            http=http,
            cache=cache,
            parent=None,
        )

        # --- Simulate search results arriving (via bus signals) ---
        # The orchestrator would emit these; we simulate them directly
        # on the bus to exercise the dialog's signal handling without
        # needing real threads.
        for i in range(num_results):
            result = ImageResult(
                provider_id="test_provider",
                thumbnail_url=f"https://example.com/thumb/test_provider/{i}.jpg",
                full_url=f"https://example.com/full/test_provider/{i}.jpg",
                extension="jpg",
                source_page_url=None,
            )
            dialog._bus.result_ready.emit(result)

        # --- Simulate re-query events (no image click) ---
        for term in requery_terms:
            dialog._search_input.setText(term)
            dialog._on_requery()

        # --- Close the picker WITHOUT selecting an image ---
        # This triggers closeEvent which calls cancel.cancel()
        dialog.closeEvent(None)

        # --- Assert: note state is byte-identical to before ---
        state_after = _capture_note_state(note)
        assert state_after == state_before, (
            f"Note state changed after picker closed without selection!\n"
            f"  Before: {state_before}\n"
            f"  After:  {state_after}\n"
            f"Property 9 violated: closing the picker without selecting "
            f"must leave the note unchanged (Req 6.4)."
        )

        # --- Assert: mw.col.update_note was never called ---
        assert len(fake_mw.col.update_note_calls) == 0, (
            f"mw.col.update_note was called {len(fake_mw.col.update_note_calls)} "
            f"time(s) despite no image being selected!\n"
            f"Property 9 violated: update_note must not be called when "
            f"the picker is closed without selection (Req 6.4)."
        )

    finally:
        # Restore original mw
        eb.mw = original_mw
