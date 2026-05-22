"""Shared helpers for constructing picker dependencies.

Both the editor-toolbar button (single-note flow) and the browser-menu
batch action (multi-note flow) need the same set of dependencies in
order to open a :class:`~ankivn_image_picker.ui.picker_dialog.PickerDialog`:

* a validated :class:`~ankivn_image_picker.config.Config`,
* a list of provider instances built from
  :class:`~ankivn_image_picker.providers.ProviderRegistry`,
* an :class:`~ankivn_image_picker.http.HttpClient`,
* a :class:`~ankivn_image_picker.cache.ThumbnailCache` rooted under
  ``user_files/thumbnail_cache``.

This helper centralises that boilerplate so the two entry points stay
in sync. It also surfaces a clean error to the user (and returns
``None``) when the configuration leaves no usable provider — instead
of silently opening a dialog that can never produce results.

The function is main-thread-only because it touches ``mw.addonManager``
and surfaces dialogs via :mod:`aqt.utils`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional

from ..logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from ..cache import ThumbnailCache
    from ..config import Config
    from ..http import HttpClient
    from ..providers.base import ImageProvider
    from ..search_cache import SearchCache


_log = get_logger("picker_factory")

# The on-disk add-on package id. ``mw.addonManager`` keys configuration
# and the add-ons folder by this name. Hard-coded here rather than
# inferred from ``__name__`` so the value is stable regardless of how
# the module is imported (the root ``__init__.py`` imports the
# subpackage, so ``__name__.split('.')[0]`` would resolve to the
# subpackage rather than the on-disk folder name in some test layouts).
ADDON_PACKAGE = "ankivn_image_picker"


class PickerDeps(NamedTuple):
    """Bundle of dependencies needed to open a :class:`PickerDialog`."""

    config: "Config"
    providers: list["ImageProvider"]
    http: "HttpClient"
    cache: "ThumbnailCache"
    search_cache: "SearchCache"


def build_picker_deps() -> Optional[PickerDeps]:
    """Build the full dependency bundle, or return ``None`` on failure.

    Returns
    -------
    PickerDeps | None
        The dependency bundle if the configuration yields at least one
        usable provider; ``None`` if the configuration is unusable
        (in which case a warning has already been shown to the user).

    Notes
    -----
    The function is intentionally tolerant: unknown provider ids in
    the config are skipped with a log warning rather than aborting,
    matching the behaviour of the existing single-note entry point.
    Only a complete absence of usable providers is treated as a hard
    failure.
    """
    try:
        from aqt import mw  # type: ignore[import-not-found]
        from aqt.utils import showWarning  # type: ignore[import-not-found]
    except ImportError:
        _log.error("Cannot build picker deps: aqt not available")
        return None

    from ..cache import ThumbnailCache
    from ..config import ConfigLoader
    from ..http import HttpClient
    from ..providers import ProviderRegistry

    # Load config fresh on every open (Req 1.7).
    raw_config = mw.addonManager.getConfig(ADDON_PACKAGE)
    config = ConfigLoader.load(raw_config, log=_log)

    # Inject API keys from config into environment variables so that
    # providers can read them. This bridges the gap between the user-
    # facing config (Anki's JSON editor) and the provider modules that
    # read keys from os.environ. Keys from config take precedence over
    # any pre-existing env var value, but only if non-empty.
    import os

    if config.unsplash_access_key:
        os.environ["UNSPLASH_ACCESS_KEY"] = config.unsplash_access_key
    if config.pexels_api_key:
        os.environ["PEXELS_API_KEY"] = config.pexels_api_key

    providers: list["ImageProvider"] = []
    for provider_id in config.providers:
        try:
            providers.append(ProviderRegistry.create(provider_id))
        except KeyError:
            _log.warning("Skipping unknown provider %r", provider_id)

    if not providers:
        showWarning(
            "No valid image providers configured. "
            "Please check your add-on configuration."
        )
        return None

    http = HttpClient()

    addon_folder = mw.addonManager.addonsFolder(ADDON_PACKAGE)
    cache_root = Path(addon_folder) / "user_files" / "thumbnail_cache"
    max_cache_bytes = config.thumbnail_cache_max_mb * 1024 * 1024
    cache = ThumbnailCache(cache_root, max_cache_bytes)

    # Search-result metadata cache lives next to the thumbnail cache.
    # Combined, the two caches let a re-run of the same batch skip
    # network entirely.
    from ..search_cache import SearchCache

    search_cache_root = Path(addon_folder) / "user_files" / "search_cache"
    search_cache = SearchCache(search_cache_root)
    # Best-effort hygiene: remove entries past their TTL so the
    # directory doesn't grow forever. Cheap (just stat + unlink).
    try:
        search_cache.prune_expired()
    except Exception:
        pass

    return PickerDeps(
        config=config,
        providers=providers,
        http=http,
        cache=cache,
        search_cache=search_cache,
    )


__all__ = ["PickerDeps", "build_picker_deps", "ADDON_PACKAGE"]
