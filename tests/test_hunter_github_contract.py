"""HGCC-1 read-only contract observation and shared agent push seam."""

from pathlib import Path

import hunter_github_contract as hgcc
import pytest


def test_observes_active_rulesets_and_required_checks():
    def get(path):
        if "?includes_parents=true" in path:
            return [{"id": 1, "enforcement": "active"}, {"id": 2, "enforcement": "disabled"}]
        if path.endswith("/1"):
            return {
                "name": "Protect main",
                "bypass_actors": [],
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


def test_hgcc_remains_at_pre_push_boundary_not_hosted_readiness():
    root = Path(__file__).resolve().parents[1]
    pre_push = (root / "scripts/hunter_pre_push.py").read_text()
    readiness = (root / "scripts/hunter_merge_readiness_v2.py").read_text()
    assert '"scripts/hunter_github_contract.py"' in pre_push
    assert "_hgcc_hosted_verdict" not in readiness
    assert "observe_contract(" not in readiness


def test_ruleset_inventory_paginates_and_includes_later_required_checks():
    calls = []

    def get(path):
        calls.append(path)
        if "&page=1" in path:
            return [{"id": i, "enforcement": "disabled"} for i in range(1, 101)]
        if "&page=2" in path:
            return [{"id": 101, "enforcement": "active"}]
        if path.endswith("/101"):
            return {
                "name": "later",
                "bypass_actors": [],
                "target": "branch",
                "conditions": {"ref_name": {"include": ["main"], "exclude": []}},
                "rules": [
                    {
                        "type": "required_status_checks",
                        "parameters": {"required_status_checks": [{"context": "Late Check"}]},
                    }
                ],
            }
        raise AssertionError(path)

    contract = hgcc.observe_contract("fafa33/Project-Hunter", get=get)
    assert "page=2" in calls[1]
    assert "Late Check" in hgcc.required_checks(contract, target_ref="refs/heads/main")


@pytest.mark.parametrize("selector", ["main", "refs/heads/main", "~DEFAULT_BRANCH"])
def test_main_ruleset_selectors_are_normalized(selector):
    rule = {
        "target": "branch",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [selector], "exclude": []}},
        "rules": [
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "main-only"}]}}
        ],
    }
    assert "main-only" in hgcc.required_checks({"rulesets": [rule]}, target_ref="refs/heads/main")
    assert hgcc.required_checks({"rulesets": [rule]}, target_ref="refs/heads/release") == {}


def test_main_exclusion_selectors_are_honored():
    rule = {
        "target": "branch",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~ALL"], "exclude": ["~DEFAULT_BRANCH"]}},
        "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "other"}]}}],
    }
    assert hgcc.required_checks({"rulesets": [rule]}, target_ref="refs/heads/main") == {}
    assert "other" in hgcc.required_checks({"rulesets": [rule]}, target_ref="refs/heads/release")


def test_cli_ruleset_read_refuses_anonymous_session(monkeypatch):
    from subprocess import CompletedProcess

    calls = []

    def run(command, **kwargs):
        calls.append(tuple(command))
        return CompletedProcess(command, 1, "", "not authenticated")

    monkeypatch.setattr(hgcc.subprocess, "run", run)
    with pytest.raises(ValueError, match="Authenticated GitHub"):
        hgcc.github_get("repos/fafa33/Project-Hunter/rulesets")
    assert calls == [("gh", "auth", "token")]


def test_excluding_only_required_context_fails_closed():
    contract = {
        "rulesets": [
            {
                "rules": [
                    {
                        "type": "required_status_checks",
                        "parameters": {"required_status_checks": [{"context": "Hunter Merge Readiness"}]},
                    }
                ]
            }
        ]
    }
    ok, reason = hgcc.verify_required_checks(
        contract,
        [{"id": 1, "name": "Hunter Merge Readiness", "status": "completed", "conclusion": "success"}],
        exclude_context="Hunter Merge Readiness",
    )
    assert not ok
    assert "No independent" in reason


@pytest.mark.parametrize("bypass", [None, [{"actor_id": 1}], "invalid"])
def test_applicable_main_ruleset_requires_no_bypass_actors(bypass):
    ruleset = {
        "target": "branch",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": [
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Quality Gates"}]}}
        ],
    }
    if bypass is not None:
        ruleset["bypass_actors"] = bypass
    with pytest.raises(ValueError, match="bypass actors"):
        hgcc.required_checks({"rulesets": [ruleset]}, target_ref="refs/heads/main")
