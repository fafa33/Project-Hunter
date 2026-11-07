"""ADR 0041 × ADR 0039: a Codex fallback result rejoins the single canonical lifecycle (demonstrated-gap regressions).

The fallback adds an *executor*, never a second validator, publisher, proof authority or learning path. These tests
pin the two links nothing else pinned:

1. In the owner-approved fallback resume run, the bound fallback result continues through the same single
   ``validate`` → ``record-validation`` → ``publish`` → ``finalize`` jobs the primary uses, and nothing downstream
   runs when no fallback result was bound. The workflow's own ``if:`` expressions are evaluated, not grepped.
2. A fallback lineage (here, the F8 canary's) verifies on the signed ledger through ``VALIDATED`` → ``PUBLISHED`` →
   ``COMPLETED``. Publication binds the fallback's own execution identity, never the primary's. The completed
   authorization keeps its bound execution branch, which is exactly what ADR 0039 L4 remediation, and therefore
   L5/L6 learning, require of a parent (``issue_agent_authorize.prepare``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_issue_agent_fallback_canary import canary_admission, canary_offer
from test_issue_agent_fallback_ledger import FALLBACK_ID, fallback_bound_evidence
from test_issue_agent_state import AUTH, ISSUE, REPO, Chain, _by, reject, validated_evidence

from hunter.automation import issue_agent_state as state

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/hunter-issue-agent-trigger.yml"
_REFERENCE = re.compile(r"\b(?:needs|inputs|github)(?:\.[A-Za-z0-9_-]+)+")


def evaluate(expression: str, context: dict[str, str]) -> bool:
    """Evaluate a GitHub Actions ``if:`` of the forms this workflow uses (``&&``, ``||``, ``==``, ``!=``, ``always()``).

    An unknown reference evaluates to the empty string, as GitHub does for a missing output.
    """

    python = _REFERENCE.sub(lambda m: repr(context.get(m.group(0), "")), expression)
    python = python.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    python = python.replace("!", " not ").replace(" not =", " !=")
    python = " ".join(python.split())  # folded YAML keeps line breaks and indentation
    if re.search(r"[A-Za-z_]+\(", python):
        raise AssertionError(f"unsupported expression: {expression}")
    return bool(eval(python, {"__builtins__": {}}, {}))  # noqa: S307 - the repository's own workflow text


def fallback_run(*, bound: bool, results: dict[str, str] | None = None) -> dict[str, str]:
    """The owner-approved fallback resume dispatch, with the outcomes of the jobs that ran before."""

    context = {
        "github.event_name": "workflow_dispatch",
        "inputs.stage": "fallback",
        "inputs.remediation_authorization": "",
        "needs.authorize.result": "skipped",
        "needs.bind.result": "skipped",
        "needs.execute.result": "skipped",
        "needs.resume-bind.result": "success",
        "needs.execute-fallback.result": "success",
        "needs.bind-fallback.result": "success",
        "needs.bind-fallback.outputs.result_bound": "true" if bound else "false",
    }
    context.update(results or {})
    return context


def runs(job: str, context: dict[str, str]) -> bool:
    definition = yaml.safe_load(WORKFLOW.read_text())["jobs"][job]
    if "if" not in definition:  # GitHub's implicit success(): every needed job succeeded
        needs = definition.get("needs") or []
        return all(
            context.get(f"needs.{need}.result") == "success" for need in ([needs] if isinstance(needs, str) else needs)
        )
    return evaluate(str(definition["if"]), context)


def test_a_bound_fallback_result_continues_through_the_single_canonical_chain() -> None:
    context = fallback_run(bound=True)
    assert runs("execute-fallback", context) and runs("bind-fallback", context)
    assert runs("validate", context)
    context.update({"needs.validate.result": "success"})
    assert runs("record-validation", context)
    context.update({"needs.record-validation.result": "success"})
    assert runs("publish", context)
    context.update({"needs.publish.result": "success"})
    assert runs("finalize", context)
    # The primary-only jobs never run in the fallback's resume run.
    assert not any(runs(job, context) for job in ("execute", "bind"))


def test_no_fallback_result_means_no_validation_or_publication() -> None:
    context = fallback_run(bound=False)
    assert not runs("validate", context)
    context.update({"needs.validate.result": "skipped", "needs.record-validation.result": "skipped"})
    assert not runs("publish", context)


def test_there_is_one_validator_and_one_publisher_for_every_executor() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]

    def callers(command: str) -> set[str]:
        return {
            name
            for name, job in jobs.items()
            for step in job.get("steps") or []
            if re.search(rf"hunter_issue_agent_lifecycle\.py {command}\b", str(step.get("run", "")))
        }

    assert callers("validate") == {"validate"} and callers("publish") == {"publish"}
    assert callers("step --role record-validation") == {"record-validation"}
    assert callers("step --role finalize") == {"finalize"}
    # The validator and publisher download the result the ledger bound, whichever executor produced it.
    for name in ("validate", "publish"):
        assert any("hunter_issue_agent_lifecycle.py bound" in str(s.get("run", "")) for s in jobs[name]["steps"])


# --- the signed ledger: a fallback lineage completes like any other ------------------------------------------


def _published(chain: Chain, execution_id: str) -> dict[str, Any]:
    view = chain.view.authorizations[AUTH]
    bound, validated = view.evidence[state.AUTHORIZED], view.evidence[state.VALIDATED]
    identity = state.publication_identity(
        repository_id=REPO,
        issue_number=ISSUE,
        authorization_id=AUTH,
        base_sha=bound["base_sha"],
        task_scope_sha256=bound["task_scope_sha256"],
        execution_id=execution_id,
        result_sha256=validated["result_sha256"],
        tree_sha=validated["tree_sha"],
        unsigned_commit_sha=validated["unsigned_commit_sha"],
        control_sha=bound["control_sha"],
        writer_login="fafa33",
    )
    return {
        "writer_login": "fafa33",
        "publication_identity": identity,
        "head_sha": "9a" * 20,
        "commit_verified": True,
        "publish_attempts": 1,
        "deadline_completed_at": "2026-10-05T10:00:00Z",
    }


COMPLETED = {
    "pull_request_number": 561,
    "pull_request_node_id": "PR_kwDO",
    "pull_request_head_sha": "9a" * 20,
    "draft": True,
    "preflight_run_id": 77,
    "preflight_conclusion": "success",
}


def validated_canary_fallback() -> Chain:
    bound = fallback_bound_evidence()
    bound["fallback"]["model"] = {
        "requested": "gpt-5.6",
        "requested_listed": False,
        "observed": "gpt-5.2",
        "provenance": "REROUTED",
    }
    return (
        canary_offer()
        .transition(state.FALLBACK_AUTHORIZED, canary_admission(), "bind")
        .transition(state.RESULT_BOUND, bound, "bind")
        .transition(state.VALIDATED, validated_evidence(), "record-validation")
    )


def test_a_canary_fallback_lineage_completes_and_stays_an_admissible_remediation_parent() -> None:
    chain = validated_canary_fallback()
    chain.transition(state.PUBLISHED, _published(chain, FALLBACK_ID), "finalize")
    chain.transition(state.COMPLETED, COMPLETED, "candidate-pr-record")
    view = state.verify_chain(chain.records, repository_id=REPO, issue_number=ISSUE, trust=_trust(), provenance=_ok)
    parent = view.authorizations[AUTH]
    # The ADR 0039 L4 remediation-parent rule, as issue_agent_authorize.prepare applies it.
    assert parent.state == state.COMPLETED
    assert parent.binding("execution_branch") == parent.evidence[state.AUTHORIZED]["execution_branch"]
    # Every link the learning loop and the audit need survives to completion, signed.
    assert parent.execution_id == FALLBACK_ID
    assert parent.evidence[state.FALLBACK_AUTHORIZED]["primary_cause"] == "CANARY_NETWORK"
    assert parent.evidence[state.RESULT_BOUND]["fallback"]["model"]["provenance"] == "REROUTED"
    lineage = parent.evidence[state.AUTHORIZED]["lineage"]
    assert {"prompt_sha256", "dpm_context_sha256", "build_record_id", "handoff_sha256"} <= set(lineage)


def test_publication_never_binds_the_primary_execution_for_a_fallback_result() -> None:
    chain = validated_canary_fallback()
    primary = chain.view.authorizations[AUTH].evidence[state.AUTHORIZED]["execution_id"]
    record = chain.make("transition", state.PUBLISHED, _published(chain, primary), _by("finalize"))
    reject(chain, record, "publication")


def _trust() -> state.TrustRoots:
    from test_issue_agent_state import TRUST

    return TRUST


def _ok(recorded_by: Any, _record: Any) -> bool:
    from test_issue_agent_state import trusted

    return bool(trusted(recorded_by, _record))


@pytest.mark.parametrize("bound", [True, False])
def test_the_evaluator_reads_this_workflow_without_unsupported_syntax(bound: bool) -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    context = fallback_run(bound=bound)
    for name in jobs:
        runs(name, context)
