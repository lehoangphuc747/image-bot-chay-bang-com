"""Property test for :func:`ankivn_image_picker.cache.compute_eviction`.

Implements **Property 8** from the design document's "Correctness
Properties" section. The property is:

    For any non-empty list of ``CacheEntry`` values ``E`` (with
    arbitrary ``size_bytes`` and arbitrary ``last_access_ts``) and any
    positive integer ``max_bytes``, let
    ``kept = compute_eviction(E, max_bytes)`` and
    ``removed = E \\ kept``. Then:

    - ``sum(e.size_bytes for e in kept) <= max(max_bytes,
      max(e.size_bytes for e in E))`` (post-condition: total size is
      at or below the limit, except when even a single entry exceeds
      the limit, in which case the largest is allowed to remain
      alone).
    - ``removed`` consists of the entries with the oldest
      ``last_access_ts`` (LRU order).
    - ``compute_eviction(kept, max_bytes) == kept`` (idempotence:
      re-running eviction on the result is a no-op).
    - If ``sum(e.size_bytes for e in E) <= max_bytes`` then
      ``kept == E`` (no evictions when already under the limit).

**Validates: Requirements 5.4**

The four sub-properties are checked together on every Hypothesis
example so a counter-example simultaneously names which leg of
Property 8 it breaks.
"""

from __future__ import annotations

from hypothesis import given, settings

from ankivn_image_picker.cache import CacheEntry, compute_eviction
from tests.property.strategies import cache_eviction_inputs


def _entry_id(entry: CacheEntry) -> tuple[str, str]:
    """Return a hashable identity for ``entry`` used to compute set
    differences without relying on ``CacheEntry.__eq__`` collisions
    on randomly generated data.

    ``cache_entries`` (in :mod:`tests.property.strategies`) seeds each
    entry with a unique ``url``/``sha256_filename`` derived from a
    32-bit nonce, so this pair is a stable identity even when two
    entries happen to share a timestamp or size.
    """

    return (entry.url, entry.sha256_filename)


@given(cache_eviction_inputs())
@settings(max_examples=200)
def test_compute_eviction_property_8(
    inputs: tuple[list[CacheEntry], int],
) -> None:
    entries, max_bytes = inputs

    kept = compute_eviction(entries, max_bytes)

    # ``removed`` is the input minus the kept set, identified by the
    # (url, sha256_filename) tuple. Using identity rather than
    # ``CacheEntry.__eq__`` keeps the partition unambiguous when two
    # entries happen to be value-equal (Hypothesis can produce
    # duplicates although the strategy seeds distinct urls).
    kept_ids = {_entry_id(e) for e in kept}
    removed = [e for e in entries if _entry_id(e) not in kept_ids]

    # --- Sub-property A: size post-condition. ---
    #
    # ``sum(kept.size_bytes) <= max(max_bytes, max single-entry size)``.
    # The right-hand side handles the documented exception: when even
    # a single entry exceeds the limit, the largest one may remain
    # alone, in which case the total size of ``kept`` is allowed to
    # equal that single entry's size rather than ``max_bytes``.
    kept_total = sum(e.size_bytes for e in kept)
    largest = max(e.size_bytes for e in entries)
    assert kept_total <= max(max_bytes, largest), (
        f"size post-condition violated: kept_total={kept_total}, "
        f"max_bytes={max_bytes}, largest_entry={largest}"
    )

    # --- Sub-property B: removed entries are the LRU set. ---
    #
    # In a stable LRU partition, every removed entry is at least as
    # old as every kept entry. Equality on ``last_access_ts`` is
    # allowed: when two entries share a timestamp, a stable
    # tie-break may keep one and remove the other. The ``<=`` check
    # captures that boundary correctly without over-constraining the
    # tie-break rule, which Property 8 leaves unspecified.
    if removed and kept:
        max_removed_ts = max(e.last_access_ts for e in removed)
        min_kept_ts = min(e.last_access_ts for e in kept)
        assert max_removed_ts <= min_kept_ts, (
            "LRU order violated: a removed entry is more recent than "
            f"a kept entry (max_removed_ts={max_removed_ts}, "
            f"min_kept_ts={min_kept_ts})"
        )

    # --- Sub-property C: idempotence. ---
    #
    # Re-running eviction on the result of a prior eviction is a
    # no-op. This is the strongest fixed-point statement Property 8
    # makes about the policy and rules out implementations that
    # accidentally over- or under-evict on the second pass.
    kept_again = compute_eviction(kept, max_bytes)
    assert kept_again == kept, (
        "idempotence violated: compute_eviction(kept, max_bytes) "
        f"returned a different list ({kept_again!r}) than its input "
        f"({kept!r})"
    )

    # --- Sub-property D: no eviction when already under budget. ---
    #
    # If the input already fits within ``max_bytes``, the policy must
    # return it unchanged (same elements, same order). This pins the
    # "trivial" branch so that adding eviction logic for the
    # over-budget branch cannot perturb already-valid inputs.
    if sum(e.size_bytes for e in entries) <= max_bytes:
        assert kept == list(entries), (
            "no-op-when-under-budget violated: input total fit in "
            f"max_bytes={max_bytes} but kept != entries (kept={kept!r}, "
            f"entries={list(entries)!r})"
        )
