"""Tests for bounded repository-owned evidence reconstruction.

The properties that make the output trustworthy, each pinned here:

- repository history, not reviewer identity or severity, selects a disposition;
- a third-party label is never converted into a defect verdict;
- "the PR merged" and "a test file mentions this symbol" are not proof, so they
  must not confirm a finding;
- an owner-cited fix is only accepted as traceable when it resolves to a real
  commit in that pull request;
- "changed after review" is a temporal filter, not the path's whole history;
- a review container with no file/line is not an individual finding and must not
  become an owner decision;
- family clustering is structural, never wording, path, reviewer, or PR;
- scan completion alone can never report closure, and the run reconciles
  exactly against the frozen ledger;
- the frozen scan and the canonical registry are never mutated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence import historical_evidence_reconstruction as recon
from hunter.evidence_intelligence import semantic_invariant_extraction as semantic

OWNER = "owner-1"
REPO = "fake/scan"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeHistory:
    """Minimal stand-in for GitHistory with explicit, inspectable state."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        history: dict[str, list[tuple[str, str, str]]] | None = None,
        tests: dict[str, list[str]] | None = None,
    ) -> None:
        self._files = files or {}
        self._history = history or {}
        self._tests = tests or {}

    def path_exists(self, path: str) -> bool:
        return path in self._files

    def content(self, path: str) -> str | None:
        return self._files.get(path)

    def commits_after(self, path: str, not_before: str | None):  # noqa: ANN201
        if not not_before:
            return ()
        return tuple(e for e in self._history.get(path, []) if e[1] > not_before)

    def test_symbol_index(self, symbols):  # noqa: ANN001, ANN201
        found = {}
        for symbol in symbols:
            hits = self._tests.get(symbol)
            if hits:
                found[symbol] = hits
        return found


def make_record(
    items: list[dict[str, Any]] | None = None,
    *,
    comments: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    issue_comments: list[dict[str, Any]] | None = None,
    commits: list[dict[str, Any]] | None = None,
    merged: bool = True,
    state: str = "closed",
    pr_number: int = 7,
) -> dict[str, Any]:
    return {
        "pr_number": pr_number,
        "merged": merged,
        "state": state,
        "ledger": {"items": items or []},
        "evidence": {
            "review_comments": comments or [],
            "reviews": reviews or [],
            "issue_comments": issue_comments or [],
            "commits": commits or [],
        },
    }


def make_item(
    *,
    event_id: str = "review-comment-1",
    reviewer: str = "bot[bot]",
    message: str = "The replay path ignores applicability_end and admits an expired rule.",
    path: str | None = "src/hunter/x.py",
    line: int | None = 10,
    classification: str | None = None,
    claimed_family_id: str | None = None,
    state: str = "insufficient-evidence",
) -> dict[str, Any]:
    return {
        "observation_id": f"obs-{event_id}",
        "state": state,
        "observation": {
            "source": "github-review",
            "provider": "github-review",
            "event_id": event_id,
            "source_pr": 7,
            "reviewed_head_sha": "a" * 40,
            "reviewed_base_sha": "b" * 40,
            "reviewer": reviewer,
            "path": path,
            "line": line,
            "message": message,
            "availability": "available",
            "classification": classification,
            "invariant": "",
            "affected_paths": [],
            "fix_reference": "",
            "regression_evidence": [],
            "claimed_family_id": claimed_family_id,
        },
        "proposal": None,
    }


def decide(
    record: dict[str, Any],
    item: dict[str, Any] | None = None,
    *,
    history: FakeHistory | None = None,
    families: list[dict[str, Any]] | None = None,
) -> recon.Reconstruction:
    return recon.reconstruct(
        (item or record["ledger"]["items"][0])["observation"],
        str((item or record["ledger"]["items"][0])["state"]),
        record,
        owner_login=OWNER,
        history=history or FakeHistory(),
        families=families or [],
        anchors={},
    )


# ---------------------------------------------------------------------------
# Precedence: proven evidence beats inference
# ---------------------------------------------------------------------------


def test_proven_existing_family_is_preserved_over_inference() -> None:
    item = make_item(classification="confirmed", claimed_family_id="DFF-018", state="existing-family")
    result = decide(make_record(items=[item]), item)
    assert result.disposition == recon.EXISTING_FAMILY
    assert result.claimed_family_id == "DFF-018"
    assert result.rule == "R0-preserve-proven-terminal-state"


def test_proven_family_beats_the_container_rule() -> None:
    """A proven canonical mapping must not be reclassified as 'not a claim'."""
    item = make_item(
        classification="confirmed",
        claimed_family_id="DFF-018",
        state="existing-family",
        path=None,
        line=None,
    )
    result = decide(make_record(items=[item]), item)
    assert result.disposition == recon.EXISTING_FAMILY


def test_proven_exclusion_is_preserved() -> None:
    item = make_item(classification="style", state="excluded")
    result = decide(make_record(items=[item]), item)
    assert result.disposition == recon.EXCLUDED_STYLE


# ---------------------------------------------------------------------------
# Review containers are not findings
# ---------------------------------------------------------------------------


def test_review_chrome_is_not_a_claim() -> None:
    """Chrome is the ground for NOT_A_CLAIM, not the absence of a location."""
    for message in (
        "### Codex Review Here are some automated review suggestions for this pull request.",
        "@copilot review",
        "Actionable comments posted: 2. Your plan provides up to 10 included reviews per hour.",
        "Copilot reviewed 3 out of 3 changed files in this pull request and generated no new comments.",
        "Copilot was unable to review this pull request because the user who requested the review "
        "has reached their quota limit.",
    ):
        result = decide(make_record([make_item(path=None, line=None, message=message)]))
        assert result.disposition == recon.EXCLUDED_NOT_A_CLAIM, message
        assert result.disposition not in {recon.OWNER_REQUIRED, recon.CONFIRMED}


def test_substantive_finding_without_a_location_anchor_is_not_dismissed() -> None:
    """A missing file/line is explicitly not sufficient to call an item a non-claim.

    Substantive findings are routinely stated at review level, several naming
    their own root cause. Dismissing them as containers deleted 73 real findings
    from this ledger, including owner review bodies that assert a blocker and
    would otherwise never even reach the owner-disposition rule.
    """
    for message in (
        "BLOCKER - local validation-receipt reuse does not actually bind reuse to the current HEAD.",
        "Two test modules define the same unnamed positional bundle tuple. The shared root cause is a "
        "missing named structure for this bundle contract.",
        "Two locks guard the same mutable maps. Correctness currently depends on CPython dict "
        "operations being atomic under the GIL, not on the declared synchronization.",
    ):
        result = decide(make_record([make_item(path=None, line=None, message=message)]))
        assert result.disposition != recon.EXCLUDED_NOT_A_CLAIM, message


def test_owner_review_body_asserting_a_finding_reaches_the_disposition_rule() -> None:
    """An owner review body is a claim, so a fix it cites is still evaluable."""
    record = make_record(
        [make_item(path=None, line=None, message="BLOCKER - the guard does not bind reuse to HEAD.")],
        reviews=[
            {
                "id": 1,
                "user": {"login": OWNER},
                "body": "BLOCKER - the guard does not bind reuse to HEAD. Fixed in deadbee1cafebabe "
                "by passing head_sha explicitly.",
            }
        ],
    )
    result = decide(record)
    assert result.disposition != recon.EXCLUDED_NOT_A_CLAIM
    assert result.disposition in {recon.OWNER_REQUIRED, recon.CONFIRMED}


# ---------------------------------------------------------------------------
# Repository-owner disposition
# ---------------------------------------------------------------------------


def test_owner_authored_correction_confirms() -> None:
    record = make_record(
        [
            make_item(
                reviewer=OWNER, message="Fixed in a535c1c. Replay now requires applicability_end to bound the window."
            )
        ]
    )
    result = decide(record)
    assert result.disposition == recon.CONFIRMED
    assert result.historical_defect_truth == recon.DEFECT_WAS_REAL_AND_CORRECTED
    assert result.fix_reference == "a535c1c"


def test_owner_rejection_is_a_false_positive() -> None:
    record = make_record([make_item(reviewer=OWNER, message="False positive: the guard already rejects this.")])
    result = decide(record)
    assert result.disposition == recon.EXCLUDED_FALSE_POSITIVE
    assert result.historical_defect_truth == recon.CLAIM_WAS_INCORRECT


def test_owner_thread_reply_disposes_of_a_third_party_finding() -> None:
    record = make_record(
        [make_item(event_id="review-comment-100")],
        comments=[
            {
                "id": 100,
                "in_reply_to_id": None,
                "user": {"login": "bot[bot]"},
                "path": "src/hunter/x.py",
                "line": 3,
                "body": "P1: the guard is bypassable.",
            },
            {
                "id": 101,
                "in_reply_to_id": 100,
                "user": {"login": OWNER},
                "body": "Resolved in `2fab2e0`. The guard now binds.",
            },
        ],
    )
    result = decide(record)
    assert result.disposition == recon.CONFIRMED
    assert result.fix_reference == "2fab2e0"


def test_unrelated_owner_pr_comment_does_not_dispose_of_a_finding() -> None:
    """Owner chatter elsewhere in the PR must not silently dispose of this claim."""
    record = make_record(
        [make_item()],
        issue_comments=[
            {"id": 1, "user": {"login": OWNER}, "body": "All gates pass, merging now. Nice work everyone."}
        ],
    )
    surviving = FakeHistory(files={"src/hunter/x.py": "def replay_rule():\n    return applicability_end\n"})
    result = decide(record, history=surviving)
    assert result.disposition == recon.OWNER_REQUIRED, "unrelated owner chatter must not dispose of a claim"


# ---------------------------------------------------------------------------
# Reviewer authority never decides
# ---------------------------------------------------------------------------


def test_third_party_severity_label_does_not_confirm() -> None:
    record = make_record([make_item(reviewer="coderabbitai[bot]", message="**P0 critical security defect** here.")])
    result = decide(record)
    assert result.disposition == recon.OWNER_REQUIRED


def test_third_party_claim_with_surviving_symbol_stays_unresolved() -> None:
    history = FakeHistory(files={"src/hunter/x.py": "def replay_rule():\n    return applicability_end\n"})
    record = make_record([make_item()])
    result = decide(record, history=history)
    assert result.disposition == recon.OWNER_REQUIRED
    assert "applicability_end" in result.symbols_surviving


# ---------------------------------------------------------------------------
# "Merged plus a test mentions the symbol" is not proof
# ---------------------------------------------------------------------------


