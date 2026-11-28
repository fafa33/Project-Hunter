"""Executable preventive obligations for Issue #575's three known PR #788 cases.

The discovery and onchain fixtures are self-contained and version-adaptive:
they execute the real product APIs on this candidate and on an isolated PR
#788 overlay without skipping. Optional product-only surfaces are exercised
when present, never treated as a reason to skip a mandatory selector.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import hunter_connector_write_ingress as ingress
import hunter_pre_ready_review as review
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import hunter_ci_impact as ci_impact  # noqa: E402

PR788_HEAD = "332638330f8b0a97dc40245e7e315239cd655168"
ONCHAIN_NOW = datetime(2026, 7, 13, tzinfo=UTC)
SURFACE_ADDRESS = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
OTHER_SURFACE = "0x0000000000000000000000000000000000000a01"
PROJECT_TOKEN = "0x0000000000000000000000000000000000000c03"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _ensure_product_import_paths() -> None:
    overlay = os.environ.get("HUNTER_575_PRODUCT_OVERLAY")
    if overlay:
        # pytest/conftest may have imported the checkout product already. An
        # overlay is authoritative for the subprocess that sets this variable.
        for name in tuple(sys.modules):
            if name == "hunter" or name.startswith("hunter."):
                del sys.modules[name]
        for local in (str(ROOT / "src"), str(ROOT / "tests")):
            if local in sys.path:
                sys.path.remove(local)
        roots = (Path(overlay) / "src", Path(overlay) / "tests")
    else:
        roots = (ROOT / "src", ROOT / "tests")
    for path in roots:
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _has_field(model: Any, name: str) -> bool:
    return name in {field.name for field in fields(model)}


# --- Discovery fixtures (independent of onchain fixtures) --------------------------------------------------


def _gecko_pool_payload(network: str, pool_address: str, token_address: str) -> dict[str, object]:
    quote = "So111" if network == "solana" else "0xweth"
    return {
        "data": [
            {
                "attributes": {"address": pool_address, "name": "Pool", "reserve_in_usd": "100"},
                "relationships": {
                    "base_token": {"data": {"id": f"{network}_{token_address}"}},
                    "quote_token": {"data": {"id": f"{network}_{quote}"}},
                    "dex": {"data": {"id": "dex"}},
                },
            }
        ],
        "included": [
            {
                "id": f"{network}_{token_address}",
                "type": "token",
                "attributes": {"name": "Base Token", "symbol": "BASE", "address": token_address},
            },
            {
                "id": f"{network}_{quote}",
                "type": "token",
                "attributes": {"name": "Quote Token", "symbol": "QUOTE", "address": quote},
            },
        ],
    }


class _FakeGeckoApi:
    def __init__(
        self,
        catalogues: dict[int, list[dict[str, object]]],
        pools: dict[tuple[str, int], object],
        *,
        trending: tuple[dict[str, object], ...] | None = None,
    ) -> None:
        self.catalogues = catalogues
        self.pools = pools
        self.trending = trending
        self.trending_index = 0
        self.calls: list[tuple[str, int]] = []

    def __call__(self, provider: object, path: str, params: dict[str, object], meter: object = None) -> object:
        if meter is not None:
            meter.consume(time.sleep)  # type: ignore[attr-defined]
        page = int(str(params.get("page", 1)))
        self.calls.append((path, page))
        if path == "/networks":
            return {
                "data": self.catalogues.get(page, []),
                "links": {"next": page + 1} if page < max(self.catalogues, default=0) else None,
            }
        if path == "/networks/trending_pools":
            if self.trending is not None:
                index = min(self.trending_index, len(self.trending) - 1)
                self.trending_index += 1
                return self.trending[index]
            return self.pools.get(("trending", page), _gecko_pool_payload("eth", "0xtrending", "0xtoken1"))
        if path.startswith("/networks/") and path.endswith("/pools"):
            network = path.split("/")[2]
            return self.pools.get((network, page), {"data": [], "included": []})
        raise AssertionError(f"unexpected path: {path}")


def _discovery_api() -> SimpleNamespace:
    _ensure_product_import_paths()
    configuration = importlib.import_module("hunter.discovery.configuration")
    engine = importlib.import_module("hunter.discovery.engine")
    providers = importlib.import_module("hunter.discovery.providers")
    repository = importlib.import_module("hunter.discovery.repository")
    return SimpleNamespace(
        DiscoveryConfig=configuration.DiscoveryConfig,
        DiscoveryProviderConfig=configuration.DiscoveryProviderConfig,
        CandidateDiscoveryEngine=engine.CandidateDiscoveryEngine,
        GeckoTerminalDiscoveryProvider=providers.GeckoTerminalDiscoveryProvider,
        CoinGeckoDiscoveryProvider=providers.CoinGeckoDiscoveryProvider,
        CandidateRegistryRepository=repository.CandidateRegistryRepository,
        overlay=_has_field(configuration.DiscoveryConfig, "capacity_inventory_path"),
    )


def _discovery_case_b_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, _FakeGeckoApi, bool]:
    api = _discovery_api()
    if api.overlay:
        fake = _FakeGeckoApi(
            {1: [{"id": "eth"}], 2: [{"id": "solana"}]},
            {
                ("eth", 1): _gecko_pool_payload("eth", "0xpool1", "0xtoken1"),
                ("solana", 1): _gecko_pool_payload("solana", "pool1", "TokenA"),
            },
        )
    else:
        trending = tuple(_gecko_pool_payload("eth", f"0xpool{index}", f"0x{index + 1:040x}") for index in range(4))
        fake = _FakeGeckoApi({}, {}, trending=trending)

    def get(provider: object, path: str, params: dict[str, object], meter: object = None) -> object:
        return fake(provider, path, params, meter)

    monkeypatch.setattr(api.GeckoTerminalDiscoveryProvider, "_get", get)

    provider_kwargs: dict[str, Any] = {"enabled": True, "limit": 4}
    if _has_field(api.DiscoveryProviderConfig, "max_requests_per_cycle"):
        provider_kwargs.update(max_requests_per_cycle=3, min_interval_seconds=0.0)
    config = api.DiscoveryConfig(providers={"geckoterminal": api.DiscoveryProviderConfig(**provider_kwargs)})
    repository = api.CandidateRegistryRepository(tmp_path / "candidates.sqlite")

    def make_engine() -> Any:
        return api.CandidateDiscoveryEngine(
            repository,
            config,
            providers={"geckoterminal": api.GeckoTerminalDiscoveryProvider()},
        )

    return repository, make_engine, fake, bool(api.overlay)


def test_case_b_pagination_budget_does_not_discard_acquired_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every fetched network catalogue observation must survive page/budget transitions."""
    repository, make_engine, fake, overlay = _discovery_case_b_fixture(tmp_path, monkeypatch)
    for _ in range(4):
        run = make_engine().sync_provider("geckoterminal")
        assert run.status in {"succeeded", "partial"}, run.message

    assert fake.calls, "discovery provider never executed an authenticated fixture request"
    if overlay and hasattr(repository, "network_observations"):
        assert repository.network_observations(), "acquired network catalogue observations were discarded"
    assert len(repository.list_candidates(limit=100)) >= 4, "acquired candidates were discarded between cycles"


