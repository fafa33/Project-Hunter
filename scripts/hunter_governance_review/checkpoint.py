"""Durable, chunk-level checkpoint/resume for the LLM Architecture Audit.

A single review run can involve hundreds of independent chunk/section LLM
calls (``chunking.py``). Every one of those calls already produces a
strictly-validated ``llm_audit.AuditVerdict`` before it is trusted
(``llm_audit.validate_audit_payload``) -- this module only changes WHERE that
already-validated result is kept: durably, on disk, the moment it is
produced, so a later run for the SAME review (same repository, pull request,
head SHA, and base SHA) can reuse it instead of spending another provider
call re-reviewing content that already produced a trustworthy verdict.

This exists because a review can be interrupted for reasons entirely outside
the gate's own correctness -- a job timeout, a cancelled workflow, or every
configured provider's quota/credits being exhausted mid-run (see PR #200's
own incident record) -- and, without this, EVERY such interruption forced
the next attempt back to chunk 1, so a review whose per-run quota budget
covers only a fraction of a large diff could never converge no matter how
many times it was retried.

Safety invariants (all enforced here, never left to the caller):

- A checkpoint entry is only ever reused when BOTH the whole-run fingerprint
  (repository, pull request, exact head SHA, exact base SHA, and
  ``REVIEW_SEMANTICS_VERSION``) AND that specific chunk's own content hash
  still match exactly (``CheckpointStore.get``). Any mismatch is a miss, not
  a stale reuse -- the chunk is simply reviewed live again, exactly as if no
  checkpoint existed.
- A resumed entry is re-validated through the SAME strict schema/consistency
  validator (``llm_audit.validate_audit_payload``) used for a live response
  before it is trusted. A corrupted or foreign entry that happens to parse
  as JSON but fails that validation is discarded as a miss, never accepted.
- A checkpoint file that is missing, unreadable, not valid JSON, or was
  written for a different fingerprint is treated as an EMPTY checkpoint --
  never as an error, and never as a reason to trust content it cannot prove
  applies to this exact run.
- Every completed chunk is flushed to disk immediately after that chunk's
  own review succeeds (``CheckpointStore.put``), not batched and not
  written only at the end of the run, so a run killed mid-way still leaves
  every already-completed chunk durably available to the next attempt.
- Checkpointing is entirely opt-in per invocation: ``CheckpointStore.load``
  with ``path=None`` (the default when ``HUNTER_GOVERNANCE_CHECKPOINT_PATH``
  is not set) returns a no-op store that never reads or writes the
  filesystem -- existing behavior is byte-for-byte unchanged unless a
  checkpoint path is explicitly configured (see ``__main__.py`` and the
  workflow YAML, which sets it unconditionally for real runs).

This module never decides what counts as complete diff/document coverage or
what verdict aggregation produces -- ``aggregate.py`` is unchanged and
unaware of whether any given ``ChunkOutcome`` came from a resumed checkpoint
entry or a fresh call; a resumed outcome is indistinguishable, to
aggregation, from one produced live in the current run. ``APPROVED`` still
requires every chunk/section -- resumed or fresh -- to have succeeded.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hunter_governance_review.llm_audit import AuditVerdict, LLMAuditError, validate_audit_payload

# Bumped whenever a change to review semantics could make a previously
# recorded chunk verdict unsafe to reuse verbatim even though the diff
# content, head SHA, and base SHA are unchanged -- e.g. the verdict schema,
# the chunking algorithm, or the aggregation/decision rules. A checkpoint
# recorded under a different value is never reused (see ``_fingerprint``);
# it is discarded exactly like a checkpoint for a foreign PR.
REVIEW_SEMANTICS_VERSION = "1"


def chunk_content_hash(text: str) -> str:
    """Content-addressed identity for one chunk/section's exact text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(
    *,
    repository: str,
    pull_request_number: int,
    source_head_sha: str,
    target_base_sha: str,
    pr_title: str,
    pr_body: str,
) -> str:
    """The whole-checkpoint fingerprint. Any of these inputs (plus
    ``REVIEW_SEMANTICS_VERSION``) differing from what a stored checkpoint
    file was written for makes that file inapplicable to this run in its
    entirety (see ``CheckpointStore.load``).

    ``pr_title``/``pr_body`` are included even though neither is diff
    content: ``llm_audit.build_chunk_audit_prompt`` embeds both verbatim in
    EVERY chunk's prompt, and the Deterministic Governance Engine's findings
    (also embedded in every chunk's prompt) are themselves derived from the
    PR body. A pull request's description can be edited with no new commit
    at all (the workflow's own ``edited`` trigger exists for exactly this),
    which would leave the head SHA -- and therefore, without this -- the
    whole fingerprint unchanged, while the actual prompt every remaining
    chunk would now receive has changed. Without this, an already-reviewed
    chunk's stale verdict (computed against the OLD title/body/findings)
    could be silently resumed under a title/body that now describes
    materially different scope or intent for that same diff content.
    """
    raw = "|".join(
        (
            repository,
            str(pull_request_number),
            source_head_sha,
            target_base_sha,
            REVIEW_SEMANTICS_VERSION,
            hashlib.sha256(pr_title.encode("utf-8")).hexdigest(),
            hashlib.sha256(pr_body.encode("utf-8")).hexdigest(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _CheckpointEntry:
    kind: str
    index: int
    chunk_hash: str
    provider: str
    verdict: dict[str, Any]


@dataclass(frozen=True)
class CheckpointHit:
    """A checkpoint entry that matched this exact chunk and re-validated
    cleanly -- safe to reuse in place of a live call."""

    verdict: AuditVerdict
    provider: str


def _verdict_to_dict(verdict: AuditVerdict) -> dict[str, Any]:
    return {
        "verdict": verdict.verdict,
        "summary": verdict.summary,
        "findings": verdict.findings,
        "rationale": verdict.rationale,
        "architectural_evidence": verdict.architectural_evidence,
    }


class CheckpointStore:
    """Durable, append-as-you-go store of successful chunk/section verdicts
    for exactly one review-contract fingerprint.

    ``kind`` namespaces entries so unrelated units of work never collide on
    the same ``index`` -- diff chunks use ``"diff"``; each authoritative
    document's sections use a per-document kind (e.g.
    ``f"document:{document_path}"``) since section indices restart at 1 for
    every document.
    """

    def __init__(self, path: Path | None, *, fingerprint: str) -> None:
        self._path = path
        self._fingerprint = fingerprint
        self._entries: dict[tuple[str, int], _CheckpointEntry] = {}

    @classmethod
    def load(
        cls,
        path: Path | None,
        *,
        repository: str,
        pull_request_number: int,
        source_head_sha: str,
        target_base_sha: str,
        pr_title: str,
        pr_body: str,
    ) -> CheckpointStore:
        """Load a checkpoint store for this exact review pair.

        ``path=None`` (checkpointing not configured) returns a no-op store
        that never touches the filesystem -- see the module docstring.
        Any problem reading or parsing an existing file at ``path``, or a
        fingerprint mismatch, is treated as an empty checkpoint, never as
        an error. ``pr_title``/``pr_body`` are part of the fingerprint --
        see ``_fingerprint``'s docstring for why an unedited head SHA is not
        enough on its own.
        """
        fingerprint = _fingerprint(
            repository=repository,
            pull_request_number=pull_request_number,
            source_head_sha=source_head_sha,
            target_base_sha=target_base_sha,
            pr_title=pr_title,
            pr_body=pr_body,
        )
        store = cls(path, fingerprint=fingerprint)
        if path is None or not path.exists():
            return store
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return store
        if not isinstance(raw, dict) or raw.get("fingerprint") != fingerprint:
            return store
        for raw_entry in raw.get("entries", []):
            try:
                entry = _CheckpointEntry(
                    kind=str(raw_entry["kind"]),
                    index=int(raw_entry["index"]),
                    chunk_hash=str(raw_entry["chunk_hash"]),
                    provider=str(raw_entry["provider"]),
                    verdict=dict(raw_entry["verdict"]),
                )
            except (KeyError, TypeError, ValueError):
                continue  # one malformed entry never invalidates the rest
            store._entries[(entry.kind, entry.index)] = entry
        return store

    def get(self, *, kind: str, index: int, chunk_hash: str) -> CheckpointHit | None:
        """A previously-recorded, still-applicable verdict for this exact
        chunk, or ``None`` (a miss -- the caller must review it live).

        A miss occurs when: no entry exists at this ``(kind, index)``; the
        stored entry's content hash does not match ``chunk_hash`` (the
        underlying content is not provably identical to what was reviewed);
        or the stored verdict fails strict re-validation. All three are
        treated identically -- silently falling back to a live review is
        always safe, since it can only ever redo work, never skip it.
        """
        entry = self._entries.get((kind, index))
        if entry is None or entry.chunk_hash != chunk_hash:
            return None
        try:
            verdict = validate_audit_payload(entry.verdict)
        except LLMAuditError:
            return None
        return CheckpointHit(verdict=verdict, provider=entry.provider)

    def put(self, *, kind: str, index: int, chunk_hash: str, provider: str, verdict: AuditVerdict) -> None:
        """Record one successful verdict and flush to disk immediately.

        A no-op when checkpointing is not configured (``path is None``).
        """
        if self._path is None:
            return
        self._entries[(kind, index)] = _CheckpointEntry(
            kind=kind, index=index, chunk_hash=chunk_hash, provider=provider, verdict=_verdict_to_dict(verdict)
        )
        self._flush()

    def _flush(self) -> None:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fingerprint": self._fingerprint,
            "entries": [
                {
                    "kind": e.kind,
                    "index": e.index,
                    "chunk_hash": e.chunk_hash,
                    "provider": e.provider,
                    "verdict": e.verdict,
                }
                for e in self._entries.values()
            ],
        }
        # Write-then-rename: a crash or kill signal mid-write must never
        # leave a half-written, truncated-JSON checkpoint file behind for
        # the next attempt to trip over.
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        tmp_path.replace(self._path)
