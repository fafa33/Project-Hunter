"""ADR 0039 S5b-3/S5b-4 mutation proof: every load-bearing decision in the loop is observed, not assumed.

A guard that never fires is indistinguishable from a guard that is not there, so each mutant below weakens one
real decision and the suite must fail. The harness restores the exact original bytes of every mutated file in a
``finally``, so a killed mutant never leaks into the next run or into the working tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = [
    "tests/test_issue_agent_remediation_roles.py",
    "tests/test_issue_agent_remediation_promotion.py",
    "tests/test_issue_agent_remediation_reconcile.py",
    "tests/test_issue_agent_remediation_resolution.py",
    "tests/test_issue_agent_remediation_acceptance.py",
]


class Mutant:
    """One targeted source rewrite, applied by exact text so a drifted file cannot mutate anything."""

    def __init__(self, relative: str, old: str, new: str, *, note: str) -> None:
        self.path = ROOT / relative
        self.old, self.new, self.note = old, new, note

    def apply(self) -> bytes:
        original = self.path.read_bytes()
        if original.count(self.old.encode()) != 1:
            raise AssertionError(f"{self.note}: the anchor is not unique in {self.path.name}")
        self.path.write_bytes(original.replace(self.old.encode(), self.new.encode()))
        return original

    @staticmethod
    def restore(path: Path, original: bytes) -> None:
        path.write_bytes(original)


def run_suite() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "pytest", *SUITE, "-x", "-q", "-p", "no:randomly"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=3600,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


MUTANTS: list[Mutant] = [
    # --- ADR 0039 L6: the promotion delta and its refusals -------------------------------------------
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '    if not entries:\n        raise PromotionRefused("a promotion delta must name at least one proven finding")',
        "    if not entries:\n        pass",
        note="an empty promotion rewrites the governed registry",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '    if [item["finding_id"] for item in entries] != sorted({item["finding_id"] for item in entries}):\n'
        '        raise PromotionRefused("the promotion entries are not unique and sorted by finding id")',
        "    if False:\n        pass",
        note="unsorted or duplicated promotion entries",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '            raise PromotionRefused(f"the proven family {family_id} does not exist at the reviewed head")',
        "            pass",
        note="a proven family that does not exist at the reviewed head",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '    if proposal["finding_id"] != finding["finding_id"]:\n'
        '        raise PromotionRefused("the proposal does not name the finding whose provenance was supplied")',
        "    if False:\n        pass",
        note="a proposal that names another finding",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '    if int(group["pull_request_number"]) != int(finding["pull_request_number"]):\n'
        '        raise PromotionRefused("the finding was not observed on the remediated pull request")',
        "    if False:\n        pass",
        note="a finding observed on another pull request",
    ),
    # --- ADR 0039 L6: the model may never write the canonical promotion files -----------------------
    Mutant(
        "src/hunter/automation/issue_agent_replacement_executor.py",
        "        if written:\n"
        '            raise ReplacementExecutorError(f"the model may not write the canonical promotion file {written[0]}")',
        "        if False:\n            pass",
        note="a result that writes the canonical registry",
    ),
    # --- ADR 0039 L3.2: the RED->GREEN pair and its optional proposal ---------------------------
    Mutant(
        "src/hunter/automation/issue_agent_replacement_executor.py",
        '            if label == "red" and completed.returncode == 0:\n'
        '                raise ReplacementExecutorError("the named regression test already passed on the reviewed head")',
        "            if False:\n                pass",
        note="a regression that already passed at the reviewed head",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_replacement_executor.py",
        '            if label == "green" and completed.returncode != 0:\n'
        '                raise ReplacementExecutorError("the named regression test does not pass on the full result")',
        "            if False:\n                pass",
        note="a fix that does not make the regression pass",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_replacement_executor.py",
        '            if proposal["finding_id"] not in binding.remediation_finding_ids:\n'
        '                raise ReplacementExecutorError("the remediation proposal names a finding outside this authorization")',
        "            if False:\n                pass",
        note="a proposal naming a finding outside the authorization",
    ),
    # --- ADR 0039 L7: the exact-lease fast-forward ---------------------------------------------
    Mutant(
        "src/hunter/automation/issue_agent_replacement_executor.py",
        "    if validated.base_sha != lease_sha:\n"
        '        raise ReplacementExecutorError("the lease must be the exact base the remediation commits onto")',
        "    if False:\n        pass",
        note="a lease that is not the exact base",
    ),
    # --- ADR 0039 L4: the executor base and the moved head --------------------------------------
    Mutant(
        "src/hunter/automation/issue_agent_roles.py",
        '        if head != base_sha:\n            raise RoleRefused("PR_HEAD_MOVED", "the remediated pull request head is no longer the bound head")',
        "        if False:\n            pass",
        note="a pull request head that moved after the bind",
    ),
    # --- ADR 0039 L4: eligibility and its budgets ----------------------------------------------
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        '    if published is None or published.get("head_sha") != head:\n'
        "        return None  # the branch moved since completion; the reviewed head is no longer the published one",
        "    if published is None:\n        return None",
        note="a branch that moved since completion",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        '        if classification is not None and classification["outcome"] == "ambiguous":\n'
        "            continue  # an ambiguous finding needs a human disposition, never a guessed family",
        "        if False:\n            continue",
        note="an ambiguous finding treated as remediable",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        "        if attempt > knowledge.MAX_REMEDIATIONS_PER_FINDING:\n"
        "            continue  # the per-finding budget is exhausted",
        "        if False:\n            continue",
        note="an exhausted per-finding remediation budget",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        "    if len(branches) != 1:\n"
        "        return None  # zero, or an ambiguous pair: never guess which pull request to remediate",
        "    if not branches:\n        return None",
        note="two open lifecycle pull requests guessed apart",
    ),
    # --- ADR 0039 L7: the exact-head proof ------------------------------------------------------
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        '    if conclusion != "success" or preflight_run is None:\n'
        "        return None  # an outstanding or failed exact-head gate is not proof",
        "    if False:\n        return None",
        note="an outstanding or failed exact-head preflight",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        "    if len(published) != 1:\n"
        "        return None  # zero or ambiguous: never resolve against an authorization we cannot name uniquely",
        "    if False:\n        return None",
        note="an ambiguous published remediation resolved anyway",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_control.py",
        '    thread = resolved.get("resolveReviewThread", {}).get("thread") if isinstance(resolved, Mapping) else None\n'
        '    return int(posted["id"]) if isinstance(thread, Mapping) and thread.get("isResolved") is True else None',
        '    thread = resolved.get("resolveReviewThread", {}).get("thread") if isinstance(resolved, Mapping) else None\n'
        '    return int(posted["id"])',
        note="a refused resolve reported as a resolution",
    ),
    # --- ADR 0039 L2/L7: the anchored proof and resolution records ------------------------------
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        "    if item is None or item.proven is not None:\n"
        "        return []  # already proven, or not ingested: the insert-only rule makes a repeat a no-op",
        "    if False:\n        return []",
        note="a repeated pass writing a second proof",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '    if item.classifications.get("proven") is None:\n'
        "        return []  # no proven mapping: the fix landed, but the finding is still only fixed, not classified",
        "    if False:\n        return []",
        note="a proof recorded without a proven classification",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        '    if item is None or item.proven is None or item.proven["remediated_head_sha"] != remediated_head_sha:\n'
        "        return []",
        "    if item is None:\n        return []",
        note="a thread resolved at a head other than the proven one",
    ),
    Mutant(
        "src/hunter/automation/issue_agent_remediation.py",
        "    if not proven or disposition is None or any(identity not in view.findings for identity in proven):\n"
        "        return []",
        "    if False:\n        return []",
        note="a classification for a finding nobody ingested",
    ),
]


@pytest.mark.parametrize("mutant", MUTANTS, ids=[mutant.note for mutant in MUTANTS])
def test_a_weakened_decision_is_always_observed(mutant: Mutant) -> None:
    original = mutant.apply()
    try:
        completed = run_suite()
    finally:
        Mutant.restore(mutant.path, original)
    assert completed.returncode != 0, f"the suite passed with {mutant.note} weakened"