def test_merged_pr_with_incidental_test_mention_does_not_confirm() -> None:
    """The exact false-positive machine: a field name is trivially in its own tests."""
    history = FakeHistory(
        files={"src/hunter/x.py": "class Rule:\n    applicability_end = None\n"},
        history={"src/hunter/x.py": [("c" * 40, "2030-01-01T00:00:00Z", "unrelated")]},
        tests={"applicability_end": ["tests/test_rule.py"]},
    )
    record = make_record(
        [make_item(message="Rule.applicability_end is not enforced during replay.")],
        commits=[{"sha": "b" * 40, "commit": {"author": {"date": "2020-01-01T00:00:00Z"}}}],
    )
    result = decide(record, history=history)
    assert result.disposition == recon.OWNER_REQUIRED, "incidental test mention must not confirm"


def test_owner_cited_fix_resolving_to_a_real_pr_commit_is_traceable() -> None:
    record = make_record(
        [make_item(reviewer=OWNER, message="Fixed in abc1234. Replay now bounds the window.")],
        commits=[
            {"sha": "aaa1111", "commit": {"author": {"date": "2026-01-01T00:00:00Z"}}},
            {"sha": "abc1234def", "commit": {"author": {"date": "2026-01-02T00:00:00Z"}}},
        ],
    )
    result = decide(record)
    assert result.disposition == recon.CONFIRMED
    assert "abc1234" in result.evidence


# ---------------------------------------------------------------------------
# Temporal anchoring
# ---------------------------------------------------------------------------


def test_commits_after_review_is_temporal_not_whole_history() -> None:
    history = FakeHistory(
        files={"src/hunter/x.py": "applicability_end"},
        history={
            "src/hunter/x.py": [
                ("a" * 40, "2026-06-01T00:00:00Z", "after the review"),
                ("b" * 40, "2020-01-01T00:00:00Z", "long before the review"),
            ]
        },
    )
    signals = recon.collect_history_evidence(
        {"path": "src/hunter/x.py"}, ["applicability_end"], history, "2026-05-01T00:00:00Z"
    )
    assert signals["commits_after_review"] == 1
    assert "after the review" in signals["post_review_subjects"][0]
    assert "long before" not in " ".join(signals["post_review_subjects"])


def test_missing_anchor_yields_no_post_review_evidence() -> None:
    """Without a timestamp, 'changed after review' must be empty, not everything."""
    history = FakeHistory(
        files={"src/hunter/x.py": "applicability_end"},
        history={"src/hunter/x.py": [("a" * 40, "2020-01-01T00:00:00Z", "ancient")]},
    )
    signals = recon.collect_history_evidence({"path": "src/hunter/x.py"}, ["x_symbol"], history, None)
    assert signals["commits_after_review"] == 0


def test_pr_anchor_uses_captured_commit_evidence() -> None:
    record = make_record(
        commits=[
            {"sha": "aaa1111", "commit": {"author": {"date": "2026-07-24T13:33:35Z"}}},
            {"sha": "bbb2222", "commit": {"author": {"date": "2026-07-24T14:18:53Z"}}},
        ]
    )
    anchor = recon.pr_anchor(record)
    assert anchor["anchor_sha"] == "bbb2222"
    assert anchor["anchor_date"] == "2026-07-24T14:18:53Z"


# ---------------------------------------------------------------------------
# Superseded / abandoned
# ---------------------------------------------------------------------------


def test_never_merged_pr_with_absent_path_is_superseded() -> None:
    record = make_record([make_item()], merged=False, state="closed")
    result = decide(record, history=FakeHistory(files={}))
    assert result.disposition == recon.EXCLUDED_OBSOLETE_OR_SUPERSEDED
    assert result.historical_defect_truth == recon.NOT_APPLICABLE


def test_deleted_path_is_superseded() -> None:
    record = make_record([make_item()])
    result = decide(record, history=FakeHistory(files={}))
    assert result.disposition == recon.EXCLUDED_OBSOLETE_OR_SUPERSEDED


def test_symbols_absent_while_file_survives_is_owner_required() -> None:
    """Ambiguous between 'corrected' and 'rewritten'; history cannot decide."""
    history = FakeHistory(
        files={"src/hunter/x.py": "def unrelated():\n    pass\n"},
        history={"src/hunter/x.py": [("a" * 40, "2030-01-01T00:00:00Z", "rewrite")]},
    )
    record = make_record([make_item()])
    result = decide(record, history=history)
    assert result.disposition == recon.OWNER_REQUIRED


# ---------------------------------------------------------------------------
# Non-behavioral and external events
# ---------------------------------------------------------------------------


def test_naming_observation_is_style() -> None:
    record = make_record([make_item(message="Consider renaming this local variable for clarity.")])
    result = decide(record)
    assert result.disposition == recon.EXCLUDED_STYLE


def test_external_rate_limit_event_is_infrastructure() -> None:
    record = make_record(
        [make_item(message="The API returned 429 rate limit exceeded during this run.", path="config.toml", line=3)]
    )
    result = decide(record, history=FakeHistory(files={"config.toml": "[x]\n"}))
    assert result.disposition == recon.INFRASTRUCTURE_OR_PROVIDER


# ---------------------------------------------------------------------------
# Clustering is structural
# ---------------------------------------------------------------------------


def _rec(**kwargs: Any) -> recon.Reconstruction:
    defaults: dict[str, Any] = {
        "observation_id": "obs-1",
        "source_pr": 1,
        "path": "src/hunter/x.py",
        "line": 1,
        "rule": "E1",
        "disposition": recon.CONFIRMED,
        "evidence": "e",
        "historical_defect_truth": recon.DEFECT_WAS_REAL_AND_CORRECTED,
        "invariant": "The replay path must reject an unvalidated applicability_end value.",
        "execution_boundary": "src/hunter/x",
    }
    defaults.update(kwargs)
    return recon.Reconstruction(**defaults)


def test_same_boundary_and_constraint_cluster_together_despite_different_wording() -> None:
    a = _rec(invariant="The replay path must reject an unvalidated applicability_end value.")
    b = _rec(invariant="Replay must refuse a malformed applicability_end before persisting anything at all.")
    assert recon.cluster_key(a) == recon.cluster_key(b)


def test_different_constraint_class_does_not_cluster() -> None:
    a = _rec(invariant="The replay path must reject an unvalidated value.")
    b = _rec(invariant="The merge readiness poll must be bounded and never block forever.")
    assert recon.cluster_key(a) != recon.cluster_key(b)


def test_different_boundary_does_not_cluster() -> None:
    a = _rec(execution_boundary="src/hunter/x")
    b = _rec(execution_boundary="src/hunter/automation")
    assert recon.cluster_key(a) != recon.cluster_key(b)


def test_reviewer_and_pr_never_affect_clustering() -> None:
    a = _rec(source_pr=11)
    b = _rec(source_pr=999, path="src/hunter/x.py")
    assert recon.cluster_key(a) == recon.cluster_key(b)


def test_many_manifestations_collapse_to_one_proposal() -> None:
    items = [
        _rec(
            observation_id=f"obs-{n}",
            source_pr=n,
            invariant=f"Replay variant {n} must reject an unvalidated applicability_end value.",
        )
        for n in range(25)
    ]
    _existing, proposals = recon.build_family_proposals(items)
    assert len(proposals) == 1, "25 manifestations of one invariant must yield one family"
    assert proposals[0].member_count == 25


def test_existing_family_items_are_mappings_not_proposals() -> None:
    items = [_rec(disposition=recon.EXISTING_FAMILY, claimed_family_id="DFF-001")]
    existing, proposals = recon.build_family_proposals(items)
    assert proposals == []
    assert existing == {"DFF-001": ["obs-1"]}


# ---------------------------------------------------------------------------
# Owner-required table
# ---------------------------------------------------------------------------


def test_owner_required_is_grouped_by_evidence_gap_not_by_wording() -> None:
    items = [
        _rec(
            observation_id=f"obs-{n}",
            disposition=recon.OWNER_REQUIRED,
            rule=recon.RULE_INSUFFICIENT_EVIDENCE,
            evidence="bounded reconstruction could not establish a disposition: "
            "no repository-owner disposition addresses this claim; the claimed symbols still exist "
            "on the integration base; no regression test on the integration base references the "
            "claimed symbols",
            historical_defect_truth=recon.UNDETERMINED,
            invariant=f"totally different wording {n}",
        )
        for n in range(12)
    ]
    table = recon.build_owner_required_table(items)
    assert table["owner_required_count"] == 12
    assert table["group_count"] == 1, "identical evidence gaps must form one decision"


# ---------------------------------------------------------------------------
# Strict reconciliation
# ---------------------------------------------------------------------------


def _seed(tmp_path: Path, items: list[dict[str, Any]], *, prs: int = 1) -> Path:
    data_dir = tmp_path / "scan"
    (data_dir / "prs").mkdir(parents=True)
    (data_dir / "snapshot.json").write_text(
        json.dumps({"prs": [{"number": n} for n in range(1, prs + 1)]}), encoding="utf-8"
    )
    for number in range(1, prs + 1):
        record = make_record(items, pr_number=number)
        # A real scanner record carries both: the state machine's terminal state
        # and the explicit status derived from it. Seeding only the former made
        # the fixture disagree with every record the scanner actually writes.
        record["scan_state"] = "complete"
        record["status"] = "FINDING_EXTRACTED"
        record["record_digest"] = f"digest-of-frozen-record-{number}"
        (data_dir / "prs" / f"{number}.json").write_text(json.dumps(record), encoding="utf-8")
    return data_dir


def test_scan_completion_alone_never_reports_closure(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(5)]
    data_dir = _seed(tmp_path, items)
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=data_dir,
        reconstructions=[_rec(observation_id=f"obs-{n}") for n in range(5)],
        scan_statused=1,
        scan_total=1,
        existing_mappings={},
        proposals=[],
        owner_table={"count": 0, "group_count": 0},
    )
    assert manifest["scan_coverage_gap"] == 0
    assert manifest["adjudication_coverage_gap"] == 0
    assert manifest["coverage_gap"] > 0, "scan completion must not stand in for evidence closure"


def test_coverage_gap_zero_requires_every_dimension_zero(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}", path=None, line=None) for n in range(4)]
    data_dir = _seed(tmp_path, items)
    reconstructions = [_rec(observation_id=f"obs-{n}", disposition=recon.EXCLUDED_NOT_A_CLAIM) for n in range(4)]
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=data_dir,
        reconstructions=reconstructions,
        scan_statused=1,
        scan_total=1,
        existing_mappings={},
        proposals=[],
        owner_table={"count": 0, "group_count": 0},
    )
    assert manifest["coverage_gap"] == 0
    assert manifest["reconciliation"]["reconciles_exactly"] is True


