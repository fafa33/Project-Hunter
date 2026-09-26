"""Tests for deterministic historical evidence adjudication.

The scan proves scan coverage; it cannot label evidence. These tests pin the
adjudication phase that turns ``insufficient-evidence`` ledger items into exactly
one of confirmed / mapped-to-canonical-family / excluded-with-evidence, or an
explicit owner queue -- and pin the properties that make that trustworthy:

- a third-party reviewer label is never converted into a confirmed defect;
- a repository-scoped family is claimed only on exact canonical-invariant
  equality plus applicability intersection (no fabricated mapping);
- an already-proven canonical mapping is preserved, never re-inferred away;
- closure is strict: ``coverage_gap == 0`` requires every dimension to be zero,
  so scan completion alone can never report closure;
- the phase is deterministic and resumable over the frozen scan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hunter.evidence_intelligence import historical_evidence_adjudication as adjudication

OWNER = "owner-1"
REPO = "fake/scan"

FAMILY_INVARIANT = "every governed asset resolves to a path its consumer can load"
FAMILY_SELECTORS = ["scripts/", "docs/"]


def make_registry(tmp_path: Path) -> Path:
    path = tmp_path / "DEFECT_REGISTRY.json"
    path.write_text(
        json.dumps(
            {
                "families": [
                    {
                        "id": "DFF-001",
                        "title": "loadable-asset",
                        "invariant": FAMILY_INVARIANT,
                        "lifecycle": "recorded",
                        "applicability": {
                            "changed_paths": FAMILY_SELECTORS,
                            "rationale": "test",
                        },
                        "prevention": {"boundary": "review"},
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def make_item(
    *,
    event_id: str = "review-comment-1",
    reviewer: str = "bot[bot]",
    message: str = "The resolver accepts an unvalidated payload and persists it.",
    path: str | None = "scripts/hunter_x.py",
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


def make_record(
    items: list[dict[str, Any]],
    comments: list[dict[str, Any]] | None = None,
    pr_number: int = 7,
) -> dict[str, Any]:
    return {
        "pr_number": pr_number,
        "ledger": {"items": items},
        "evidence": {"review_comments": comments or []},
    }


def families_of(tmp_path: Path) -> list[dict[str, Any]]:
    return adjudication._family_index(make_registry(tmp_path))


def decide(tmp_path: Path, record: dict[str, Any], owner: str = OWNER) -> adjudication.Adjudication:
    families = families_of(tmp_path)
    return adjudication.adjudicate_item(record["ledger"]["items"][0], record, owner_login=owner, families=families)


# ---------------------------------------------------------------------------
# R1: containers are not findings
# ---------------------------------------------------------------------------


def test_observation_without_location_anchor_is_excluded(tmp_path: Path) -> None:
    record = make_record(
        [make_item(path=None, line=None, message="### Codex Review Here are some automated review suggestions.")]
    )
    result = decide(tmp_path, record)
    assert result.rule == adjudication.RULE_NO_LOCATION_ANCHOR
    assert result.disposition == adjudication.DISPOSITION_EXCLUDED
    assert "no file/line anchor" in result.evidence


# ---------------------------------------------------------------------------
# R2: owner authority
# ---------------------------------------------------------------------------


def test_owner_authored_finding_is_confirmed(tmp_path: Path) -> None:
    record = make_record([make_item(reviewer=OWNER, message="The replay path ignores applicability_end.")])
    result = decide(tmp_path, record)
    assert result.rule == adjudication.RULE_OWNER_ASSERTED
    assert result.disposition == adjudication.DISPOSITION_CONFIRMED
    assert result.classification == "confirmed"


def test_owner_authored_false_positive_is_excluded_with_evidence(tmp_path: Path) -> None:
    record = make_record([make_item(reviewer=OWNER, message="False positive: the guard already rejects this.")])
    result = decide(tmp_path, record)
    assert result.disposition == adjudication.DISPOSITION_EXCLUDED
    assert result.classification == "false-positive"


def test_owner_disposition_extracts_fix_reference(tmp_path: Path) -> None:
    record = make_record(
        [
            make_item(
                reviewer=OWNER, message="Fixed in a535c1c. Replay now requires applicability_end to bound the window."
            )
        ]
    )
    result = decide(tmp_path, record)
    assert result.fix_reference == "a535c1c"
    assert "applicability_end" in result.invariant


# ---------------------------------------------------------------------------
# R3: owner disposition of a third-party finding on its own thread
# ---------------------------------------------------------------------------


def test_third_party_finding_disposed_by_owner_reply_is_confirmed(tmp_path: Path) -> None:
    record = make_record(
        [make_item(event_id="review-comment-100", reviewer="bot[bot]")],
        comments=[
            {
                "id": 100,
                "in_reply_to_id": None,
                "user": {"login": "bot[bot]"},
                "path": "scripts/a.py",
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
    result = decide(tmp_path, record)
    assert result.rule == adjudication.RULE_OWNER_DISPOSED_THREAD
    assert result.disposition == adjudication.DISPOSITION_CONFIRMED
    assert result.fix_reference == "2fab2e0"


def test_third_party_finding_marked_style_by_owner_is_excluded(tmp_path: Path) -> None:
    record = make_record(
        [make_item(event_id="review-comment-100", reviewer="bot[bot]")],
        comments=[
            {
                "id": 100,
                "in_reply_to_id": None,
                "user": {"login": "bot[bot]"},
                "path": "scripts/a.py",
                "line": 3,
                "body": "Consider renaming this local variable for clarity.",
            },
            {"id": 101, "in_reply_to_id": 100, "user": {"login": OWNER}, "body": "Style, leaving as is."},
        ],
    )
    result = decide(tmp_path, record)
    assert result.disposition == adjudication.DISPOSITION_EXCLUDED
    assert result.classification == "style"


# ---------------------------------------------------------------------------
# R0: proven terminal state is preserved, never re-inferred away
# ---------------------------------------------------------------------------


def test_proven_existing_family_mapping_is_preserved(tmp_path: Path) -> None:
    item = make_item(
        classification="confirmed", claimed_family_id="DFF-018", state="existing-family", reviewer="bot[bot]"
    )
    result = decide(tmp_path, make_record([item]))
    assert result.rule == adjudication.RULE_PRESERVE_TERMINAL
    assert result.disposition == adjudication.DISPOSITION_EXISTING_FAMILY
    assert result.claimed_family_id == "DFF-018"


def test_proven_exclusion_is_preserved(tmp_path: Path) -> None:
    item = make_item(classification="style", state="excluded", reviewer="bot[bot]", path=None, line=None)
    result = decide(tmp_path, make_record([item]))
    assert result.rule == adjudication.RULE_PRESERVE_TERMINAL
    assert result.disposition == adjudication.DISPOSITION_EXCLUDED
    assert result.classification == "style"


# ---------------------------------------------------------------------------
# R5: a third-party label is evidence, never authority
# ---------------------------------------------------------------------------


def test_unadjudicated_third_party_claim_is_queued_not_confirmed(tmp_path: Path) -> None:
    record = make_record([make_item(reviewer="coderabbitai[bot]")])
    result = decide(tmp_path, record)
    assert result.rule == adjudication.RULE_UNADJUDICATED_THIRD_PARTY
    assert result.disposition == adjudication.DISPOSITION_AMBIGUOUS
    assert result.classification == ""
    assert result.claimed_family_id is None


def test_third_party_reply_does_not_dispose_a_finding(tmp_path: Path) -> None:
    """Only the owner adjudicates; another bot's reply leaves it ambiguous."""
    record = make_record(
        [make_item(event_id="review-comment-100", reviewer="bot[bot]")],
        comments=[
            {
                "id": 100,
                "in_reply_to_id": None,
                "user": {"login": "bot[bot]"},
                "path": "scripts/a.py",
                "line": 3,
                "body": "P1: the guard is bypassable.",
            },
            {
                "id": 101,
                "in_reply_to_id": 100,
                "user": {"login": "other-bot[bot]"},
                "body": "Confirmed, this is a real bug.",
            },
        ],
    )
    result = decide(tmp_path, record)
    assert result.disposition == adjudication.DISPOSITION_AMBIGUOUS


