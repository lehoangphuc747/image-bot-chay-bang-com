"""On-disk LRU thumbnail cache for the AnkiVN Smart Image Picker add-on.

The cache stores raw thumbnail bytes keyed by their source URL. It is
backed by a directory laid out as:

    <root>/
        index.json              # metadata: list of CacheEntry, JSON
        <sha256(url)>.bin       # one file per cached entry, raw bytes

Two responsibilities live in this module:

* :func:`compute_eviction` - a pure, total function from
  ``(entries, max_bytes)`` to the list of entries that should remain
  after LRU eviction. It performs no I/O and is the test surface for
  Property 8 ("LRU eviction correctness"). It is exposed at the module
  level so property tests do not need to instantiate
  :class:`ThumbnailCache` (which would force a real filesystem root).
* :class:`ThumbnailCache` - the impure facade that keeps the on-disk
  layout in sync with an in-memory dict of :class:`CacheEntry` records.
  It is thread-safe via a :class:`threading.RLock`, so worker threads
  in the orchestrator may call :meth:`~ThumbnailCache.get` and
  :meth:`~ThumbnailCache.put` directly without going through the Qt
  main thread.

This module deliberately does **not** import ``aqt``. The cache root is
the caller's responsibility (the add-on entry point passes in
``Path(mw.addonManager.addonsFolder(__name__)) / "user_files" /
"thumbnail_cache"``); keeping the dependency out makes the module
unit-testable in plain CPython without an Anki environment.

Validates Requirements 5.1, 5.2, 5.3, 5.4.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

from .logging import get_logger

__all__ = ["CacheEntry", "ThumbnailCache", "compute_eviction"]


_log = get_logger("cache")

_INDEX_FILENAME = "index.json"
_DATA_SUFFIX = ".bin"


@dataclass(frozen=True)
class CacheEntry:
    """Metadata for one entry in the thumbnail cache.

    Frozen so an entry is safe to share across threads and to use as the
    payload in pure functions like :func:`compute_eviction`.

    Attributes
    ----------
    url:
        The original thumbnail URL. The cache is keyed by this string;
        ``sha256_filename`` is derived from it deterministically.
    sha256_filename:
        The on-disk filename (without directory) for this entry's
        bytes. Equal to ``sha256(url.encode("utf-8")).hexdigest() +
        ".bin"``.
    size_bytes:
        The size of the on-disk file in bytes.
    last_access_ts:
        POSIX timestamp of the most recent ``get``/``put`` for this
        URL. Used as the LRU ordering key by :func:`compute_eviction`.
    """

    url: str
    sha256_filename: str
    size_bytes: int
    last_access_ts: float


# ---------------------------------------------------------------------------
# Pure eviction policy. See Property 8 in design.md for the invariants.
# ---------------------------------------------------------------------------


def compute_eviction(
    entries: Iterable[CacheEntry], max_bytes: int
) -> list[CacheEntry]:
    """Return the entries that should remain after LRU eviction.

    The function is pure and total: it never raises, never does I/O,
    and accepts every input. Property 8 ("LRU eviction correctness")
    guarantees:

    * If ``sum(e.size_bytes for e in entries) <= max_bytes`` the input
      is returned unchanged (input order preserved). No eviction
      occurs when already within budget.
    * Otherwise, entries are evicted in ascending ``last_access_ts``
      order (oldest first; ties broken stably by input position) until
      the cumulative size of the remaining entries is at or below
      ``max_bytes``. This is a clean LRU partition: every removed
      entry is older than every kept entry.
    * The result preserves the relative order of the kept entries from
      the input, so calling :func:`compute_eviction` on the result is
      a no-op (idempotence).
    * ``max_bytes`` <= 0 evicts everything; an empty input returns an
      empty list.

    Returning the *kept* set (rather than the *removed* set) matches
    the design's stated signature in the "cache.py" section and the
    ``kept = compute_eviction(...)`` notation used in Property 8.
    """

    # Materialize once so we can index by position (for stable tie-breaking)
    # and iterate twice (for the under-budget short-circuit).
    items = list(entries)
    if not items:
        return []

    total = sum(e.size_bytes for e in items)
    if total <= max_bytes:
        # Already under budget: no eviction, original order preserved
        # so that compute_eviction is the identity on already-valid
        # inputs (idempotence).
        return list(items)

    # Sort indices by (last_access_ts asc, original_position asc) to
    # define the eviction order without disturbing input order in the
    # result. Python's sort is stable, so the (i,) suffix only matters
    # when two entries share a timestamp.
    eviction_order = sorted(
        range(len(items)),
        key=lambda i: (items[i].last_access_ts, i),
    )

    evicted: set[int] = set()
    remaining = total
    for i in eviction_order:
        if remaining <= max_bytes:
            break
        evicted.add(i)
        remaining -= items[i].size_bytes

    return [e for i, e in enumerate(items) if i not in evicted]


# ---------------------------------------------------------------------------
# On-disk cache facade.
# ---------------------------------------------------------------------------


def _hash_filename(url: str) -> str:
    """Return the deterministic on-disk filename for ``url``.

    SHA-256 is used because the URL space is open-ended and may contain
    characters that are not safe on every filesystem. The hex digest is
    case-insensitive ASCII and short enough (64 chars + ``.bin``) to
    stay well under filesystem name-length limits on every platform.
    """

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return digest + _DATA_SUFFIX


class ThumbnailCache:
    """Thread-safe, on-disk LRU cache of thumbnail bytes.

    Parameters
    ----------
    root:
        Directory in which to store the cache. Created (with parents)
        if it does not exist. The caller is responsible for choosing
        the right location; the add-on entry point uses
        ``mw.addonManager.addonsFolder(__name__)/user_files/thumbnail_cache``
        per Req 5.3, but this class deliberately does not depend on
        ``aqt`` so it can be tested in isolation.
    max_bytes:
        Soft upper bound on the on-disk size of the cache (Req 5.4).
        ``put`` triggers eviction whenever the post-write total would
        exceed this value; the cache may still briefly exceed the
        limit during ``put`` and immediately before
        :meth:`evict_to` runs.

    Thread safety
    -------------
    A single :class:`threading.RLock` guards the in-memory index and
    every filesystem mutation. Reentrancy is required because
    :meth:`put` calls :meth:`evict_to` while already holding the lock.
    """

    def __init__(self, root: Path, max_bytes: int) -> None:
        self._root = Path(root)
        self._max_bytes = int(max_bytes)
        self._lock = threading.RLock()

        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / _INDEX_FILENAME
        self._entries: dict[str, CacheEntry] = self._load_index()

        # If max_bytes shrank since the last run, bring the on-disk
        # state back under budget eagerly so memory and disk agree.
        if self._current_size() > self._max_bytes:
            self.evict_to(self._max_bytes)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, url: str) -> Optional[bytes]:
        """Return the cached bytes for ``url`` or ``None`` on miss.

        On hit, the entry's ``last_access_ts`` is bumped to "now" so
        that subsequent eviction runs treat it as recently used. The
        bump is in-memory only; the index is rewritten lazily by
        :meth:`evict_to` to keep the read path off the disk-write hot
        path.
        """

        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None
            data_path = self._root / entry.sha256_filename
            try:
                data = data_path.read_bytes()
            except OSError as exc:
                # File disappeared between checking the index and
                # reading; treat as a miss and forget the index entry.
                _log.warning(
                    "Cache hit for %r but file %s could not be read: %s; "
                    "dropping index entry",
                    url,
                    data_path,
                    exc,
                )
                del self._entries[url]
                return None

            self._entries[url] = CacheEntry(
                url=entry.url,
                sha256_filename=entry.sha256_filename,
                size_bytes=entry.size_bytes,
                last_access_ts=time.time(),
            )
            return data

    def put(self, url: str, data: bytes) -> None:
        """Store ``data`` under ``url``, replacing any prior entry.

        Triggers an eviction batch if the resulting on-disk size would
        exceed ``max_bytes``. The index file is rewritten as part of
        the eviction batch so the on-disk state always matches the
        in-memory state at every quiescent point.
        """

        filename = _hash_filename(url)
        data_path = self._root / filename

        with self._lock:
            # Write the bytes first so a crash between the write and
            # the index update leaves a consistent state (an orphaned
            # data file at worst, never an index entry pointing at a
            # missing file).
            data_path.write_bytes(data)
            entry = CacheEntry(
                url=url,
                sha256_filename=filename,
                size_bytes=len(data),
                last_access_ts=time.time(),
            )
            self._entries[url] = entry

            if self._current_size() > self._max_bytes:
                # ``evict_to`` rewrites index.json itself.
                self.evict_to(self._max_bytes)
            else:
                self._write_index()

    def size_bytes(self) -> int:
        """Return the current cumulative on-disk size of all entries."""

        with self._lock:
            return self._current_size()

    def evict_to(self, target_bytes: int) -> list[str]:
        """Evict LRU entries until the on-disk size is at or below ``target_bytes``.

        Returns the list of URLs that were evicted, in eviction order
        (oldest first). The index file is rewritten exactly once per
        call, after all data files have been removed, so a crash mid-
        eviction can only leave orphan ``.bin`` files - never an index
        entry pointing at a missing file.
        """

        with self._lock:
            entries = list(self._entries.values())
            kept = compute_eviction(entries, target_bytes)
            kept_urls = {e.url for e in kept}

            evicted_urls: list[str] = []
            # Iterate in eviction order (oldest first) so the returned
            # list reflects the order in which entries were dropped.
            for entry in sorted(entries, key=lambda e: e.last_access_ts):
                if entry.url in kept_urls:
                    continue
                self._remove_entry_file(entry)
                self._entries.pop(entry.url, None)
                evicted_urls.append(entry.url)

            self._write_index()
            return evicted_urls

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _current_size(self) -> int:
        """Sum of on-disk sizes; caller must hold ``self._lock``."""

        return sum(entry.size_bytes for entry in self._entries.values())

    def _remove_entry_file(self, entry: CacheEntry) -> None:
        """Delete an entry's data file, ignoring "already gone"."""

        data_path = self._root / entry.sha256_filename
        try:
            data_path.unlink()
        except FileNotFoundError:
            # The file was already gone; the index is being cleaned
            # up so this is benign.
            pass
        except OSError as exc:
            # Don't propagate: eviction is best-effort and the index
            # rewrite below removes the dangling reference anyway.
            _log.warning(
                "Failed to remove cache file %s: %s", data_path, exc
            )

    def _write_index(self) -> None:
        """Rewrite ``index.json`` from the in-memory entry dict.

        Uses an atomic replace so a crash mid-write cannot corrupt the
        index. Caller must hold ``self._lock``.
        """

        payload = [asdict(entry) for entry in self._entries.values()]
        tmp_path = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=0),
                encoding="utf-8",
            )
            tmp_path.replace(self._index_path)
        except OSError as exc:
            _log.warning(
                "Failed to write cache index %s: %s", self._index_path, exc
            )

    def _load_index(self) -> dict[str, CacheEntry]:
        """Read ``index.json`` if present, ignoring corrupt or stale entries.

        Entries whose data file is missing on disk are silently
        dropped: the cache treats them as misses and the on-disk
        state is healed on the next ``put``/``evict_to`` cycle.
        """

        if not self._index_path.exists():
            return {}

        try:
            raw = self._index_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "Could not read cache index %s: %s; starting empty",
                self._index_path,
                exc,
            )
            return {}

        if not isinstance(payload, list):
            _log.warning(
                "Cache index %s is not a JSON list; starting empty",
                self._index_path,
            )
            return {}

        loaded: dict[str, CacheEntry] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                entry = CacheEntry(
                    url=str(item["url"]),
                    sha256_filename=str(item["sha256_filename"]),
                    size_bytes=int(item["size_bytes"]),
                    last_access_ts=float(item["last_access_ts"]),
                )
            except (KeyError, TypeError, ValueError):
                # Malformed entry: skip silently. The healing path on
                # next put/evict_to will correct any drift.
                continue
            data_path = self._root / entry.sha256_filename
            if not data_path.exists():
                # Data file gone: entry is stale, drop it.
                continue
            loaded[entry.url] = entry
        return loaded