def test_owner_required_keeps_coverage_open(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(3)]
    data_dir = _seed(tmp_path, items)
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=data_dir,
        reconstructions=[_rec(observation_id=f"obs-{n}", disposition=recon.OWNER_REQUIRED) for n in range(3)],
        scan_statused=1,
        scan_total=1,
        existing_mappings={},
        proposals=[],
        owner_table={"count": 3, "group_count": 1},
    )
    assert manifest["unresolved_evidence_count"] == 3
    assert manifest["coverage_gap"] == 3


def test_manifest_reconciles_exactly_to_the_frozen_ledger(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(9)]
    data_dir = _seed(tmp_path, items)
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=data_dir,
        reconstructions=[_rec(observation_id=f"obs-{n}") for n in range(9)],
        scan_statused=1,
        scan_total=1,
        existing_mappings={},
        proposals=[],
        owner_table={"count": 0, "group_count": 0},
    )
    total = sum(manifest["dispositions"].values())
    assert total == manifest["reconciliation"]["raw_scan_ledger_items"] == 9


def test_missing_adjudication_shows_in_coverage(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(6)]
    data_dir = _seed(tmp_path, items)
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=data_dir,
        reconstructions=[],
        scan_statused=1,
        scan_total=1,
        existing_mappings={},
        proposals=[],
        owner_table={"count": 0, "group_count": 0},
    )
    assert manifest["adjudication_coverage_gap"] == 6


# ---------------------------------------------------------------------------
# Frozen inputs are never mutated
# ---------------------------------------------------------------------------


def test_reconstruction_never_mutates_the_frozen_scan_or_registry(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(3)]
    data_dir = _seed(tmp_path, items)
    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    snapshot_before = (data_dir / "snapshot.json").read_bytes()
    records_before = {p.name: p.read_bytes() for p in (data_dir / "prs").glob("*.json")}
    registry_before = registry.read_bytes()

    recon.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
    )

    assert (data_dir / "snapshot.json").read_bytes() == snapshot_before
    assert {p.name: p.read_bytes() for p in (data_dir / "prs").glob("*.json")} == records_before
    assert registry.read_bytes() == registry_before, "no canonical history may be written"


def test_reconstruction_is_resumable(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(3)]
    data_dir = _seed(tmp_path, items, prs=2)
    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    first = recon.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
    )
    assert (data_dir / "reconstruction" / "1.json").is_file()
    second = recon.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
    )
    assert first["closure_manifest_digest"] == second["closure_manifest_digest"]
    assert second["reused_pr_records"] == 2


def test_stale_rule_digest_is_not_reused(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(2)]
    data_dir = _seed(tmp_path, items)
    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    recon.run_reconstruction(
        data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO, repo_root=tmp_path
    )
    target = data_dir / "reconstruction" / "1.json"
    payload = json.loads(target.read_text())
    payload["engine_digest"] = "rules-from-a-previous-version"
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = recon.run_reconstruction(
        data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO, repo_root=tmp_path
    )
    assert result["reused_pr_records"] == 0
    assert result["reconciliation"]["reconciles_exactly"] is True