def test_third_party_self_label_never_confirms(tmp_path: Path) -> None:
    record = make_record([make_item(reviewer="coderabbitai[bot]", message="Confirmed critical security defect here.")])
    assert decide(tmp_path, record).disposition == adjudication.DISPOSITION_AMBIGUOUS


# ---------------------------------------------------------------------------
# Family mapping is strict: no fabricated canonical history
# ---------------------------------------------------------------------------


def test_exact_invariant_and_applicability_maps_to_existing_family(tmp_path: Path) -> None:
    record = make_record(
        [make_item(reviewer=OWNER, message=f"Confirmed. {FAMILY_INVARIANT} is violated here.", path="scripts/a.py")]
    )
    result = decide(tmp_path, record)
    assert result.disposition == adjudication.DISPOSITION_EXISTING_FAMILY
    assert result.claimed_family_id == "DFF-001"


def test_similar_but_unequal_invariant_does_not_claim_a_family(tmp_path: Path) -> None:
    record = make_record(
        [make_item(reviewer=OWNER, message="Confirmed. every governed asset resolves to a path it can load, roughly.")]
    )
    result = decide(tmp_path, record)
    assert result.disposition == adjudication.DISPOSITION_CONFIRMED
    assert result.claimed_family_id is None


def test_matching_invariant_outside_applicability_does_not_claim_a_family(tmp_path: Path) -> None:
    record = make_record(
        [make_item(reviewer=OWNER, message=f"Confirmed. {FAMILY_INVARIANT}.", path="src/other/place.py")]
    )
    result = decide(tmp_path, record)
    assert result.disposition == adjudication.DISPOSITION_CONFIRMED
    assert result.claimed_family_id is None


