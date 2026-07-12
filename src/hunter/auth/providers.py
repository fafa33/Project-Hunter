from __future__ import annotations

from hunter.auth.configuration import ProviderAuthConfig
from hunter.auth.contracts import ProviderQuota


def provider_capabilities(config: ProviderAuthConfig) -> tuple[str, ...]:
    capabilities = ["unauthenticated"]
    if config.credentials:
        capabilities.append("authenticated")
    if config.anonymous_quota is not None or config.authenticated_quota is not None:
        capabilities.append("quota_tracking")
    return tuple(capabilities)


def provider_quota(config: ProviderAuthConfig, *, authenticated: bool) -> ProviderQuota:
    limit = config.authenticated_quota if authenticated else config.anonymous_quota
    return ProviderQuota(
        provider=config.name,
        authenticated=authenticated,
        limit=limit,
        remaining=limit,
        source="configured",
    )
