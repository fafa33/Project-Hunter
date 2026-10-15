"""HGCC-1 read-only contract observation and shared agent push seam."""

from pathlib import Path

import pytest

from scripts import hunter_github_contract as hgcc


def test_observes_active_rulesets_and_required_checks():
    def get(path):
        if "?includes_parents=true" in path:
            return [{"id": 1, "enforcement": "active"}, {"id": 2, "enforcement": "disabled"}]
        if path.endswith("/1"):
            return {
                "name": "Protect main",
                "rules": [
                    {
                        "type": "required_status_checks",
                        "parameters": {"required_status_checks": [{"context": "Quality Gates"}]},
                    }
                ],
            }
        raise AssertionError(path)

    contract = hgcc.observe_contract("fafa33/Project-Hunter", get=get)
    assert contract["rulesets"][0]["rules"][0]["parameters"]["required_status_checks"][0]["context"] == "Quality Gates"
    assert len(contract["rulesets"]) == 1


@pytest.mark.parametrize("response", [{}, None, "bad", []])
def test_fails_closed_on_missing_rulesets(response):
    with pytest.raises(ValueError):
        hgcc.observe_contract("fafa33/Project-Hunter", get=lambda _path: response)


def test_every_agent_push_uses_same_contract_boundary():
    script = (Path(__file__).resolve().parents[1] / "scripts/hunter_pre_push.py").read_text()
    assert '"scripts/hunter_github_contract.py"' in script
    assert "contract_check.returncode" in script
    assert "HGCC-1 contract unavailable" in script