# ---------------------------------------------------------------------------
# Strict closure metrics
# ---------------------------------------------------------------------------


def _seed_scan(tmp_path: Path, items: list[dict[str, Any]], *, prs: int = 1) -> Path:
    data_dir = tmp_path / "scan"
    (data_dir / "prs").mkdir(parents=True)
    (data_dir / "snapshot.json").write_text(
        json.dumps({"prs": [{"number": n} for n in range(1, prs + 1)]}), encoding="utf-8"
    )
    for number in range(1, prs + 1):
        (data_dir / "prs" / f"{number}.json").write_text(
            json.dumps(make_record(items, pr_number=number)), encoding="utf-8"
        )
    return data_dir


def test_scan_completion_alone_never_reports_closure(tmp_path: Path) -> None:
    """Every PR scanned and every item adjudicated, but closure is still open."""
    items = [make_item(event_id=f"review-comment-{n}") for n in range(5)]
    data_dir = _seed_scan(tmp_path, items)
    families = families_of(tmp_path)
    decided = [
        adjudication.adjudicate_item(item, make_record(items), owner_login=OWNER, families=families) for item in items
    ]
    manifest = adjudication.build_closure_manifest(
        repository=REPO,
        data_dir=data_dir,
        adjudications=decided,
        scan_statused=1,
        scan_total=1,
    )
    assert manifest["scan_coverage_gap"] == 0
    assert manifest["adjudication_coverage_gap"] == 0
    assert manifest["coverage_gap"] > 0, "scan completion must not stand in for evidence closure"


def test_coverage_gap_is_zero_only_when_every_dimension_is_zero(tmp_path: Path) -> None:
    excluded = [make_item(event_id=f"review-comment-{n}", path=None, line=None) for n in range(4)]
    mapped = [
        make_item(
            event_id=f"review-comment-{n}",
            classification="confirmed",
            claimed_family_id="DFF-001",
            state="existing-family",
        )
        for n in range(4, 8)
    ]
    items = excluded + mapped
    data_dir = _seed_scan(tmp_path, items)
    families = families_of(tmp_path)
    decided = [
        adjudication.adjudicate_item(item, make_record(items), owner_login=OWNER, families=families) for item in items
    ]
    manifest = adjudication.build_closure_manifest(
        repository=REPO, data_dir=data_dir, adjudications=decided, scan_statused=1, scan_total=1
    )
    assert manifest["dispositions"][adjudication.DISPOSITION_AMBIGUOUS] == 0
    assert manifest["canonical_mapping_gap"] == 0
    assert manifest["coverage_gap"] == 0
    assert manifest["reconciliation"]["reconciles_exactly"] is True


def test_manifest_reconciles_exactly_to_ledger_items(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(7)]
    data_dir = _seed_scan(tmp_path, items)
    families = families_of(tmp_path)
    decided = [
        adjudication.adjudicate_item(item, make_record(items), owner_login=OWNER, families=families) for item in items
    ]
    manifest = adjudication.build_closure_manifest(
        repository=REPO, data_dir=data_dir, adjudications=decided, scan_statused=1, scan_total=1
    )
    total = sum(manifest["dispositions"].values())
    assert total == manifest["reconciliation"]["raw_scan_ledger_items"] == 7


