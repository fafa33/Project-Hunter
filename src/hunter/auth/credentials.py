from __future__ import annotations

import os
from pathlib import Path

from hunter.auth.configuration import CredentialConfig
from hunter.auth.contracts import ProviderCredential


class CredentialResolver:
    def resolve(self, provider: str, config: CredentialConfig) -> ProviderCredential | None:
        if config.env:
            value = os.environ.get(config.env)
            if value:
                return ProviderCredential(provider, config.name, value.strip(), f"env:{config.env}")
        if config.secret_file:
            path = Path(config.secret_file).expanduser()
            if path.exists():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return ProviderCredential(provider, config.name, value, f"secret_file:{path}")
        if config.value:
            return ProviderCredential(provider, config.name, config.value.strip(), "config")
        return None
