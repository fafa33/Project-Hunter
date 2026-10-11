"""Accepted design: readiness refresh latency is bounded by a scheduled sweep, not by privilege.

Owner decision (#592): keep the lower-privilege design. Nothing here may be fixed by
granting a workflow more authority just to shorten a status delay.
"""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(document):
    return document.get(True) or document.get("on")  # YAML 1.1 reads the bare key `on` as True


def test_both_controllers_have_the_scheduled_sweep_and_the_trusted_recovery_wakeup():
    for name in ("hunter-merge-readiness.yml", "hunter-governance-reconcile.yml"):
        on = triggers(load(name))
        assert on["schedule"] == [{"cron": "*/5 * * * *"}], name
        assert on["repository_dispatch"]["types"] == ["hunter-trusted-recovery"], name
        assert "workflow_dispatch" not in on, name  # a caller-selected ref is not a trust boundary


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


def test_the_latency_is_documented_as_best_effort_not_as_five_minutes():
    audit = (ROOT / "docs/ARCHITECTURE_AUDITS/HUNTER_FAST_MERGE_M1.md").read_text(encoding="utf-8")
    assert "not a freshness guarantee" in audit
    assert "Accepted refresh latency" in audit and "hunter-trusted-recovery" in audit
    reconcile = (WORKFLOWS / "hunter-governance-reconcile.yml").read_text(encoding="utf-8")
    assert "not a freshness guarantee" in reconcile