def test_missing_adjudication_shows_in_adjudication_coverage_gap(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(6)]
    data_dir = _seed_scan(tmp_path, items)
    manifest = adjudication.build_closure_manifest(
        repository=REPO, data_dir=data_dir, adjudications=[], scan_statused=1, scan_total=1
    )
    assert manifest["adjudication_coverage_gap"] == 6


# ---------------------------------------------------------------------------
# Reviewer UI chrome is not evidence
# ---------------------------------------------------------------------------


def test_hidden_details_transcript_is_stripped_but_claim_survives() -> None:
    body = (
        "_Data Integrity_ | _Minor_ | _Quick win_\n\n"
        "**Clarify the complete preflight action inventory.**\n\n"
        "The list omits `self-check` and `live-pr`, although this document "
        "claims the inventory is complete.\n\n"
        "<details><summary>Analysis chain</summary>Script executed: shell "
        "!/bin/bash git show --stat ast-grep outline scripts/x.py</details>"
    )
    cleaned = adjudication.clean_comment_text(body)
    assert "preflight action inventory" in cleaned
    assert "Analysis chain" not in cleaned
    assert "ast-grep" not in cleaned
    assert "Quick win" not in cleaned, "review taxonomy banner is not a claim"


def test_bot_skip_notice_is_not_an_owner_decision(tmp_path: Path) -> None:
    """A bot declining another bot's comment states no claim, even with a path."""
    record = make_record(
        [
            make_item(
                path="src/hunter/evidence_intelligence/x.py",
                line=5,
                message=(
                    "> Skipped: comment is from another GitHub bot.\n\n"
                    "<!-- This is an auto-generated reply by CodeRabbit -->"
                ),
            )
        ]
    )
    result = decide(tmp_path, record)
    assert result.rule == adjudication.RULE_NO_SUBSTANTIVE_CONTENT
    assert result.disposition == adjudication.DISPOSITION_EXCLUDED


def test_html_comment_trailer_is_stripped() -> None:
    cleaned = adjudication.clean_comment_text(
        "The inventory omits `self-check`. <!-- This is an auto-generated reply by CodeRabbit -->"
    )
    assert "self-check" in cleaned
    assert "CodeRabbit" not in cleaned
    assert "<!--" not in cleaned


def test_details_only_comment_has_no_substantive_content() -> None:
    body = "<details><summary>Analysis chain</summary>Script executed: shell !/bin/bash</details>"
    cleaned = adjudication.clean_comment_text(body)
    assert len(cleaned) < adjudication._MIN_SUBSTANTIVE_CHARS
    assert adjudication._APPROVAL_ONLY.match(cleaned) is None


def test_taxonomy_banner_does_not_become_the_group_signature() -> None:
    with_banner = adjudication.group_key(
        "_Data Integrity_ | _Minor_ | _Quick win_\n\nThe inventory omits `self-check`."
    )
    without_banner = adjudication.group_key("The inventory omits `self-check`.")
    assert with_banner == without_banner


def test_owner_queue_evidence_is_the_claim_not_the_transcript(tmp_path: Path) -> None:
    items = [
        make_item(
            event_id=f"review-comment-{n}",
            message=(
                "<details><summary>Analysis chain</summary>Script executed: shell "
                "!/bin/bash ast-grep outline</details>\n\n"
                "**The preflight inventory is incomplete.** It omits `self-check`, "
                "so the documented completeness claim is false."
            ),
        )
        for n in range(2)
    ]
    _seed_scan(tmp_path, items)
    families = families_of(tmp_path)
    decided = [
        adjudication.adjudicate_item(item, make_record(items), owner_login=OWNER, families=families) for item in items
    ]
    queue = adjudication._decision_queue(decided)
    for group in queue["groups"]:
        assert "ast-grep" not in group["example_evidence"]
        assert "Script executed" not in group["group_signature"]


# ---------------------------------------------------------------------------
# Determinism and resumability over the frozen scan
# ---------------------------------------------------------------------------


def test_adjudication_is_deterministic_and_resumable(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(4)]
    items[1]["observation"]["reviewer"] = OWNER
    data_dir = _seed_scan(tmp_path, items, prs=2)
    registry = make_registry(tmp_path)

    first = adjudication.run_adjudication(data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO)
    assert (data_dir / "adjudication" / "1.json").is_file()
    second = adjudication.run_adjudication(
        data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO
    )
    assert first["closure_manifest_digest"] == second["closure_manifest_digest"]
    queue = json.loads((data_dir / "adjudication" / "owner_decision_queue.json").read_text())
    assert queue["reused_pr_records"] == 2, "resume must reuse persisted per-PR decisions"


