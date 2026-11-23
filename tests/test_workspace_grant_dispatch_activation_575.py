from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _load_lifecycle():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    path = root / "scripts" / "hunter_issue_agent_lifecycle.py"
    spec = importlib.util.spec_from_file_location("hunter_issue_agent_lifecycle_575_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_authorize_prepare_fails_closed_without_checkpoint(tmp_path, monkeypatch):
    lc = _load_lifecycle()
    key = Ed25519PrivateKey.generate()
    key_hex = key.private_bytes_raw().hex()
    monkeypatch.setenv("HUNTER_WORKSPACE_CHECKPOINT", "")
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY", key_hex)
    monkeypatch.setattr(lc, "_run_context", lambda: (1, 1, "1" * 40))
    monkeypatch.setattr(lc, "_timestamp", lambda: "2026-01-01T00:00:00Z")
    # cmd_authorize_prepare takes (configuration, arguments) and will check checkpoint early enough?
    # It does _run_context(), parses args/document, builds document, then reaches checkpoint check.
    # We can make it reach checkpoint by mocking heavy deps after checkpoint? Easier: monkeypatch trigger and authorize.
    import types

    fake_trigger = types.ModuleType("hunter_issue_agent_trigger")
    fake_trigger.SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY"
    fake_trigger.authorize_event = lambda *a, **k: SimpleNamespace(issue_number=575)
    fake_trigger.sign_authorization = lambda *a, **k: SimpleNamespace(to_json=lambda: "{}")
    fake_trigger.load_signing_key = lambda *_a, **_k: key
    monkeypatch.setitem(__import__("sys").modules, "hunter_issue_agent_trigger", fake_trigger)
    monkeypatch.setattr(lc, "_export_public_trust", lambda *_a, **_k: None)
    monkeypatch.setattr(lc, "_github", lambda *_a, **_k: object())
    monkeypatch.setattr(lc, "_require_anchors", lambda *_a, **_k: None)
    monkeypatch.setattr(lc, "_authorize_dependencies", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(lc, "_store", lambda *_a, **_k: object())
    prepared = SimpleNamespace(authorization_id="auth:1", issue_number=575, to_json=lambda: "{}")
    monkeypatch.setattr(lc.authorize, "prepare", lambda *a, **k: (prepared, b"sealed"))
    config = SimpleNamespace(repository="fafa33/Project-Hunter", owner_login="fafa33")
    args2 = SimpleNamespace(event=str(tmp_path / "event.json"), document=None, out_dir=str(tmp_path / "out2"))
    (tmp_path / "event.json").write_text("{}", encoding="utf-8")
    with pytest.raises(lc.LifecycleRefused) as exc:
        lc.cmd_authorize_prepare(config, args2)
    assert "HUNTER_WORKSPACE_CHECKPOINT" in str(exc.value)
