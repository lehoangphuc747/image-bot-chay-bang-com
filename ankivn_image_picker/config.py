"""Pure configuration loader for the AnkiVN Smart Image Picker add-on.

The loader is the only place in the add-on allowed to deal with raw,
untrusted dicts coming from Anki's add-on configuration system. The rest
of the codebase consumes a fully validated :class:`Config` value object.

By design (see ``design.md``), :func:`ConfigLoader.validate` is a pure
total function: it never raises, never performs I/O, and accepts every
possible input that ``mw.addonManager.getConfig`` might ever return
(including ``None`` when the config file is absent). The behavior matrix
below comes verbatim from Req 1.8–1.11:

* ``raw is None`` -> return ``DEFAULTS``, log one warning identifying the
  missing config file (Req 1.8).
* unknown key -> ignore the key, log one warning naming each unknown
  key (Req 1.9).
* missing known key -> use the documented default for that key, no
  warning (Req 1.10).
* wrong type, out-of-range int, or empty string -> use the documented
  default for that key, no warning (Req 1.11).
* ``providers`` list whose every entry is invalid -> use
  ``DEFAULTS.providers`` (Req 1.4 + Req 1.11).

:func:`ConfigLoader.load` is the thin caller used by the rest of the
add-on; it simply forwards to :func:`validate` and lives in this module
so callers do not need to thread the logger through twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from typing import Any, ClassVar, FrozenSet, Mapping

__all__ = ["Config", "ConfigLoader", "resolve_fields"]


# Range bounds come straight from the design's "Data Models > Config" table
# and Req 1.5 / Req 1.6.
_MAX_RESULTS_MIN = 1
_MAX_RESULTS_MAX = 200
_CACHE_MB_MIN = 1
_CACHE_MB_MAX = 1024


@dataclass(frozen=True)
class Config:
    """Validated configuration for the add-on.

    Every field is guaranteed to satisfy its documented invariant:

    * ``source_field`` and ``target_field`` are non-empty strings.
    * ``providers`` is a non-empty tuple of non-empty strings.
    * ``max_results_per_provider`` is an ``int`` in
      ``[_MAX_RESULTS_MIN, _MAX_RESULTS_MAX]``.
    * ``thumbnail_cache_max_mb`` is an ``int`` in
      ``[_CACHE_MB_MIN, _CACHE_MB_MAX]``.
    * ``unsplash_access_key`` and ``pixabay_api_key`` are strings
      (possibly empty — empty means "not configured").

    The dataclass is frozen so a ``Config`` instance can be safely shared
    across threads without further synchronisation.
    """

    source_field: str
    target_field: str
    providers: tuple[str, ...]
    max_results_per_provider: int
    thumbnail_cache_max_mb: int
    unsplash_access_key: str = ""
    pexels_api_key: str = ""
    # Per-provider limits (0 = use max_results_per_provider as fallback)
    unsplash_max_results: int = 0
    pexels_max_results: int = 0
    wikimedia_max_results: int = 0
    openverse_max_results: int = 0
    # Number of notes to prefetch ahead in batch mode (0 = disabled)
    prefetch_notes_ahead: int = 8
    # When True, auto-translate non-English queries to English using
    # Google Translate's free endpoint. Improves results for Vietnamese,
    # Korean, Japanese, Chinese, etc. Original query is preserved if
    # translation fails.
    translate_to_english: bool = True
    # Per-note-type field mappings. Each entry is
    # ``(note_type_name, source_field, target_field)``. When a note's
    # note type matches one of these entries, the mapped fields are
    # used instead of the global ``source_field`` / ``target_field``
    # fall-back. Stored as a tuple of tuples so the frozen dataclass
    # can be safely shared across threads.
    field_mappings: tuple[tuple[str, str, str], ...] = ()


def resolve_fields(note_type_name: str, config: "Config") -> tuple[str, str]:
    """Return ``(source_field, target_field)`` for ``note_type_name``.

    Looks up ``note_type_name`` in :attr:`Config.field_mappings` first;
    falls back to the global ``source_field`` / ``target_field`` when
    no mapping is defined for the type. The function is total — every
    note type, even one not yet seen, gets a usable answer.
    """
    if note_type_name:
        for nt, src, tgt in config.field_mappings:
            if nt == note_type_name:
                return src, tgt
    return config.source_field, config.target_field


class ConfigLoader:
    """Pure validator and thin loader for raw add-on config dicts."""

    # The defaults shipped with the add-on (see ``config.json`` and
    # Req 1.2-1.6). They are the canonical fall-back for every key whose
    # value in ``raw`` is missing or invalid.
    DEFAULTS: ClassVar[Config] = Config(
        source_field="word",
        target_field="image",
        providers=("unsplash",),
        max_results_per_provider=12,
        thumbnail_cache_max_mb=64,
        unsplash_access_key="",
        pexels_api_key="",
        unsplash_max_results=0,
        pexels_max_results=0,
        wikimedia_max_results=0,
        openverse_max_results=0,
        prefetch_notes_ahead=8,
        translate_to_english=True,
        field_mappings=(),
    )

    # The set of keys the add-on understands. Computed from ``Config``'s
    # dataclass fields so this never drifts away from the dataclass.
    KNOWN_KEYS: ClassVar[FrozenSet[str]] = frozenset(f.name for f in fields(Config))

    @staticmethod
    def load(raw: Mapping[str, Any] | None, *, log: logging.Logger) -> Config:
        """Validate ``raw`` and emit accumulated warnings to ``log``.

        Currently this is a one-line wrapper around :func:`validate` so
        that callers have a single entry point even if the future grows
        side effects (for example, recording a config-load metric). The
        warning emission contract is defined entirely by ``validate``;
        ``load`` adds nothing of its own.
        """

        return ConfigLoader.validate(raw, log=log)

    @staticmethod
    def validate(
        raw: Mapping[str, Any] | None, *, log: logging.Logger
    ) -> Config:
        """Return a fully-valid :class:`Config` for any input.

        The function is total: it never raises, never performs I/O, and
        accepts every possible value of ``raw``. See the module
        docstring for the full behavior matrix.
        """

        # Req 1.8: absent config file -> defaults + one warning.
        if raw is None:
            log.warning(
                "Config file is absent; using documented default values "
                "for every key."
            )
            return ConfigLoader.DEFAULTS

        # Defensive: ``getConfig`` is documented to return ``dict | None``
        # but we treat any non-mapping value the same way we treat a
        # dict whose every key is missing (Req 1.10/1.11: silent fall-back
        # to defaults). ``unknown_keys`` will be empty in that case so no
        # spurious warning is emitted.
        if not isinstance(raw, Mapping):
            raw_map: Mapping[str, Any] = {}
        else:
            raw_map = raw

        # Req 1.9: warn about every unknown key, exactly once per call,
        # in a deterministic order so log output is stable.
        unknown_keys = sorted(set(raw_map.keys()) - ConfigLoader.KNOWN_KEYS)
        if unknown_keys:
            log.warning(
                "Ignoring unknown config key(s): %s",
                ", ".join(repr(k) for k in unknown_keys),
            )

        defaults = ConfigLoader.DEFAULTS
        return Config(
            source_field=_coerce_nonempty_str(
                raw_map.get("source_field"), defaults.source_field
            ),
            target_field=_coerce_nonempty_str(
                raw_map.get("target_field"), defaults.target_field
            ),
            providers=_coerce_providers(
                raw_map.get("providers"), defaults.providers
            ),
            max_results_per_provider=_coerce_int_in_range(
                raw_map.get("max_results_per_provider"),
                _MAX_RESULTS_MIN,
                _MAX_RESULTS_MAX,
                defaults.max_results_per_provider,
            ),
            thumbnail_cache_max_mb=_coerce_int_in_range(
                raw_map.get("thumbnail_cache_max_mb"),
                _CACHE_MB_MIN,
                _CACHE_MB_MAX,
                defaults.thumbnail_cache_max_mb,
            ),
            unsplash_access_key=_coerce_str(
                raw_map.get("unsplash_access_key"),
                defaults.unsplash_access_key,
            ),
            pexels_api_key=_coerce_str(
                raw_map.get("pexels_api_key"),
                defaults.pexels_api_key,
            ),
            unsplash_max_results=_coerce_int_in_range(
                raw_map.get("unsplash_max_results"),
                0,  # 0 = use fallback
                _MAX_RESULTS_MAX,
                defaults.unsplash_max_results,
            ),
            pexels_max_results=_coerce_int_in_range(
                raw_map.get("pexels_max_results"),
                0,
                _MAX_RESULTS_MAX,
                defaults.pexels_max_results,
            ),
            wikimedia_max_results=_coerce_int_in_range(
                raw_map.get("wikimedia_max_results"),
                0,
                _MAX_RESULTS_MAX,
                defaults.wikimedia_max_results,
            ),
            openverse_max_results=_coerce_int_in_range(
                raw_map.get("openverse_max_results"),
                0,
                _MAX_RESULTS_MAX,
                defaults.openverse_max_results,
            ),
            prefetch_notes_ahead=_coerce_int_in_range(
                raw_map.get("prefetch_notes_ahead"),
                0,  # 0 = disable prefetch
                20,  # max 20 to avoid blowing up rate limits
                defaults.prefetch_notes_ahead,
            ),
            translate_to_english=_coerce_bool(
                raw_map.get("translate_to_english"),
                defaults.translate_to_english,
            ),
            field_mappings=_coerce_field_mappings(
                raw_map.get("field_mappings"),
                defaults.field_mappings,
            ),
        )


# ---------------------------------------------------------------------------
# Internal coercion helpers. Each one is a total function from
# ``(value, default)`` to a validated value. Per Req 1.10/1.11 they never
# log anything: only ``validate`` itself is allowed to emit warnings, and
# only for the absent-config and unknown-key cases.
# ---------------------------------------------------------------------------


def _coerce_nonempty_str(value: Any, default: str) -> str:
    """Return ``value`` if it is a non-empty ``str``, otherwise ``default``.

    ``bool`` would never satisfy ``isinstance(_, str)`` so no special
    case is needed here.
    """

    if isinstance(value, str) and value != "":
        return value
    return default


def _coerce_bool(value: Any, default: bool) -> bool:
    """Return ``value`` if it is a ``bool``, otherwise ``default``."""
    if isinstance(value, bool):
        return value
    return default


def _coerce_str(value: Any, default: str) -> str:
    """Return ``value`` if it is a ``str`` (including empty), otherwise ``default``.

    Used for optional string fields like API keys where an empty string
    is a valid value meaning "not configured".
    """

    if isinstance(value, str):
        return value.strip()
    return default


def _coerce_int_in_range(
    value: Any, lo: int, hi: int, default: int
) -> int:
    """Return ``value`` if it is an ``int`` in ``[lo, hi]``, otherwise ``default``.

    ``bool`` is a subclass of ``int`` in Python; it is rejected here so
    that ``True`` / ``False`` are treated as a wrong-type value (Req 1.11)
    rather than coerced to ``1`` / ``0``.
    """

    if isinstance(value, bool):
        return default
    if isinstance(value, int) and lo <= value <= hi:
        return value
    return default


def _coerce_providers(
    value: Any, default: tuple[str, ...]
) -> tuple[str, ...]:
    """Return a non-empty tuple of non-empty strings from ``value``.

    The full behavior:

    * ``value`` is not a list/tuple -> ``default``.
    * ``value`` is empty -> ``default``.
    * ``value`` contains some valid entries -> tuple of those entries
      in original order, duplicates preserved.
    * ``value`` contains only invalid entries (non-strings or empty
      strings) -> ``default`` (per design "all-invalid entries" rule).
    """

    if not isinstance(value, (list, tuple)):
        return default
    cleaned: list[str] = [
        item for item in value if isinstance(item, str) and item != ""
    ]
    if not cleaned:
        return default
    return tuple(cleaned)


def _coerce_field_mappings(
    value: Any,
    default: tuple[tuple[str, str, str], ...],
) -> tuple[tuple[str, str, str], ...]:
    """Return a tuple of validated ``(note_type, source, target)`` triples.

    Accepts two on-disk shapes for backwards/UX flexibility:

    * a list of dicts ``[{"note_type": ..., "source": ..., "target": ...}, ...]``
      — preferred, easier to read in JSON
    * a list of ``[note_type, source, target]`` lists/tuples — also valid

    Entries with missing or non-string keys are silently dropped; if the
    whole list is unrecognisable, ``default`` is returned. Duplicate
    note-type entries are kept in original order so the first match wins
    in :func:`resolve_fields`.
    """

    if value is None:
        return default
    if not isinstance(value, (list, tuple)):
        return default

    cleaned: list[tuple[str, str, str]] = []
    for entry in value:
        nt: Any = None
        src: Any = None
        tgt: Any = None
        if isinstance(entry, Mapping):
            nt = entry.get("note_type")
            src = entry.get("source") or entry.get("source_field")
            tgt = entry.get("target") or entry.get("target_field")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
            nt, src, tgt = entry[0], entry[1], entry[2]
        else:
            continue
        if not (
            isinstance(nt, str) and nt
            and isinstance(src, str) and src
            and isinstance(tgt, str) and tgt
        ):
            continue
        cleaned.append((nt, src, tgt))

    if not cleaned and value:
        # The user supplied a list but nothing in it parsed. Keep the
        # default rather than silently dropping their config to ()
        # which would look like "no mappings configured".
        return default
    return tuple(cleaned)
