from pathlib import Path


def test_trusted_black_upgrade_bootstrap_is_explicit_and_fail_closed() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hunter-trusted-preflight-upgrade.yml"
    ).read_text(encoding="utf-8")

    assert "Select allowlisted formatter for candidate dependency upgrades" in workflow
    assert "candidate/requirements/ci-constraints.txt" in workflow
    assert 'candidate_black}" == "26.3.1"' in workflow
    assert "not an approved trusted-upgrade bootstrap version" in workflow
    assert 'python -m pip install --disable-pip-version-check "black==${candidate_black}"' in workflow
    assert "python -m black --version" in workflow
