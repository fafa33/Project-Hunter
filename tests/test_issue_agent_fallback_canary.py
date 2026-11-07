"""ADR 0041 F8: the owner-armed, single-use fallback canary (adversarial and regression).

The canary induces one real, observable refusal of the pinned provider host by the primary run's own egress gateway.
It is admitted only from the signed ``AUTHORIZED.fallback_canary`` flag, only as ``CANARY_NETWORK``, and only with the
gateway's refusal evidence; it can never be mistaken for, or relabelled as, a real provider failure.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_roles as base
from test_issue_agent_fallback_executor import offer_and_admit
from test_issue_agent_fallback_ledger import FALLBACK_ID, admitted_evidence, facts, offered, offered_evidence
from test_issue_agent_roles import AUTH, CANARY, ISSUE, RecordingIsolation, executor_config
from test_issue_agent_state import Chain, _by, reject

from hunter.automation import issue_agent_authorize as authorize
from hunter.automation import issue_agent_kilo_container as kilo
from hunter.automation import issue_agent_provider_probe as probe_module
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state

repos = base.repos
_prompt_keys = base._prompt_keys

_gateway_path = Path(__file__).resolve().parents[1] / "scripts/issue_agent/model_egress_gateway.py"
_spec = importlib.util.spec_from_file_location("hunter_gateway_canary_test_entrypoint", _gateway_path)
assert _spec is not None and _spec.loader is not None
gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway)

HOST = state.CANARY_PROVIDER_HOST
REFUSAL = {"provider_host": HOST, "primary_refusals": 2, "probe_refusals": 1, "refusal_digest": "c" * 64}


def canary_offer() -> Chain:
    return Chain().transition(state.AUTHORIZED, {**offered_evidence(), "fallback_canary": True}, "authorize")


def canary_admission(**changes: Any) -> dict[str, Any]:
    defaults = {
        "primary_cause": "CANARY_NETWORK",
        "stderr_category": "NETWORK",
        "probe_category": "NETWORK",
        "canary_refusal": dict(REFUSAL),
    }
    return admitted_evidence(**{**defaults, **changes})


def canary_facts(**changes: Any) -> Any:
    base_changes = {
        "executor_stderr_category": "NETWORK",
        "executor_probe_category": "NETWORK",
        "executor_canary_refusal": dict(REFUSAL),
    }
    return facts(**{**base_changes, **changes})


# --- one pinned provider host ---------------------------------------------------------------------------------


def test_every_layer_pins_the_same_provider_host() -> None:
    assert gateway.KILO_PROVIDER_HOST == probe_module.KILO_PROVIDER_HOST == state.CANARY_PROVIDER_HOST
    assert probe_module.KILO_CANARY_PROBE_URL == f"https://{HOST}/"


# --- gateway: the canary profile ------------------------------------------------------------------------------


def test_the_canary_profile_is_the_kilo_profile_without_the_provider_host() -> None:
    kilo_hosts, kilo_listen = gateway.PROFILES["kilo"]
    canary_hosts, canary_listen = gateway.PROFILES["kilo-canary"]
    assert canary_hosts == kilo_hosts - {HOST} and HOST in kilo_hosts and canary_listen == kilo_listen
    assert HOST not in gateway.PROFILES["codex"][0]  # the fallback never uses the canary profile


class _Peer:
    def __init__(self, request: bytes) -> None:
        self.inbound, self.sent = io.BytesIO(request), b""

    def settimeout(self, _: Any) -> None:
        pass

    def recv(self, size: int) -> bytes:
        return self.inbound.read(size)

    def sendall(self, data: bytes) -> None:
        self.sent += data


def _handle(request: bytes, allowed: frozenset[str]) -> _Peer:
    peer = _Peer(request)
    handler = object.__new__(type("H", (gateway.Handler,), {"allowed": allowed}))
    handler.request = peer
    handler.handle()
    return peer


def test_the_canary_gateway_refuses_the_provider_and_names_only_the_host(capsys: pytest.CaptureFixture[str]) -> None:
    peer = _handle(
        f"CONNECT {HOST}:443 HTTP/1.1\r\nAuthorization: x\r\n\r\n".encode(), gateway.PROFILES["kilo-canary"][0]
    )
    assert peer.sent.startswith(b"HTTP/1.1 403")
    assert capsys.readouterr().out == f"GATEWAY_REFUSED_HOST {HOST}\n"


@pytest.mark.parametrize(
    "request_line",
    [
        b"CONNECT ai-gateway.kilo.ai:443 HTTP/1.0\r\n\r\n",
        b"GET http://x/ HTTP/1.1\r\n\r\n",
        b"CONNECT A.B:443 HTTP/1.1\r\n\r\n",
    ],
)
def test_a_malformed_request_is_never_counted_as_a_host_refusal(
    request_line: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    _handle(request_line, gateway.PROFILES["kilo-canary"][0])
    assert capsys.readouterr().out.startswith("GATEWAY_DENIED_CONNECT")


# --- isolation: refusal evidence ------------------------------------------------------------------------------


def _evidence(primary: list[str], everything: list[str], other: int = 0) -> Any:
    return kilo.canary_refusal_evidence(
        gateway_instance="gw-1", provider_host=HOST, primary_hosts=primary, all_hosts=everything, other=other
    )


def test_refusal_evidence_binds_the_primary_and_one_probe_refusal() -> None:
    evidence = _evidence([HOST, HOST], [HOST, HOST, HOST])
    assert evidence is not None and evidence["primary_refusals"] == 2 and evidence["probe_refusals"] == 1
    assert state.canary_refusal_conforms({"provider_host": HOST, **evidence})
    assert evidence != _evidence([HOST], [HOST, HOST])  # the digest binds the counts
    other_instance = kilo.canary_refusal_evidence(
        gateway_instance="gw-2", provider_host=HOST, primary_hosts=[HOST], all_hosts=[HOST, HOST], other=0
    )
    assert other_instance != _evidence([HOST], [HOST, HOST])  # and the gateway instance


@pytest.mark.parametrize(
    ("primary", "everything", "other"),
    [
        ([], [HOST], 0),  # the primary was never refused: its failure was something else
        ([HOST], [HOST], 0),  # the probe did not meet the refusal
        ([HOST], [HOST, HOST, HOST], 0),  # more than one probe refusal
        ([HOST, "telemetry.example"], [HOST, "telemetry.example", HOST], 0),  # another host was refused
        ([HOST], [HOST, "models.dev"], 0),  # the probe was refused for a different host
        ([HOST], [HOST, HOST], 1),  # a malformed request was denied
    ],
)
def test_anything_but_exactly_the_canary_refusal_yields_no_evidence(
    primary: list[str], everything: list[str], other: int
) -> None:
    assert _evidence(primary, everything, other) is None


def test_gateway_logs_are_parsed_without_content() -> None:
    log = f"GATEWAY_REFUSED_HOST {HOST}\nnoise\nGATEWAY_DENIED_CONNECT b'GET /'\nGATEWAY_REFUSED_HOST bad host!\n"
    assert kilo.gateway_refusals(log) == ([HOST], 1)


def test_the_canary_profile_reaches_only_the_armed_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("model_egress_gateway.py", "real_broker_server.py"):
        (scripts / name).write_text("")
    calls: list[tuple[str, ...]] = []

    def docker(*args: str, **_: Any) -> Any:
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": b"BROKER_GIT_SNAPSHOT:", "stderr": b""})()

    monkeypatch.setattr(kilo.KiloContainerIsolation, "_docker", staticmethod(docker))
    for armed in (False, True):
        calls.clear()
        port = kilo.KiloContainerIsolation(scripts)
        if armed:
            port.arm_canary()
        (tmp_path / "workspace").mkdir(exist_ok=True)
        (tmp_path / "home").mkdir(exist_ok=True)
        port.prepare(tmp_path / "workspace", tmp_path / "home")
        started = next(call for call in calls if call[:4] == ("run", "-d", "--name", port.gateway))
        expected = "HUNTER_GATEWAY_PROFILE=kilo-canary" if armed else "HUNTER_GATEWAY_PROFILE=kilo"
        assert expected in started and sum("HUNTER_GATEWAY_PROFILE" in word for word in started) == 1
        with pytest.raises(roles.RoleRefused):
            port.arm_canary()  # never after the gateway exists


def test_an_unarmed_gateway_is_never_observed_as_a_canary(tmp_path: Path) -> None:
    port = kilo.KiloContainerIsolation(Path(__file__).resolve().parents[1] / "scripts/issue_agent")
    with pytest.raises(roles.RoleRefused):
        port.observe_canary(lambda _: "NETWORK", HOST)


# --- the trusted probe through the gateway --------------------------------------------------------------------


class _Tunnel:
    def __init__(self, answer: bytes) -> None:
        self.answer, self.sent = io.BytesIO(answer), b""

    def __enter__(self) -> _Tunnel:
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        return self.answer.read(size)


def test_a_refused_tunnel_is_the_network_failure_the_primary_saw() -> None:
    tunnel = _Tunnel(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    seen: list[Any] = []

    def connect(address: Any, timeout: float) -> _Tunnel:
        seen.append(address)
        return tunnel

    category = probe_module.probe_through_gateway(
        probe_module.KILO_CANARY_PROBE_URL, gateway=("172.17.0.2", 8080), connect=connect
    )
    assert category == "NETWORK" and seen == [("172.17.0.2", 8080)]
    # One well-formed HTTP/1.1 CONNECT the gateway can attribute to the host, and never a credential.
    assert tunnel.sent == f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n\r\n".encode()


def test_an_unreachable_gateway_is_network_and_a_probe_defect_is_unknown() -> None:
    def refused(*_: Any, **__: Any) -> Any:
        raise ConnectionRefusedError

    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("defect")

    assert (
        probe_module.probe_through_gateway(probe_module.KILO_CANARY_PROBE_URL, gateway=("h", 1), connect=refused)
        == "NETWORK"
    )
    assert (
        probe_module.probe_through_gateway(probe_module.KILO_CANARY_PROBE_URL, gateway=("h", 1), connect=broken)
        == "UNCLASSIFIED"
    )
    assert probe_module.probe_through_gateway("http://plain/", gateway=("h", 1), connect=broken) == "UNCLASSIFIED"


# --- authorize: owner arming ----------------------------------------------------------------------------------

OWNER, EXECUTE, AT = "fafa33", "hunter-agent-execute", "2026-11-07T12:00:10Z"


def _label(kind: str, label: str, at: str, actor: str = OWNER) -> dict[str, Any]:
    return {"event": kind, "label": {"name": label}, "actor": {"login": actor}, "created_at": at}


def _armed(*events: dict[str, Any], authorized_at: str = AT) -> bool:
    return authorize.fallback_canary_armed(
        [{"event": "commented", "created_at": "2026-11-07T11:00:00Z"}, *events],
        owner_login=OWNER,
        authorization_label=EXECUTE,
        authorized_at=authorized_at,
    )


CANARY_LABEL = authorize.FALLBACK_CANARY_LABEL


def test_the_owner_arms_the_canary_before_the_authorizing_event() -> None:
    assert CANARY_LABEL == "hunter-codex-fallback-canary"
    assert _armed(_label("labeled", CANARY_LABEL, "2026-11-07T12:00:00Z"), _label("labeled", EXECUTE, AT))


@pytest.mark.parametrize(
    "events",
    [
        # applied after the authorizing event, or in the same second: never armed retroactively
        [_label("labeled", EXECUTE, AT), _label("labeled", CANARY_LABEL, "2026-11-07T12:00:11Z")],
        [_label("labeled", CANARY_LABEL, AT), _label("labeled", EXECUTE, AT)],
        # applied by anyone but the owner
        [_label("labeled", CANARY_LABEL, "2026-11-07T12:00:00Z", "mallory"), _label("labeled", EXECUTE, AT)],
        # removed again before authorization
        [
            _label("labeled", CANARY_LABEL, "2026-11-07T11:59:00Z"),
            _label("unlabeled", CANARY_LABEL, "2026-11-07T12:00:00Z", "mallory"),
            _label("labeled", EXECUTE, AT),
        ],
        # single use: an arming that preceded an earlier authorization never carries over to this one
        [
            _label("labeled", CANARY_LABEL, "2026-11-07T10:00:00Z"),
            _label("labeled", EXECUTE, "2026-11-07T10:00:05Z"),
            _label("labeled", EXECUTE, AT),
        ],
        # the authorizing event was not the owner's
        [_label("labeled", CANARY_LABEL, "2026-11-07T12:00:00Z"), _label("labeled", EXECUTE, AT, "mallory")],
        # no canary label at all
        [_label("labeled", EXECUTE, AT)],
    ],
)
def test_anything_else_leaves_the_canary_unarmed(events: list[dict[str, Any]]) -> None:
    assert not _armed(*events)


def test_a_rearmed_canary_applies_to_the_next_authorization_only() -> None:
    assert _armed(
        _label("labeled", CANARY_LABEL, "2026-11-07T10:00:00Z"),
        _label("labeled", EXECUTE, "2026-11-07T10:00:05Z"),
        _label("unlabeled", CANARY_LABEL, "2026-11-07T11:00:00Z"),
        _label("labeled", CANARY_LABEL, "2026-11-07T12:00:00Z"),
        _label("labeled", EXECUTE, AT),
    )


def test_a_malformed_label_event_is_indefinite_not_unarmed() -> None:
    with pytest.raises(ValueError):
        _armed({"event": "labeled", "label": {"name": CANARY_LABEL}, "created_at": AT})


# --- ledger: schema and cross-bindings ------------------------------------------------------------------------


def test_a_canary_chain_verifies_through_its_admission() -> None:
    chain = canary_offer().transition(state.FALLBACK_AUTHORIZED, canary_admission(), "bind")
    view = chain.view.authorizations[AUTH]
    assert view.state == state.FALLBACK_AUTHORIZED
    assert view.evidence[state.FALLBACK_AUTHORIZED]["primary_cause"] == "CANARY_NETWORK"


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        (canary_admission(), "without the signed canary flag"),  # CANARY_NETWORK is never admissible unflagged
        (admitted_evidence(canary_refusal=dict(REFUSAL)), "without the signed canary flag"),
    ],
)
def test_an_unflagged_authorization_never_admits_a_canary(evidence: dict[str, Any], message: str) -> None:
    chain = offered()
    reject(chain, chain.make("transition", state.FALLBACK_AUTHORIZED, evidence, _by("bind")), message)


@pytest.mark.parametrize(
    "evidence",
    [
        admitted_evidence(),  # a real RATE_LIMIT on a canary authorization
        admitted_evidence(primary_cause="NETWORK", stderr_category="NETWORK", probe_category="NETWORK"),
        {k: v for k, v in canary_admission().items() if k != "canary_refusal"},
        canary_admission(stderr_category="RATE_LIMIT"),
    ],
)
def test_a_canary_authorization_admits_only_its_canary(evidence: dict[str, Any]) -> None:
    chain = canary_offer()
    reject(chain, chain.make("transition", state.FALLBACK_AUTHORIZED, evidence, _by("bind")), "armed canary")


@pytest.mark.parametrize(
    "refusal",
    [
        {**REFUSAL, "primary_refusals": 0},
        {**REFUSAL, "probe_refusals": 2},
        {**REFUSAL, "provider_host": "models.dev"},
        {**REFUSAL, "refusal_digest": "z" * 64},
        {**REFUSAL, "extra": 1},
    ],
)
def test_malformed_refusal_evidence_never_conforms(refusal: dict[str, Any]) -> None:
    assert not state.canary_refusal_conforms(refusal)
    chain = canary_offer()
    record = chain.make("transition", state.FALLBACK_AUTHORIZED, canary_admission(canary_refusal=refusal), _by("bind"))
    reject(chain, record, "schema")


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_the_canary_flag_is_a_strict_boolean(flag: Any) -> None:
    chain = Chain()
    record = chain.make(
        "transition", state.AUTHORIZED, {**offered_evidence(), "fallback_canary": flag}, _by("authorize")
    )
    reject(chain, record, "must be a boolean")


def test_a_canary_needs_a_bound_fallback() -> None:
    from test_issue_agent_state import authorized_evidence

    chain = Chain()
    record = chain.make(
        "transition", state.AUTHORIZED, {**authorized_evidence(), "fallback_canary": False}, _by("authorize")
    )
    reject(chain, record, "bound no fallback")


# --- advance: admission and replay ----------------------------------------------------------------------------


def test_the_armed_canary_is_admitted_only_as_canary_network() -> None:
    decision = state.advance(canary_offer().view.authorizations[AUTH], canary_facts())
    assert decision.target_state == state.FALLBACK_AUTHORIZED and decision.evidence_hint == {"cause": "CANARY_NETWORK"}


@pytest.mark.parametrize(
    "changes",
    [
        {"executor_canary_refusal": None},  # no gateway refusal evidence
        {"executor_canary_refusal": {**REFUSAL, "probe_refusals": 0}},
        {"executor_stderr_category": "RATE_LIMIT", "executor_probe_category": "RATE_LIMIT"},  # never a real cause
        {"executor_stderr_category": "QUOTA_EXCEEDED", "executor_probe_category": "QUOTA_EXCEEDED"},
        {"executor_probe_category": "UNCLASSIFIED"},  # the probe did not meet the refusal
        {"executor_stderr_category": "UNCLASSIFIED"},
        {"now": "2026-10-04T16:00:00Z"},
    ],
)
def test_a_canary_without_both_observations_and_evidence_is_terminal(changes: dict[str, Any]) -> None:
    decision = state.advance(canary_offer().view.authorizations[AUTH], canary_facts(**changes))
    assert decision == state.Decision("fail", code="EXECUTION_NOT_COMPLETED")


def test_an_unarmed_authorization_still_admits_a_real_network_failure_as_network() -> None:
    view = offered().view.authorizations[AUTH]
    decision = state.advance(view, canary_facts())  # stray refusal evidence never turns a real failure into a canary
    assert decision.evidence_hint == {"cause": "NETWORK"}


def test_a_canary_is_never_replayed_into_a_second_fallback() -> None:
    chain = canary_offer().transition(state.FALLBACK_AUTHORIZED, canary_admission(), "bind")
    again = chain.make("transition", state.FALLBACK_AUTHORIZED, canary_admission(), _by("bind"))
    reject(chain, again, "illegal transition")
    view = chain.view.authorizations[AUTH]
    assert state.advance(view, canary_facts()).target_state != state.FALLBACK_AUTHORIZED
    assert FALLBACK_ID == view.evidence[state.FALLBACK_AUTHORIZED]["fallback_execution_id"]


# --- executor: the signed flag alone arms the primary's gateway -----------------------------------------------


class CanaryIsolation(RecordingIsolation):
    def __init__(self, *args: Any, refusal: Any = REFUSAL, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.armed, self.order, self.refusal = False, [], refusal
        self.last_stderr_category = "NETWORK"

    def arm_canary(self) -> None:
        self.order.append("arm")
        self.armed = True

    def observe_canary(self, probe: Any, provider_host: str) -> tuple[str, Any]:
        assert provider_host == HOST
        self.order.append("observe")
        return "NETWORK", self.refusal

    def stop(self) -> None:
        self.order.append("stop")
        super().stop()


KILO_ARGV = ("/opt/hunter/node_modules/.bin/kilo", "run", "--model", "kilo/example:free")


def _run(repos: dict[str, Any], isolation: Any, *, canary: bool | None, argv: tuple[str, ...] = KILO_ARGV) -> Any:
    ledger, envelope, _ = offer_and_admit(repos, admit=False, canary=canary)
    fields = {name: getattr(executor_config(repos), name) for name in roles.ExecutorConfig.__dataclass_fields__}
    config = roles.ExecutorConfig(**{**fields, "model_argv": argv, "model_secret_env": {}})
    return roles.run_executor(
        issue=ISSUE,
        authorization_id=AUTH,
        context=roles.RoleContext(100, 1),
        ledger=ledger.access,
        handoff_envelope=envelope,
        config=config,
        isolation=isolation,
        workroot=repos["tmp"] / f"canary-{canary}",
    )


@pytest.fixture
def any_kilo_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(roles, "require_governed_kilo_isolation", lambda *_: None)


def test_the_armed_primary_probes_its_own_gateway_before_stop(repos: dict[str, Any], any_kilo_port: None) -> None:
    isolation = CanaryIsolation(CANARY, fail=True)
    outcome = _run(repos, isolation, canary=True)
    assert outcome == roles.ExecutorOutcome(None, "PROVIDER_UNAVAILABLE", "NETWORK", "NETWORK", REFUSAL)
    assert isolation.order == ["arm", "observe", "stop"]


@pytest.mark.parametrize("canary", [False, None])
def test_an_unarmed_primary_never_arms_or_observes(repos: dict[str, Any], any_kilo_port: None, canary: Any) -> None:
    isolation = CanaryIsolation(CANARY, fail=True)
    outcome = _run(repos, isolation, canary=canary)
    assert isolation.order == ["stop"] and outcome.canary_refusal is None


def test_an_armed_primary_that_succeeds_is_never_observed(repos: dict[str, Any], any_kilo_port: None) -> None:
    isolation = CanaryIsolation(CANARY)
    _run(repos, isolation, canary=True)
    assert isolation.order == ["arm", "stop"]


def test_an_armed_canary_never_runs_without_the_kilo_gateway(repos: dict[str, Any]) -> None:
    with pytest.raises(roles.RoleRefused, match="Kilo gateway"):
        _run(repos, RecordingIsolation(CANARY, fail=True), canary=True, argv=("opencode", "run"))
