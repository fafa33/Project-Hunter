from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AuthMode = Literal["authenticated", "anonymous", "disabled"]
CredentialStatus = Literal["available", "missing", "invalid"]


@dataclass(frozen=True)
class ProviderCredential:
    provider: str
    name: str
    value: str
    source: str

    def masked(self) -> str:
        if len(self.value) <= 8:
            return "****"
        return f"{self.value[:4]}...{self.value[-4:]}"


@dataclass(frozen=True)
class ProviderAuthState:
    provider: str
    mode: AuthMode
    credential_status: CredentialStatus
    credential_source: str = ""
    message: str = ""


@dataclass(frozen=True)
class ProviderQuota:
    provider: str
    authenticated: bool
    limit: int | None
    remaining: int | None
    reset_at: str | None = None
    source: str = "static"
