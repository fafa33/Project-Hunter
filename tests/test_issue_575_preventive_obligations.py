"""Executable preventive obligations for Issue #575's three known PR #788 cases.

These tests are intentionally RED against the known-defective candidate HEAD
332638330f8b0a97dc40245e7e315239cd655168. They skip on a checkout that does not
yet carry the product surfaces so the infrastructure candidate itself can be
verified without importing absent product modules.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import hunter_connector_write_ingress as ingress
import hunter_pre_ready_review as review
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _candidate_modules() -> tuple[Any, Any]:
    """Import the PR #788 product test fixtures only where they exist."""
    if not (ROOT / "src" / "hunter" / "onchain" / "engine.py").is_file():
        pytest.skip("product surfaces under test are not present on this candidate")
    for path in (str(ROOT / "src"), str(ROOT / "tests")):
        if path not in sys.path:
            sys.path.insert(0, path)
    discovery = importlib.import_module("test_global_discovery_candidate_registry")
    onchain = importlib.import_module("test_onchain_capital_flow_acquisition")
    required = (
        (
            "_FakeGeckoApi",
            "_gecko_pool_payload",
            "DiscoveryConfig",
            "DiscoveryProviderConfig",
            "CandidateDiscoveryEngine",
            "CandidateRegistryRepository",
            "GeckoTerminalDiscoveryProvider",
        ),
        (
            "signing_material",
            "StaticTransport",
            "PROJECT_TOKEN",
            "TRANSFER_TOPIC",
            "OTHER_SURFACE",
            "SURFACE_ADDRESS",
            "chain",
            "surface",
            "FixedClock",
            "ACQUIRED_AT",
        ),
    )
    for module, names in ((discovery, required[0]), (onchain, required[1])):
        missing = [name for name in names if not hasattr(module, name)]
        if missing:
            pytest.skip("product test fixtures are unavailable: " + ", ".join(missing))
    return discovery, onchain


def test_case_a_surface_bound_signature_identity_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two surfaces, one transfer: distinct signed payloads must not share one signature ID."""
    _discovery, onchain = _candidate_modules()
    from hunter.onchain.evidence import TRUST_FILE_ENV  # type: ignore[import-untyped]

    from hunter.onchain.adapters import EVMJsonRpcAdapter
    from hunter.onchain.configuration import OnChainConfig
    from hunter.onchain.engine import CapitalFlowEngine
    from hunter.onchain.models import AssetConfig
    from hunter.onchain.registry import SurfaceRegistry
    from hunter.onchain.repository import OnChainRepository

    key, _key_path, trust_path, _trust = onchain.signing_material(tmp_path)
    monkeypatch.setenv(TRUST_FILE_ENV, str(trust_path))

    class TwoSurfaceTransport:
        def __init__(self) -> None:
            self._delegate = onchain.StaticTransport()

        def rpc(self, method: str, params: tuple[object, ...]) -> object:
            if method == "eth_getTransactionReceipt":
                logs = cast(list[dict[str, Any]], self.rpc("eth_getLogs", ()))
                return {**logs[0], "status": "0x1", "logs": logs}
            if method == "eth_getLogs":
                return [
                    {
                        "address": onchain.PROJECT_TOKEN,
                        "topics": [
                            onchain.TRANSFER_TOPIC,
                            "0x" + onchain.OTHER_SURFACE[2:].zfill(64),
                            "0x" + onchain.SURFACE_ADDRESS[2:].zfill(64),
                        ],
                        "data": "0x" + format(2500000, "064x"),
                        "transactionHash": "0x" + "ab" * 32,
                        "logIndex": "0x7",
                        "blockNumber": "0x384",
                        "blockHash": "0x" + "ef" * 32,
                    }
                ]
            return self._delegate.rpc(method, params)

    first = replace(onchain.surface(), address=onchain.SURFACE_ADDRESS, evidence_id="surface-evidence-1")
    second = replace(onchain.surface(), address=onchain.OTHER_SURFACE, evidence_id="surface-evidence-2")
    config = OnChainConfig(
        enabled=True,
        version="test",
        chains=(onchain.chain(),),
        tracked_assets=(AssetConfig(1, "TEST", "stablecoin", 6, onchain.PROJECT_TOKEN),),
        surfaces=(first, second),
        snapshot_windows=("24h",),
        retention={"runtime_root": "unused"},
    )
    repository = OnChainRepository(tmp_path / "runtime")
    registry = SurfaceRegistry(config)
    validation = registry.validate()
    assert validation.valid and len(registry.surfaces_for_project("aave")) >= 2, validation.issues
    engine = CapitalFlowEngine(  # type: ignore[call-arg]
        config,
        repository=repository,
        evidence_signing_key=key,
        clock=onchain.FixedClock(onchain.ACQUIRED_AT),
        adapters={
            1: EVMJsonRpcAdapter(
                replace(onchain.chain(), rpc_endpoints=("https://a.example", "https://b.example", "https://c.example")),
                transport_factory=lambda _: TwoSurfaceTransport(),
            )
        },
    )
    engine.sync()

    signatures = repository.evidence_signatures()
    identities = {str(item.get("surface_evidence_id")) for item in signatures}
    assert len(identities) == len(
        signatures
    ), "distinct signed surface observations share one signature/storage identity"


def test_case_b_pagination_budget_does_not_discard_acquired_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every fetched network catalogue observation must survive page/budget transitions."""
    discovery, _onchain = _candidate_modules()
    api = discovery._FakeGeckoApi(
        {1: [{"id": "eth"}], 2: [{"id": "solana"}]},
        {
            ("eth", 1): discovery._gecko_pool_payload("eth", "0xpool1", "0xtoken1"),
            ("solana", 1): discovery._gecko_pool_payload("solana", "pool1", "TokenA"),
        },
    )
    monkeypatch.setattr(
        discovery.GeckoTerminalDiscoveryProvider,
        "_get",
        lambda source, path, params, meter=None: api(source, path, params, meter),
    )
    repository = discovery.CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = discovery.DiscoveryConfig(
        providers={"geckoterminal": discovery.DiscoveryProviderConfig(enabled=True, max_requests_per_cycle=3)}
    )
    for _ in range(4):
        engine = discovery.CandidateDiscoveryEngine(
            repository,
            config,
            providers={"geckoterminal": discovery.GeckoTerminalDiscoveryProvider()},
        )
        engine.sync_provider("geckoterminal")

    assert (
        repository.network_observations()
    ), "acquired network catalogue observations were discarded by pagination/budget transitions"


