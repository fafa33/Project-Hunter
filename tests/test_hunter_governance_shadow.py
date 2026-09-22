from pathlib import Path

import hunter_governance_shadow as shadow


def test_shadow_projection_never_claims_semantic_parity():
    p = shadow.project({"head_sha": "abc", "draft": True, "mergeable": None})
    assert set(p) == set(shadow.DOMAINS)


def test_shadow_workflow_has_no_write_permission():
    text = Path(".github/workflows/hunter-governance-shadow.yml").read_text()
    assert ": write" not in text
    assert "statuses: read" in text


def test_shadow_script_has_no_mutating_rest_method():
    text = Path("scripts/hunter_governance_shadow.py").read_text()
    assert 'method="POST"' not in text
    assert 'method="PATCH"' not in text
    assert 'method="PUT"' not in text
    assert 'method="DELETE"' not in text
