from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CredentialConfig:
    name: str
    env: str | None = None
    value: str | None = None
    secret_file: str | None = None
    required: bool = False
    allow_anonymous: bool = True


@dataclass(frozen=True)
class ProviderAuthConfig:
    name: str
    enabled: bool = True
    credentials: tuple[CredentialConfig, ...] = ()
    anonymous_quota: int | None = None
    authenticated_quota: int | None = None


@dataclass(frozen=True)
class AuthConfig:
    providers: tuple[ProviderAuthConfig, ...] = ()

    def provider(self, name: str) -> ProviderAuthConfig | None:
        return next((provider for provider in self.providers if provider.name == name), None)


def load_auth_config(path: str | Path = "configs/providers.yaml") -> AuthConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AuthConfig()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        msg = "provider auth configuration must be a mapping"
        raise ValueError(msg)
    return auth_config_from_mapping(payload)


def auth_config_from_mapping(payload: dict[str, Any]) -> AuthConfig:
    providers = []
    for raw_provider in payload.get("providers", ()):
        if not isinstance(raw_provider, dict) or "name" not in raw_provider:
            continue
        credentials = []
        for raw_credential in raw_provider.get("credentials", ()):
            if not isinstance(raw_credential, dict) or "name" not in raw_credential:
                continue
            credentials.append(
                CredentialConfig(
                    name=str(raw_credential["name"]),
                    env=str(raw_credential["env"]) if raw_credential.get("env") else None,
                    value=str(raw_credential["value"]) if raw_credential.get("value") else None,
                    secret_file=str(raw_credential["secret_file"]) if raw_credential.get("secret_file") else None,
                    required=bool(raw_credential.get("required", False)),
                    allow_anonymous=bool(raw_credential.get("allow_anonymous", True)),
                )
            )
        providers.append(
            ProviderAuthConfig(
                name=str(raw_provider["name"]),
                enabled=bool(raw_provider.get("enabled", True)),
                credentials=tuple(credentials),
                anonymous_quota=_optional_int(raw_provider.get("anonymous_quota")),
                authenticated_quota=_optional_int(raw_provider.get("authenticated_quota")),
            )
        )
    return AuthConfig(providers=tuple(providers))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int | float | str | bytes | bytearray):
        return int(value)
    msg = "quota values must be numeric"
    raise ValueError(msg)
