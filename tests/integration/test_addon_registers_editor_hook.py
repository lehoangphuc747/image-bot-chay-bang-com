"""Integration test: addon registers editor hook.

Task 10.2: Verify importing the package adds a callback to
``gui_hooks.editor_did_init_buttons``.

Requirements: 1.1, 2.1
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def fake_aqt(monkeypatch: pytest.MonkeyPatch):
    """Set up fake aqt modules so the package can register hooks.

    This fixture creates mock ``aqt``, ``aqt.gui_hooks``, and ``aqt.utils``
    modules with the minimal interface needed for hook registration, then
    forces a fresh re-import of ``ankivn_image_picker`` so that
    ``_setup_hooks()`` runs against the mocked environment.
    """
    # Build a fake gui_hooks module with editor_did_init_buttons as a list-like hook
    fake_gui_hooks = MagicMock()
    fake_gui_hooks.editor_did_init_buttons = MagicMock()
    fake_gui_hooks.editor_did_init_buttons.append = MagicMock()

    # Build a fake mw with addonManager
    fake_mw = MagicMock()
    fake_mw.addonManager.addonFromModule.return_value = "ankivn_image_picker"

    # Build the fake aqt module
    fake_aqt_mod = MagicMock()
    fake_aqt_mod.gui_hooks = fake_gui_hooks
    fake_aqt_mod.mw = fake_mw

    # Build fake aqt.utils
    fake_utils = MagicMock()

    # Inject into sys.modules
    modules_to_inject = {
        "aqt": fake_aqt_mod,
        "aqt.gui_hooks": fake_gui_hooks,
        "aqt.utils": fake_utils,
    }

    for mod_name, mod in modules_to_inject.items():
        monkeypatch.setitem(sys.modules, mod_name, mod)

    # Remove any previously-imported ankivn_image_picker modules so
    # re-import triggers _setup_hooks() again with our mocked aqt.
    modules_to_remove = [
        key for key in sys.modules if key.startswith("ankivn_image_picker")
    ]
    for key in modules_to_remove:
        monkeypatch.delitem(sys.modules, key)

    return fake_gui_hooks


def test_import_registers_editor_did_init_buttons_hook(fake_aqt: MagicMock) -> None:
    """Importing ankivn_image_picker appends a callback to
    gui_hooks.editor_did_init_buttons (Req 1.1, 2.1).
    """
    # Import the package fresh — this triggers _setup_hooks()
    import ankivn_image_picker  # noqa: F401

    # Verify that append was called on editor_did_init_buttons
    fake_aqt.editor_did_init_buttons.append.assert_called_once()


def test_registered_callback_is_callable(fake_aqt: MagicMock) -> None:
    """The callback appended to editor_did_init_buttons is callable (Req 2.1)."""
    import ankivn_image_picker  # noqa: F401

    # Get the callback that was passed to append
    call_args = fake_aqt.editor_did_init_buttons.append.call_args
    callback = call_args[0][0]

    assert callable(callback)


def test_registered_callback_is_on_editor_init_buttons(fake_aqt: MagicMock) -> None:
    """The registered callback is the _on_editor_init_buttons function (Req 2.1)."""
    import ankivn_image_picker

    # Get the callback that was passed to append
    call_args = fake_aqt.editor_did_init_buttons.append.call_args
    callback = call_args[0][0]

    # Verify it's the expected function from the module
    assert callback is ankivn_image_picker._on_editor_init_buttons
