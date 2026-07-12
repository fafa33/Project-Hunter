from __future__ import annotations

from hunter.auth.configuration import CredentialConfig
from hunter.auth.contracts import ProviderAuthState, ProviderCredential


class CredentialValidator:
    def validate(
        self, provider: str, config: CredentialConfig, credential: ProviderCredential | None
    ) -> ProviderAuthState:
        if credential is None:
            if config.required and not config.allow_anonymous:
                return ProviderAuthState(provider, "disabled", "missing", message=f"{config.name} is required")
            return ProviderAuthState(provider, "anonymous", "missing", message="anonymous mode")
        if not credential.value.strip():
            return ProviderAuthState(provider, "anonymous", "invalid", credential.source, "credential is blank")
        if _looks_invalid(provider, credential):
            mode = "anonymous" if config.allow_anonymous else "disabled"
            return ProviderAuthState(provider, mode, "invalid", credential.source, "credential format is invalid")
        return ProviderAuthState(provider, "authenticated", "available", credential.source, "credential available")


def _looks_invalid(provider: str, credential: ProviderCredential) -> bool:
    value = credential.value.strip()
    if provider == "github":
        return len(value) < 20 or " " in value
    if provider == "coingecko":
        return len(value) < 8 or " " in value
    return False