def test_case_c_explicit_limit_still_obeys_request_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit candidate limit must not bypass the configured request allowance."""
    discovery, _onchain = _candidate_modules()
    from hunter.discovery.engine import CandidateDiscoveryEngine
    from hunter.discovery.providers import CoinGeckoDiscoveryProvider
    from hunter.discovery.repository import CandidateRegistryRepository

    pages: list[int] = []

    def fetch(_self: Any, _path: str, params: dict[str, Any], meter: Any = None) -> list[dict[str, str]]:
        page = int(params["page"])
        pages.append(page)
        return [{"id": f"asset-{page}-{index}", "name": f"Asset {page}-{index}"} for index in range(250)]

    monkeypatch.setattr(CoinGeckoDiscoveryProvider, "_get", fetch)
    config = discovery.DiscoveryConfig(
        providers={
            "coingecko": discovery.DiscoveryProviderConfig(
                enabled=True,
                limit=250,
                max_requests_per_cycle=1,
                min_interval_seconds=0.0,
            )
        }
    )
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    engine = CandidateDiscoveryEngine(
        repository,
        config,
        providers={"coingecko": CoinGeckoDiscoveryProvider(per_page=250)},
    )
    run = engine.sync_provider("coingecko", limit=750)

    assert len(pages) <= 1, f"request allowance=1 but pages fetched={pages}; status={run.status}, message={run.message}"


def test_preventive_obligations_cover_all_three_known_cases() -> None:
    """The canonical obligation data must stay bound to the three known findings."""
    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_ci_impact")
    obligations, error = module.load_obligations(ROOT)
    assert not error, error
    identifiers = {str(item["id"]) for item in obligations}
    assert {"PO-575-A", "PO-575-B", "PO-575-C"} <= identifiers
    case_c = next(item for item in obligations if item["id"] == "PO-575-C")
    assert "PRRT_kwDOTRDHr868k0Fu" in case_c["finding_ids"]
    assert "4713782958" in case_c["finding_ids"]


def test_applicable_obligations_select_all_three_cases() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_ci_impact")
    obligations, error = module.load_obligations(ROOT)
    assert not error, error
    changed = (
        "src/hunter/onchain/engine.py",
        "src/hunter/onchain/evidence.py",
        "src/hunter/discovery/engine.py",
        "src/hunter/discovery/providers.py",
    )
    selected = {str(item["id"]) for item in module.applicable_obligations(obligations, changed)}
    assert selected == {"PO-575-A", "PO-575-B", "PO-575-C"}


def test_missing_focused_test_fails_closed_in_bound_record(tmp_path: Path) -> None:
    import subprocess

    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_ci_impact")
    (tmp_path / "README.md").write_text("clean exact content\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(("git", "add", "README.md"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "fixture"),
        cwd=tmp_path,
        check=True,
    )
    missing = (
        {
            "id": "PO-MISSING",
            "changed_paths": ["src/hunter/discovery/engine.py"],
            "selectors": ["tests/test_does_not_exist.py::test_missing"],
        },
    )
    record, code = module.execute_obligations(tmp_path, missing, cwd=tmp_path)
    assert code == 2
    assert record["outcome"] == "blocked"
    assert "focused test selectors are missing" in record["error"]
    assert record["trusted_proof"] is False
    assert record["evidence_digest"] == module.evidence_digest(
        {key: value for key, value in record.items() if key != "evidence_digest"}
    )


def _committed_fixture_repo(tmp_path: Path, test_source: str) -> Path:
    """Create a clean committed repository containing one focused test."""
    import subprocess

    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_focused.py").write_text(test_source, encoding="utf-8")
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    subprocess.run(("git", "add", "."), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "fixture"),
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _assert_digest_describes_final_record(record: dict[str, Any]) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_ci_impact")
    assert record["evidence_digest"] == module.evidence_digest(
        {key: value for key, value in record.items() if key != "evidence_digest"}
    )


def test_skipped_focused_test_is_blocked_in_bound_record(tmp_path: Path) -> None:
    """A skipped selector exits zero under pytest but must not count as proof."""
    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_ci_impact")
    fixture = _committed_fixture_repo(
        tmp_path,
        "import pytest\n\ndef test_focused() -> None:\n    pytest.skip('not executed')\n",
    )
    obligations = (
        {
            "id": "PO-SKIP",
            "changed_paths": ["src/hunter/discovery/providers.py"],
            "selectors": ["tests/test_focused.py::test_focused"],
        },
    )
    record, code = module.execute_obligations(fixture, obligations, cwd=fixture)
    assert code != 0
    assert record["outcome"] == "blocked"
    assert "skipped" in record["error"]
    assert record["test_inputs"][0]["skipped"] == 1
    assert record["trusted_proof"] is False
    _assert_digest_describes_final_record(record)


def test_executed_focused_test_digest_describes_final_record(tmp_path: Path) -> None:
    """The emitted digest must cover the final execution record exactly once."""
    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_ci_impact")
    fixture = _committed_fixture_repo(
        tmp_path,
        "def test_focused() -> None:\n    assert True\n",
    )
    obligations = (
        {
            "id": "PO-EXEC",
            "changed_paths": ["src/hunter/discovery/providers.py"],
            "selectors": ["tests/test_focused.py::test_focused"],
        },
    )
    record, code = module.execute_obligations(fixture, obligations, cwd=fixture)
    assert code == 0
    assert record["outcome"] == "passed"
    assert record["test_inputs"][0]["tests"] == 1
    assert record["test_inputs"][0]["skipped"] == 0
    assert record["trusted_proof"] is False
    _assert_digest_describes_final_record(record)


def _review_for_family_evidence(evidence: str) -> dict[str, Any]:
    changes = (ingress.ConnectorFileChange("modified", "src/hunter/onchain/evidence.py", "", "a" * 40),)
    claims = review.build_claims(
        issue="575",
        base_ref="main",
        base_sha="b" * 40,
        changes=changes,
        acceptance_criteria=(
            {
                "id": "AC-1",
                "criterion": "surface-bound observations keep distinct signed identities",
                "verdict": "satisfied",
                "evidence": evidence,
            },
        ),
        defect_families=({"family": "DFF-062", "outcome": "clear", "evidence": evidence},),
        findings=(),
        adversarial_dimensions=tuple(review.REQUIRED_ADVERSARIAL_DIMENSIONS),
    )
    return review.document_for(claims)


def test_prompt_digest_or_selected_rules_digest_is_not_execution_evidence() -> None:
    """Manual adoption prose, prompt SHA and selected-rules SHA are assertions, not proof."""
    for evidence in (
        "prompt_sha256=" + "0" * 64,
        "selected_prevention_rules_sha256=" + "1" * 64,
        "manual OpenCode session swept DFF-062 and found it clear",
    ):
        verdict = review.verify_claims(
            _review_for_family_evidence(evidence),
            base_sha="b" * 40,
            changes=(ingress.ConnectorFileChange("modified", "src/hunter/onchain/evidence.py", "", "a" * 40),),
            families=({"id": "DFF-062", "applicability": {"changed_paths": ["src/hunter/onchain/evidence.py"]}},),
            require_authority=False,
        )
        assert verdict.ok is False, evidence
        assert "committed regression selector" in verdict.reason


def test_committed_regression_selector_names_satisfies_the_evidence_shape() -> None:
    selector = "tests/test_issue_575_preventive_obligations.py::test_case_a_surface_bound_signature_identity_collision"
    changes = (ingress.ConnectorFileChange("modified", "src/hunter/onchain/evidence.py", "", "a" * 40),)
    verdict = review.verify_claims(
        _review_for_family_evidence("executed " + selector + " against exact HEAD"),
        base_sha="b" * 40,
        changes=changes,
        families=({"id": "DFF-062", "applicability": {"changed_paths": ["src/hunter/onchain/evidence.py"]}},),
        require_authority=False,
    )
    assert verdict.ok is True, verdict.reason


def test_unscoped_manual_entry_without_registry_obligations_fails_closed(tmp_path: Path) -> None:
    """A plain manual session has no scoped obligation data and cannot opt out."""
    import json

    sys.path.insert(0, str(ROOT / "scripts"))
    module = importlib.import_module("hunter_workflow_state")
    payload = json.loads((ROOT / "docs" / "DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    payload.pop("preventive_obligations")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text(json.dumps(payload), encoding="utf-8")
    obligations, error = module.load_preventive_obligations(tmp_path)
    assert obligations == ()
    assert "preventive_obligations" in error