def _cached_reconstruction_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Two frozen PRs and a registry, so a partial cache reuse is observable."""
    items = [make_item(event_id=f"review-comment-{n}") for n in range(2)]
    data_dir = _seed(tmp_path, items, prs=2)
    registry = tmp_path / "DEFECT_REGISTRY.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    return data_dir, registry


def test_changed_evidence_inputs_invalidate_the_reconstruction_cache(tmp_path: Path) -> None:
    """A cached disposition may only be reused while all of its inputs hold.

    The resume check once accepted a finalized reconstruction on the registry and
    rule digests alone, although the disposition was also decided from the owner
    login, the integration base the implementation was checked against, the
    verdicts, and the frozen record itself. Because the post-passes revisit only
    CONFIRMED findings, a stale RESOLVED_NON_RECURRING or family outcome restored
    here could never be rolled back, and the regenerated closure manifest would
    republish obsolete evidence as current.
    """
    base_kwargs: dict[str, Any] = {"repository": REPO, "repo_root": tmp_path}

    def run(**overrides: Any) -> dict[str, Any]:
        data_dir, registry = overrides.pop("paths")
        kwargs = {
            "data_dir": data_dir,
            "registry_path": registry,
            "owner_login": OWNER,
            **base_kwargs,
            **overrides,
        }
        return recon.run_reconstruction(**kwargs)

    # Each mutation changes an input the cached disposition was decided from.
    for label, overrides in (
        ("owner login", {"owner_login": "someone-else"}),
        ("invariant verdicts", {"invariant_verdicts": {"obs-x": {"verified": True}}}),
        ("recurrence verdicts", {"recurrence_verdicts": {"c": {"same_invariant": True}}}),
    ):
        paths = _cached_reconstruction_inputs(tmp_path / label.replace(" ", "-"))
        first = run(paths=paths)
        assert first["reused_pr_records"] == 0, label
        assert run(paths=paths)["reused_pr_records"] == 2, f"{label}: unchanged inputs must still resume"
        assert (
            run(paths=paths, **overrides)["reused_pr_records"] == 0
        ), f"{label} changed but the stale cache was reused"


def test_changed_frozen_record_invalidates_the_reconstruction_cache(tmp_path: Path) -> None:
    """The frozen record is an input too, so its digest binds the cache."""
    data_dir, registry = _cached_reconstruction_inputs(tmp_path)
    kwargs: dict[str, Any] = {
        "data_dir": data_dir,
        "registry_path": registry,
        "owner_login": OWNER,
        "repository": REPO,
        "repo_root": tmp_path,
    }
    recon.run_reconstruction(**kwargs)
    assert recon.run_reconstruction(**kwargs)["reused_pr_records"] == 2

    cached = data_dir / "reconstruction" / "1.json"
    payload = json.loads(cached.read_text())
    payload["record_digest"] = "digest-of-a-different-frozen-record"
    cached.write_text(json.dumps(payload), encoding="utf-8")

    # Only PR 1's cache no longer matches its frozen record, so only PR 1 is
    # re-decided; PR 2 still legitimately resumes. The invalidation is per
    # record, not a blanket cache drop.
    assert recon.run_reconstruction(**kwargs)["reused_pr_records"] == 1


def test_reconstruction_cache_records_every_input_it_is_keyed_on(tmp_path: Path) -> None:
    """The persisted cache must carry the identity the resume check compares.

    A key the writer never records is a key that can never match, which would
    silently turn resume off rather than make it correct.
    """
    data_dir, registry = _cached_reconstruction_inputs(tmp_path)
    recon.run_reconstruction(
        data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO, repo_root=tmp_path
    )
    payload = json.loads((data_dir / "reconstruction" / "1.json").read_text())
    for key in ("registry_digest", "engine_digest", "inputs_digest", "record_digest"):
        assert payload.get(key), f"cache is keyed on {key} but does not persist it"


def test_record_without_a_digest_is_never_reused_from_cache(tmp_path: Path) -> None:
    """An unidentifiable frozen record fails closed rather than matching on None.

    Two absent digests comparing equal would "prove" a record unchanged while
    establishing nothing about it, which is the weakest possible form of the
    same defect.
    """
    data_dir, registry = _cached_reconstruction_inputs(tmp_path)
    kwargs: dict[str, Any] = {
        "data_dir": data_dir,
        "registry_path": registry,
        "owner_login": OWNER,
        "repository": REPO,
        "repo_root": tmp_path,
    }
    recon.run_reconstruction(**kwargs)
    assert recon.run_reconstruction(**kwargs)["reused_pr_records"] == 2

    frozen = data_dir / "prs" / "1.json"
    record = json.loads(frozen.read_text())
    del record["record_digest"]
    frozen.write_text(json.dumps(record), encoding="utf-8")

    assert recon.run_reconstruction(**kwargs)["reused_pr_records"] == 1


def test_scan_status_is_derived_not_assumed(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(2)]
    data_dir = _seed(tmp_path, items, prs=2)
    assert recon.scan_status_counts(data_dir) == (2, 2)
    # Mark one record genuinely non-terminal, the shape the scanner writes while
    # a PR is still inside its bounded infrastructure retries: no explicit status
    # yet. The gap must appear rather than round away.
    target = data_dir / "prs" / "2.json"
    payload = json.loads(target.read_text())
    payload["scan_state"] = "infra-retryable"
    payload["status"] = None
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert recon.scan_status_counts(data_dir) == (1, 2)


def test_permanent_scan_outcomes_count_as_statused(tmp_path: Path) -> None:
    """A PR the scanner is permanently done with is statused, not a scan gap.

    ``build_coverage_manifest`` counts any record carrying an explicit status,
    ``INFRA_PROVIDER_FAILURE`` and ``UNRESOLVED`` included. Counting only
    ``scan_state == "complete"`` here made the same persisted scan report a
    ``scan_coverage_gap`` that no further work could ever close, because the
    scanner had already finished with those PRs.
    """
    items = [make_item(event_id=f"review-comment-{n}") for n in range(2)]
    data_dir = _seed(tmp_path, items, prs=3)
    for number, scan_state, status in (
        (2, "infra-permanent", "INFRA_PROVIDER_FAILURE"),
        (3, "permanent-error", "UNRESOLVED"),
    ):
        target = data_dir / "prs" / f"{number}.json"
        payload = json.loads(target.read_text())
        payload["scan_state"] = scan_state
        payload["status"] = status
        target.write_text(json.dumps(payload), encoding="utf-8")

    assert recon.scan_status_counts(data_dir) == (3, 3)

    # The rule is the record's own explicit status, so an unknown status is not
    # silently promoted to terminal just because its scan_state looks finished.
    target = data_dir / "prs" / "3.json"
    payload = json.loads(target.read_text())
    payload["status"] = "NOT_A_REAL_STATUS"
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert recon.scan_status_counts(data_dir) == (2, 3)


def test_statused_rule_matches_the_coverage_manifest(tmp_path: Path) -> None:
    """The two components must not keep separate definitions of "statused".

    This is the defect itself: reconstruction and the scan coverage manifest
    each decided terminality independently, so the same records produced two
    different statused counts.
    """
    from hunter.evidence_intelligence import full_history_defect_scan as base

    records: list[dict[str, Any]] = [
        {"pr_number": 1, "status": "FINDING_EXTRACTED", "scan_state": "complete"},
        {"pr_number": 2, "status": "INFRA_PROVIDER_FAILURE", "scan_state": "infra-permanent"},
        {"pr_number": 3, "status": "UNRESOLVED", "scan_state": "permanent-error"},
        {"pr_number": 4, "status": None, "scan_state": "infra-retryable"},
    ]
    data_dir = tmp_path / "scan"
    (data_dir / "prs").mkdir(parents=True)
    (data_dir / "snapshot.json").write_text(
        json.dumps({"prs": [{"number": r["pr_number"]} for r in records]}), encoding="utf-8"
    )
    for record in records:
        (data_dir / "prs" / f"{record['pr_number']}.json").write_text(json.dumps(record), encoding="utf-8")

    manifest_statused = sum(1 for r in records if r["status"] in base.PR_STATUSES)
    assert recon.scan_status_counts(data_dir) == (manifest_statused, 4)
    assert manifest_statused == 3


# ---------------------------------------------------------------------------
# The real GitHistory, against this repository
# ---------------------------------------------------------------------------
#
# The temporal filter is the load-bearing part of "was this corrected after the
# review", and a fake cannot catch a regression in it: a non-temporal
# implementation still returns a superset, so every downstream rule keeps
# passing while resting on commits that predate the review. These tests drive
# the production accessor so that failure mode is visible.

REPO_ROOT = Path(__file__).resolve().parents[1]
# A path with substantial history on HEAD, so the temporal filter has something
# to partition. Pinned rather than discovered so the test cannot silently weaken.
LIVE_PATH = "scripts/hunter_governance_review_v2.py"


def _live_history() -> recon.GitHistory:
    return recon.GitHistory(REPO_ROOT, "HEAD")


def test_path_history_entries_carry_sha_date_and_subject() -> None:
    history = _live_history().path_history(LIVE_PATH)
    assert len(history) > 1, "the pinned path must have multi-commit history on HEAD"
    for sha, date, subject in history:
        assert len(sha) == 40
        assert date[:2] == "20", f"author date must be ISO-8601, got {date!r}"
        assert isinstance(subject, str)


def test_commits_after_is_temporal_not_whole_history() -> None:
    history = _live_history()
    everything = history.path_history(LIVE_PATH)
    assert len(everything) > 1, "needs a multi-commit path for this to mean anything"
    midpoint = everything[len(everything) // 2][1]
    later = history.commits_after(LIVE_PATH, midpoint)
    assert 0 < len(later) < len(everything), "must return a strict, non-empty subset"
    assert all(entry[1] > midpoint for entry in later)


def test_commits_after_without_anchor_yields_nothing() -> None:
    """No timestamp must mean no temporal evidence, never the whole history."""
    assert _live_history().commits_after(LIVE_PATH, None) == ()


def test_commits_after_anchor_before_all_history_returns_everything() -> None:
    history = _live_history()
    entries = history.path_history(LIVE_PATH)
    assert len(history.commits_after(LIVE_PATH, "1970-01-01T00:00:00Z")) == len(entries)


def test_absent_path_is_reported_absent() -> None:
    history = _live_history()
    assert history.path_exists(LIVE_PATH) is True
    assert history.path_exists("definitely/not/a/real/path.py") is False
    assert history.content("definitely/not/a/real/path.py") is None


def test_owner_pr_comment_naming_the_symbol_does_not_dispose_of_a_finding() -> None:
    """The rejected weak matcher, pinned as a regression.

    Adversarial on purpose: the owner comment carries a correction keyword *and*
    names the finding's exact file *and* its exact symbol. It is still about a
    different change, so it dispositions nothing here. Matching on symbol/path
    presence was the single largest source of over-confirmation on the real
    ledger, so the rejection must survive refactoring.
    """
    record = make_record(
        [make_item(message="The replay path ignores applicability_end and admits an expired rule.")],
        issue_comments=[
            {"id": 1, "user": {"login": OWNER}, "body": "Fixed the applicability_end computation in src/hunter/x.py."}
        ],
    )
    surviving = FakeHistory(files={"src/hunter/x.py": "def replay_rule():\n    return applicability_end\n"})
    result = decide(record, history=surviving)
    assert result.disposition == recon.OWNER_REQUIRED


def test_owner_inline_comment_in_the_same_file_does_not_dispose_of_a_finding() -> None:
    """Same-file is not same-finding: the other weak matcher, also pinned.

    The decoy sits on the same file and carries a rejection keyword, so
    same-file matching would fire on it. Only thread association may dispose of
    a finding, so a neighbouring line must not.
    """
    target = make_item(line=10, message="The guard here admits an expired rule.")
    decoy = make_item(line=40, event_id="review-comment-500")
    record = make_record(
        [target, decoy],
        comments=[
            {"id": 500, "path": "src/hunter/x.py", "line": 40, "user": {"login": OWNER}, "body": "False positive."}
        ],
    )
    surviving = FakeHistory(files={"src/hunter/x.py": "def guard():\n    pass\n"})
    assert decide(record, target, history=surviving).disposition == recon.OWNER_REQUIRED


def test_reconstruction_carries_the_ledger_canonical_observation_id(tmp_path: Path) -> None:
    """Reconstruction ids must join back to the frozen ledger by identity.

    A count-only reconciliation cannot detect a dropped, duplicated, or renamed
    observation, and nothing downstream (family proposals, the owner-required
    table, the backfill ledger) can join on an invented id. So the ledger item's
    own ``observation_id`` is carried through verbatim.
    """
    canonical = "0be20b17b2ddcdf5b1c75c570c83f2b0d1e7e26a9cc5a9d291e0331d16b7a096"
    target = make_item(event_id="review-comment-3971669511")
    target["observation_id"] = canonical
    decoy = make_item(line=11, event_id="review-comment-3971669512")
    decoy["observation_id"] = "a" * 64
    data_dir = _seed(tmp_path, [target, decoy])
    registry = data_dir / "registry.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")

    recon.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
        ref="HEAD",
        resume=False,
    )
    payload = json.loads((data_dir / "reconstruction" / "1.json").read_text())
    ids = [entry["observation_id"] for entry in payload["reconstructions"]]
    assert canonical in ids, "the ledger's canonical id must survive reconstruction"
    assert "a" * 64 in ids
    assert not any(i.startswith("obs-review-comment-") for i in ids), "ids must not be re-derived from event ids"
    assert len(ids) == len(set(ids)) == 2, "reconstruction ids must be unique"


def test_correction_keyword_in_a_commit_subject_does_not_confirm() -> None:
    """A commit subject is written for a change, never for an individual finding.

    One generic commit was confirming every finding in the file it touched, 37
    findings on the real ledger, with no link between the commit and the claim.
    The post-review commit below is dated after the anchor precisely so that the
    temporal filter admits it -- the only thing that must still reject it is the
    subject keyword.
    """
    record = make_record(
        [make_item()],
        commits=[
            {
                "sha": "f" * 40,
                "commit": {"message": "Fail closed on legacy evidence", "author": {"date": "2021-06-01T00:00:00Z"}},
            }
        ],
    )
    history = FakeHistory(
        files={"src/hunter/x.py": "def replay_rule():\n    return applicability_end\n"},
        history={"src/hunter/x.py": [("f" * 40, "2022-01-01T00:00:00+00:00", "Fail closed on legacy evidence")]},
    )
    result = decide(record, history=history)
    assert result.disposition == recon.OWNER_REQUIRED
    assert "post-review commit" not in result.evidence


def test_owner_cited_fix_that_resolves_to_no_commit_is_not_verified() -> None:
    """An unverifiable citation is not evidence; the SHA must exist in the PR."""
    anchors = {"anchor_sha": "a" * 40, "anchor_date": "2020-01-01T00:00:00Z", "commit_shas": ["a" * 40]}
    assert recon._verifiable_fix({"fix_reference": "deadbee1"}, anchors, {}) == ""
    # the first seven characters of the PR's commit SHA are "aaaaaaa"
    assert "is a commit in this pull request" in recon._verifiable_fix({"fix_reference": "aaaaaaa"}, anchors, {})
    assert recon._verifiable_fix({"fix_reference": "aaaaaaa1"}, anchors, {}) == ""
    assert recon._verifiable_fix({"fix_reference": ""}, anchors, {}) == ""
    assert recon._verifiable_fix(None, anchors, {"post_review_subjects": ("Fixed it",)}) == ""


def test_proposals_self_report_when_they_are_not_yet_families() -> None:
    """A keyword bucket must not be presentable as a recurring family.

    The proposals are grouped by an extracted constraint class, not by root
    cause, so a bucket can hold findings that share wording yet have nothing in
    common structurally. Publishing those as families would invent recurrence the
    evidence does not show, so each proposal carries its own verdict and reason.
    """
    _existing, proposals = recon.build_family_proposals(
        [
            _rec(
                observation_id=f"obs-{n}",
                disposition=recon.CONFIRMED,
                invariant=f"Adopt invariant {n} for the replay path.",
                execution_boundary="scripts",
                applicability_surface=("scripts/x.py",),
            )
            for n in range(6)
        ]
    )
    assert proposals, "confirmed items must still produce reviewable proposals"
    for proposal in proposals:
        payload = proposal.to_json()
        assert payload["clustering_status"] == "unverified-hypothesis"
        assert payload["clustering_evidence"]
        if payload["distinct_invariant_wording"] >= max(3, payload["manifest_count"] * 0.6):
            assert "no single root cause is demonstrated" in payload["clustering_evidence"]


# ---------------------------------------------------------------------------
# Semantic invariant extraction
# ---------------------------------------------------------------------------


def _extraction(**kwargs: Any) -> semantic.InvariantExtraction:
    defaults: dict[str, Any] = {
        "observation_id": "obs-1",
        "source_pr": 1,
        "finding_text_source": "observation_itself",
        "violated_invariant": "The replay path must reject an unvalidated applicability_end.",
        "root_cause": "",
        "execution_boundary": ("def evaluate",),
        "applicability": (),
        "prevention_symbols": (),
        "fix_commit": "abc1234",
        "invariant_verified": True,
        "verification_evidence": "reviewed: the owner states this obligation directly",
    }
    defaults.update(kwargs)
    return semantic.InvariantExtraction(**defaults)


def test_an_instruction_to_a_reviewer_is_not_a_violated_invariant() -> None:
    # Regression: "before" made this match the old shape test, so a review
    # instruction was recorded as the rule the code violated.
    assert semantic.rule_statement("Carefully review the code before committing.") == ""
    assert semantic.rule_statement("One or more issues must be addressed before approval.") == ""
    assert (
        semantic.rule_statement(
            "Learning: Applies to /: Before reporting completion, Claude MUST verify the actual repository state."
        )
        == ""
    )


def test_progress_narration_is_not_a_violated_invariant() -> None:
    assert semantic.rule_statement("I will keep this thread open until the required checks are green.") == ""
    assert (
        semantic.rule_statement(
            "Fixed on current pushed HEAD 860aa7d3: candidate_mode checks trusted controller existence."
        )
        == ""
    )


def test_a_real_rule_is_still_extracted_from_either_genre() -> None:
    assert (
        semantic.rule_statement("Reject non-ASCII signatures before compare_digest")
        == "Reject non-ASCII signatures before compare_digest"
    )
    assert (
        semantic.rule_statement(
            "read_trusted_upgrade_status must return exactly success, pending, failure, or missing."
        )
        == "read_trusted_upgrade_status must return exactly success, pending, failure, or missing."
    )
    # A rule stated in domain language carries no symbol and is still a rule.
    assert (
        semantic.rule_statement("Preserve PR edits in the freshness boundary")
        == "Preserve PR edits in the freshness boundary"
    )


def test_markup_does_not_leak_into_the_extracted_rule() -> None:
    finding = (
        "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  "
        "Reject non-ASCII signatures before compare_digest**\n\nBody text."
    )
    assert semantic.rule_statement(finding) == "Reject non-ASCII signatures before compare_digest"


def test_a_bolded_title_is_not_fused_into_the_first_body_sentence() -> None:
    # clean_comment_text joins every line, which used to merge the title into
    # the body and destroy the segmentation the rule test depends on.
    finding = (
        "**Preserve PR edits in the freshness boundary**\n\n"
        "When a same-head title or body edit races with scheduled reconciliation, "
        "`evaluate()` accepts the previous Governance state."
    )
    assert semantic.rule_statement(finding) == "Preserve PR edits in the freshness boundary"
    assert any("same-head title" in condition for condition in semantic.applicability(finding))


def test_execution_boundary_is_resolved_from_real_nesting() -> None:
    source = "class A:\n    def m(self):\n        x = 1\n        return x\n"
    assert semantic.enclosing_symbols("a.py", 4, source) == ("class A", "def m")
    assert semantic.enclosing_symbols("a.py", None, source) == ()
    assert semantic.enclosing_symbols("a.py", 99, source) == ()


def test_the_owner_disposition_is_never_read_as_the_invariant() -> None:
    record = make_record(
        [make_item(event_id="review-comment-1")],
        comments=[
            {
                "id": 1,
                "path": "src/hunter/x.py",
                "line": 3,
                "user": {"login": "bot"},
                "body": "**Reject non-ASCII signatures before compare_digest**\n\n"
                "An untrusted caller can construct a valid authorization.",
            },
            {
                "id": 2,
                "in_reply_to_id": 1,
                "user": {"login": OWNER},
                "body": "Fixed on current pushed HEAD abc1234. candidate_mode now checks the controller.",
            },
        ],
    )
    record["ledger"]["items"][0]["observation"] = {
        "event_id": "review-comment-1",
        "path": "src/hunter/x.py",
        "line": 3,
        "message": "Fixed on current pushed HEAD abc1234.",
        "reviewer": OWNER,
    }
    item = _rec(observation_id="obs-review-comment-1", owner_authority="thread_reply:thread_reply")
    extraction = semantic.extract(item, record, FakeHistory(files={"src/hunter/x.py": "x = 1\n"}), OWNER)
    assert extraction.finding_text_source == "parent_finding_on_thread"
    assert extraction.violated_invariant == "Reject non-ASCII signatures before compare_digest"


def test_an_extracted_candidate_is_not_evidence_until_a_verdict_says_so() -> None:
    extractions = {"obs-1": _extraction(invariant_verified=False, verification_evidence="")}
    assert not extractions["obs-1"].clusterable
    verdicted = semantic.apply_verdicts(
        extractions, {"obs-1": {"verified": True, "evidence": "owner states the obligation"}}
    )
    assert verdicted["obs-1"].clusterable


def test_an_unmentioned_observation_is_never_verified_by_default() -> None:
    out = semantic.apply_verdicts({"obs-1": _extraction(invariant_verified=False)}, {"obs-9": {"verified": True}})
    assert not out["obs-1"].invariant_verified


def test_a_rejecting_verdict_cannot_verify_a_candidate() -> None:
    out = semantic.apply_verdicts(
        {"obs-1": _extraction(invariant_verified=False)}, {"obs-1": {"verified": False, "evidence": "chrome"}}
    )
    assert not out["obs-1"].invariant_verified
    assert out["obs-1"].ambiguous_reasons


# ---------------------------------------------------------------------------
# Recurrence detection
# ---------------------------------------------------------------------------


def test_a_cluster_never_infers_a_shared_invariant() -> None:
    extractions = {
        "a": _extraction(observation_id="a", violated_invariant="Reject non-ASCII signatures"),
        "b": _extraction(observation_id="b", violated_invariant="Reject non-ASCII signatures"),
    }
    clusters = semantic.recurrence_clusters(extractions, {"a": ("src/x.py", ("f",)), "b": ("src/x.py", ("f",))})
    assert clusters
    assert all(c.same_invariant is None for c in clusters)
    assert all("unverified-hypothesis" not in json.dumps(c.to_json()) for c in clusters)


def test_a_verdict_can_record_that_two_findings_differ() -> None:
    extractions = {
        "a": _extraction(observation_id="a", violated_invariant="Reject non-ASCII signatures"),
        "b": _extraction(observation_id="b", violated_invariant="Reject non-ASCII signatures"),
    }
    clusters = semantic.recurrence_clusters(extractions, {"a": ("s", ()), "b": ("s", ())})
    cluster = clusters[0]
    stamped = recon._apply_recurrence_verdicts(
        [cluster], {f"{cluster.basis}::{cluster.key}": {"same_invariant": False, "evidence": "different rules"}}
    )
    assert stamped[0].same_invariant is False


def test_repeating_wording_alone_does_not_license_a_family() -> None:
    # Identical text is surfaced, but the verdict is still required.
    extractions = {
        oid: _extraction(observation_id=oid, violated_invariant="Treat finding text as untrusted review data")
        for oid in ("a", "b", "c")
    }
    clusters = semantic.recurrence_clusters(extractions, {oid: ("s", ()) for oid in extractions})
    wording = [c for c in clusters if c.basis == "invariant_wording"]
    assert len(wording) == 1
    assert wording[0].same_invariant is None


# ---------------------------------------------------------------------------
# Non-recurrence resolution
# ---------------------------------------------------------------------------


def _no_clusters() -> list[Any]:
    return []


def test_a_confirmed_finding_resolves_only_when_every_condition_holds() -> None:
    items = [
        _rec(observation_id="obs-1", fix_reference="abc1234", regression_test_added=True, path_exists_at_base=True)
    ]
    out, summary = recon.resolve_non_recurring(items, {"obs-1": _extraction()}, _no_clusters())
    assert out[0].disposition == recon.RESOLVED_NON_RECURRING
    assert out[0].rule == recon.RULE_RESOLVED_NON_RECURRING
    assert out[0].historical_defect_truth == recon.DEFECT_WAS_REAL_AND_CORRECTED
    assert summary["resolved_non_recurring"] == 1


def test_no_recurrence_resolution_without_a_verified_invariant() -> None:
    items = [
        _rec(observation_id="obs-1", fix_reference="abc1234", regression_test_added=True, path_exists_at_base=True)
    ]
    out, summary = recon.resolve_non_recurring(items, {"obs-1": _extraction(invariant_verified=False)}, _no_clusters())
    assert out[0].disposition == recon.CONFIRMED
    assert summary["resolved_non_recurring"] == 0
    assert "no verified violated invariant" in summary["withheld_reasons"]


def test_no_recurrence_resolution_without_a_verified_fix_commit() -> None:
    items = [_rec(observation_id="obs-1", fix_reference="", regression_test_added=True, path_exists_at_base=True)]
    out, summary = recon.resolve_non_recurring(items, {"obs-1": _extraction()}, _no_clusters())
    assert out[0].disposition == recon.CONFIRMED
    assert "fix is not traceable to a verified commit" in summary["withheld_reasons"]


def test_no_recurrence_resolution_without_prevention_at_base() -> None:
    items = [_rec(observation_id="obs-1", fix_reference="abc1234", path_exists_at_base=True)]
    out, summary = recon.resolve_non_recurring(items, {"obs-1": _extraction()}, _no_clusters())
    assert out[0].disposition == recon.CONFIRMED
    assert "no regression test or guard on the integration base" in summary["withheld_reasons"]


def test_a_finding_awaiting_recurrence_review_is_never_resolved() -> None:
    items = [
        _rec(observation_id="obs-1", fix_reference="abc1234", regression_test_added=True, path_exists_at_base=True)
    ]
    cluster = semantic.RecurrenceCluster(basis="code_location", key="src/x.py::f", observation_ids=("obs-1",))
    out, summary = recon.resolve_non_recurring(items, {"obs-1": _extraction()}, [cluster])
    assert out[0].disposition == recon.CONFIRMED
    assert "a shared violated invariant is still under review" in summary["withheld_reasons"]


def test_a_reviewed_different_invariant_cluster_frees_the_finding() -> None:
    items = [
        _rec(observation_id="obs-1", fix_reference="abc1234", regression_test_added=True, path_exists_at_base=True)
    ]
    cluster = semantic.RecurrenceCluster(
        basis="code_location", key="k", observation_ids=("obs-1",), same_invariant=False
    )
    out, _summary = recon.resolve_non_recurring(items, {"obs-1": _extraction()}, [cluster])
    assert out[0].disposition == recon.RESOLVED_NON_RECURRING


def test_only_confirmed_findings_are_candidates_for_resolution() -> None:
    items = [
        _rec(
            observation_id="obs-1",
            disposition=recon.OWNER_REQUIRED,
            fix_reference="abc1234",
            regression_test_added=True,
            path_exists_at_base=True,
        ),
        _rec(
            observation_id="obs-2",
            disposition=recon.EXCLUDED_STYLE,
            fix_reference="abc1234",
            regression_test_added=True,
            path_exists_at_base=True,
        ),
    ]
    out, summary = recon.resolve_non_recurring(items, {"obs-1": _extraction(), "obs-2": _extraction()}, _no_clusters())
    assert [i.disposition for i in out] == [recon.OWNER_REQUIRED, recon.EXCLUDED_STYLE]
    assert summary["resolved_non_recurring"] == 0


def test_a_resolved_one_off_does_not_count_against_the_canonical_mapping_gap() -> None:
    items = [
        _rec(observation_id="obs-1", disposition=recon.RESOLVED_NON_RECURRING),
        _rec(observation_id="obs-2", disposition=recon.CONFIRMED),
    ]
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=Path("."),
        reconstructions=items,
        scan_statused=2,
        scan_total=2,
        existing_mappings={},
        proposals=[],
        owner_table={"group_count": 0},
    )
    # The one confirmed finding is still unmapped; the resolved one is not.
    assert manifest["canonical_mapping_gap"] == 1


def test_the_closure_manifest_reports_the_non_recurrence_outcome() -> None:
    manifest = recon.build_reconstruction_manifest(
        repository=REPO,
        data_dir=Path("."),
        reconstructions=[_rec(observation_id="obs-1", disposition=recon.RESOLVED_NON_RECURRING)],
        scan_statused=1,
        scan_total=1,
        existing_mappings={},
        proposals=[],
        owner_table={"group_count": 0},
        non_recurrence={"resolved_non_recurring": 1, "withheld_reasons": {}},
    )
    assert manifest["non_recurrence_resolution"]["resolved_non_recurring"] == 1
    assert manifest["dispositions"][recon.RESOLVED_NON_RECURRING] == 1


# ---------------------------------------------------------------------------
# Declared families and the review surface
# ---------------------------------------------------------------------------


def test_a_verdict_file_may_carry_both_verdicts_and_declarations() -> None:
    payload = {
        "schema_version": 1,
        "verdicts": {"code_location::src/x.py::f": {"same_invariant": True}},
        "clusters": {"declared-family": {"observation_ids": ["a", "b"]}},
    }
    assert semantic.verdict_map(payload) == {"code_location::src/x.py::f": {"same_invariant": True}}
    # Unwrapping the verdicts must not cost the declarations filed beside them.
    assert list(payload["clusters"].keys()) == ["declared-family"]
    # Reading the verdicts and reading the declarations off one payload both work.
    assert semantic.verdict_map(payload)["code_location::src/x.py::f"]["same_invariant"] is True
    assert semantic.declared_clusters(payload)[0].key == "declared-family"


def test_a_verdict_map_accepts_a_bare_map() -> None:
    assert semantic.verdict_map({"obs-1": {"verified": True}}) == {"obs-1": {"verified": True}}
    assert semantic.verdict_map(None) == {}


def test_a_declared_cluster_becomes_a_cluster_with_its_stated_invariant() -> None:
    clusters = semantic.declared_clusters(
        {
            "clusters": {
                "ingress-decision": {
                    "observation_ids": ["b", "a"],
                    "violated_invariant": "Admission requires an authorized ingress decision.",
                    "evidence": "fixed in two pull requests against the same rule",
                    "family_id": "ingress-decision",
                }
            }
        }
    )
    assert len(clusters) == 1
    assert clusters[0].basis == "declared"
    assert clusters[0].observation_ids == ("a", "b")
    assert clusters[0].invariants == ("Admission requires an authorized ingress decision.",)


def test_a_declaration_of_one_finding_is_never_a_family() -> None:
    assert semantic.declared_clusters({"clusters": {"x": {"observation_ids": ["a"]}}}) == []


def test_a_declared_family_whose_id_is_not_canonical_is_a_new_family_proposal() -> None:
    items = [
        _rec(observation_id="obs-1", source_pr=391, fix_reference="4c5dd059"),
        _rec(observation_id="obs-2", source_pr=404, fix_reference="d3ca3c612"),
    ]
    extractions = {
        "obs-1": _extraction(observation_id="obs-1", source_pr=391),
        "obs-2": _extraction(observation_id="obs-2", source_pr=404),
    }
    clusters = semantic.declared_clusters(
        {
            "clusters": {
                "ingress-decision": {
                    "observation_ids": ["obs-1", "obs-2"],
                    "violated_invariant": "Admission requires an authorized ingress decision.",
                    "family_id": "ingress-decision",
                }
            }
        }
    )
    out, families = recon.apply_family_mappings(items, extractions, clusters, frozenset({"DFF-001"}))
    # The slug is a proposal, not registry history, so it may not be recorded as
    # a mapping onto a canonical family that does not exist.
    assert [i.disposition for i in out] == [recon.PROPOSED_NEW_FAMILY] * 2
    assert all(i.rule == recon.RULE_PROPOSED_NEW_FAMILY for i in out)
    assert families[0].violated_invariant == "Admission requires an authorized ingress decision."


def test_a_verdict_naming_a_canonical_family_maps_onto_it() -> None:
    items = [
        _rec(observation_id="obs-1", source_pr=391, fix_reference="4c5dd059"),
        _rec(observation_id="obs-2", source_pr=404, fix_reference="d3ca3c612"),
    ]
    extractions = {
        "obs-1": _extraction(observation_id="obs-1", source_pr=391),
        "obs-2": _extraction(observation_id="obs-2", source_pr=404),
    }
    clusters = semantic.declared_clusters(
        {
            "clusters": {
                "DFF-001": {
                    "observation_ids": ["obs-1", "obs-2"],
                    "violated_invariant": "Admission requires an authorized ingress decision.",
                    "family_id": "DFF-001",
                }
            }
        }
    )
    out, _families = recon.apply_family_mappings(items, extractions, clusters, frozenset({"DFF-001"}))
    assert [i.disposition for i in out] == [recon.EXISTING_FAMILY] * 2
    assert {i.claimed_family_id for i in out} == {"DFF-001"}


def test_a_finding_and_its_own_fix_in_one_pr_are_never_published_as_a_family() -> None:
    items = [
        _rec(observation_id="obs-1", source_pr=258, fix_reference="a7f6307"),
        _rec(observation_id="obs-2", source_pr=258, fix_reference="a7f6307"),
    ]
    extractions = {
        "obs-1": _extraction(observation_id="obs-1", source_pr=258),
        "obs-2": _extraction(observation_id="obs-2", source_pr=258),
    }
    cluster = semantic.RecurrenceCluster(
        basis="code_location",
        key="src/x.py::f",
        observation_ids=("obs-1", "obs-2"),
        same_invariant=True,
    )
    out, families = recon.apply_family_mappings(items, extractions, [cluster])
    assert families == []
    assert [i.disposition for i in out] == [recon.CONFIRMED] * 2


def test_a_shared_invariant_verdict_needs_the_members_to_agree_on_the_rule() -> None:
    items = [
        _rec(observation_id="obs-1", source_pr=391),
        _rec(observation_id="obs-2", source_pr=404),
    ]
    extractions = {
        "obs-1": _extraction(observation_id="obs-1", source_pr=391, violated_invariant="Admit only on ingress proof."),
        "obs-2": _extraction(
            observation_id="obs-2", source_pr=404, violated_invariant="Bind commits to an ingress decision."
        ),
    }
    cluster = semantic.RecurrenceCluster(
        basis="code_location",
        key="src/x.py::f",
        observation_ids=("obs-1", "obs-2"),
        same_invariant=True,
    )
    _out, families = recon.apply_family_mappings(items, extractions, [cluster])
    # A weak signal may not author the family invariant, so members that state
    # different rules never publish one.
    assert families == []


def test_an_unreviewed_cluster_blocks_resolution_and_a_reviewed_one_does_not() -> None:
    items = [
        _rec(
            observation_id="obs-1",
            source_pr=258,
            fix_reference="a7f6307",
            regression_test_added=True,
            path_exists_at_base=True,
        ),
        _rec(
            observation_id="obs-2",
            source_pr=258,
            fix_reference="a7f6307",
            regression_test_added=True,
            path_exists_at_base=True,
        ),
    ]
    extractions = {
        "obs-1": _extraction(observation_id="obs-1", source_pr=258),
        "obs-2": _extraction(observation_id="obs-2", source_pr=258),
    }
    unreviewed = semantic.RecurrenceCluster(basis="code_location", key="k", observation_ids=("obs-1", "obs-2"))
    out, summary = recon.resolve_non_recurring(items, extractions, [unreviewed])
    assert [i.disposition for i in out] == [recon.CONFIRMED] * 2

    # Once reviewed as one defect inside a single change, the same two findings
    # are no longer waiting on anybody and can resolve as fixed one-offs.
    reviewed = semantic.RecurrenceCluster(
        basis="code_location", key="k", observation_ids=("obs-1", "obs-2"), same_invariant=True
    )
    out, summary = recon.resolve_non_recurring(items, extractions, [reviewed])
    assert [i.disposition for i in out] == [recon.RESOLVED_NON_RECURRING] * 2
    assert summary["resolved_non_recurring"] == 2


def test_a_claimed_cross_pr_family_that_published_nothing_still_blocks() -> None:
    items = [
        _rec(
            observation_id="obs-1",
            source_pr=391,
            fix_reference="4c5dd059",
            regression_test_added=True,
            path_exists_at_base=True,
        ),
        _rec(
            observation_id="obs-2",
            source_pr=404,
            fix_reference="d3ca3c612",
            regression_test_added=True,
            path_exists_at_base=True,
        ),
    ]
    extractions = {
        "obs-1": _extraction(observation_id="obs-1", source_pr=391, violated_invariant="Admit only on ingress proof."),
        "obs-2": _extraction(
            observation_id="obs-2", source_pr=404, violated_invariant="Bind commits to an ingress decision."
        ),
    }
    cluster = semantic.RecurrenceCluster(
        basis="code_location", key="k", observation_ids=("obs-1", "obs-2"), same_invariant=True
    )
    out, _summary = recon.resolve_non_recurring(items, extractions, [cluster])
    # A reviewer said these share an invariant, so they may not quietly resolve
    # as unrelated one-offs just because no family could be published.
    assert [i.disposition for i in out] == [recon.CONFIRMED] * 2


def test_only_the_confirmed_population_enters_recurrence_review() -> None:
    items = [
        _rec(observation_id="obs-1", disposition=recon.CONFIRMED, path="src/x.py"),
        _rec(observation_id="obs-2", disposition=recon.EXCLUDED_NOT_A_CLAIM, path="src/x.py"),
        _rec(observation_id="obs-3", disposition=recon.OWNER_REQUIRED, path="src/x.py"),
        _rec(observation_id="obs-4", disposition=recon.EXCLUDED_STYLE, path="src/x.py"),
    ]
    extractions = {f"obs-{n}": _extraction(observation_id=f"obs-{n}") for n in range(1, 5)}
    view, locations = recon._confirmed_recurrence_view(items, extractions)
    # A status line or a closed row co-located with a real finding must not be
    # presented as a recurrence candidate for it.
    assert set(view) == {"obs-1"}
    assert set(locations) == {"obs-1"}
    assert locations["obs-1"] == ("src/x.py", ())


# ---------------------------------------------------------------------------
# Grouped/summary review bodies: claims carried inside a container
# ---------------------------------------------------------------------------
#
# Real bodies, trimmed only in length. Every shape below exists in the frozen
# ledger; the counts asserted against them are the counts the corpus produces, so
# a regression that stopped extracting, or started extracting chrome, fails here.

CODERABBIT_CONTAINER = """**Actionable comments posted: 2**

