"""Property test for re-query refans-out.

Implements **Property 10** from the design document:

    For any pair of non-empty queries ``q1 != q2`` and any provider list
    ``P``, after the picker has run ``q1`` and the user submits ``q2``
    in the search bar, the grid model is empty before any new results
    arrive and exactly ``len(P)`` new provider tasks are scheduled with
    ``q2`` as the query.

The test models the picker lifecycle with a re-query event:

1. Construct a picker dialog with query ``q1`` and provider list ``P``.
2. Simulate initial search results arriving for ``q1``.
3. Submit ``q2`` via the search bar (triggering ``_on_requery``).
4. Assert that the grid model is empty immediately after the re-query
   (before any new results arrive).
5. Assert that exactly ``len(P)`` new provider tasks were scheduled
   with ``q2`` as the query.

The key insight is that ``_on_requery`` synchronously clears the grid
model and then calls ``_start_search(q2)`` which fans out to all
providers. We intercept the orchestrator's ``run`` call to verify the
query argument and count the provider tasks scheduled.

**Validates: Requirements 6.5**
"""

from __future__ import annotations

from typing import Any, List, Optional
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from ankivn_image_picker.cancellation import CancellationToken
from ankivn_image_picker.config import Config
from ankivn_image_picker.providers.base import ImageResult
from ankivn_image_picker.ui.picker_dialog import PickerDialog
from ankivn_image_picker.ui.worker_bus import WorkerBus


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Non-empty query strings that are already in normalized form.
#: We use only ASCII letters and digits so that normalize_query is
#: guaranteed to be an identity on them (no whitespace, no HTML, no
#: control characters). This ensures q1 and q2 remain distinct after
#: normalization.
_nonempty_query = st.from_regex(r"[A-Za-z0-9]{1,30}", fullmatch=True)

#: Provider ID strategy — short non-empty strings.
_provider_id = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        whitelist_characters="_-",
    ),
    min_size=1,
    max_size=12,
)


@st.composite
def requery_scenario(draw: st.DrawFn) -> dict:
    """Generate a scenario with two distinct queries and a provider list.

    Returns a dict with:
    - q1: the initial query
    - q2: the re-query (guaranteed different from q1)
    - provider_ids: a non-empty list of provider IDs
    - num_initial_results: number of results to simulate for q1
    """
    q1 = draw(_nonempty_query)
    q2 = draw(_nonempty_query)
    assume(q1 != q2)

    num_providers = draw(st.integers(min_value=1, max_value=6))
    provider_ids = draw(
        st.lists(
            _provider_id,
            min_size=num_providers,
            max_size=num_providers,
        )
    )

    num_initial_results = draw(st.integers(min_value=0, max_value=8))

    return {
        "q1": q1,
        "q2": q2,
        "provider_ids": provider_ids,
        "num_initial_results": num_initial_results,
    }


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProvider:
    """A provider that records search calls for verification."""

    def __init__(self, provider_id: str) -> None:
        self.id = provider_id
        self.display_name = f"Mock {provider_id}"
        self.search_calls: List[str] = []

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> list:
        self.search_calls.append(query)
        return []


class _FakeHttpClient:
    """HTTP client that never actually makes requests."""

    def get(self, url: str, *, cancel: Any = None) -> Any:
        resp = MagicMock()
        resp.body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp.content_type = "image/png"
        return resp


