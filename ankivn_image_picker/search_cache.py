"""On-disk cache for provider search results.

The thumbnail cache (see :mod:`cache`) already stores raw image bytes
keyed by URL. That alone is not enough for fast re-runs: the picker
still needs the ``ImageResult`` metadata (full-image URL, author,
license, etc.) which only the provider's search API can produce.

This module caches search-result lists keyed by ``(provider_id,
normalised_query)`` so a re-run of the same batch picks them up
without hitting the provider API again. Combined with the thumbnail
cache, a fully-cached re-run skips network entirely.

Design points:

* Cache lives on disk so it survives Anki restarts.
* Each (provider, query) entry is one JSON file. Atomic writes via
  ``tempfile + os.replace`` keep the cache consistent across sudden
  shutdowns.
* TTL of 7 days by default — search results don't change much
  day-to-day for the same query, but shouldn't be stale forever.
* ``get`` returns ``None`` on any failure (missing file, parse error,
  TTL expired) so the caller transparently falls back to a live
  search.
* The cache is intentionally small in size — search-result JSONs are
  a few KB each — so a hard size cap is unnecessary; a periodic
  ``prune_expired`` is enough.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from .providers.base import ImageResult

_log = get_logger("search_cache")

#: Default TTL: results older than this are treated as cache misses.
DEFAULT_TTL_SECONDS: int = 7 * 24 * 60 * 60  # 7 days


def _cache_key(provider_id: str, query: str) -> str:
    """Stable filesystem-safe key for a (provider, query) pair.

    SHA-1 keeps the filename short and avoids problems with non-ASCII
    queries (Korean, Vietnamese, etc.) on Windows, which has stricter
    rules about filename characters than POSIX.
    """
    raw = f"{provider_id}\x00{query}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


class SearchCache:
    """Per-(provider, query) cached search results, on disk.

    Parameters
    ----------
    root:
        Directory where cache files are written. Created on first use.
    ttl_seconds:
        Maximum age of a cache entry before it is treated as a miss.
        Defaults to one week (:data:`DEFAULT_TTL_SECONDS`).
    """

    def __init__(self, root: Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._root = Path(root)
        self._ttl = int(ttl_seconds)
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            # The picker still works without a cache, so fall back
            # silently and log the reason.
            _log.warning("Could not create search cache dir %s: %s", root, exc)

    # ------------------------------------------------------------------

    def get(self, provider_id: str, query: str) -> Optional[list]:
        """Return cached results or ``None`` if missing/expired/corrupt."""
        path = self._path_for(provider_id, query)
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            _log.debug("Cache stat failed for %s: %s", path, exc)
            return None

        age = time.time() - stat.st_mtime
        if age > self._ttl:
            return None

        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as exc:
            _log.debug("Cache read failed for %s: %s", path, exc)
            return None

        results = payload.get("results")
        if not isinstance(results, list):
            return None

        # Lazy import keeps the cache module import-clean when used in
        # tests or environments without the providers package fully
        # initialised.
        from .providers.base import ImageResult

        out: list = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(_dict_to_result(entry))
            except (TypeError, ValueError) as exc:
                _log.debug(
                    "Skipping malformed cache entry in %s: %s", path, exc
                )
                continue
        return out

    def put(self, provider_id: str, query: str, results: list) -> None:
        """Persist ``results`` for ``(provider_id, query)``.

        Writes are atomic (temp-file + replace) so a partial write
        cannot corrupt an existing cache entry. Failures are logged
        but never raised — caching is best-effort.
        """
        if not results:
            # Empty results aren't worth caching; if the provider had
            # nothing to say, a re-run might do better.
            return

        payload = {
            "version": 1,
            "provider_id": provider_id,
            "query": query,
            "ts": int(time.time()),
            "results": [_result_to_dict(r) for r in results],
        }

        path = self._path_for(provider_id, query)
        try:
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=".tmp_", suffix=".json", dir=str(self._root)
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                os.replace(tmp_name, path)
            except Exception:
                # Clean up the temp file on any failure. ``os.unlink``
                # may itself fail if the file no longer exists; that's
                # fine — we just want the file gone.
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as exc:
            _log.debug("Cache write failed for %s: %s", path, exc)

    def has(self, provider_id: str, query: str) -> bool:
        """Cheap presence check (does NOT load the JSON)."""
        path = self._path_for(provider_id, query)
        try:
            stat = path.stat()
        except OSError:
            return False
        return (time.time() - stat.st_mtime) <= self._ttl

    def prune_expired(self) -> int:
        """Delete entries older than the TTL. Returns count removed."""
        removed = 0
        cutoff = time.time() - self._ttl
        try:
            entries = list(self._root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------------

    def _path_for(self, provider_id: str, query: str) -> Path:
        return self._root / f"{_cache_key(provider_id, query)}.json"


# --- (de)serialisation helpers --------------------------------------------


def _result_to_dict(r: "ImageResult") -> dict:
    """Convert ImageResult → JSON-friendly dict."""
    d = asdict(r)
    # ``fallback_full_urls`` is stored as a tuple for hashability;
    # JSON only has lists.
    d["fallback_full_urls"] = list(d.get("fallback_full_urls") or [])
    return d


def _dict_to_result(d: dict) -> "ImageResult":
    """Convert a cached dict back into an ImageResult.

    Filters keys to those the dataclass actually accepts so a future
    schema bump on disk doesn't crash older readers.
    """
    from .providers.base import ImageResult

    accepted = {f.name for f in fields(ImageResult)}
    kwargs: dict[str, Any] = {k: v for k, v in d.items() if k in accepted}
    fb = kwargs.get("fallback_full_urls")
    if isinstance(fb, list):
        kwargs["fallback_full_urls"] = tuple(fb)
    return ImageResult(**kwargs)


__all__ = ["SearchCache", "DEFAULT_TTL_SECONDS"]
