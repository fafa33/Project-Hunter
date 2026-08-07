"""Unit tests for the durable chunk-level review checkpoint store."""

from __future__ import annotations

from pathlib import Path

from hunter_governance_review.checkpoint import CheckpointStore, chunk_content_hash
from hunter_governance_review.llm_audit import AuditVerdict

VERDICT = AuditVerdict(
    verdict="APPROVED",
    summary="no blocking findings",
    findings=[],
    rationale="clean chunk",
    architectural_evidence={},
)

CHANGES_REQUIRED_VERDICT = AuditVerdict(
    verdict="CHANGES_REQUIRED",
    summary="ownership conflict",
    findings=[
        {
            "id": "F-001",
            "severity": "blocking",
            "location": "src/a.py",
            "description": "ownership boundary crossed",
            "decision_impact": "silently changes canonical ownership",
        }
    ],
    rationale="rejected",
    architectural_evidence={},
)


def _store(path: Path | None, **kwargs: object) -> CheckpointStore:
    defaults: dict[str, object] = dict(
        repository="fafa33/Project-Hunter",
        pull_request_number=200,
        source_head_sha="a" * 40,
        target_base_sha="b" * 40,
        pr_title="feat: add durable checkpoint",
        pr_body="## Summary\n\nsome PR body text",
    )
    defaults.update(kwargs)
    return CheckpointStore.load(path, **defaults)  # type: ignore[arg-type]


def test_content_hash_is_deterministic_and_content_sensitive() -> None:
    assert chunk_content_hash("same text") == chunk_content_hash("same text")
    assert chunk_content_hash("text a") != chunk_content_hash("text b")


def test_disabled_store_never_touches_the_filesystem(tmp_path: Path) -> None:
    # path=None is the "checkpointing not configured" no-op mode.
    store = _store(None)
    store.put(kind="diff", index=1, chunk_hash=chunk_content_hash("x"), provider="p", verdict=VERDICT)
    assert store.get(kind="diff", index=1, chunk_hash=chunk_content_hash("x")) is None
    assert list(tmp_path.iterdir()) == []


def test_missing_checkpoint_file_is_treated_as_empty(tmp_path: Path) -> None:
    store = _store(tmp_path / "nope" / "checkpoint.json")
    assert store.get(kind="diff", index=1, chunk_hash=chunk_content_hash("x")) is None


def test_put_then_get_round_trips_within_one_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    store = _store(path)
    text_hash = chunk_content_hash("diff text")
    store.put(kind="diff", index=1, chunk_hash=text_hash, provider="slot 1", verdict=VERDICT)
    hit = store.get(kind="diff", index=1, chunk_hash=text_hash)
    assert hit is not None
    assert hit.provider == "slot 1"
    assert hit.verdict.verdict == "APPROVED"
    assert hit.verdict.summary == "no blocking findings"


def test_put_flushes_immediately_and_a_fresh_store_instance_can_resume(tmp_path: Path) -> None:
    """The whole point of durability: a NEW ``CheckpointStore`` instance
    (simulating a fresh process on a fresh workflow run) must see what a
    PRIOR instance already wrote, without either instance needing to be
    kept alive or explicitly closed."""
    path = tmp_path / "checkpoint.json"
    first_run = _store(path)
    text_hash = chunk_content_hash("diff text")
    first_run.put(kind="diff", index=1, chunk_hash=text_hash, provider="slot 1", verdict=VERDICT)

    second_run = _store(path)
    hit = second_run.get(kind="diff", index=1, chunk_hash=text_hash)
    assert hit is not None
    assert hit.provider == "slot 1"


def test_multiple_chunks_and_findings_survive_a_full_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    first_run = _store(path)
    first_run.put(kind="diff", index=1, chunk_hash=chunk_content_hash("chunk 1"), provider="slot 1", verdict=VERDICT)
    first_run.put(
        kind="diff",
        index=2,
        chunk_hash=chunk_content_hash("chunk 2"),
        provider="slot 2",
        verdict=CHANGES_REQUIRED_VERDICT,
    )

    second_run = _store(path)
    hit1 = second_run.get(kind="diff", index=1, chunk_hash=chunk_content_hash("chunk 1"))
    hit2 = second_run.get(kind="diff", index=2, chunk_hash=chunk_content_hash("chunk 2"))
    assert hit1 is not None and hit1.verdict.verdict == "APPROVED"
    assert hit2 is not None and hit2.verdict.verdict == "CHANGES_REQUIRED"
    assert hit2.verdict.findings[0]["id"] == "F-001"