class _FakeCache:
    """Cache that always misses."""

    def get(self, url: str) -> Optional[bytes]:
        return None

    def put(self, url: str, data: bytes) -> None:
        pass

    def size_bytes(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# Tracking orchestrator
# ---------------------------------------------------------------------------


class _TrackingOrchestrator:
    """An orchestrator replacement that records run() calls.

    Instead of actually submitting tasks to a thread pool, this records
    the query and the number of providers it would fan out to. This
    allows us to verify the re-query behaviour synchronously without
    race conditions.
    """

    def __init__(
        self,
        providers: list,
        cfg: Any,
        http: Any,
        cache: Any,
        bus: Any,
        cancel: Any,
    ) -> None:
        self.providers = providers
        self.cfg = cfg
        self.http = http
        self.cache = cache
        self.bus = bus
        self.cancel = cancel
        self.run_calls: List[str] = []

    def run(self, query: str) -> None:
        """Record the query and provider count."""
        self.run_calls.append(query)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(scenario=requery_scenario())
@settings(max_examples=200, deadline=10000)
def test_requery_clears_grid_and_refans_out(scenario: dict) -> None:
    """Property 10: Re-query clears the grid and refans out to all providers.

    For any pair of non-empty queries q1 != q2 and any provider list P,
    after the picker has run q1 and the user submits q2 in the search
    bar, the grid model is empty before any new results arrive and
    exactly len(P) new provider tasks are scheduled with q2 as the
    query.

    **Validates: Requirements 6.5**
    """
    q1 = scenario["q1"]
    q2 = scenario["q2"]
    provider_ids = scenario["provider_ids"]
    num_initial_results = scenario["num_initial_results"]

    # --- Build providers ---
    providers = [_FakeProvider(pid) for pid in provider_ids]

    # --- Build config ---
    config = Config(
        source_field="word",
        target_field="image",
        providers=tuple(provider_ids),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
    )

    http = _FakeHttpClient()
    cache = _FakeCache()

    # Track orchestrator instantiations and run calls
    orchestrator_instances: List[_TrackingOrchestrator] = []

    def _mock_orchestrator_class(*args: Any, **kwargs: Any) -> _TrackingOrchestrator:
        orch = _TrackingOrchestrator(**kwargs)
        orchestrator_instances.append(orch)
        return orch

    # Patch SearchOrchestrator at the source module (it's imported
    # locally inside _start_search via `from ..orchestrator import
    # SearchOrchestrator`)
    with patch(
        "ankivn_image_picker.orchestrator.SearchOrchestrator",
        side_effect=_mock_orchestrator_class,
    ):
        # Create the picker dialog with q1
        dialog = PickerDialog(
            editor=MagicMock(),
            config=config,
            query=q1,
            providers=providers,
            http=http,
            cache=cache,
            parent=None,
        )

        # Verify initial search was started with q1
        assert len(orchestrator_instances) == 1
        assert orchestrator_instances[0].run_calls == [q1]

        # --- Simulate initial results arriving for q1 ---
        for i in range(num_initial_results):
            result = ImageResult(
                provider_id=provider_ids[i % len(provider_ids)],
                thumbnail_url=f"https://example.com/thumb/{i}.jpg",
                full_url=f"https://example.com/full/{i}.jpg",
                extension="jpg",
                source_page_url=None,
            )
            dialog._bus.result_ready.emit(result)

        # Verify grid has results from q1
        assert dialog._grid_model.row_count() == num_initial_results

        # --- Submit re-query q2 ---
        dialog._search_input.setText(q2)
        dialog._on_requery()

    # --- Assert: grid model is empty after re-query (before new results) ---
    assert dialog._grid_model.row_count() == 0, (
        f"Grid model should be empty after re-query but has "
        f"{dialog._grid_model.row_count()} rows.\n"
        f"  q1={q1!r}, q2={q2!r}, initial_results={num_initial_results}\n"
        f"Property 10 violated: re-query must clear the grid (Req 6.5)."
    )

    # --- Assert: a new orchestrator was created for q2 ---
    # The second orchestrator instance is from the re-query
    assert len(orchestrator_instances) == 2, (
        f"Expected 2 orchestrator instances (one for q1, one for q2), "
        f"got {len(orchestrator_instances)}."
    )

    requery_orch = orchestrator_instances[1]

    # --- Assert: the new orchestrator was called with q2 ---
    assert requery_orch.run_calls == [q2], (
        f"Re-query orchestrator should have been called with q2={q2!r}, "
        f"but run_calls={requery_orch.run_calls!r}.\n"
        f"Property 10 violated: re-query must fan out with the new query."
    )

    # --- Assert: the new orchestrator received all providers ---
    assert len(requery_orch.providers) == len(provider_ids), (
        f"Re-query orchestrator should have {len(provider_ids)} providers, "
        f"but got {len(requery_orch.providers)}.\n"
        f"  provider_ids={provider_ids}\n"
        f"Property 10 violated: re-query must fan out to ALL providers "
        f"(Req 6.5)."
    )

    # Verify provider IDs match
    actual_provider_ids = [p.id for p in requery_orch.providers]
    assert actual_provider_ids == provider_ids, (
        f"Re-query orchestrator provider IDs don't match.\n"
        f"  Expected: {provider_ids}\n"
        f"  Actual: {actual_provider_ids}\n"
        f"Property 10 violated: re-query must fan out to all configured "
        f"providers (Req 6.5)."
    )