def test_case_c_explicit_limit_still_obeys_request_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit candidate limit must not bypass a configured request allowance."""
    api = _discovery_api()
    pages: list[int] = []

    def fetch(_self: Any, _path: str, params: dict[str, Any], meter: Any = None) -> list[dict[str, str]]:
        if meter is not None:
            meter.consume(time.sleep)
        page = int(params["page"])
        pages.append(page)
        return [{"id": f"asset-{page}-{index}", "name": f"Asset {page}-{index}"} for index in range(250)]

    monkeypatch.setattr(api.CoinGeckoDiscoveryProvider, "_get", fetch)
    provider_kwargs: dict[str, Any] = {"enabled": True, "limit": 250}
    if _has_field(api.DiscoveryProviderConfig, "max_requests_per_cycle"):
        provider_kwargs.update(max_requests_per_cycle=1, min_interval_seconds=0.0)
    config = api.DiscoveryConfig(providers={"coingecko": api.DiscoveryProviderConfig(**provider_kwargs)})
    repository = api.CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    engine = api.CandidateDiscoveryEngine(
        repository,
        config,
        providers={"coingecko": api.CoinGeckoDiscoveryProvider(per_page=250)},
    )
    engine.sync_provider("coingecko", limit=750)

    if api.overlay:
        assert len(pages) <= 1, f"request allowance=1 but pages fetched={pages}"
    else:
        # This candidate has no request-budget field: the explicit limit is the
        # only allowance, so the real provider must fetch exactly three pages.
        assert pages == [1, 2, 3], pages


# --- Onchain fixtures (independent of discovery fixtures) --------------------------------------------------


class _StaticTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        self.calls.append(method)
        if method == "eth_chainId":
            return hex(1)
        if method == "eth_blockNumber":
            return hex(1_000)
        if method == "eth_getBlockByNumber":
            number = 936 if params and params[0] == "finalized" else int(str(params[0]), 16)
            return {"number": hex(number), "hash": "0x" + "ef" * 32, "timestamp": hex(int(ONCHAIN_NOW.timestamp()))}
        if method == "eth_getBalance":
            return hex(10**18)
        if method == "eth_getLogs":
            return [{"transactionHash": "0xtx", "logIndex": hex(7)}]
        raise AssertionError(method)


class _TwoSurfaceTransport(_StaticTransport):
    def rpc(self, method: str, params: tuple[object, ...]) -> object:
        if method == "eth_getTransactionReceipt":
            logs = cast(list[dict[str, Any]], self.rpc("eth_getLogs", ()))
            return {**logs[0], "status": "0x1", "logs": logs}
        if method == "eth_getLogs":
            return [
                {
                    "address": PROJECT_TOKEN,
                    "topics": [
                        TRANSFER_TOPIC,
                        "0x" + OTHER_SURFACE[2:].zfill(64),
                        "0x" + SURFACE_ADDRESS[2:].zfill(64),
                    ],
                    "data": "0x" + format(2_500_000, "064x"),
                    "transactionHash": "0x" + "ab" * 32,
                    "logIndex": "0x7",
                    "blockNumber": "0x384",
                    "blockHash": "0x" + "ef" * 32,
                }
            ]
        return super().rpc(method, params)


def _onchain_api() -> SimpleNamespace:
    _ensure_product_import_paths()
    configuration = importlib.import_module("hunter.onchain.configuration")
    engine = importlib.import_module("hunter.onchain.engine")
    models = importlib.import_module("hunter.onchain.models")
    adapters = importlib.import_module("hunter.onchain.adapters")
    registry = importlib.import_module("hunter.onchain.registry")
    repository = importlib.import_module("hunter.onchain.repository")
    return SimpleNamespace(
        OnChainConfig=configuration.OnChainConfig,
        CapitalFlowEngine=engine.CapitalFlowEngine,
        EVMJsonRpcAdapter=adapters.EVMJsonRpcAdapter,
        OnChainSurface=models.OnChainSurface,
        ChainConfig=models.ChainConfig,
        AssetConfig=models.AssetConfig,
        SurfaceRegistry=registry.SurfaceRegistry,
        OnChainRepository=repository.OnChainRepository,
        overlay=_has_field(configuration.OnChainConfig, "dex_pools"),
    )


def _onchain_case_a_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[SimpleNamespace, dict[str, Any]]:
    api = _onchain_api()
    key: Any = None
    engine_kwargs: dict[str, Any] = {}
    if api.overlay:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from hunter.onchain.evidence import (  # type: ignore[import-untyped]
            TRUST_FILE_ENV,
            write_signing_key,
            write_trust_document,
        )

        from hunter.execution import FixedClock

        key = Ed25519PrivateKey.generate()
        key_path = tmp_path / "signing.key"
        trust_path = tmp_path / "trust.json"
        write_signing_key(key_path, key)
        write_trust_document(trust_path, key)
        monkeypatch.setenv(TRUST_FILE_ENV, str(trust_path))
        engine_kwargs = {"evidence_signing_key": key, "clock": FixedClock(ONCHAIN_NOW)}

    def surface(address: str, evidence_id: str) -> Any:
        return api.OnChainSurface(
            project="aave",
            chain_id=1,
            network="ethereum",
            address=address,
            address_type="lending_market",
            protocol_role="aave_v3_pool",
            asset_scope="protocol_collateral",
            source_url="https://etherscan.io/address/" + address,
            source_type="verified_block_explorer",
            verification_timestamp=ONCHAIN_NOW,
            confidence=0.95,
            active=True,
            valid_from=ONCHAIN_NOW,
            evidence_id=evidence_id,
        )

    chain = api.ChainConfig(
        chain_id=1,
        network="ethereum",
        family="evm",
        enabled=True,
        rpc_endpoint="https://a.example",
        rpc_endpoints=("https://a.example", "https://b.example", "https://c.example"),
        rpc_env=None,
        explorer_url="https://etherscan.io",
        finality_depth=64,
        max_block_range=5000,
        polling_interval_seconds=300,
        retry_limit=1,
        timeout_seconds=1,
    )
    config = api.OnChainConfig(
        enabled=True,
        version="test",
        chains=(chain,),
        tracked_assets=(api.AssetConfig(1, "TEST", "stablecoin", 6, PROJECT_TOKEN),),
        surfaces=(surface(SURFACE_ADDRESS, "surface-evidence-1"), surface(OTHER_SURFACE, "surface-evidence-2")),
        snapshot_windows=("24h",),
        retention={"runtime_root": "unused"},
    )
    repository = api.OnChainRepository(tmp_path / "runtime")
    validation = api.SurfaceRegistry(config).validate()
    assert validation.valid and len(api.SurfaceRegistry(config).surfaces_for_project("aave")) >= 2, validation.issues
    engine = api.CapitalFlowEngine(
        config,
        repository=repository,
        adapters={1: api.EVMJsonRpcAdapter(chain, transport_factory=lambda _: _TwoSurfaceTransport())},
        **engine_kwargs,
    )
    return api, {"engine": engine, "repository": repository}


def test_case_a_surface_bound_signature_identity_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two surfaces, one transfer: distinct signed payloads must not share one identity."""
    api, fixture = _onchain_case_a_fixture(tmp_path, monkeypatch)
    fixture["engine"].sync()

    if api.overlay:
        signatures = fixture["repository"].evidence_signatures()
        assert signatures, "PR #788 overlay did not persist signed evidence"
        identities = {str(item.get("surface_evidence_id")) for item in signatures}
        assert len(identities) == len(signatures), "distinct signed surface observations share one signature identity"
    raw_ids = {str(item.get("evidence_id")) for item in fixture["repository"].raw() if item.get("evidence_id")}
    assert len(raw_ids) == 2, f"two surfaces must keep two evidence identities, got {sorted(raw_ids)}"


