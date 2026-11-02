"""Ensure the Mac executor prerequisite checker fails closed."""

from pathlib import Path
from types import SimpleNamespace

from scripts import hunter_mac_codex_preflight as preflight


def test_missing_user_fails_closed(monkeypatch, tmp_path: Path):
    def missing(_user):
        raise KeyError

    monkeypatch.setattr(preflight.pwd, "getpwnam", missing)
    assert preflight.verify_principal("missing", current_uid=501, owner_home=tmp_path) == ["ISOLATION_ACCOUNT_MISSING"]


def test_same_uid_and_traversable_home_fail(monkeypatch, tmp_path: Path):
    home = tmp_path / "agent"
    home.mkdir()
    owner = tmp_path / "owner"
    owner.mkdir(mode=0o755)
    owner.chmod(0o755)
    monkeypatch.setattr(preflight.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=501, pw_dir=str(home)))
    assert preflight.verify_principal("agent", current_uid=501, owner_home=owner) == [
        "ISOLATION_UID_NOT_DISTINCT",
        "OWNER_HOME_TRAVERSABLE",
    ]


def test_private_owner_home_does_not_claim_full_isolation(monkeypatch, tmp_path: Path):
    home = tmp_path / "agent"
    home.mkdir()
    owner = tmp_path / "owner"
    owner.mkdir()
    owner.chmod(0o700)
    monkeypatch.setattr(preflight.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=502, pw_dir=str(home)))
    assert preflight.verify_principal("agent", current_uid=501, owner_home=owner) == []
