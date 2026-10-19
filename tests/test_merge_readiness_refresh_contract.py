"""Readiness refresh contract: bounded sweep wakeups, identity-only dispatch, no privileged recovery path.

Tracks the architecture merged through PR #615 (main): merge readiness wakes on
`pull_request_target`, trusted `workflow_run` completions, a 5-minute sweep and a
dispatch that carries PR identity only. Nothing here may be fixed by granting a
workflow more authority just to shorten a status delay, and no recovery path may
select a ref or accept a caller-authored payload.
"""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(document):
    return document.get(True) or document.get("on")  # YAML 1.1 reads the bare key `on` as True


def test_merge_readiness_wakes_on_trusted_events_with_identity_only_dispatch():
    on = triggers(load("hunter-merge-readiness.yml"))
    assert "pull_request_target" in on and "workflow_run" in on
    # The same completion-by-completion wakeup list the M1 contract documents.
    assert set(on["workflow_run"]["workflows"]) == {
        "CI",
        "Dependency review",
        "Hunter Governance Review Reconcile",
        "Hunter / Pre-PR Preflight",
        "Hunter / Trusted Preflight Upgrade",
        "CodeQL default setup bridge",
    }
    assert on["schedule"] == [{"cron": "*/5 * * * *"}]
    # Recovery is the sweep plus the default-branch reconcile workflow. The manual
    # wake carries PR identity only -- no ref selection -- and no
    # `repository_dispatch` payload path exists for any caller to author.
    assert "repository_dispatch" not in on
    assert set((on.get("workflow_dispatch") or {}).get("inputs") or {}) == {"pr_number"}
    reconcile = WORKFLOWS / "hunter-governance-reconcile.yml"
    assert reconcile.exists()
    # Reconcile is the privileged wake path, and it must never run the
    # candidate's own definition.
    assert "pull_request" not in triggers(load(reconcile.name))


def test_merge_readiness_controller_holds_no_write_access_beyond_its_status():
    permissions = load("hunter-merge-readiness.yml")["permissions"]
    assert permissions["statuses"] == "write"
    assert permissions.get("contents") == "read"
    assert "actions" not in permissions or permissions["actions"] == "read"


def test_resolving_a_finding_neither_dispatches_nor_gains_actions_write():
    jobs = load("hunter-issue-agent-reconcile.yml")["jobs"]
    assert "actions" not in jobs["resolve-finding"]["permissions"]
    source = (ROOT / "scripts/hunter_issue_agent_lifecycle.py").read_text(encoding="utf-8")
    resolve = source.split("def cmd_resolve", 1)[1].split("\ndef cmd_resume_bind", 1)[0]
    code = "\n".join(line for line in resolve.splitlines() if not line.strip().startswith("#"))
    assert "dispatches" not in code and "workflow_run" not in code and "workflow run" not in code
    assert "merge readiness refresh deferred to trusted scheduled sweep" in code


def test_the_latency_and_recovery_are_documented_as_a_bounded_sweep():
    """The sweep's bounded role and the recovery posture must stay written down.

    The audit doc is the M1 contract the guards read, so the two claims that make
    the design safe are asserted here against both the doc and the live workflow:
    the cron is recovery, never a freshness guarantee, and recovery fails closed
    rather than escalating privilege or dispatching a ref.
    """
    audit = (ROOT / "docs/ARCHITECTURE_AUDITS/HUNTER_FAST_MERGE_M1.md").read_text(encoding="utf-8")
    on = triggers(load("hunter-merge-readiness.yml"))
    assert "the cron is a recovery path, not a freshness guarantee" in audit
    assert on["schedule"] == [{"cron": "*/5 * * * *"}]
    assert "with PR number" in audit
    assert set((on.get("workflow_dispatch") or {}).get("inputs") or {}) == {"pr_number"}
    assert "fail closed" in audit and "fail-closed rollback" in audit
    # Recovery is never a privileged re-dispatch path: no named recovery workflow
    # exists, and none may be introduced by documentation alone.
    assert "hunter-trusted-recovery" not in audit
    assert not (WORKFLOWS / "hunter-trusted-recovery.yml").exists()


def test_no_hunter_workflow_accepts_a_dispatch_payload_or_selects_a_ref():
    """No caller-authored event, and no dispatch input that picks what gets run.

    `repository_dispatch` bodies are caller-authored execution payloads, and a
    dispatch input that selects a ref hands the run definition to whoever called
    it. Both are absent across every Hunter workflow: wakeups are GitHub events or
    a dispatch carrying PR identity only.
    """
    selectors = {"ref", "branch", "revision", "head_ref", "git_ref", "target_ref"}
    for path in sorted(WORKFLOWS.glob("hunter-*.yml")):
        on = triggers(yaml.safe_load(path.read_text(encoding="utf-8"))) or {}
        assert "repository_dispatch" not in on, path.name
        if "workflow_dispatch" not in on:
            continue
        block = on.get("workflow_dispatch") or {}
        inputs = set(block.get("inputs") or {}) if isinstance(block, dict) else set()
        assert not (inputs & selectors), path.name
