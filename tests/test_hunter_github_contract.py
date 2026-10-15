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


def test_trusted_publisher_checks_contract_even_without_local_hook():
    source = (Path(__file__).resolve().parents[1] / "scripts/hunter_issue_agent_lifecycle.py").read_text()
    publisher = source.split("def cmd_publish(", 1)[1].split("\ndef cmd_candidate_gate(", 1)[0]
    assert "observe_contract(" in publisher
    assert "PUBLICATION_UNAVAILABLE" in publisher
    assert publisher.index("observe_contract(") < publisher.index("roles.run_publisher(")


def test_required_check_identity_and_success():
    contract = {
        "rulesets": [
            {
                "rules": [
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "required_status_checks": [{"context": "Quality Gates", "integration_id": 15368}]
                        },
                    }
                ]
            }
        ]
    }
    valid = [{"id": 7, "name": "Quality Gates", "status": "completed", "conclusion": "success", "app": {"id": 15368}}]
    assert hgcc.verify_required_checks(contract, valid)[0]
    assert not hgcc.verify_required_checks(contract, [{**valid[0], "app": {"id": 42}}])[0]
    assert not hgcc.verify_required_checks(contract, [{**valid[0], "conclusion": "failure"}])[0]
    assert not hgcc.verify_required_checks(contract, [valid[0], {**valid[0], "id": 8, "conclusion": "failure"}])[0]


def test_conflicting_required_check_publishers_rejected():
    contract = {
        "rulesets": [
            {
                "rules": [
                    {
                        "type": "required_status_checks",
                        "parameters": {
                            "required_status_checks": [
                                {"context": "CodeQL", "integration_id": 1},
                                {"context": "CodeQL", "integration_id": 2},
                            ]
                        },
                    }
                ]
            }
        ]
    }
    with pytest.raises(ValueError):
        hgcc.required_checks(contract)


def test_owner_merge_boundary_consumes_live_hgcc():
    source = (Path(__file__).resolve().parents[1] / "scripts/hunter_merge_readiness_v2.py").read_text()
    owner_guard = source.split("def owner_merge_guard(", 1)[1].split("\ndef main(", 1)[0]
    assert "observe_contract(" in owner_guard
    assert "verify_required_checks(contract, all_check_runs(pr_head))" in owner_guard
    assert owner_guard.index("verify_required_checks(") < owner_guard.index("decide_completion(")


def test_hosted_readiness_never_publishes_success_without_hgcc(monkeypatch):
    import hunter_merge_readiness_v2 as readiness

    published = []
    monkeypatch.setattr(
        readiness, "_hgcc_hosted_verdict", lambda _sha: readiness.Decision("pending", "HGCC-1 unavailable")
    )
    monkeypatch.setattr(readiness, "request_json", lambda method, path, payload=None: published.append(payload))
    readiness.publish("a" * 40, readiness.Decision("success", "ready"))
    assert len(published) == 1
    assert published[0]["context"] == readiness.CONTEXT
    assert published[0]["state"] == "pending"


def test_hosted_readiness_allows_existing_success_with_valid_hgcc(monkeypatch):
    import hunter_merge_readiness_v2 as readiness

    published = []
    monkeypatch.setattr(readiness, "_hgcc_hosted_verdict", lambda _sha: None)
    monkeypatch.setattr(readiness, "request_json", lambda method, path, payload=None: published.append(payload))
    readiness.publish("a" * 40, readiness.Decision("success", "ready"))
    assert published[0]["state"] == "success"
