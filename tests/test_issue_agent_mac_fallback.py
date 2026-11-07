"""ADR 0041 F2/F5/F6: the trusted probe, the Codex launcher and the runner-side Mac port (adversarial)."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from hunter.automation import issue_agent_mac_fallback as mac
from hunter.automation.issue_agent_provider_probe import classify_probe, probe_provider
from hunter.automation.issue_agent_roles import RoleRefused

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "issue_agent" / "codex_fallback_launcher.py"
PROFILE = (ROOT / "scripts" / "issue_agent" / "codex-fallback.sb").read_text()
CODEX_SHA = "c" * 64


def load_launcher() -> Any:
    spec = importlib.util.spec_from_file_location("codex_fallback_launcher", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load_launcher()


# --- trusted provider probe ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,category",
    [
        (None, "NETWORK"),
        (429, "RATE_LIMIT"),
        (402, "QUOTA_EXCEEDED"),
        (401, "AUTHENTICATION"),
        (403, "PERMISSION"),
        (503, "PROVIDER_UNAVAILABLE"),
        (200, "UNCLASSIFIED"),  # a healthy provider leaves nothing to fall back from
        (302, "UNCLASSIFIED"),
        (404, "UNCLASSIFIED"),
    ],
)
def test_the_probe_maps_only_into_the_closed_vocabulary(status: int | None, category: str) -> None:
    assert classify_probe(status) == category


class _Response(io.BytesIO):
    def __init__(self, status: int) -> None:
        super().__init__(b"provider text that must never be read or returned")
        self.status = status


def test_the_probe_sends_one_bodiless_authenticated_get_and_returns_no_provider_text() -> None:
    seen: list[Any] = []

    def opener(request: Any, timeout: float) -> _Response:
        seen.append(
            (request.get_method(), request.full_url, request.data, request.get_header("Authorization"), timeout)
        )
        return _Response(200)

    assert probe_provider("https://api.example/models", api_key="sk-secret", opener=opener) == "UNCLASSIFIED"
    assert seen == [("GET", "https://api.example/models", None, "Bearer sk-secret", 10.0)]


@pytest.mark.parametrize(
    "raised,category",
    [
        (urllib.error.HTTPError("u", 429, "Too Many", {}, None), "RATE_LIMIT"),  # type: ignore[arg-type]
        (urllib.error.URLError("refused"), "NETWORK"),
        (TimeoutError(), "NETWORK"),
        (ValueError("defect"), "UNCLASSIFIED"),
    ],
)
def test_probe_failures_are_categories_never_exceptions(raised: Exception, category: str) -> None:
    def opener(request: Any, timeout: float) -> Any:
        raise raised

    assert probe_provider("https://api.example/models", api_key="k", opener=opener) == category


@pytest.mark.parametrize(
    "url,key", [("http://api.example/models", "k"), ("https://api.example", ""), ("https://a.example", "k\r\nX: y")]
)
def test_the_probe_refuses_plaintext_keyless_or_header_injecting_requests(url: str, key: str) -> None:
    def opener(request: Any, timeout: float) -> Any:
        raise AssertionError("no request may be sent")

    assert probe_provider(url, api_key=key, opener=opener) == "UNCLASSIFIED"


# --- launcher: closed request schema and launcher-built argv --------------------------------------------------


def test_launcher_and_runner_agree_on_every_pinned_constant() -> None:
    assert launcher.WORK_ROOT == mac.WORK_ROOT and launcher.OWNER_INBOX == mac.OWNER_INBOX
    assert launcher.CODEX_BINARY == mac.CODEX_BINARY and launcher.GATEWAY_PORT == mac.GATEWAY_PORT
    assert f'"localhost:{mac.GATEWAY_PORT}"' in PROFILE
    assert str(launcher.PROFILE_PATH) in {str(path) for path in mac.INSTALLED_SOURCES}


@pytest.mark.parametrize(
    "header,prompt",
    [
        ({"kind": "run", "workroot_id": "a" * 32, "model": "gpt-5-codex", "argv": ["--add-dir", "/"]}, b"x"),
        ({"kind": "run", "workroot_id": "a" * 32, "model": "--dangerously-bypass-approvals-and-sandbox"}, b"x"),
        ({"kind": "run", "workroot_id": "a" * 32, "model": "m -c x=1"}, b"x"),
        ({"kind": "run", "workroot_id": "../../etc", "model": "m"}, b"x"),
        ({"kind": "run", "workroot_id": "A" * 32, "model": "m"}, b"x"),
        ({"kind": "run", "workroot_id": "a" * 32, "model": "m"}, b""),
        ({"kind": "run", "workroot_id": "a" * 32, "model": "m"}, b"x" * (256 * 1024 + 1)),
        ({"kind": "attest", "workroot_id": "a" * 32}, b"unexpected"),
        ({"kind": "attest", "workroot_id": "a" * 32, "env": {"HTTPS_PROXY": "evil"}}, b""),
        ({"kind": "shell", "workroot_id": "a" * 32}, b""),
        ({"kind": "run", "workroot_id": 7, "model": "m"}, b"x"),
        (["run"], b"x"),
    ],
)
def test_the_launcher_refuses_anything_outside_its_closed_schema(header: Any, prompt: bytes) -> None:
    with pytest.raises(launcher.Refused):
        launcher.validate_request(header, prompt)


def test_the_launcher_admits_exactly_the_two_request_shapes() -> None:
    assert launcher.validate_request({"kind": "attest", "workroot_id": "a" * 32}, b"")["kind"] == "attest"
    run = {"kind": "run", "workroot_id": "a" * 32, "model": "gpt-5.1-codex"}
    assert launcher.validate_request(run, b"task") == run


def test_codex_always_runs_inside_the_outer_profile_with_launcher_built_arguments() -> None:
    workspace, home = Path("/w/workspace"), Path("/w/home")
    argv = launcher.sandbox_argv(workspace, home, launcher.codex_exec_argv("gpt-5.1-codex", workspace))
    assert argv[:3] == ["/usr/bin/sandbox-exec", "-f", str(launcher.PROFILE_PATH)]
    codex = argv.index(str(launcher.CODEX_BINARY))
    assert argv[codex + 1] == "exec" and argv[-1] == "-"
    # The inner sandbox is disabled only because the outer profile is the boundary (P0: nesting is impossible).
    assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv and "--add-dir" not in argv
    assert 'cli_auth_credentials_store="file"' in argv


def test_the_model_environment_routes_only_through_the_loopback_gateway() -> None:
    env = launcher.model_environment(Path("/w/home"))
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == env["ALL_PROXY"] == "http://127.0.0.1:18443"
    assert env["CODEX_HOME"] == "/w/home/.codex" and env["NO_PROXY"] == ""
    assert not any("KEY" in name or "TOKEN" in name for name in env)


def test_session_secrets_are_collected_from_any_depth_of_the_session_file(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "a" * 40, "refresh_token": "r" * 40, "nested": {"id_token": "i" * 30}}})
    )
    assert sorted(launcher._session_secrets(tmp_path)) == sorted(["a" * 40, "r" * 40, "i" * 30])
    assert launcher._session_secrets(tmp_path / "missing") == []


def test_the_device_code_inbox_write_is_create_only_and_printable(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(launcher, "OWNER_INBOX", tmp_path)
    launcher._inbox_write("a" * 32, b"Open https://auth.openai.com/codex/device\nCode: ABCD-1234\x1b[0m\x00")
    written = (tmp_path / ("a" * 32 + ".device-code")).read_bytes()
    assert b"ABCD-1234" in written and b"\x1b" not in written and b"\x00" not in written
    with pytest.raises(FileExistsError):
        launcher._inbox_write("a" * 32, b"second")  # never overwrite: one code per run


# --- the sandbox profile (structural) -------------------------------------------------------------------------


def _balanced(text: str, head: str) -> str:
    """The complete s-expression that starts with ``head`` (comments in the profile hold no parentheses)."""

    start, depth = text.index(head), 0
    for index in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[index], 0)
        if depth == 0:
            return text[start : index + 1]
    raise AssertionError("unbalanced profile")


def _forms(text: str) -> list[str]:
    body = "\n".join(line.split(";", 1)[0] for line in text.splitlines())
    forms, index = [], 0
    while (index := body.find("(", index)) != -1:
        form = _balanced(body, body[index:])
        forms.append(" ".join(form.split()))
        index += len(form)
    return forms


def test_the_profile_denies_by_default_and_admits_only_loopback_gateway_egress() -> None:
    forms = _forms(PROFILE)
    assert forms[0] == "(version 1)" and "(deny default)" in forms
    assert [form for form in forms if "network" in form] == ['(allow network-outbound (remote ip "localhost:18443"))']
    assert not any(form.startswith(("(allow default", "(allow file*", "(allow network*")) for form in forms)
    writes = [form for form in forms if form.startswith("(allow file-write*")]
    assert writes == [
        '(allow file-write* (literal "/dev/null") (subpath (param "WORKSPACE")) (subpath (param "HOME")))'
    ]
    reads = next(form for form in forms if form.startswith("(allow file-read*"))
    assert "/Users" not in reads and "hunter-fallback" not in reads and "/Library/Hunter" not in reads


# --- runner-side port -------------------------------------------------------------------------------------------


def provisioned_checkout(tmp_path: Path, **changes: Any) -> Path:
    checkout = tmp_path / "checkout"
    for source in [*mac.INSTALLED_SOURCES.values(), mac.PLIST_TEMPLATE]:
        target = checkout / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / source).read_bytes())
    config = {
        "schema_version": mac.HOST_SCHEMA_VERSION,
        "provisioned": True,
        "runner_user": "hunterrunner",
        "agent_user": "hunteragent",
        "runner_uid": 503,
        "runner_gid": 503,
        "agent_uid": 502,
        "codex_version": "0.167.0",
        "codex_sha256": CODEX_SHA,
        "codex_model": "gpt-5.1-codex",
        **changes,
    }
    (checkout / mac.HOST_CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    (checkout / mac.HOST_CONFIG_PATH).write_text(json.dumps(config))
    return checkout


def test_an_unprovisioned_host_pin_keeps_the_fallback_inert(tmp_path: Path) -> None:
    with pytest.raises(RoleRefused, match="not provisioned"):
        mac.load_host_config(provisioned_checkout(tmp_path, provisioned=False))


def test_the_repository_pins_the_provisioned_least_privilege_host() -> None:
    # Owner-provisioned values, measured from the installed host (dscl and the installed launchd socket owner).
    config = mac.load_host_config(ROOT)
    assert (config.runner_user, config.agent_user) == ("hunterrunner", "hunteragent")
    assert (config.runner_uid, config.runner_gid, config.agent_uid) == (505, 504, 502)
    assert len({config.runner_uid, config.agent_uid}) == 2 and min(config.runner_uid, config.agent_uid) >= 501


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_uid": 503},  # one principal for both roles
        {"runner_uid": 0},  # root
        {"agent_uid": 80},  # a system account
        {"codex_sha256": ""},
        {"codex_version": ""},
        {"extra": True},
    ],
)
def test_a_malformed_host_pin_refuses(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises(RoleRefused):
        mac.load_host_config(provisioned_checkout(tmp_path, **changes))


def installed(checkout: Path, config: mac.HostConfig, codex: bytes = b"codex") -> dict[Path, bytes]:
    files = {path: (checkout / source).read_bytes() for path, source in mac.INSTALLED_SOURCES.items()}
    files[mac.PLIST_PATH] = mac.render_plist(checkout, config)
    files[mac.INSTALLED_CONFIG_PATH] = mac.render_installed_config(config)
    files[mac.CODEX_BINARY] = codex
    return files


def make_port(tmp_path: Path, monkeypatch: Any, *, files: dict[Path, bytes] | None = None, **kw: Any) -> Any:
    import hashlib

    checkout = provisioned_checkout(tmp_path, codex_sha256=hashlib.sha256(b"codex").hexdigest())
    config = mac.load_host_config(checkout)
    monkeypatch.setattr(mac, "WORK_ROOT", tmp_path / "work")
    monkeypatch.setattr(mac, "grant_agent_access", lambda path, cfg: None)
    monkeypatch.setattr(mac, "_agent_processes", lambda user: False)
    monkeypatch.setattr(mac, "_chmod_acl", lambda entry, path: None)
    table = files if files is not None else installed(checkout, config)
    port = mac.MacCodexIsolation(
        checkout,
        launcher=kw.get(
            "launcher", lambda header, prompt, timeout: {"status": "ATTESTED", "cases": {"identity": "PASS"}}
        ),
        read=lambda path: table[path],
        principals=kw.get("principals", lambda cfg: {"agent_not_admin": True}),
        gateway=lambda checkout: None,
    )
    workroot = tmp_path / "work" / ("a" * 32)
    workroot.mkdir(parents=True)
    return port, workroot, config


def test_a_fully_attested_port_records_a_digest_over_installation_principals_and_live_cases(
    tmp_path: Path, monkeypatch: Any
) -> None:
    port, workroot, _ = make_port(tmp_path, monkeypatch)
    digest = port.attest(workroot)
    assert port.attested and re.fullmatch(r"[0-9a-f]{64}", digest)
    assert not (workroot / "attest-canary").exists()


@pytest.mark.parametrize("target", ["launcher", "profile", "gateway", "plist", "config", "codex"])
def test_any_installed_byte_difference_refuses_before_any_key_is_used(
    tmp_path: Path, monkeypatch: Any, target: str
) -> None:
    checkout = provisioned_checkout(tmp_path / "pin")
    config = mac.load_host_config(checkout)
    files = installed(checkout, config)
    path = {
        "launcher": Path("/Library/Hunter/codex-fallback-launcher.py"),
        "profile": Path("/Library/Hunter/codex-fallback.sb"),
        "gateway": Path("/Library/Hunter/model_egress_gateway.py"),
        "plist": mac.PLIST_PATH,
        "config": mac.INSTALLED_CONFIG_PATH,
        "codex": mac.CODEX_BINARY,
    }[target]
    files[path] = files[path] + b"\n(allow default)\n"
    port, workroot, _ = make_port(tmp_path, monkeypatch, files=files)
    with pytest.raises(RoleRefused):
        port.attest(workroot)
    assert not port.attested


@pytest.mark.parametrize(
    "principals,live",
    [
        ({"agent_not_admin": False}, {"status": "ATTESTED", "cases": {"identity": "PASS"}}),
        ({}, {"status": "ATTESTED", "cases": {"identity": "PASS"}}),
        ({"agent_not_admin": True}, {"status": "ATTESTED", "cases": {"identity": "PASS", "direct-egress": "FAIL"}}),
        ({"agent_not_admin": True}, {"status": "FAILED", "cases": {"identity": "PASS"}}),
        ({"agent_not_admin": True}, {"status": "ATTESTED", "cases": {}}),
        ({"agent_not_admin": True}, {"status": "ATTESTED", "cases": "PASS"}),
    ],
)
def test_a_principal_or_live_denial_failure_refuses(
    tmp_path: Path, monkeypatch: Any, principals: dict[str, bool], live: dict[str, Any]
) -> None:
    port, workroot, _ = make_port(
        tmp_path, monkeypatch, principals=lambda cfg: principals, launcher=lambda header, prompt, timeout: live
    )
    with pytest.raises(RoleRefused):
        port.attest(workroot)
    assert not port.attested


def test_an_unattested_port_never_prepares_or_runs(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, _ = make_port(tmp_path, monkeypatch)
    with pytest.raises(RoleRefused, match="attested"):
        port.prepare(workroot / "workspace", workroot / "home")
    with pytest.raises(RoleRefused, match="attested"):
        port.run(
            ("codex", "exec", "--model", "m"),
            cwd=workroot / "workspace",
            public_env={},
            secret_env={},
            stdin=b"x",
            timeout=1,
        )


def attested_port(tmp_path: Path, monkeypatch: Any, response: dict[str, Any]) -> tuple[Any, Path, list[Any]]:
    sent: list[Any] = []

    def fake(header: Any, prompt: bytes, timeout: float) -> dict[str, Any]:
        sent.append((dict(header), prompt))
        if header["kind"] == "attest":
            return {"status": "ATTESTED", "cases": {"identity": "PASS"}}
        return response

    port, workroot, _ = make_port(tmp_path, monkeypatch, launcher=fake)
    port.attest(workroot)
    assert (workroot / "workspace").is_dir()
    assert (workroot / "home").is_dir()
    port.prepare(workroot / "workspace", workroot / "home")
    return port, workroot, sent


def test_only_the_validated_model_and_the_sealed_prompt_cross_to_the_launcher(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, sent = attested_port(
        tmp_path,
        monkeypatch,
        {
            "status": "COMPLETED",
            "exit": "ZERO",
            "stderr_category": "UNCLASSIFIED",
            "model_identity": "VERIFIED",
            "session_secrets": ["t" * 40],
        },
    )
    status = port.run(
        ("codex", "exec", "--model", "gpt-5.1-codex", "--sandbox", "workspace-write", "-c", "x=1"),
        cwd=workroot / "workspace",
        public_env={"HTTPS_PROXY": "http://evil", "PATH": "/tmp"},
        secret_env={},
        stdin=b"sealed task",
        timeout=60,
    )
    assert status == 0 and port.session_secrets == ("t" * 40,)
    assert sent[-1] == ({"kind": "run", "workroot_id": "a" * 32, "model": "gpt-5.1-codex"}, b"sealed task")


@pytest.mark.parametrize(
    "argv,secret_env,cwd",
    [
        (("codex", "exec", "--model", "m"), {"OPENAI_API_KEY": "sk"}, "workspace"),  # no provider key, ever
        (("codex", "exec", "--model", "m"), {}, "home"),
        (("codex", "exec", "--model", "-c"), {}, "workspace"),
        (("codex", "exec", "--profile", "evil"), {}, "workspace"),
        (("kilo", "run", "--model", "m"), {}, "workspace"),
    ],
)
def test_the_port_refuses_keys_foreign_directories_and_injected_argv(
    tmp_path: Path, monkeypatch: Any, argv: tuple[str, ...], secret_env: dict[str, str], cwd: str
) -> None:
    port, workroot, sent = attested_port(
        tmp_path, monkeypatch, {"status": "COMPLETED", "exit": "ZERO", "model_identity": "VERIFIED"}
    )
    with pytest.raises(RoleRefused):
        port.run(argv, cwd=workroot / cwd, public_env={}, secret_env=secret_env, stdin=b"x", timeout=1)
    assert all(header["kind"] == "attest" for header, _ in sent)


@pytest.mark.parametrize(
    "status", ["DEVICE_CODE_EXPIRED", "DEVICE_AUTH_FAILED", "SANDBOX_SETUP_FAILED", "REFUSED", "SESSION_NOT_STARTED"]
)
def test_a_session_that_never_started_is_not_started_and_still_reports_its_secrets(
    tmp_path: Path, monkeypatch: Any, status: str
) -> None:
    port, workroot, _ = attested_port(tmp_path, monkeypatch, {"status": status, "session_secrets": ["s" * 40]})
    with pytest.raises(RoleRefused) as refused:
        port.run(
            ("codex", "exec", "--model", "gpt-5.1-codex"),
            cwd=workroot / "workspace",
            public_env={},
            secret_env={},
            stdin=b"x",
            timeout=1,
        )
    assert refused.value.code == "EXECUTION_NOT_STARTED" and port.session_secrets == ("s" * 40,)


def test_a_launcher_timeout_is_a_model_timeout(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, _ = attested_port(tmp_path, monkeypatch, {"status": "COMPLETED", "exit": "TIMEOUT"})
    with pytest.raises(subprocess.TimeoutExpired):
        port.run(
            ("codex", "exec", "--model", "gpt-5.1-codex"),
            cwd=workroot / "workspace",
            public_env={},
            secret_env={},
            stdin=b"x",
            timeout=1,
        )


def test_the_port_prepares_only_its_own_attested_workroot(tmp_path: Path, monkeypatch: Any) -> None:
    port, workroot, _ = attested_port(
        tmp_path, monkeypatch, {"status": "COMPLETED", "exit": "ZERO", "model_identity": "VERIFIED"}
    )
    other = tmp_path / "work" / ("b" * 32)
    (other / "workspace").mkdir(parents=True)
    with pytest.raises(RoleRefused):
        port.prepare(other / "workspace", workroot / "home")


@pytest.mark.parametrize("name", ["x", "A" * 32, "a" * 31, "../" + "a" * 32])
def test_a_workroot_outside_the_pinned_per_run_root_refuses(tmp_path: Path, monkeypatch: Any, name: str) -> None:
    monkeypatch.setattr(mac, "WORK_ROOT", tmp_path / "work")
    with pytest.raises(RoleRefused):
        mac.workroot_identity(tmp_path / "work" / name)


def test_a_launcher_answer_outside_its_vocabulary_refuses(monkeypatch: Any) -> None:
    class Socket:
        def __init__(self, *a: Any) -> None:
            body = json.dumps({"status": "SHELL_OK"}).encode()
            self.data = len(body).to_bytes(4, "big") + body

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def settimeout(self, t: float) -> None:
            return None

        def connect(self, p: str) -> None:
            return None

        def sendall(self, b: bytes) -> None:
            return None

        def recv(self, n: int) -> bytes:
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

    monkeypatch.setattr(mac.socket, "socket", Socket)
    with pytest.raises(RoleRefused):
        mac._send(Path("/x"), {"kind": "attest", "workroot_id": "a" * 32}, b"", timeout=1)
