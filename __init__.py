"""AnkiVN Smart Image Picker — root entry point for Anki 2.1+.

Anki imports add-ons by folder name, so the folder containing this file
must be a valid Python identifier (no spaces, no non-ASCII characters).
The actual implementation lives in the ``ankivn_image_picker``
subpackage one directory below; importing that subpackage runs its
``_setup_hooks()`` and registers every Anki hook the add-on needs.

The ``manifest.json`` ``package`` field documents the canonical install
name (``ankivn_image_picker``); this shim keeps the on-disk layout
consistent with that.
"""

from . import ankivn_image_picker  # noqa: F401  (triggers _setup_hooks)
