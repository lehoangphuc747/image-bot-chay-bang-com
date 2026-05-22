"""Smoke test: the shipped config.json contains every documented key with
values inside the documented ranges.

Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# The config.json lives at the root of the add-on package (next to manifest.json).
_ADDON_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ADDON_ROOT / "config.json"


@pytest.fixture()
def shipped_config() -> dict:
    """Load the shipped config.json as a plain dict."""
    assert _CONFIG_PATH.exists(), f"config.json not found at {_CONFIG_PATH}"
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict), "config.json root must be a JSON object"
    return data


# -- Documented keys (from config.md and requirements) --

_DOCUMENTED_KEYS = {
    "source_field",
    "target_field",
    "providers",
    "max_results_per_provider",
    "thumbnail_cache_max_mb",
    "unsplash_access_key",
    "pixabay_api_key",
    "pexels_api_key",
    "google_api_key",
    "google_cse_id",
}


class TestDefaultConfigKeysPresent:
    """Verify the shipped config.json contains every documented key."""

    def test_all_documented_keys_present(self, shipped_config: dict) -> None:
        """Every documented key must exist in config.json."""
        missing = _DOCUMENTED_KEYS - set(shipped_config.keys())
        assert not missing, f"Missing documented keys: {missing}"

    def test_no_undocumented_keys(self, shipped_config: dict) -> None:
        """config.json must not contain keys outside the documented schema."""
        extra = set(shipped_config.keys()) - _DOCUMENTED_KEYS
        assert not extra, f"Undocumented keys found: {extra}"


class TestSourceField:
    """Req 1.2: source_field is a non-empty string, default 'word'."""

    def test_type_is_string(self, shipped_config: dict) -> None:
        assert isinstance(shipped_config["source_field"], str)

    def test_non_empty(self, shipped_config: dict) -> None:
        assert shipped_config["source_field"] != ""

    def test_default_value(self, shipped_config: dict) -> None:
        assert shipped_config["source_field"] == "word"


class TestTargetField:
    """Req 1.3: target_field is a non-empty string, default 'image'."""

    def test_type_is_string(self, shipped_config: dict) -> None:
        assert isinstance(shipped_config["target_field"], str)

    def test_non_empty(self, shipped_config: dict) -> None:
        assert shipped_config["target_field"] != ""

    def test_default_value(self, shipped_config: dict) -> None:
        assert shipped_config["target_field"] == "image"


class TestProviders:
    """Req 1.4: providers is a non-empty list with at least one entry."""

    def test_type_is_list(self, shipped_config: dict) -> None:
        assert isinstance(shipped_config["providers"], list)

    def test_non_empty(self, shipped_config: dict) -> None:
        assert len(shipped_config["providers"]) >= 1

    def test_entries_are_non_empty_strings(self, shipped_config: dict) -> None:
        for entry in shipped_config["providers"]:
            assert isinstance(entry, str), f"Provider entry {entry!r} is not a string"
            assert entry != "", "Provider entry must not be empty"

    def test_default_value(self, shipped_config: dict) -> None:
        assert shipped_config["providers"] == ["unsplash"]


class TestMaxResultsPerProvider:
    """Req 1.5: max_results_per_provider is an integer in [1, 50], default 12."""

    def test_type_is_int(self, shipped_config: dict) -> None:
        value = shipped_config["max_results_per_provider"]
        assert isinstance(value, int) and not isinstance(value, bool)

    def test_in_range(self, shipped_config: dict) -> None:
        value = shipped_config["max_results_per_provider"]
        assert 1 <= value <= 50, f"max_results_per_provider={value} outside [1, 50]"

    def test_default_value(self, shipped_config: dict) -> None:
        assert shipped_config["max_results_per_provider"] == 12


class TestThumbnailCacheMaxMb:
    """Req 1.6: thumbnail_cache_max_mb is an integer in [1, 1024], default 64."""

    def test_type_is_int(self, shipped_config: dict) -> None:
        value = shipped_config["thumbnail_cache_max_mb"]
        assert isinstance(value, int) and not isinstance(value, bool)

    def test_in_range(self, shipped_config: dict) -> None:
        value = shipped_config["thumbnail_cache_max_mb"]
        assert 1 <= value <= 1024, f"thumbnail_cache_max_mb={value} outside [1, 1024]"

    def test_default_value(self, shipped_config: dict) -> None:
        assert shipped_config["thumbnail_cache_max_mb"] == 64
