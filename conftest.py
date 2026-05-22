"""Root conftest: delegates to tests/conftest.py for path setup.

This file exists so pytest recognises the project root as the rootdir
and loads pyproject.toml for configuration. The actual sys.path
manipulation lives in tests/conftest.py.
"""
