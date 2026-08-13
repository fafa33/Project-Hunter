"""Canonical semantic revision for Hunter Governance Review evidence.

Why this module exists
----------------------

Hunter Governance Review is an *evaluator*: it reads a pull request's current
state, decides, and publishes exactly one commit status. Hunter Merge Readiness
is a *consumer*: it must decide whether that published verdict still describes
the pull request as it exists right now.

A commit status is keyed only by SHA. It records neither which pull request was
evaluated nor which version of that pull request's governance-relevant state the
verdict applies to. Every historical Merge Readiness defect in this repository
came from trying to reconstruct those two facts after the fact -- from event
payloads, run start times, publication times, and durable timestamp markers.
Time is the wrong primitive: it makes readiness a function of delivery history
rather than of current state.

This module replaces that reconstruction with an *identity carried by the
evidence itself*. The evaluator stamps two facts into the status description:

1. the pull request number it evaluated, and
2. a deterministic digest of the exact governance-relevant inputs it evaluated.

The consumer recomputes the digest from current canonical state and accepts the
verdict only when both the pull-request number and the digest match. No clock is
consulted, so duplicate, delayed, replayed, reordered, and cancelled deliveries
are all indistinguishable from a first delivery -- which is precisely the
convergence property Merge Readiness needs.

Authority boundary
------------------

``GovernanceInputs`` is deliberately *exactly* the set of mutable facts the
Hunter Governance Review engine actually evaluates
(``hunter_governance_review.__main__.run_review`` plus every validator in
``hunter_governance_review.deterministic``):

- ``pull_request_number`` -- the evaluated pull request's identity;
- ``head_sha`` / ``base_sha`` -- the exact review pair; all repository content
  the engine resolves (canonical documents, referenced ADR/ADPR records) is
  addressed at these two commits, so pinning them pins that content;
- ``base_ref`` -- decides whether the gate is required at all;
- ``title`` -- ``V-010``;
- ``body`` -- ``V-020``/``V-021``/``V-030``/``V-040``/``V-050``/``V-060``, and
  the referenced-record set resolved by ``context.resolve_context``;
- ``draft`` -- switches structural findings between INFO and BLOCKING;
- ``conflicting`` -- ``V-080``;
- ``changed_paths`` -- ``code_change``, ``V-090``, ``is_trusted_dependency_pr``,
  and the proposed-record resolution in ``context.resolve_context``.

Two governance inputs are deliberately *absent*, and neither omission weakens
the binding:

- **author login.** It is a genuine validator input (``is_trusted_dependency_pr``)
  but a pull request's author is immutable for the life of the pull request, so
  it can never change between evaluation and consumption. Including a field that
  cannot vary adds no discriminating power, and it is the one field whose textual
  representation differs between the GitHub APIs the two sides use (``gh pr view``
  renders bots as ``app/dependabot``, REST as ``dependabot[bot]``), so including
  it would add normalization-drift risk for zero benefit.
- **changed-file status/additions/deletions.** The engine reads only
  ``ChangedFile.filename``; ``status``, ``additions``, and ``deletions`` reach no
  validator. Fingerprinting them would invent a semantic input that no authority
  evaluates.

``conflicting`` is normalized to a boolean rather than carrying GitHub's raw
mergeability value on purpose: the only governance use is ``mergeable ==
"CONFLICTING"``, and GitHub computes mergeability lazily, so ``UNKNOWN`` and
``MERGEABLE`` are the same fact ("not conflicting") to every authority that
reads it. Normalizing to the evaluated meaning avoids revision churn that
carries no governance consequence.

Changing what this module fingerprints changes what evidence is considered
current. Bump ``REVISION_SCHEMA_VERSION`` when the input set changes so old
stamps can never be silently reinterpreted under new semantics: a version bump
makes every pre-existing stamp mismatch, which fails closed (not ready) until
the evaluator republishes under the new definition.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

# Bump whenever the fingerprinted input set or canonical encoding changes.
REVISION_SCHEMA_VERSION = 1

# 48 bits of digest. The digest never authorizes anything by itself -- it only
# answers "was this verdict produced for the state I am looking at right now?"
# for one pull request on one exact head SHA, and a mismatch fails closed.
REVISION_DIGEST_LENGTH = 12

# Rendered into the Hunter Governance Review status description as, e.g.,
# "[hgr:257:9f1c0a4b7d2e] Approved for head ...". The marker is written first so
# GitHub's 140-character status-description limit can only ever truncate the
# human-readable reason, never the machine-readable identity.
_MARKER_TEMPLATE = "[hgr:{pull_request}:{revision}]"
MARKER_PATTERN = re.compile(rf"\[hgr:(?P<pull_request>\d+):(?P<revision>[0-9a-f]{{{REVISION_DIGEST_LENGTH}}})\]")


def _normalize_text(value: str) -> str:
    """Normalize line endings before fingerprinting free-text governance inputs.

    The evaluator reads the title and body through GitHub's GraphQL API and the
    consumer through REST. Both return the author's text verbatim, but line-ending
    representation is the one place those two surfaces could plausibly differ, and
    a difference there would produce two revisions for one state -- which fails
    closed but would strand the pull request. ``\r\n`` and ``\n`` carry no
    governance meaning, so collapsing them removes the risk without discarding any
    signal a validator reads.
    """
    return value.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class GovernanceInputs:
    """The exact mutable facts Hunter Governance Review evaluates.

    Construct this on both sides -- evaluator and consumer -- from live GitHub
    state, never from an event payload.
    """

    pull_request_number: int
    head_sha: str
    base_sha: str
    base_ref: str
    title: str
    body: str
    draft: bool
    conflicting: bool
    changed_paths: tuple[str, ...]

    def canonical_payload(self) -> dict[str, object]:
        """Order-independent, encoding-stable representation of these inputs.

        ``changed_paths`` is sorted and de-duplicated because the GitHub files
        endpoint's ordering and pagination are not part of the governance
        meaning of "which paths this pull request changes".
        """
        return {
            "schema": REVISION_SCHEMA_VERSION,
            "pull_request": int(self.pull_request_number),
            "head_sha": self.head_sha.strip(),
            "base_sha": self.base_sha.strip(),
            "base_ref": self.base_ref.strip(),
            "title": _normalize_text(self.title),
            "body": _normalize_text(self.body),
            "draft": bool(self.draft),
            "conflicting": bool(self.conflicting),
            "changed_paths": sorted(set(self.changed_paths)),
        }


def governance_revision(inputs: GovernanceInputs) -> str:
    """Deterministic digest of one pull request's governance-relevant state."""
    encoded = json.dumps(
        inputs.canonical_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:REVISION_DIGEST_LENGTH]