def test_a_different_head_sha_invalidates_the_whole_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path, source_head_sha="a" * 40).put(
        kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT
    )

    resumed = _store(path, source_head_sha="c" * 40)
    assert resumed.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_a_different_base_sha_invalidates_the_whole_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path, target_base_sha="b" * 40).put(
        kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT
    )

    resumed = _store(path, target_base_sha="d" * 40)
    assert resumed.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_a_different_pull_request_number_invalidates_the_whole_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path, pull_request_number=200).put(kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT)

    resumed = _store(path, pull_request_number=201)
    assert resumed.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_a_different_repository_invalidates_the_whole_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path, repository="fafa33/Project-Hunter").put(
        kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT
    )

    resumed = _store(path, repository="someone-else/fork")
    assert resumed.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_an_edited_pr_body_invalidates_the_whole_checkpoint_even_with_an_unchanged_head_sha(
    tmp_path: Path,
) -> None:
    """A pull request description can be edited with no new commit at all
    (head SHA unchanged) -- but every chunk's audit prompt embeds the PR
    body verbatim, and the Deterministic Governance Engine's findings
    (also embedded in every prompt) are themselves derived from it. A
    stale chunk verdict computed against the OLD body must never be
    resumed under the NEW one."""
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path, pr_body="## Summary\n\noriginal description").put(
        kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT
    )

    resumed = _store(path, pr_body="## Summary\n\nsignificantly rewritten description")
    assert resumed.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_an_edited_pr_title_invalidates_the_whole_checkpoint_even_with_an_unchanged_head_sha(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path, pr_title="feat: original title").put(
        kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT
    )

    resumed = _store(path, pr_title="feat: renamed and rescoped title")
    assert resumed.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_an_unchanged_pr_title_and_body_still_resumes_normally(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    text_hash = chunk_content_hash("diff text")
    _store(path).put(kind="diff", index=1, chunk_hash=text_hash, provider="p", verdict=VERDICT)

    resumed = _store(path)  # identical title/body (both default to the same values)
    hit = resumed.get(kind="diff", index=1, chunk_hash=text_hash)
    assert hit is not None


def test_a_changed_chunk_hash_at_the_same_index_is_a_miss_not_a_wrong_reuse(tmp_path: Path) -> None:
    """Same fingerprint (same head/base/PR/repo), but the content at this
    specific index changed -- e.g. a different token-budget configuration
    reshuffled chunk boundaries. The stale entry must never be handed back
    for genuinely different content."""
    path = tmp_path / "checkpoint.json"
    store = _store(path)
    store.put(kind="diff", index=1, chunk_hash=chunk_content_hash("old content"), provider="p", verdict=VERDICT)

    resumed = _store(path)
    assert resumed.get(kind="diff", index=1, chunk_hash=chunk_content_hash("new content")) is None


def test_a_corrupt_json_checkpoint_file_is_treated_as_empty_not_an_error(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = _store(path)
    assert store.get(kind="diff", index=1, chunk_hash=chunk_content_hash("x")) is None


def test_a_checkpoint_file_for_a_foreign_fingerprint_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text('{"fingerprint": "not-a-real-fingerprint", "entries": []}', encoding="utf-8")
    store = _store(path)
    store.put(kind="diff", index=1, chunk_hash=chunk_content_hash("x"), provider="p", verdict=VERDICT)
    # put() overwrites the file under THIS run's own fingerprint, so
    # reloading now must see the freshly-written entry, not the foreign one.
    reloaded = _store(path)
    hit = reloaded.get(kind="diff", index=1, chunk_hash=chunk_content_hash("x"))
    assert hit is not None


def test_an_entry_that_fails_strict_revalidation_is_discarded_as_a_miss(tmp_path: Path) -> None:
    """A checkpoint entry whose stored verdict payload no longer satisfies
    llm_audit's strict schema validation (e.g. hand-corrupted, or written
    by an incompatible version) must never be trusted -- it is discarded
    exactly like a hash mismatch, falling back to a live review."""
    import json

    from hunter_governance_review.checkpoint import _fingerprint  # noqa: PLC0415

    path = tmp_path / "checkpoint.json"
    fingerprint = _fingerprint(
        repository="fafa33/Project-Hunter",
        pull_request_number=200,
        source_head_sha="a" * 40,
        target_base_sha="b" * 40,
        pr_title="feat: add durable checkpoint",
        pr_body="## Summary\n\nsome PR body text",
    )
    text_hash = chunk_content_hash("x")
    payload = {
        "fingerprint": fingerprint,
        "entries": [
            {
                "kind": "diff",
                "index": 1,
                "chunk_hash": text_hash,
                "provider": "slot 1",
                # Missing required fields (e.g. "summary") -- fails
                # llm_audit.validate_audit_payload.
                "verdict": {"verdict": "APPROVED"},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = _store(path)
    assert store.get(kind="diff", index=1, chunk_hash=text_hash) is None


def test_distinct_kinds_do_not_collide_at_the_same_index(tmp_path: Path) -> None:
    """Diff chunks and per-document sections both index from 1 -- ``kind``
    must keep them from colliding (e.g. document A's section 1 vs
    document B's section 1 vs diff chunk 1)."""
    path = tmp_path / "checkpoint.json"
    store = _store(path)
    same_hash = chunk_content_hash("identical text, different unit of work")
    store.put(kind="diff", index=1, chunk_hash=same_hash, provider="diff-provider", verdict=VERDICT)
    store.put(kind="document:docs/A.md", index=1, chunk_hash=same_hash, provider="doc-a-provider", verdict=VERDICT)
    store.put(
        kind="document:docs/B.md",
        index=1,
        chunk_hash=same_hash,
        provider="doc-b-provider",
        verdict=CHANGES_REQUIRED_VERDICT,
    )

    resumed = _store(path)
    diff_hit = resumed.get(kind="diff", index=1, chunk_hash=same_hash)
    doc_a_hit = resumed.get(kind="document:docs/A.md", index=1, chunk_hash=same_hash)
    doc_b_hit = resumed.get(kind="document:docs/B.md", index=1, chunk_hash=same_hash)
    assert diff_hit is not None and diff_hit.provider == "diff-provider"
    assert doc_a_hit is not None and doc_a_hit.provider == "doc-a-provider"
    assert doc_b_hit is not None and doc_b_hit.verdict.verdict == "CHANGES_REQUIRED"
