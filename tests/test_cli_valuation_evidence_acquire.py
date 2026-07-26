from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from hunter import __main__ as hunter_main
from hunter.valuation_evidence import command as valuation_evidence_command

_ENTITY_ID = "entity:test-token"
_ASSET_ID = "asset:test-token"
_REPRESENTATION_ID = "representation:test-token-chain"
_CHAIN = "test-chain"
_CONTRACT_ADDRESS = "0xtestcontract0000000000000000000000000001"
_PROVIDER_LISTING_ID = "test-token-listing"
_ECONOMIC_CLAIM_ID = "test-token-claim"
_TOKEN_ID = "test-token"

SIGNING_KEY_ID = "valuation-evidence-test-key-v1"
SIGNING_KEY_HEX = "ab" * 32


class StaticTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get_json(self, url: str, timeout_seconds: float) -> object:
        assert timeout_seconds > 0
        self.urls.append(url)
        return self.payload


def _market_fact_identity() -> dict[str, str]:
    return {
        "entity_id": _ENTITY_ID,
        "asset_id": _ASSET_ID,
        "representation_id": _REPRESENTATION_ID,
        "chain": _CHAIN,
        "contract_address": _CONTRACT_ADDRESS,
        "provider_listing_id": _PROVIDER_LISTING_ID,
    }


def _value_capture_identity() -> dict[str, str]:
    return {
        "entity_id": _ENTITY_ID,
        "economic_claim_id": _ECONOMIC_CLAIM_ID,
        "asset_id": _ASSET_ID,
        "representation_id": _REPRESENTATION_ID,
        "token_id": _TOKEN_ID,
        "chain": _CHAIN,
        "contract_address": _CONTRACT_ADDRESS,
    }