# --- Canonical obligation execution and version overlays ---------------------------------------------------


def _committed_candidate_repo(tmp_path: Path) -> Path:
    """Snapshot committed content plus current candidate edits into a clean repo.

    Preventive evidence binds only to a clean tree. The exact candidate bytes
    are copied from the real worktree, then committed in an isolated repo so the
    CLI exercises the same content without mutating the governed checkout.
    """
    destination = tmp_path / "candidate"
    destination.mkdir()
    archive = subprocess.run(("git", "archive", "HEAD"), cwd=ROOT, capture_output=True, check=True).stdout
    subprocess.run(("tar", "-x", "-C", str(destination)), input=archive, check=True)
    tracked_changes = subprocess.run(
        ("git", "diff", "--name-only"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ("git", "ls-files", "--others", "--exclude-standard"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    changed = [*tracked_changes, *untracked]
    for relative in changed:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    subprocess.run(("git", "init", "-q"), cwd=destination, check=True)
    subprocess.run(("git", "add", "-A"), cwd=destination, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=test",
            "commit",
            "-qm",
            "candidate",
        ),
        cwd=destination,
        check=True,
    )
    return destination


def _run_preventive_cli(root: Path, *changed_paths: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    command = [sys.executable, "scripts/hunter_preventive_obligations.py", "--root", str(root)]
    for path in changed_paths:
        command.extend(("--changed-path", path))
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, env=env)
    payload = next(
        line.split(": ", 1)[1]
        for line in completed.stdout.splitlines()
        if line.startswith("[Hunter Preventive Obligations] ")
    )
    record = json.loads(payload)
    assert completed.returncode == int(record["exit_code"]), completed.stderr
    return record


def test_discovery_preventive_cli_executes_every_mandatory_selector(tmp_path: Path) -> None:
    """The registered discovery path must execute both selectors, never skip them."""
    root = _committed_candidate_repo(tmp_path)
    record = _run_preventive_cli(root, "src/hunter/discovery/providers.py")
    assert record["outcome"] == "passed"
    assert record["obligations"] == ["PO-575-B", "PO-575-C"]
    assert len(record["test_inputs"]) == 2
    assert all(item["tests"] > 0 for item in record["test_inputs"])
    assert all(item["skipped"] == 0 for item in record["test_inputs"])
    assert all(item["failures"] == 0 and item["errors"] == 0 for item in record["test_inputs"])
    assert record["evidence_digest"] == ci_impact.evidence_digest(
        {key: value for key, value in record.items() if key != "evidence_digest"}
    )


def test_onchain_preventive_cli_executes_every_mandatory_selector(tmp_path: Path) -> None:
    """The canonical onchain obligation must execute against real product APIs."""
    root = _committed_candidate_repo(tmp_path)
    record = _run_preventive_cli(
        root,
        "src/hunter/onchain/engine.py",
        "src/hunter/onchain/evidence.py",
        "src/hunter/onchain/repository.py",
        "tests/test_onchain_capital_flow_acquisition.py",
    )
    assert record["outcome"] == "passed"
    assert record["obligations"] == ["PO-575-A"]
    assert len(record["test_inputs"]) == 1
    assert record["test_inputs"][0]["tests"] > 0
    assert record["test_inputs"][0]["skipped"] == 0
    assert record["test_inputs"][0]["failures"] == 0
    assert record["test_inputs"][0]["errors"] == 0


def _extract_committed_overlay(commit: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(("git", "archive", commit), cwd=ROOT, capture_output=True, check=True).stdout
    subprocess.run(("tar", "-x", "-C", str(destination)), input=archive, check=True)


def test_pr788_product_overlay_executes_all_three_selectors(tmp_path: Path) -> None:
    """Compatibility with PR #788 is proved in an isolated read-only source overlay."""
    overlay = tmp_path / "pr788-product"
    _extract_committed_overlay(PR788_HEAD, overlay)
    assert (overlay / "src" / "hunter" / "onchain" / "evidence.py").is_file()
    selectors = (
        "tests/test_issue_575_preventive_obligations.py::test_case_a_surface_bound_signature_identity_collision",
        "tests/test_issue_575_preventive_obligations.py::test_case_b_pagination_budget_does_not_discard_acquired_catalogue",
        "tests/test_issue_575_preventive_obligations.py::test_case_c_explicit_limit_still_obeys_request_budget",
    )
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["HUNTER_575_PRODUCT_OVERLAY"] = str(overlay)
    report = tmp_path / "pr788-junit.xml"
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            f"--junitxml={report}",
            *selectors,
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    counts = ci_impact._junit_counts(report)
    assert counts["tests"] == 3, completed.stdout + completed.stderr
    assert counts["skipped"] == 0, completed.stdout + completed.stderr
    assert len(counts["cases"]) == 3, counts
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr


def test_defective_product_overlays_are_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An overlay missing the real product surfaces cannot masquerade as executed proof."""
    overlay = tmp_path / "defective-product"
    _extract_committed_overlay(PR788_HEAD, overlay)
    (overlay / "src" / "hunter" / "discovery" / "providers.py").unlink()
    (overlay / "src" / "hunter" / "onchain" / "engine.py").unlink()
    monkeypatch.setenv("HUNTER_575_PRODUCT_OVERLAY", str(overlay))
    candidate = _committed_candidate_repo(tmp_path)
    obligations = (
        {
            "id": "PO-575-B",
            "changed_paths": (),
            "selectors": [
                "tests/test_issue_575_preventive_obligations.py::test_case_b_pagination_budget_does_not_discard_acquired_catalogue"
            ],
        },
    )
    record, code = ci_impact.execute_obligations(candidate, obligations, cwd=candidate)
    assert code == 2
    assert record["outcome"] == "blocked"
    assert record.get("error"), record


def test_preventive_obligations_cover_all_three_known_cases() -> None:
    """The canonical obligation data must stay bound to the three known findings."""
    obligations, error = ci_impact.load_obligations(ROOT)
    assert not error, error
    identifiers = {str(item["id"]) for item in obligations}
    assert {"PO-575-A", "PO-575-B", "PO-575-C"} <= identifiers
    case_c = next(item for item in obligations if item["id"] == "PO-575-C")
    assert "PRRT_kwDOTRDHr868k0Fu" in case_c["finding_ids"]
    assert "4713782958" in case_c["finding_ids"]


def test_applicable_obligations_select_all_three_cases() -> None:
    obligations, error = ci_impact.load_obligations(ROOT)
    assert not error, error
    changed = (
        "src/hunter/onchain/engine.py",
        "src/hunter/onchain/evidence.py",
        "src/hunter/discovery/engine.py",
        "src/hunter/discovery/providers.py",
    )
    selected = {str(item["id"]) for item in ci_impact.applicable_obligations(obligations, changed)}
    assert selected == {"PO-575-A", "PO-575-B", "PO-575-C"}


def test_missing_focused_test_fails_closed_in_bound_record(tmp_path: Path) -> None:
    """A missing selector exits zero under pytest but must not count as proof."""
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
    record, code = ci_impact.execute_obligations(tmp_path, missing, cwd=tmp_path)
    assert code == 2
    assert record["outcome"] == "blocked"
    assert "focused test selectors are missing" in record["error"]
    assert record["trusted_proof"] is False
    assert record["evidence_digest"] == ci_impact.evidence_digest(
        {key: value for key, value in record.items() if key != "evidence_digest"}
    )


def _committed_fixture_repo(tmp_path: Path, test_source: str) -> Path:
    """Create a clean committed repository containing one focused test."""
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
    assert record["evidence_digest"] == ci_impact.evidence_digest(
        {key: value for key, value in record.items() if key != "evidence_digest"}
    )


def test_skipped_focused_test_is_blocked_in_bound_record(tmp_path: Path) -> None:
    """A skipped selector exits zero under pytest but must not count as proof."""
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
    record, code = ci_impact.execute_obligations(fixture, obligations, cwd=fixture)
    assert code != 0
    assert record["outcome"] == "blocked"
    assert "skipped" in record["error"]
    assert record["test_inputs"][0]["skipped"] == 1
    assert record["trusted_proof"] is False
    _assert_digest_describes_final_record(record)


def test_executed_focused_test_digest_describes_final_record(tmp_path: Path) -> None:
    """The emitted digest must cover the final execution record exactly once."""
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
    record, code = ci_impact.execute_obligations(fixture, obligations, cwd=fixture)
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
    payload = json.loads((ROOT / "docs" / "DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    payload.pop("preventive_obligations")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DEFECT_REGISTRY.json").write_text(json.dumps(payload), encoding="utf-8")
    module = importlib.import_module("hunter_workflow_state")
    obligations, error = module.load_preventive_obligations(tmp_path)
    assert obligations == ()
    assert "preventive_obligations" in error
