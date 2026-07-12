from __future__ import annotations

from hunter.auth.configuration import AuthConfig, ProviderAuthConfig
from hunter.auth.contracts import ProviderAuthState, ProviderCredential, ProviderQuota
from hunter.auth.credentials import CredentialResolver
from hunter.auth.providers import provider_quota
from hunter.auth.validation import CredentialValidator


class AuthRegistry:
    def __init__(
        self,
        config: AuthConfig,
        *,
        resolver: CredentialResolver | None = None,
        validator: CredentialValidator | None = None,
    ) -> None:
        self.config = config
        self.resolver = resolver or CredentialResolver()
        self.validator = validator or CredentialValidator()

    def providers(self) -> tuple[ProviderAuthConfig, ...]:
        return tuple(self.config.providers)

    def credential(self, provider: str, name: str) -> ProviderCredential | None:
        provider_config = self.config.provider(provider)
        if provider_config is None or not provider_config.enabled:
            return None
        credential_config = next((item for item in provider_config.credentials if item.name == name), None)
        if credential_config is None:
            return None
        credential = self.resolver.resolve(provider, credential_config)
        state = self.validator.validate(provider, credential_config, credential)
        if state.mode != "authenticated":
            return None
        return credential

    def state(self, provider: str) -> ProviderAuthState:
        provider_config = self.config.provider(provider)
        if provider_config is None or not provider_config.enabled:
            return ProviderAuthState(provider, "disabled", "missing", message="provider auth not configured")
        if not provider_config.credentials:
            return ProviderAuthState(provider, "anonymous", "missing", message="no credential required")
        states = []
        for credential_config in provider_config.credentials:
            credential = self.resolver.resolve(provider, credential_config)
            states.append(self.validator.validate(provider, credential_config, credential))
        authenticated = next((state for state in states if state.mode == "authenticated"), None)
        if authenticated is not None:
            return authenticated
        disabled = next((state for state in states if state.mode == "disabled"), None)
        if disabled is not None:
            return disabled
        return states[0]

    def quota(self, provider: str) -> ProviderQuota:
        provider_config = self.config.provider(provider)
        if provider_config is None:
            return ProviderQuota(provider, False, None, None, source="unconfigured")
        return provider_quota(provider_config, authenticated=self.state(provider).mode == "authenticated")