def _application_root(tmp_path: Path) -> Path:
    root = tmp_path / "application-root"
    configs = root / "configs"
    configs.mkdir(parents=True)
    (configs / "market_fact_sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "source_id": "test-coingecko-market-facts",
                        "provider_id": "coingecko",
                        "endpoint_template": "https://api.coingecko.com/api/v3/coins/{listing_id}",
                        "allowed_hosts": ["api.coingecko.com"],
                        "parser_version": "coingecko-coin-v1",
                        "enabled": True,
                        "capabilities": ["circulating_supply", "total_supply"],
                        "quote_currencies": ["usd"],
                        "units": {"circulating_supply": "native_units", "total_supply": "native_units"},
                        "supported_entity_scope": ["canonical_asset_representation"],
                        "identity_bindings": [
                            {
                                "entity_id": _ENTITY_ID,
                                "asset_id": _ASSET_ID,
                                "representation_id": _REPRESENTATION_ID,
                                "chain": _CHAIN,
                                "contract_address": _CONTRACT_ADDRESS,
                                "provider_listing_id": _PROVIDER_LISTING_ID,
                            }
                        ],
                        "freshness_seconds": 3600,
                        "observation_confidence": "0.8",
                        "historical_support": "current-only",
                        "limitations": "test fixture observed market fact only",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (configs / "value_capture_sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "source_id": "test-official-tokenomics-disclosure",
                        "authority_tier": "official",
                        "source_type": "official_disclosure",
                        "allowed_hosts": ["example.org"],
                        "endpoint_patterns": ["https://example.org/tokenomics/"],
                        "parser_version": "official-tokenomics-v1",
                        "capabilities": [
                            "evidence:official_disclosure",
                            "supply:circulating_supply",
                            "rule:buyback_and_burn",
                        ],
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return root


def _market_fact_manifest(tmp_path: Path, *, now: datetime) -> Path:
    manifest = {
        "operation": "market_fact",
        "source_id": "test-coingecko-market-facts",
        "provider_id": "coingecko",
        "identity": _market_fact_identity(),
        "requested_fact_types": ["circulating_supply"],
        "quote_currency": "usd",
        "requested_at": (now - timedelta(days=1)).isoformat(),
    }
    path = tmp_path / "market-fact-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _coingecko_payload(*, now: datetime) -> dict[str, object]:
    return {
        "id": _PROVIDER_LISTING_ID,
        "last_updated": now.isoformat(),
        "market_data": {"circulating_supply": 1000000, "total_supply": 2000000},
    }


def _evidence_manifest(tmp_path: Path, *, now: datetime, acquisition_id: str = "evidence-1") -> Path:
    manifest = {
        "operation": "value_capture",
        "kind": "evidence",
        "source_id": "test-official-tokenomics-disclosure",
        "capability": "evidence:official_disclosure",
        "endpoint": "https://example.org/tokenomics/page",
        "acquisition_id": acquisition_id,
        "acquired_at": (now + timedelta(minutes=1)).isoformat(),
        "identity": _value_capture_identity(),
        "payload": {
            "evidence_type": "official_disclosure",
            "source_reference": "official-tokenomics-page",
            "extracted_claim": "Protocol surplus funds a documented buyback-and-burn mechanism.",
            "accounting_period_start": (now - timedelta(days=30)).isoformat(),
            "accounting_period_end": now.isoformat(),
            "attribution_rule_id": "test-buyback-and-burn-rule-v1",
            "source_methodology": "official-accrual-disclosure-v1",
            "source_record_id": "official-tokenomics-page",
            "source_record_version": "2026-07-20",
            "entity_link_confidence": "1",
            "evidence_confidence": "0.95",
            "uncertainty": "0.05",
            "effective_at": now.isoformat(),
            "quality_state": "accepted",
            "conflict_state": "none",
        },
    }
    path = tmp_path / f"{acquisition_id}-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _supply_manifest(
    tmp_path: Path,
    *,
    now: datetime,
    evidence_record_id: str,
    market_fact_record_id: str,
    market_fact_semantic_version: str,
) -> Path:
    manifest = {
        "operation": "value_capture",
        "kind": "supply",
        "source_id": "test-official-tokenomics-disclosure",
        "capability": "supply:circulating_supply",
        "endpoint": "https://example.org/tokenomics/page",
        "acquisition_id": "supply-1",
        "acquired_at": (now + timedelta(minutes=2)).isoformat(),
        "identity": _value_capture_identity(),
        "payload": {
            "supply_basis_type": "circulating_supply",
            "quantity": "1000000",
            "unit": "native_units",
            "denominator_meaning": "Provider-observed circulating units for the canonical representation.",
            "supply_policy_id": "canonical-token-supply-v1",
            "supply_policy_version": "1.0.0",
            "quantity_components": [
                ["circulating_supply", "1000000"],
                ["total_supply", "2000000"],
            ],
            "observed_market_fact_ids": [market_fact_record_id],
            "observed_market_fact_versions": [market_fact_semantic_version],
            "source_record_id": "official-supply-disclosure",
            "source_record_version": "2026-07-20",
            "confidence": "0.9",
            "uncertainty": "0.1",
            "effective_at": (now + timedelta(minutes=2)).isoformat(),
            "evidence_record_ids": [evidence_record_id],
            "quality_state": "accepted",
            "conflict_state": "none",
        },
    }
    path = tmp_path / "supply-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _set_env(monkeypatch: pytest.MonkeyPatch, application_root: Path) -> None:
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(application_root))
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", SIGNING_KEY_ID)
    monkeypatch.setenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", SIGNING_KEY_HEX)


def test_market_fact_acquisition_persists_record_for_configured_entity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC)
    application_root = _application_root(tmp_path)
    _set_env(monkeypatch, application_root)
    manifest_path = _market_fact_manifest(tmp_path, now=now)
    transport = StaticTransport(_coingecko_payload(now=now))

    exit_code = valuation_evidence_command.main(["acquire", str(manifest_path)], transport=transport)

    assert exit_code == 0
    assert transport.urls == [f"https://api.coingecko.com/api/v3/coins/{_PROVIDER_LISTING_ID}"]
    output = json.loads(capsys.readouterr().out)
    assert output["operation"] == "market_fact"
    assert len(output["records"]) == 1
    record = output["records"][0]
    assert record["fact_type"] == "circulating_supply"
    assert record["quality_state"] == "accepted"
    assert (application_root / "data" / "data_ops.sqlite").exists()