def render_marker(pull_request_number: int, revision: str) -> str:
    """Render the identity marker an evaluator stamps into its status description."""
    return _MARKER_TEMPLATE.format(pull_request=int(pull_request_number), revision=revision)


def parse_marker(description: str | None) -> tuple[int, str] | None:
    """Extract ``(pull_request_number, revision)`` from a status description.

    Returns ``None`` when no well-formed marker is present. Callers must treat
    ``None`` as *unattributable evidence* and fail closed: an unstamped status
    proves neither which pull request it evaluated nor which state it evaluated,
    and no weaker inference may be substituted for that proof.
    """
    if not description:
        return None
    match = MARKER_PATTERN.search(description)
    if match is None:
        return None
    return int(match.group("pull_request")), match.group("revision")


def conflicting_from_graphql(mergeable: str | None) -> bool:
    """Normalize GitHub's GraphQL ``mergeable`` enum to the evaluated meaning."""
    return (mergeable or "").strip().upper() == "CONFLICTING"


def conflicting_from_rest(mergeable: object) -> bool:
    """Normalize GitHub's REST ``mergeable`` tri-state to the evaluated meaning.

    REST returns ``true``/``false``/``null``; ``null`` means "GitHub has not
    finished computing mergeability", which is the same fact as GraphQL's
    ``UNKNOWN``. Only an explicit ``false`` is a conflict.
    """
    return mergeable is False