> [!NOTE]
> Quiet mode is enabled, so only the most important comments were posted inline. Other review comments are grouped below.

<details>
<summary>🟡 Other comments (2)</summary><blockquote>

<details>
<summary>scripts/bootstrap_hunter_review_runner.sh-11-11 (1)</summary><blockquote>

`11-11`: _Stability & Availability_ | _Minor_ | _Quick win_

**Add a bounded timeout to the Ollama health check.**

`curl` has no connection or total timeout. If the health check accepts the connection
but never completes the response, bootstrap never reaches runner startup.

</blockquote></details>

<details>
<summary>tests/test_hunter_local_reviewer_bootstrap.py-15-17 (1)</summary><blockquote>

`15-17`: _Functional Correctness_ | _Minor_ | _Quick win_

**Detect quoted token output commands.**

These assertions do not match a quoted form, so the test can still pass after a
token-logging regression.

</blockquote></details>
</details>
"""

COPILOT_SUPPRESSED = """## Pull request overview

Copilot reviewed 2 out of 2 changed files in this pull request and generated no new comments.

<details>
<summary>Suppressed comments (2)</summary>

**docs/AI_REVIEW_PROTOCOL.md:177**
* This sentence restates lifecycle implications and duplicates the invalidation
rule already added to `docs/DEVELOPMENT_GOVERNANCE.md`. To preserve single
ownership, keep this document focused on review validity.
```
The passing outcome is valid only for the exact reviewed source-head pair.
```