def test_value_capture_evidence_and_supply_acquisition_chain_through_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC)
    application_root = _application_root(tmp_path)
    _set_env(monkeypatch, application_root)

    market_fact_manifest = _market_fact_manifest(tmp_path, now=now)
    transport = StaticTransport(_coingecko_payload(now=now))
    assert valuation_evidence_command.main(["acquire", str(market_fact_manifest)], transport=transport) == 0
    market_fact_output = json.loads(capsys.readouterr().out)
    market_fact_record = market_fact_output["records"][0]

    evidence_manifest = _evidence_manifest(tmp_path, now=now)
    assert valuation_evidence_command.main(["acquire", str(evidence_manifest)]) == 0
    evidence_output = json.loads(capsys.readouterr().out)
    assert evidence_output["kind"] == "evidence"
    assert evidence_output["quality_state"] == "accepted"

    supply_manifest = _supply_manifest(
        tmp_path,
        now=now,
        evidence_record_id=evidence_output["record_id"],
        market_fact_record_id=market_fact_record["record_id"],
        market_fact_semantic_version=market_fact_record["semantic_version"],
    )
    assert valuation_evidence_command.main(["acquire", str(supply_manifest)]) == 0
    supply_output = json.loads(capsys.readouterr().out)
    assert supply_output["kind"] == "supply"
    assert supply_output["quality_state"] == "accepted"


def test_missing_signing_key_env_var_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    application_root = _application_root(tmp_path)
    monkeypatch.setenv("HUNTER_APPLICATION_ROOT", str(application_root))
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID", raising=False)
    monkeypatch.delenv("HUNTER_VALUE_CAPTURE_SIGNING_KEY", raising=False)
    evidence_manifest = _evidence_manifest(tmp_path, now=datetime.now(UTC))

    with pytest.raises(ValueError, match="HUNTER_VALUE_CAPTURE_SIGNING_KEY_ID"):
        valuation_evidence_command.main(["acquire", str(evidence_manifest)])


def test_unregistered_market_fact_provider_id_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    application_root = _application_root(tmp_path)
    _set_env(monkeypatch, application_root)
    manifest = {
        "operation": "market_fact",
        "source_id": "test-coingecko-market-facts",
        "provider_id": "unregistered-provider",
        "identity": _market_fact_identity(),
        "requested_fact_types": ["circulating_supply"],
        "quote_currency": "usd",
        "requested_at": (now - timedelta(days=1)).isoformat(),
    }
    manifest_path = tmp_path / "bad-provider-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported market fact provider_id"):
        valuation_evidence_command.main(["acquire", str(manifest_path)])


def test_dispatch_from_hunter_main_reaches_valuation_evidence_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    application_root = _application_root(tmp_path)
    _set_env(monkeypatch, application_root)
    evidence_manifest = _evidence_manifest(tmp_path, now=datetime.now(UTC), acquisition_id="evidence-dispatch")

    exit_code = hunter_main.main(["valuation-evidence", "acquire", str(evidence_manifest)])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["operation"] == "value_capture"
    assert output["kind"] == "evidence"


def test_repository_configs_register_a_pilot_entity_with_buyback_and_burn_rule() -> None:
    market_fact_config = yaml.safe_load(Path("configs/market_fact_sources.yaml").read_text(encoding="utf-8"))
    value_capture_config = yaml.safe_load(Path("configs/value_capture_sources.yaml").read_text(encoding="utf-8"))

    coingecko_source = next(
        source for source in market_fact_config["sources"] if source["source_id"] == "coingecko-coin-market-facts"
    )
    bound_entities = {binding["entity_id"] for binding in coingecko_source["identity_bindings"]}
    assert len(bound_entities) >= 2, "market fact registry must not be hardcoded to a single pilot entity"

    enabled_value_capture_sources = [source for source in value_capture_config["sources"] if source["enabled"]]
    assert any(
        "rule:buyback_and_burn" in source["capabilities"] for source in enabled_value_capture_sources
    ), "at least one enabled value-capture source must disclose a permitted value-capture rule"