def test_adjudication_never_mutates_the_frozen_scan(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(3)]
    data_dir = _seed_scan(tmp_path, items)
    registry = make_registry(tmp_path)
    snapshot_before = (data_dir / "snapshot.json").read_bytes()
    records_before = {p.name: p.read_bytes() for p in (data_dir / "prs").glob("*.json")}
    registry_before = registry.read_bytes()

    adjudication.run_adjudication(data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO)

    assert (data_dir / "snapshot.json").read_bytes() == snapshot_before
    assert {p.name: p.read_bytes() for p in (data_dir / "prs").glob("*.json")} == records_before
    assert registry.read_bytes() == registry_before, "adjudication must not write canonical history"


def test_registry_change_invalidates_cached_adjudication(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}") for n in range(2)]
    data_dir = _seed_scan(tmp_path, items)
    registry = make_registry(tmp_path)
    adjudication.run_adjudication(data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO)
    payload = json.loads((data_dir / "adjudication" / "1.json").read_text())
    payload["registry_digest"] = "stale"
    (data_dir / "adjudication" / "1.json").write_text(json.dumps(payload), encoding="utf-8")

    result = adjudication.run_adjudication(
        data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO
    )
    assert result["reconciliation"]["reconciles_exactly"] is True
    refreshed = json.loads((data_dir / "adjudication" / "1.json").read_text())
    assert refreshed["registry_digest"] != "stale"


def test_rule_change_invalidates_cached_adjudication(tmp_path: Path) -> None:
    """The cache is keyed on the decision logic, not only on its inputs.

    Without this, editing a rule would reuse decisions produced by the previous
    rule set and report a clean reconciliation over stale verdicts.
    """
    items = [make_item(event_id=f"review-comment-{n}") for n in range(2)]
    data_dir = _seed_scan(tmp_path, items)
    registry = make_registry(tmp_path)
    adjudication.run_adjudication(data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO)
    assert json.loads((data_dir / "adjudication" / "1.json").read_text())["engine_digest"]

    payload = json.loads((data_dir / "adjudication" / "1.json").read_text())
    payload["engine_digest"] = "rules-from-a-previous-version"
    (data_dir / "adjudication" / "1.json").write_text(json.dumps(payload), encoding="utf-8")

    result = adjudication.run_adjudication(
        data_dir=data_dir, registry_path=registry, owner_login=OWNER, repository=REPO
    )
    queue = json.loads((data_dir / "adjudication" / "owner_decision_queue.json").read_text())
    assert queue["reused_pr_records"] == 0, "stale rule digest must not be reused"
    refreshed = json.loads((data_dir / "adjudication" / "1.json").read_text())
    assert refreshed["engine_digest"] == adjudication.engine_digest()
    assert result["reconciliation"]["reconciles_exactly"] is True


def test_owner_queue_groups_instead_of_listing_items(tmp_path: Path) -> None:
    items = [make_item(event_id=f"review-comment-{n}", path="scripts/a.py") for n in range(4)]
    _seed_scan(tmp_path, items)
    families = families_of(tmp_path)
    decided = [
        adjudication.adjudicate_item(item, make_record(items), owner_login=OWNER, families=families) for item in items
    ]
    queue = adjudication._decision_queue(decided)
    assert queue["ambiguous_item_count"] == 4
    assert queue["group_count"] == 1, "identical cases must collapse into one owner decision"
    assert queue["groups"][0]["item_count"] == 4


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_adjudicate_requires_owner_login(tmp_path: Path) -> None:
    import hunter_full_history_defect_scan as cli

    data_dir = _seed_scan(tmp_path, [make_item()])
    with pytest.raises(SystemExit, match="owner-login"):
        cli.main(["--adjudicate", "--data-dir", str(data_dir), "--owner-login", ""])


def test_cli_adjudicate_exits_nonzero_while_coverage_open(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import hunter_full_history_defect_scan as cli

    registry = make_registry(tmp_path)
    data_dir = _seed_scan(tmp_path, [make_item(event_id="review-comment-1")])
    code = cli.main(
        [
            "--adjudicate",
            "--data-dir",
            str(data_dir),
            "--registry",
            str(registry),
            "--owner-login",
            OWNER,
        ]
    )
    out = capsys.readouterr().out
    assert code == 2, "an open coverage_gap must not report success"
    assert "reconciles exactly          : True" in out
