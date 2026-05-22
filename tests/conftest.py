"""Tests conftest: fix sys.path for the ankivn_image_picker package.

The project root directory is named ``ankivn_image_picker`` and contains
an ``__init__.py`` (the Anki add-on shim). When pytest adds the parent
directory (``addons21``) to ``sys.path``, ``import ankivn_image_picker``
resolves to the project root shim. The shim does
``from . import ankivn_image_picker`` which imports the inner subpackage,
but ``ankivn_image_picker.ui`` is not directly accessible because the
root package's ``__path__`` points to the project root, not the inner
subpackage directory.

This conftest patches the root package's ``__path__`` to include the
inner subpackage directory so that ``ankivn_image_picker.ui`` resolves
correctly.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
_INNER_PKG = str(Path(_PROJECT_ROOT) / "ankivn_image_picker")

# Ensure project root is on sys.path.
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# If ankivn_image_picker is already imported (from the project root),
# patch its __path__ to include the inner subpackage directory so that
# submodule imports like `ankivn_image_picker.ui` work.
import ankivn_image_picker  # noqa: E402

if hasattr(ankivn_image_picker, "__path__"):
    if _INNER_PKG not in ankivn_image_picker.__path__:
        ankivn_image_picker.__path__.insert(0, _INNER_PKG)
