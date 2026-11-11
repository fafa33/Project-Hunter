"""Regression: the Trusted Orchestrator Replay sandbox provisioning step must fail closed, never
silently skip real isolation, and never retry the hosted package archive blindly.

Hosted evidence (PR #728, #729) showed `sudo apt-get install -y bubblewrap` failing repeatedly on
`Temporary failure resolving 'azure.archive.ubuntu.com'` -- a real, recurring runner-archive
flakiness, not a security or code defect. This asserts the provisioning step's actual text: it
prefers an already-provisioned `bwrap`, bounds (never blindly retries) the apt operations, fails
closed with an explicit, classifiable `INFRA_PROVISIONING_FAILURE` + distinct exit code on a real
provisioning failure, and -- most importantly -- never weakens, mocks, or skips the real sandbox
isolation test itself.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "hunter-trusted-orchestrator-replay-validate.yml"


def _provisioning_step() -> str:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["validate-replay"]["steps"]
    (step,) = (s for s in steps if s.get("name") == "Provision and verify trusted network namespace")
    return step["run"]


def test_a_an_already_provisioned_bwrap_skips_package_installation() -> None:
    script = _provisioning_step()
    assert "if [[ -x /usr/bin/bwrap ]]" in script
    assert "no package installation needed" in script


def test_b_missing_bwrap_installs_through_a_bounded_retry_configuration() -> None:
    script = _provisioning_step()
    assert "apt-get install -y bubblewrap" in script
    assert "Acquire::Retries=3" in script
    assert "Acquire::http::Timeout" in script and "Acquire::https::Timeout" in script


def test_c_an_unreachable_package_archive_fails_closed_explicitly() -> None:
    script = _provisioning_step()
    assert "apt-get update" in script
    assert script.count("INFRA_PROVISIONING_FAILURE") >= 1
    assert "could not reach the package archive" in script
    assert "exit 86" in script


def test_d_a_failed_package_installation_fails_closed_explicitly() -> None:
    script = _provisioning_step()
    assert "could not be installed" in script
    # Every INFRA_PROVISIONING_FAILURE branch must actually exit non-zero, not merely log.
    lines = script.splitlines()
    for index, line in enumerate(lines):
        if "INFRA_PROVISIONING_FAILURE" in line:
            following = "\n".join(lines[index : index + 3])
            assert "exit 86" in following, following


def test_e_the_real_sandbox_isolation_assertion_is_unchanged() -> None:
    """The actual security test: never mocked, skipped, or weakened by the provisioning rework."""
    script = _provisioning_step()
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--cap-drop ALL", "--new-session"):
        assert flag in script, flag
    assert "1.1.1.1" in script and "443" in script and "s.connect" in script
    assert "raise SystemExit(1)" in script  # connecting out is the failure condition, not the success one
    assert "test -x /usr/bin/bwrap" in script  # the binary's presence is still verified either way


def test_f_no_unsafe_fallback_and_no_silently_swallowed_sysctl_failure() -> None:
    """Issue #559-adjacent hosted evidence: a bare `|| true` on the sysctl toggle hid real failures."""
    script = _provisioning_step()
    assert "|| true" not in script
    assert "could not relax the unprivileged user namespace restriction" in script


def test_no_blind_unbounded_retry_loop_was_introduced() -> None:
    import re

    script = _provisioning_step()
    code_only = "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))
    assert re.search(r"\bwhile\b", code_only) is None
    assert re.search(r"\buntil\b", code_only) is None
    assert re.search(r"\bsleep\b", code_only) is None
