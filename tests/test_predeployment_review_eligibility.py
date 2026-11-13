"""Review requests are not final acceptance or candidate policy authority."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import hunter_connector_write_ingress as ingress
import hunter_pre_ready_review as review
import pytest

BASE, HEAD = "a" * 40, "b" * 40
CRITERIA = ("focused regressions pass", "production receipt is published")
PATH = "scripts/control.py"
WORKFLOW = ".github/workflows/production.yml"


def fixture(tmp_path, pr=738, issue="586"):
    root = tmp_path / "trusted"
    workflow = root / WORKFLOW
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("trusted-main-only producer\n")
    policy = {
        "schema": "hunter.predeployment-review-policy.v1",
        "repository": "fafa33/Project-Hunter",
        "authorized_by": "fafa33",
        "authorization": "owner-approved persistent policy",
        "review_only": True,
        "stop_work_issues": [603],
        "dependencies": [
            {
                "issue": issue,
                "criteria": [CRITERIA[1]],
                "changed_paths": [PATH],
                "trust_root": {"path": WORKFLOW, "sha256": hashlib.sha256(workflow.read_bytes()).hexdigest()},
            }
        ],
    }
    changes = (ingress.ConnectorFileChange("modified", PATH, "", "c" * 40),)
    claims = review.build_claims(
        issue=issue,
        base_ref="main",
        base_sha=BASE,
        changes=changes,
        acceptance_criteria=(
            {"id": "AC-1", "criterion": CRITERIA[0], "verdict": "satisfied", "evidence": "focused regression proof"},
            {
                "id": "AC-2",
                "criterion": CRITERIA[1],
                "verdict": "pending-deployment",
                "evidence": "trusted producer requires deployment",
            },
        ),
        defect_families=(),
        findings=(),
        adversarial_dimensions=review.REQUIRED_ADVERSARIAL_DIMENSIONS,
    )
    doc = review.document_for(claims)
    doc["review_request"] = {"schema": "hunter.review-request.v1", "claims_id": doc["review_id"]}
    doc["review_eligibility"] = {"mode": "pre_deployment", "repository": "fafa33/Project-Hunter", "pr_number": pr}
    facts = {
        "repository": "fafa33/Project-Hunter",
        "pr_number": pr,
        "head_sha": HEAD,
        "base_sha": BASE,
        "issue": issue,
        "preflight_state": "success",
        "ingress_state": "success",
        "closed_stop_work_issues": [603],
    }
    return root, policy, changes, doc, facts


def evaluate(root, policy, changes, doc, facts, **kwargs):
    return review.verify_review_request(
        doc,
        base_sha=BASE,
        changes=changes,
        families=(),
        issue_criteria=CRITERIA,
        head_sha=HEAD,
        eligibility_facts=facts,
        trusted_review_policy=policy,
        trusted_root=root,
        **kwargs,
    )


@pytest.mark.parametrize("pr,issue", [(738, "586"), (999, "900")])
def test_trusted_policy_allows_current_and_future_pr_without_accepting_pending_ac(tmp_path, pr, issue):
    root, policy, changes, doc, facts = fixture(tmp_path, pr, issue)
    decision = evaluate(root, policy, changes, doc, facts)
    assert decision.state == "predeployment-review-eligible"
    assert not decision.ok  # never final acceptance or merge authority
    assert doc["claims"]["acceptance_criteria"][1]["verdict"] == "pending-deployment"
    final = copy.deepcopy(doc)
    final.pop("review_request")
    assert not review.verify_claims(
        final,
        base_sha=BASE,
        changes=changes,
        families=(),
        issue_criteria=CRITERIA,
        head_sha=HEAD,
        require_authority=False,
    ).ok


def test_pending_request_can_be_prepared_without_self_authorizing(tmp_path):
    root, policy, changes, doc, facts = fixture(tmp_path)
    decision = review.verify_review_request(
        doc, base_sha=BASE, changes=changes, families=(), issue_criteria=CRITERIA, head_sha=HEAD
    )
    assert decision.state == "awaiting-trusted-review-policy"
    assert not decision.ok


def test_malformed_stop_work_closure_is_denied_without_crashing(tmp_path):
    root, policy, changes, doc, facts = fixture(tmp_path)
    facts["closed_stop_work_issues"] = [603, {}]
    assert evaluate(root, policy, changes, doc, facts).state == "awaiting-trusted-review-policy"


@pytest.mark.parametrize("scope", ["../scripts/control.py", "/scripts/control.py", "scripts/*.py"])
def test_dependency_scope_cannot_expand_to_unsafe_paths(tmp_path, scope):
    root, policy, changes, doc, facts = fixture(tmp_path)
    policy["dependencies"][0]["changed_paths"] = [PATH, scope]
    assert evaluate(root, policy, changes, doc, facts).state == "awaiting-trusted-review-policy"


def test_producer_symlink_cannot_impersonate_trusted_definition(tmp_path):
    root, policy, changes, doc, facts = fixture(tmp_path)
    outside = tmp_path / "producer.yml"
    outside.write_bytes((root / WORKFLOW).read_bytes())
    (root / WORKFLOW).unlink()
    (root / WORKFLOW).symlink_to(outside)
    assert evaluate(root, policy, changes, doc, facts).state == "awaiting-trusted-review-policy"


@pytest.mark.parametrize(
    "bad",
    [
        "unrelated",
        "missing_policy",
        "wrong_repo",
        "wrong_pr",
        "head",
        "base",
        "preflight",
        "scope",
        "stop_work",
        "trust_root",
        "paths",
        "missing_issue",
        "invalid_criteria",
        "candidate_policy",
        "changed_content",
        "generic_pending",
    ],
)
def test_unverified_or_foreign_review_eligibility_fails_closed(tmp_path, bad):
    root, policy, changes, doc, facts = fixture(tmp_path)
    if bad == "unrelated":
        policy["dependencies"][0]["criteria"] = ["some unrelated criterion"]
    elif bad in {"missing_policy", "candidate_policy"}:
        doc["candidate_policy"] = copy.deepcopy(policy)
        policy = None
    elif bad == "wrong_repo":
        facts["repository"] = "attacker/repository"
    elif bad == "wrong_pr":
        facts["pr_number"] = 739
    elif bad in {"head", "base"}:
        facts[bad + "_sha"] = "d" * 40
    elif bad in {"preflight", "scope"}:
        facts["preflight_state" if bad == "preflight" else "ingress_state"] = "pending"
    elif bad == "stop_work":
        facts["closed_stop_work_issues"] = []
    elif bad == "trust_root":
        (root / WORKFLOW).write_text("changed authority\n")
    elif bad == "paths":
        policy["dependencies"][0]["changed_paths"] = ["scripts/unrelated.py"]
    elif bad == "missing_issue":
        facts["issue"] = ""
    elif bad in {"invalid_criteria", "generic_pending"}:
        doc["claims"]["acceptance_criteria"][1]["verdict"] = "unknown" if bad == "invalid_criteria" else "pending"
        doc["review_id"] = review.review_id(doc["claims"])
        doc["review_request"]["claims_id"] = doc["review_id"]
    else:
        doc["claims"]["review_target_digest"] = "0" * 64
        doc["review_id"] = review.review_id(doc["claims"])
        doc["review_request"]["claims_id"] = doc["review_id"]
    assert evaluate(root, policy, changes, doc, facts).state != "predeployment-review-eligible"


def test_normal_complete_request_path_is_unchanged(tmp_path):
    root, policy, changes, doc, facts = fixture(tmp_path)
    doc.pop("review_eligibility")
    doc["claims"]["acceptance_criteria"][1]["verdict"] = "satisfied"
    doc["review_id"] = review.review_id(doc["claims"])
    doc["review_request"]["claims_id"] = doc["review_id"]
    assert review.verify_review_request(
        doc, base_sha=BASE, changes=changes, families=(), issue_criteria=CRITERIA, head_sha=HEAD
    ).ok


def test_request_without_satisfied_or_deployment_pending_criteria_is_incomplete(tmp_path):
    _root, _policy, changes, doc, _facts = fixture(tmp_path)
    for criterion in doc["claims"]["acceptance_criteria"]:
        criterion["verdict"] = "not-applicable"
    doc["review_id"] = review.review_id(doc["claims"])
    doc["review_request"]["claims_id"] = doc["review_id"]
    decision = review.verify_review_request(
        doc,
        base_sha=BASE,
        changes=changes,
        families=(),
        issue_criteria=CRITERIA,
        head_sha=HEAD,
    )
    assert decision.state == "incomplete"
    assert not decision.ok


@pytest.mark.parametrize(
    "case",
    ["eligible", "head-race", "base-race", "stop-open", "candidate-only", "scope-denied", "missing-issue", "fork"],
)
def test_canonical_governance_derives_eligibility_from_trusted_state(tmp_path, monkeypatch, case):
    import hunter_governance_review_v2 as gov

    root, policy, changes, doc, _facts = fixture(tmp_path)
    policy_path = root / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "review_progression": {
                    "review_authority": {
                        "automatic_predeployment_review": policy if case != "candidate-only" else None,
                    }
                }
            }
        )
    )
    doc["candidate_policy"] = policy
    monkeypatch.setattr(review, "ROOT", root)
    monkeypatch.setattr(review, "CODE_WRITE_POLICY_PATH", policy_path)
    monkeypatch.setattr(gov, "read_head_pre_ready_review", lambda *_: ("present", doc, None))
    monkeypatch.setattr(gov, "read_pr_refs", lambda *_: (True, "fix/586-repair", "main", None))
    monkeypatch.setattr(gov, "read_merge_base", lambda *_: (True, BASE, None))
    files = [
        SimpleNamespace(status=c.status, path=c.path, previous_path=c.previous_path, blob_sha=c.blob_sha)
        for c in changes
    ]
    monkeypatch.setattr(gov, "read_pr_changed_files", lambda *_: (True, files, None))
    monkeypatch.setattr(
        gov, "read_issue_acceptance_criteria", lambda *_: ("present", () if case == "missing-issue" else CRITERIA, "")
    )
    monkeypatch.setattr(review, "load_families", lambda: ((), ""))
    monkeypatch.setattr(review, "load_coverage_scopes", lambda: ((), ""))
    monkeypatch.setattr(gov, "read_trusted_upgrade_status", lambda *_: ("success", "proof"))
    monkeypatch.setattr(
        gov,
        "verify_code_write_ingress_provenance",
        lambda *_: ("failure" if case == "scope-denied" else "success", "writer proof"),
    )
    reads = []

    def request(_repo, _token, _method, path, **kwargs):
        if path == "issues/603":
            assert kwargs == {"diagnostic_operation": "GET security STOP-WORK Issue state"}
            return {"state": "open" if case == "stop-open" else "closed", "closed_by": {"login": "fafa33"}}
        assert path == "pulls/738"
        reads.append(path)
        return {
            "number": 738,
            "head": {
                "sha": "d" * 40 if len(reads) > 1 and case == "head-race" else HEAD,
                "ref": "fix/586-repair",
                "repo": {"id": 2 if case == "fork" else 1},
            },
            "base": {
                "sha": "e" * 40 if len(reads) > 1 and case == "base-race" else BASE,
                "repo": {"id": 1, "full_name": "fafa33/Project-Hunter", "default_branch": "main"},
            },
        }

    monkeypatch.setattr(gov, "request_json", request)
    valid, reason = gov.valid_current_review_request("fafa33/Project-Hunter", "test-token", 738, HEAD, doc)
    assert valid is (case == "eligible"), reason


def test_writer_regeneration_is_deterministic_and_retains_unresolved_findings(tmp_path, monkeypatch):
    root, _policy, changes, doc, _facts = fixture(tmp_path)
    prior = copy.deepcopy(doc)
    finding = {
        "id": "prior-blocker",
        "severity": "blocking",
        "resolution": "unresolved",
        "evidence": "authenticated prior finding",
    }
    prior["claims"]["findings"] = [finding]
    target = root / review.REVIEW_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(prior))
    monkeypatch.setattr(review, "local_changes", lambda *_a, **_k: changes)
    judgement = {k: doc["claims"][k] for k in review.REQUEST_JUDGEMENT_KEYS}
    judgement["review_eligibility"] = doc["review_eligibility"]
    kwargs = dict(issue="586", base=BASE, head=HEAD, base_ref="main", judgement=judgement, cwd=root)
    rebuilt = review.prepare_request(**kwargs)
    assert rebuilt["claims"]["findings"] == [finding]
    assert review.prepare_request(**kwargs) == rebuilt
    assert "authority" not in rebuilt
    monkeypatch.setattr(
        review, "local_changes", lambda *_a, **_k: (ingress.ConnectorFileChange("modified", PATH, "", "f" * 40),)
    )
    changed = review.prepare_request(**kwargs)
    assert changed["review_id"] != rebuilt["review_id"]
    assert changed["claims"]["findings"] == [finding]


@pytest.mark.parametrize("operation", [None, "GET security STOP-WORK Issue state"])
def test_stop_work_lookup_retries_without_logging_policy_derived_endpoint(monkeypatch, capsys, operation):
    import hunter_github_transport as transport
    import hunter_governance_review_v2 as gov

    attempts = []

    def rest(**kwargs):
        attempts.append(kwargs["url"])
        if len(attempts) == 1:
            raise transport.GitHubRequestError("temporary upstream failure", category="transient", retry_after=0)
        return {"state": "closed"}

    monkeypatch.setattr(transport, "rest_json", rest)
    result = gov.request_json(
        "fafa33/Project-Hunter",
        "credential-must-not-be-logged",
        "GET",
        "issues/603",
        diagnostic_operation=operation,
    )
    assert result == {"state": "closed"}
    assert attempts == ["https://api.github.com/repos/fafa33/Project-Hunter/issues/603"] * 2
    diagnostics = capsys.readouterr().out
    assert (operation or "GET repository API request") in diagnostics
    assert "retrying" in diagnostics
    assert "issues/603" not in diagnostics
    assert "credential-must-not-be-logged" not in diagnostics


@pytest.mark.parametrize("pr", [738, 9001])
def test_proposed_standing_policy_reviews_future_matching_prs_without_final_acceptance(tmp_path, pr):
    """The inert deployment patch must work without a per-PR authorization record."""
    base = "08f565fd14e9ded796a63c9fc45dce6f8997aeff"
    workflow = ".github/workflows/hunter-issue-agent-knowledge.yml"
    root = Path(review.__file__).resolve().parents[1]
    for path in ("docs/CODE_WRITE_POLICY.json", workflow):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output(["git", "show", f"{base}:{path}"], cwd=root))
    patch = root / "docs/ISSUE_586_PERMANENT_REVIEW_POLICY.patch"
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True, capture_output=True)
    proposed = json.loads((tmp_path / "docs/CODE_WRITE_POLICY.json").read_text())
    policy = proposed["review_progression"]["review_authority"]["automatic_predeployment_review"]
    dependency = policy["dependencies"][0]
    criteria = tuple(dependency["criteria"])
    changes = (ingress.ConnectorFileChange("modified", dependency["changed_paths"][0], "", "c" * 40),)
    claims = review.build_claims(
        issue="586",
        base_ref="main",
        base_sha=BASE,
        changes=changes,
        acceptance_criteria=tuple(
            {
                "id": f"AC-{i}",
                "criterion": c,
                "verdict": "pending-deployment",
                "evidence": "deployment proof remains outstanding",
            }
            for i, c in enumerate(criteria, 1)
        ),
        defect_families=(),
        findings=(),
        adversarial_dimensions=review.REQUIRED_ADVERSARIAL_DIMENSIONS,
    )
    doc = review.document_for(
        claims, review_eligibility={"mode": "pre_deployment", "repository": policy["repository"], "pr_number": pr}
    )
    doc["review_request"] = {"schema": "hunter.review-request.v1", "claims_id": doc["review_id"]}
    facts = {
        "repository": policy["repository"],
        "pr_number": pr,
        "issue": "586",
        "head_sha": HEAD,
        "base_sha": BASE,
        "preflight_state": "success",
        "ingress_state": "success",
        "closed_stop_work_issues": [603],
    }
    kwargs = dict(base_sha=BASE, changes=changes, families=(), issue_criteria=criteria, head_sha=HEAD)
    # Candidate-side copy is not authority, even when the proposed policy is valid.
    doc["candidate_policy"] = policy
    assert (
        review.verify_review_request(doc, **kwargs, eligibility_facts=facts).state == "awaiting-trusted-review-policy"
    )
    assert (
        review.verify_review_request(
            doc, **kwargs, eligibility_facts=facts, trusted_review_policy=policy, trusted_root=tmp_path
        ).state
        == "predeployment-review-eligible"
    )
    facts["closed_stop_work_issues"] = []
    assert (
        review.verify_review_request(
            doc, **kwargs, eligibility_facts=facts, trusted_review_policy=policy, trusted_root=tmp_path
        ).state
        == "awaiting-trusted-review-policy"
    )
    doc.pop("review_request")
    assert not review.verify_claims(doc, **kwargs, require_authority=False).ok
