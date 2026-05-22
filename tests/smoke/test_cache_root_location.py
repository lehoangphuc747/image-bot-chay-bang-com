"""Smoke test: thumbnail cache root resolves under the add-on's ``user_files/`` directory.

Task 3.3: Verify the cache root resolves under the add-on's ``user_files/``
directory.

The design (see ``cache.py`` docstring and Req 5.3) requires the cache root
to live at::

    <mw.addonManager.addonsFolder(__name__)>/user_files/thumbnail_cache/

This convention matters because Anki preserves the ``user_files/`` subtree
across add-on upgrades (any other location inside the add-on directory is
wiped on update). A regression that puts the cache somewhere else would be
silently catastrophic: every upgrade would lose the user's thumbnail cache.

This is a pure smoke test:

* It does **not** require a running Anki instance. ``aqt`` is stubbed out
  with ``MagicMock``\\ s that mimic the small slice of the API the add-on
  actually touches when opening the picker.
* It does **not** start a real network search. The picker open flow is
  short-circuited by patching :meth:`PickerDialog.validate_and_open` to
  return ``None`` (which is the same code path the dialog uses when source-
  field validation fails).
* It captures the ``Path`` that the add-on hands to
  :class:`~ankivn_image_picker.cache.ThumbnailCache` and asserts it lives
  inside the simulated ``addonsFolder`` and follows the documented layout.

The ``addonsFolder`` is faked via :class:`tempfile.TemporaryDirectory` rather
than pytest's ``tmp_path`` fixture so the test does not depend on the
runner's temp-directory permissions; the surrounding test files in this
repository follow the same convention.

Validates: Requirements 5.3
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, List
from unittest.mock import MagicMock, patch

from ankivn_image_picker.providers.base import ImageResult


# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


class _NoOpProvider:
    """Provider stand-in: registers under ``unsplash`` and yields nothing.

    The smoke test does not exercise the search path; it only needs a
    provider object that survives ``ProviderRegistry.create`` so the
    open-picker flow proceeds far enough to construct the
    :class:`ThumbnailCache`.
    """

    def __init__(self, provider_id: str = "unsplash") -> None:
        self.id = provider_id
        self.display_name = f"Provider {provider_id}"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        http: Any,
        cancel: Any,
    ) -> Iterable[ImageResult]:
        return []


def _make_fake_note() -> Any:
    """Build a minimal fake note with the default source/target fields."""
    note = MagicMock()
    note.note_type.return_value = {
        "flds": [{"name": "word"}, {"name": "image"}]
    }
    note.fields = ["chó", ""]
    return note


def _make_fake_editor() -> Any:
    """Build a minimal fake editor wrapping the fake note."""
    editor = MagicMock()
    editor.note = _make_fake_note()
    editor.parentWindow = None
    editor.addButton.return_value = "<button>fake</button>"
    return editor


def _default_config_dict() -> dict:
    """Return the documented default config as a plain dict."""
    return {
        "source_field": "word",
        "target_field": "image",
        "providers": ["unsplash"],
        "max_results_per_provider": 12,
        "thumbnail_cache_max_mb": 64,
    }


def _open_picker_and_capture_cache_root(
    addons_folder: Path,
    config_dict: dict | None = None,
) -> List[Path]:
    """Drive the toolbar-button click flow with a stubbed Anki and
    capture every ``Path`` handed to :class:`ThumbnailCache`.

    Returns the list of recorded roots so callers can assert against
    them. The picker open flow is short-circuited via a patched
    ``PickerDialog.validate_and_open`` to avoid touching Qt or the
    network.
    """
    # Late import so the patches below see the module's current state.
    from ankivn_image_picker.ui.toolbar_button import _on_button_click

    config_dict = config_dict if config_dict is not None else _default_config_dict()

    # Stub the slice of Anki we touch: mw.addonManager.getConfig and
    # mw.addonManager.addonsFolder. addonsFolder must return the
    # simulated add-ons directory so we can verify the resolved path.
    mock_mw = MagicMock()
    mock_mw.addonManager.getConfig.return_value = config_dict
    mock_mw.addonManager.addonsFolder.return_value = str(addons_folder)

    mock_aqt = MagicMock()
    mock_aqt.mw = mock_mw

    mock_aqt_utils = MagicMock()

    # Capture every cache root the open-picker flow constructs. We
    # intercept ThumbnailCache.__init__ rather than replacing the
    # whole class so the mkdir(parents=True, exist_ok=True) call
    # still runs against a real directory (which proves the path is
    # creatable, not just a string).
    captured_roots: List[Path] = []
    from ankivn_image_picker.cache import ThumbnailCache

    original_init = ThumbnailCache.__init__

    def _spy_init(self: ThumbnailCache, root: Path, max_bytes: int) -> None:
        captured_roots.append(Path(root))
        original_init(self, root, max_bytes)

    with (
        patch.dict(
            sys.modules,
            {
                "aqt": mock_aqt,
                "aqt.mw": mock_mw,
                "aqt.utils": mock_aqt_utils,
            },
        ),
        patch.object(ThumbnailCache, "__init__", _spy_init),
        # Short-circuit the dialog so we don't need a Qt event loop.
        # validate_and_open returning None matches the failure path
        # used in production for invalid source fields.
        patch(
            "ankivn_image_picker.ui.picker_dialog.PickerDialog.validate_and_open",
            return_value=None,
        ),
        # Stub ProviderRegistry.create so the configured "unsplash"
        # provider resolves without forcing the real provider module
        # to import.
        patch(
            "ankivn_image_picker.providers.ProviderRegistry.create",
            return_value=_NoOpProvider("unsplash"),
        ),
    ):
        _on_button_click(_make_fake_editor())

    return captured_roots


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestCacheRootLocation:
    """Smoke tests for the thumbnail cache root location (Req 5.3)."""

    def test_cache_root_lives_under_addons_folder(self) -> None:
        """The cache root must resolve under the simulated add-ons folder.

        Anki's ``addonsFolder(__name__)`` returns the per-add-on directory.
        Anything outside that subtree is not preserved across upgrades, so
        a regression that places the cache elsewhere would silently lose
        user data on every upgrade.
        """
        with tempfile.TemporaryDirectory() as tmp:
            addons_folder = Path(tmp) / "addons21" / "ankivn_image_picker"
            addons_folder.mkdir(parents=True)

            roots = _open_picker_and_capture_cache_root(addons_folder)

            assert roots, "ThumbnailCache was not constructed during open-picker flow"
            cache_root = roots[0].resolve()
            assert cache_root.is_relative_to(addons_folder.resolve()), (
                f"Cache root {cache_root} is not under addons folder "
                f"{addons_folder.resolve()}"
            )

    def test_cache_root_lives_under_user_files(self) -> None:
        """The cache root must live under ``<addons_folder>/user_files/``.

        Anki documents ``user_files/`` as the *only* subdirectory that
        survives add-on upgrades. The design (Req 5.3) explicitly anchors
        the cache there so users keep their warmed thumbnails after an
        update.
        """
        with tempfile.TemporaryDirectory() as tmp:
            addons_folder = Path(tmp) / "addons21" / "ankivn_image_picker"
            addons_folder.mkdir(parents=True)

            roots = _open_picker_and_capture_cache_root(addons_folder)

            assert roots, "ThumbnailCache was not constructed during open-picker flow"
            cache_root = roots[0].resolve()
            user_files = (addons_folder / "user_files").resolve()
            assert cache_root.is_relative_to(user_files), (
                f"Cache root {cache_root} is not under {user_files}; "
                "Req 5.3 mandates the user_files/ subtree so the cache "
                "survives add-on upgrades."
            )

    def test_cache_root_is_thumbnail_cache_subdir(self) -> None:
        """The cache root must be exactly ``<addons_folder>/user_files/thumbnail_cache``.

        The full layout is part of the design contract (cache.py module
        docstring): a single sibling-free directory under ``user_files/``
        keeps the cache self-contained and avoids polluting that subtree
        with bare ``index.json`` / ``*.bin`` files in the user-visible
        location.
        """
        with tempfile.TemporaryDirectory() as tmp:
            addons_folder = Path(tmp) / "addons21" / "ankivn_image_picker"
            addons_folder.mkdir(parents=True)

            roots = _open_picker_and_capture_cache_root(addons_folder)

            assert roots, "ThumbnailCache was not constructed during open-picker flow"
            cache_root = roots[0].resolve()
            expected = (addons_folder / "user_files" / "thumbnail_cache").resolve()
            assert cache_root == expected, (
                f"Cache root {cache_root} != expected {expected}"
            )

    def test_cache_root_directory_is_created(self) -> None:
        """Opening the picker creates the cache root directory on disk.

        The cache constructor calls ``mkdir(parents=True, exist_ok=True)``
        so a missing ``user_files/`` directory is materialised lazily.
        That keeps the add-on's installed footprint clean (no empty
        directories shipped) while guaranteeing the cache works on first
        open.
        """
        with tempfile.TemporaryDirectory() as tmp:
            addons_folder = Path(tmp) / "addons21" / "ankivn_image_picker"
            addons_folder.mkdir(parents=True)

            # Sanity: neither user_files/ nor user_files/thumbnail_cache/
            # exist yet.
            assert not (addons_folder / "user_files").exists()

            _open_picker_and_capture_cache_root(addons_folder)

            cache_dir = addons_folder / "user_files" / "thumbnail_cache"
            assert cache_dir.is_dir(), (
                f"Cache directory {cache_dir} was not created on picker open"
            )
