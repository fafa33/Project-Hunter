"""Runner's Kilo Isolation port must fail closed on wrong runtime or workspace."""

from pathlib import Path

import pytest

from hunter.automation.issue_agent_kilo_container import KiloContainerIsolation
from hunter.automation.issue_agent_roles import RoleRefused, require_governed_kilo_isolation


def test_kilo_requires_exact_container_port(tmp_path: Path) -> None:
    isolation = KiloContainerIsolation(Path(__file__).resolve().parents[1] / "scripts/issue_agent")
    require_governed_kilo_isolation(("kilo", "run", "--model", "kilo/kilo-auto/free"), isolation)
    with pytest.raises(RoleRefused, match="not prepared"):
        isolation.run(("kilo", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)


def test_wrong_isolation_refused() -> None:
    with pytest.raises(RoleRefused, match="governed container"):
        require_governed_kilo_isolation(("kilo", "run", "--model", "free"), object())


def test_unrelated_provider_keeps_existing_port() -> None:
    require_governed_kilo_isolation(("opencode", "run"), object())