**src/hunter/evidence_intelligence/pre_model_persistence.py:204**
* `save` never checks that `source_handling_authority.cutoff` is timezone-aware, so
a naive cutoff raises `TypeError` instead of a governed lineage error.
```
"""

INDEX_ONLY_OVERVIEW = """<!-- ccr-overview-v2 -->
## Copilot review overview

### 🟡 Changes recommended
One or more issues must be addressed before approval. *Get a fresh assessment by
requesting another Copilot review.*

**New Resolved since last review**

#### Post-creation trigger failures are misclassified as zero-id refusals ([#discussion_r4054618366](https://github.com/o/r/pull/473#discussion_r4054618366))

#### Readiness publication is not bound to the observed head ([#discussion_r4054618367](https://github.com/o/r/pull/473#discussion_r4054618367))
"""


def _container_item(message: str, *, event_id: str = "review-comment-container") -> dict[str, Any]:
    item = make_item(event_id=event_id, path=None, line=None, message=message)
    item["observation"]["source_pr"] = 1
    return item


def test_grouped_body_is_a_container_not_a_body_that_asserted_nothing() -> None:
    """The container is recorded as a container, never as "no claim here".

    This body carries two real findings. Calling it "not a claim" is what deleted
    them from history: ``clean_comment_text`` strips ``<details>`` wholesale, so
    the finding text was gone before any claim test ran and only the vendor's own
    count line survived.
    """
    item = _container_item(CODERABBIT_CONTAINER)
    parent = decide(make_record([item]), item)
    assert parent.disposition == recon.EXCLUDED_SUMMARY_CONTAINER
    assert parent.rule == recon.RULE_SUMMARY_CONTAINER
    assert parent.disposition != recon.EXCLUDED_NOT_A_CLAIM
    assert parent.evidence_signals["derived_claim_count"] == 2
    # The container's own invariant and symbols belong to the claims it carried,
    # not to itself, so they are not asserted on its behalf.
    assert parent.invariant == ""
    assert parent.symbols == ()


def test_grouped_body_yields_one_derived_claim_per_anchored_block() -> None:
    item = _container_item(CODERABBIT_CONTAINER)
    claims, reason = recon.derived_claims(item["observation"], item["observation_id"])
    assert len(claims) == 2, reason
    assert [c["path"] for c in claims] == [
        "scripts/bootstrap_hunter_review_runner.sh",
        "tests/test_hunter_local_reviewer_bootstrap.py",
    ]
    assert [(c["line"], c["line_end"]) for c in claims] == [(11, 11), (15, 17)]
    assert all(c["grammar"] == "coderabbit-claim-block" for c in claims)
    # CodeRabbit's own decoration (range, category, severity, effort) is not the
    # claim, and the claim's own text must survive it.
    assert claims[0]["claim_text"].startswith("Add a bounded timeout to the Ollama health check.")
    assert "Quick win" not in claims[0]["claim_text"]
    assert claims[1]["claim_text"].startswith("Detect quoted token output commands.")


def test_grouped_body_line_range_is_recovered_from_the_reviewers_own_metadata() -> None:
    """A block whose summary omits the range still gets its exact anchor.

    CodeRabbit writes the range on the body's first line, not in the summary, for
    grouped "Outside diff range" and nitpick blocks. Dropping it would leave the
    claim with a path and no location.
    """
    body = (
        "**Actionable comments posted: 1**\n\n"
        "<details>\n<summary>docs/GOVERNANCE_ENFORCEMENT.md (1)</summary><blockquote>\n\n"
        "`158-167`: _Maintainability_ | _Minor_ | _Quick win_\n\n"
        "**Refer to the pull request as the merge event.**\n\n"
        'The line says "once the issue is merged", but an issue is closed, not '
        "merged, so operators cannot tell when the exception ends.\n\n"
        "</blockquote></details>\n"
    )
    claims, reason = recon.derived_claims({"message": body}, "obs-parent")
    assert len(claims) == 1, reason
    assert (claims[0]["line"], claims[0]["line_end"]) == (158, 167)
    assert claims[0]["claim_text"].startswith("Refer to the pull request as the merge event.")


def test_suppressed_comment_entries_become_derived_claims() -> None:
    item = _container_item(COPILOT_SUPPRESSED)
    claims, reason = recon.derived_claims(item["observation"], item["observation_id"])
    assert len(claims) == 2, reason
    assert [c["path"] for c in claims] == [
        "docs/AI_REVIEW_PROTOCOL.md",
        "src/hunter/evidence_intelligence/pre_model_persistence.py",
    ]
    assert [c["line"] for c in claims] == [177, 204]
    assert all(c["grammar"] == "copilot-suppressed-entry" for c in claims)
    assert "duplicates the invalidation rule" in claims[0]["claim_text"]


def test_index_only_overview_is_never_expanded() -> None:
    """A thread index is navigation, not a finding.

    The overview lists truncated titles and anchors to discussion threads: no rule,
    no evidence, no location. Expanding it would manufacture claims.
    """
    item = _container_item(INDEX_ONLY_OVERVIEW)
    claims, reason = recon.derived_claims(item["observation"], item["observation_id"])
    assert claims == []
    assert "index-only" in reason
    parent = decide(make_record([item]), item)
    assert parent.disposition == recon.EXCLUDED_NOT_A_CLAIM
    assert parent.rule == recon.base.RULE_NO_LOCATION_ANCHOR
    assert "index-only" in parent.evidence


def test_a_body_with_no_anchor_is_still_just_chrome() -> None:
    claims, reason = recon.derived_claims(
        {"message": "**Actionable comments posted: 0**\n\nNo comments to post."}, "obs-parent"
    )
    assert claims == []
    assert "no anchored claim block" in reason


def test_derived_claim_identity_is_stable_and_joins_back_to_its_parent() -> None:
    item = _container_item(CODERABBIT_CONTAINER)
    first, _ = recon.derived_claims(item["observation"], item["observation_id"])
    second, _ = recon.derived_claims(item["observation"], item["observation_id"])
    assert [c["observation_id"] for c in first] == [c["observation_id"] for c in second]
    assert len({c["observation_id"] for c in first}) == 2
    assert all(c["parent_observation_id"] == item["observation_id"] for c in first)
    assert all(not c["observation_id"].startswith("obs-") for c in first)
    # Two blocks in one body are two different claims, not one duplicated claim.
    assert first[0]["observation_id"] != first[1]["observation_id"]


def test_derived_claims_are_adjudicated_by_the_identical_rule_order() -> None:
    """A recovered claim is disposed of exactly as a posted claim would be.

    Nothing about a claim changes because a reviewer grouped it: the same rules,
    the same evidence, the same owner-authority limits apply to it.
    """
    body = (
        "**Actionable comments posted: 2**\n\n"
        "<details>\n<summary>src/hunter/x.py-10-12 (1)</summary><blockquote>\n\n"
        "`10-12`: _Data Integrity_ | _Major_ | _Quick win_\n\n"
        "**The replay path ignores applicability_end.**\n\n"
        "An expired rule is still applied, so a stale record is admitted.\n\n"
        "</blockquote></details>\n\n"
        "<details>\n<summary>src/hunter/x.py-30-31 (1)</summary><blockquote>\n\n"
        "`30-31`: _Maintainability_ | _Trivial_ | _Low value_\n\n"
        "**Rename the variable to match the module alias.**\n\n"
        "</blockquote></details>\n"
    )
    item = _container_item(body)
    record = make_record([item], pr_number=1)
    history = FakeHistory(
        files={"src/hunter/x.py": "def replay_rule():\n    return applicability_end\n"},
        tests={"applicability_end": ["tests/test_x.py"]},
    )
    parent = recon.reconstruct(
        item["observation"],
        str(item["state"]),
        record,
        observation_id=item["observation_id"],
        owner_login=OWNER,
        history=history,
        families=[],
        anchors={},
    )
    _top, derived = recon.expand_derived_claims(
        [parent], record, owner_login=OWNER, history=history, families=[], anchors={}
    )
    assert [d.disposition for d in derived] == [recon.OWNER_REQUIRED, recon.EXCLUDED_STYLE]
    assert derived[0].rule == recon.RULE_INSUFFICIENT_EVIDENCE
    assert derived[1].rule == recon.RULE_NON_BEHAVIORAL
    # The container is not counted twice, and no derived claim is filed as chrome.
    assert parent.disposition == recon.EXCLUDED_SUMMARY_CONTAINER
    assert all(d.disposition not in {recon.EXCLUDED_NOT_A_CLAIM, recon.EXCLUDED_SUMMARY_CONTAINER} for d in derived)


def test_derived_claim_records_exact_parent_provenance() -> None:
    item = _container_item(CODERABBIT_CONTAINER)
    record = make_record([item], pr_number=1)
    parent = recon.reconstruct(
        item["observation"],
        str(item["state"]),
        record,
        observation_id=item["observation_id"],
        owner_login=OWNER,
        history=FakeHistory(),
        families=[],
        anchors={},
    )
    _top, derived = recon.expand_derived_claims(
        [parent], record, owner_login=OWNER, history=FakeHistory(), families=[], anchors={}
    )
    provenance = derived[0].derived_from
    assert provenance is not None
    assert provenance["parent_observation_id"] == item["observation_id"]
    assert provenance["parent_event_id"] == "review-comment-container"
    assert provenance["parent_reviewer"] == "bot[bot]"
    assert provenance["parent_reviewed_head_sha"] == "a" * 40
    assert provenance["block_index"] == 0
    assert provenance["grammar"] == "coderabbit-claim-block"
    assert provenance["anchor_path"] == "scripts/bootstrap_hunter_review_runner.sh"
    assert (provenance["anchor_line"], provenance["anchor_line_end"]) == (11, 11)
    assert derived[0].path == "scripts/bootstrap_hunter_review_runner.sh"
    assert derived[0].line == 11
    assert derived[0].evidence_signals["derived_block_index"] == 0
    # The claim travels with its provenance, so a reviewer can read the recovered
    # finding itself rather than a disposition string summarizing it.
    assert provenance["claim_text"].startswith("Add a bounded timeout to the Ollama health check.")
    # A ledger observation never carries provenance: only a derived claim does.
    assert parent.derived_from is None


def test_a_claim_the_reviewer_also_posted_alone_is_not_counted_twice() -> None:
    """A finding posted once alone and once in the summary is one finding.

    Copilot and CodeRabbit both publish the grouped copy and can also post the
    same comment inline. Deriving the grouped copy as well would add a claim the
    owner has already been shown, and the owner queue is the thing this mission
    has to keep honest.
    """
    body = (
        "**Actionable comments posted: 1**\n\n"
        "<details>\n<summary>docs/ADR/README.md-44-44 (1)</summary><blockquote>\n\n"
        "`44-44`: _Documentation_ | _Minor_ | _Quick win_\n\n"
        "**This repository-wide ADR guidance becomes time-sensitive by calling out ADR 0028 "
        "specifically.**\n\n"
        "Once implementation completes, this sentence will become incorrect, so the README "
        "should stay generic.\n\n"
        "</blockquote></details>\n"
    )
    container = _container_item(body)
    already_posted = make_item(
        event_id="review-comment-inline",
        path="docs/ADR/README.md",
        line=44,
        message=(
            "This repository-wide ADR guidance becomes time-sensitive by calling out ADR 0028 "
            "specifically; once implementation completes, this sentence will become incorrect. "
            "The README should stay generic."
        ),
    )
    already_posted["observation"]["source_pr"] = 1
    record = make_record([container, already_posted], pr_number=1)
    history = FakeHistory(files={"docs/ADR/README.md": "ADR 0028 is accepted.\n"})
    parent = recon.reconstruct(
        container["observation"],
        str(container["state"]),
        record,
        observation_id=container["observation_id"],
        owner_login=OWNER,
        history=history,
        families=[],
        anchors={},
    )
    assert parent.evidence_signals["derived_claim_count"] == 1
    top, derived = recon.expand_derived_claims(
        [parent], record, owner_login=OWNER, history=history, families=[], anchors={}
    )
    assert derived == []
    # The suppression names its twin instead of quietly dropping the claim.
    twins = top[0].evidence_signals["derived_claim_duplicates"]
    assert twins == {
        recon.derived_claim_id(container["observation_id"], 0, "docs/ADR/README.md", 44, 44): (
            already_posted["observation_id"]
        )
    }
    assert top[0].evidence_signals["derived_claim_count"] == 0
    # The container keeps its own place in the ledger's item count, and the inline
    # original is still adjudicated on its own by the normal path, exactly once.
    assert [item.observation_id for item in top] == [container["observation_id"]]
    assert top[0].disposition == recon.EXCLUDED_SUMMARY_CONTAINER


def test_a_shared_topic_or_a_different_anchor_is_not_treated_as_a_duplicate() -> None:
    """Overlapping wording is not duplication, and must never suppress a claim.

    Suppressing on text alone would delete real findings: a summary comment that
    lists several findings from one pull request overlaps each of them, and two
    reviewers on the same file share boilerplate.
    """
    body = (
        "**Actionable comments posted: 1**\n\n"
        "<details>\n<summary>src/hunter/other.py-900-901 (1)</summary><blockquote>\n\n"
        "`900-901`: _Documentation_ | _Minor_ | _Quick win_\n\n"
        "**The fixture module alias is shadowed by a local variable.**\n\n"
        "The module alias must stay reachable inside the test.\n\n"
        "</blockquote></details>\n"
    )
    container = _container_item(body)
    same_topic_elsewhere = make_item(
        event_id="review-comment-inline",
        path="src/hunter/other.py",
        line=12,
        message="The fixture module alias is shadowed by a local variable in this test file.",
    )
    same_topic_elsewhere["observation"]["source_pr"] = 1
    record = make_record([container, same_topic_elsewhere], pr_number=1)
    history = FakeHistory(files={"src/hunter/other.py": "import fixture\n"})
    parent = recon.reconstruct(
        container["observation"],
        str(container["state"]),
        record,
        observation_id=container["observation_id"],
        owner_login=OWNER,
        history=history,
        families=[],
        anchors={},
    )
    _top, derived = recon.expand_derived_claims(
        [parent], record, owner_login=OWNER, history=history, families=[], anchors={}
    )
    assert len(derived) == 1
    assert derived[0].derived_from is not None
    assert derived[0].derived_from["anchor_line"] == 900


def test_the_same_anchor_alone_does_not_make_a_claim_a_duplicate() -> None:
    """Two findings on one line are two findings.

    Copilot raises several distinct issues on a single long line of prose, and the
    owner replies on that same line. Suppressing on the anchor alone would delete
    those claims silently, which is the one failure this work cannot make.
    """
    body = (
        "**Actionable comments posted: 1**\n\n"
        "<details>\n<summary>docs/ADR/README.md-44-44 (1)</summary><blockquote>\n\n"
        "`44-44`: _Documentation_ | _Minor_ | _Quick win_\n\n"
        "**The ADR index leaks implementation status into durable guidance.**\n\n"
        "The index should stay generic and leave implementation status to the ADR "
        "entry itself.\n\n"
        "</blockquote></details>\n"
    )
    container = _container_item(body)
    unrelated_on_same_line = make_item(
        event_id="review-comment-inline",
        path="docs/ADR/README.md",
        line=44,
        message="This table is missing the revision column, so the accepted revision cannot be read off it.",
    )
    unrelated_on_same_line["observation"]["source_pr"] = 1
    record = make_record([container, unrelated_on_same_line], pr_number=1)
    history = FakeHistory(files={"docs/ADR/README.md": "| ADR | Title |\n"})
    parent = recon.reconstruct(
        container["observation"],
        str(container["state"]),
        record,
        observation_id=container["observation_id"],
        owner_login=OWNER,
        history=history,
        families=[],
        anchors={},
    )
    _top, derived = recon.expand_derived_claims(
        [parent], record, owner_login=OWNER, history=history, families=[], anchors={}
    )
    assert len(derived) == 1
    assert "derived_claim_duplicates" not in derived[0].evidence_signals


def test_derived_claims_are_counted_apart_from_the_ledger_and_inside_the_gap(tmp_path: Path) -> None:
    """Reconciliation stays exact; the gap grows by exactly what was recovered.

    Counting derived claims as ledger items would break reconciliation against a
    ledger they are deliberately not in. Leaving them out of the gap would
    understate the work that recovering them created.
    """
    data_dir = _seed(tmp_path, [_container_item(CODERABBIT_CONTAINER)])
    registry = data_dir / "registry.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    frozen = (data_dir / "prs" / "1.json").read_bytes()

    manifest = recon.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
        ref="HEAD",
        resume=False,
    )
    reconciliation = manifest["reconciliation"]
    assert reconciliation["raw_scan_ledger_items"] == 1
    assert reconciliation["reconstructed_items"] == 1, "a derived claim is not a ledger item"
    assert reconciliation["disposition_sum"] == 1
    assert reconciliation["reconciles_exactly"] is True
    assert reconciliation["summary_container_bodies"] == 1
    assert reconciliation["derived_claims"] == 2
    assert reconciliation["derived_claims_reconcile_exactly"] is True
    assert manifest["dispositions"][recon.EXCLUDED_SUMMARY_CONTAINER] == 1
    assert manifest["dispositions"][recon.EXCLUDED_NOT_A_CLAIM] == 0
    derived = manifest["derived_claims"]
    assert derived["count"] == 2
    assert sum(derived["dispositions"].values()) == 2
    assert derived["parent_container_bodies"] == 1
    # The frozen ledger is untouched: this is a second, additive view.
    assert (data_dir / "prs" / "1.json").read_bytes() == frozen

    rows = json.loads((data_dir / "reconstruction" / "1.json").read_text())["reconstructions"]
    assert len(rows) == 3
    assert sum(1 for row in rows if row["derived_from"]) == 2
    assert sum(1 for row in rows if not row["derived_from"]) == 1
    assert len({row["observation_id"] for row in rows}) == 3


def test_derived_claim_gap_is_counted_in_the_closure_dimensions(tmp_path: Path) -> None:
    """A recovered confirmed claim adds to the gap, it does not vanish from it."""
    data_dir = _seed(tmp_path, [_container_item(CODERABBIT_CONTAINER)])
    registry = data_dir / "registry.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    manifest = recon.run_reconstruction(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
        ref="HEAD",
        resume=False,
    )
    derived = manifest["derived_claims"]
    counted = derived["dispositions"].get(recon.CONFIRMED, 0) + derived["dispositions"].get(recon.OWNER_REQUIRED, 0)
    assert manifest["canonical_mapping_gap"] + manifest["unresolved_evidence_count"] >= counted
    assert (
        manifest["owner_required"]["count"]
        == manifest["owner_required"]["ledger_item_count"] + manifest["owner_required"]["derived_claim_count"]
    )


def test_a_resumed_run_reproduces_the_derived_claims_exactly(tmp_path: Path) -> None:
    """Resume must not drop, duplicate, or re-derive the recovered claims.

    The per-PR cache is keyed on the rule digest, so a changed rule can never be
    masked by stale rows; what it must still get right is the top-level/derived
    split on the way back in.
    """
    data_dir = _seed(tmp_path, [_container_item(CODERABBIT_CONTAINER)])
    registry = data_dir / "registry.json"
    registry.write_text(json.dumps({"families": []}), encoding="utf-8")
    kwargs = dict(
        data_dir=data_dir,
        registry_path=registry,
        owner_login=OWNER,
        repository=REPO,
        repo_root=tmp_path,
        ref="HEAD",
    )
    first = recon.run_reconstruction(**kwargs, resume=False)
    rows_first = json.loads((data_dir / "reconstruction" / "1.json").read_text())["reconstructions"]
    second = recon.run_reconstruction(**kwargs, resume=True)
    rows_second = json.loads((data_dir / "reconstruction" / "1.json").read_text())["reconstructions"]
    assert second["reconciliation"]["derived_claims"] == first["reconciliation"]["derived_claims"] == 2
    assert [row["observation_id"] for row in rows_first] == [row["observation_id"] for row in rows_second]
    assert len({row["observation_id"] for row in rows_second}) == 3
    assert second["reconciliation"]["reconciles_exactly"] is True
    assert second["dispositions"] == first["dispositions"]
