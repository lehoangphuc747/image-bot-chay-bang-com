"""Provider metadata: API limits, display names, etc.

Single source of truth for per-provider hard limits. Used by:
- Settings dialog (to show user the max value)
- Orchestrator (to clamp requests to provider limits)
- Config validation
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderInfo:
    """Static metadata for an image provider."""

    id: str
    display_name: str
    max_per_request: int
    default_limit: int
    requires_api_key: bool
    free_tier_note: str
    #: Optional URL where the user can sign up for an API key. Empty
    #: for providers that don't need one (Wikimedia, Openverse).
    signup_url: str = ""


# Hard limits enforced by each provider's API
PROVIDER_INFO: dict[str, ProviderInfo] = {
    "unsplash": ProviderInfo(
        id="unsplash",
        display_name="Unsplash",
        max_per_request=30,
        default_limit=30,
        requires_api_key=True,
        free_tier_note="50 requests/hour (demo)",
        signup_url="https://unsplash.com/developers",
    ),
    "pexels": ProviderInfo(
        id="pexels",
        display_name="Pexels",
        max_per_request=80,
        default_limit=80,
        requires_api_key=True,
        free_tier_note="200 requests/hour",
        signup_url="https://www.pexels.com/api/",
    ),
    "wikimedia": ProviderInfo(
        id="wikimedia",
        display_name="Wikimedia Commons",
        max_per_request=50,
        default_limit=50,
        requires_api_key=False,
        free_tier_note="Free, no rate limit",
    ),
    "openverse": ProviderInfo(
        id="openverse",
        display_name="Openverse",
        max_per_request=20,
        default_limit=20,
        requires_api_key=False,
        free_tier_note="100 requests/day (anonymous)",
    ),
}


def get_provider_limit(provider_id: str, config) -> int:
    """Get the effective max_results for a provider.

    Returns the per-provider config value if set (>0), otherwise
    falls back to the global ``max_results_per_provider``, clamped
    to the provider's API limit.
    """
    info = PROVIDER_INFO.get(provider_id)
    hard_cap = info.max_per_request if info else 50

    # Get per-provider limit from config (e.g. config.unsplash_max_results)
    per_provider_attr = f"{provider_id}_max_results"
    per_provider_limit = getattr(config, per_provider_attr, 0)

    if per_provider_limit > 0:
        # User specified a per-provider limit
        return min(per_provider_limit, hard_cap)

    # Fall back to global limit, clamped
    return min(config.max_results_per_provider, hard_cap)


def total_max_results(config) -> int:
    """Calculate total max results across all configured providers."""
    total = 0
    for pid in config.providers:
        total += get_provider_limit(pid, config)
    return total


__all__ = ["ProviderInfo", "PROVIDER_INFO", "get_provider_limit", "total_max_results"]
