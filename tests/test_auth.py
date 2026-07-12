from __future__ import annotations

from hunter.acquisition import AcquisitionConfig, ProviderConfig
from hunter.auth import AuthRegistry, load_auth_config
from hunter.auth.configuration import auth_config_from_mapping
from hunter.cli import _coingecko_provider_config, _github_provider_config, main


def test_auth_environment_config_and_secret_loading(monkeypatch, tmp_path) -> None:
    secret = tmp_path / "github_token"
    secret.write_text("ghp_secret_token_value_1234567890", encoding="utf-8")
    monkeypatch.setenv("COINGECKO_API_KEY", "coingecko-key-123")
    config = auth_config_from_mapping(
        {
            "providers": [
                {
                    "name": "github",
                    "credentials": [{"name": "token", "secret_file": str(secret)}],
                    "anonymous_quota": 60,
                    "authenticated_quota": 5000,
                },
                {
                    "name": "coingecko",
                    "credentials": [{"name": "api_key", "env": "COINGECKO_API_KEY"}],
                },
            ]
        }
    )
    registry = AuthRegistry(config)

    assert registry.credential("github", "token").value == "ghp_secret_token_value_1234567890"
    assert registry.credential("coingecko", "api_key").value == "coingecko-key-123"
    assert registry.state("github").mode == "authenticated"
    assert registry.quota("github").remaining == 5000


def test_auth_missing_invalid_and_fallback_modes(monkeypatch) -> None:
    monkeypatch.setenv("BAD_TOKEN", "bad")
    config = auth_config_from_mapping(
        {
            "providers": [
                {
                    "name": "github",
                    "credentials": [{"name": "token", "env": "BAD_TOKEN", "allow_anonymous": True}],
                    "anonymous_quota": 60,
                    "authenticated_quota": 5000,
                },
                {
                    "name": "required",
                    "credentials": [{"name": "token", "required": True, "allow_anonymous": False}],
                },
            ]
        }
    )
    registry = AuthRegistry(config)

    assert registry.state("github").credential_status == "invalid"
    assert registry.state("github").mode == "anonymous"
    assert registry.quota("github").remaining == 60
    assert registry.state("required").mode == "disabled"


def test_auth_provider_configuration_is_applied(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_valid_token_value_1234567890")
    monkeypatch.setenv("COINGECKO_API_KEY", "coingecko-key-123")

    github = _github_provider_config(ProviderConfig(name="github"), AcquisitionConfig())
    coingecko = _coingecko_provider_config(ProviderConfig(name="coingecko"), AcquisitionConfig())

    assert github.token == "ghp_valid_token_value_1234567890"
    assert coingecko.api_key == "coingecko-key-123"


def test_auth_config_file_and_cli_commands(tmp_path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        """
providers:
  - name: github
    anonymous_quota: 60
    authenticated_quota: 5000
    credentials:
      - name: token
        required: false
        allow_anonymous: true
""",
        encoding="utf-8",
    )

    loaded = load_auth_config(config)

    assert loaded.provider("github") is not None
    assert main(["auth", "--providers-config", str(config), "status"]) == 0
    assert main(["auth", "--providers-config", str(config), "validate"]) == 0
    assert main(["auth", "--providers-config", str(config), "providers"]) == 0
    assert main(["auth", "--providers-config", str(config), "quota"]) == 0
    assert main(["auth", "--providers-config", str(config), "doctor"]) == 0
