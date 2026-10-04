"""Anchored knowledge ledger for the review → knowledge → prevention → remediation loop (ADR 0039 L1–L3, L8).

One forward-only chain on ``refs/heads/hunter-state/v1/knowledge``: one ``record.json`` per commit,
K_STATE-signed under its own domain, under the same anchor, lease CAS and run provenance as the Issue and
Source Handling ledgers (ADR 0037 D2/D2a). Every record kind is insert-only and unique per key, so duplicate
delivery, crashes and lost acknowledgements resolve to exactly one record.

The ledger stores only what is already public on the PR (provenance, path, a bounded normalized claim) plus
digests. Reviewer prose is evidence: it is never an invariant. A family mapping is either deterministic
(structured tag or an already-mapped fingerprint) or proven by a RED→GREEN regression (``basis: proven``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_state import (
    POSITIVE,
    RECORDED_BY_VALIDATOR,
    SHA40,
    SHA64,
    TIMESTAMP,
    GitLedgerStore,
    LedgerCorruptError,
    LedgerSchemaError,
    ProvenanceCheck,
    TrustRoots,
    _enum,
    _exact,
    _int,
    _list,
    _object,
    _optional,
    _pattern,
    canonical_json,
    record_digest,
    sha256_hex,
    sign_record,
    verify_record_signature,
)

KNOWLEDGE_LEDGER_REF: Final = "refs/heads/hunter-state/v1/knowledge"
KNOWLEDGE_LEDGER_DOMAIN: Final = "hunter-knowledge-ledger-v1"
RECORD_SCHEMA_VERSION: Final = "hunter-knowledge-record-v1"
MAX_CLAIM_CHARS: Final = 280
MAX_REMEDIATIONS_PER_FINDING: Final = 2
MAX_REMEDIATIONS_PER_PR: Final = 5
_FILES: Final = frozenset({"record.json"})

_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
_PATH = re.compile(r"[A-Za-z0-9._@+/-]{1,512}")
_FAMILY = re.compile(r"DFF-[0-9]{3}")
_FAMILY_TAG = re.compile(r"\[family:(DFF-[0-9]{3})\]", re.IGNORECASE)
_TEST_ID = re.compile(r"tests/[A-Za-z0-9_./-]{1,200}\.py::[A-Za-z0-9_\[\]-]{1,200}")
_CLAIM = re.compile(r"[\x20-\x7e]{1,280}")
_TITLE = re.compile(r"[a-z0-9][a-z0-9-]{2,99}")
_TEXT = re.compile(r"[\x20-\x7e]{12,1000}")

#: Which ledger role may write which kind (ADR 0039 L2).
WRITER_ROLES: Final[Mapping[str, frozenset[str]]] = {
    "finding_ingested": frozenset({"knowledge-ingest", "reconcile"}),
    "finding_classified": frozenset({"knowledge-ingest", "record-validation", "reconcile"}),
    "family_candidate": frozenset({"record-validation", "reconcile"}),
    "remediation_requested": frozenset({"reconcile"}),
    "finding_proven": frozenset({"reconcile"}),
    "thread_resolved": frozenset({"reconcile"}),
    "recurrence": frozenset({"reconcile"}),
}

_PROVENANCE = _object(
    {
        "pull_request_number": POSITIVE,
        "reviewed_head_sha": SHA40,
        "reviewed_base_sha": SHA40,
        "source_event_head_sha": _optional(SHA40),
        "provider": _exact("github-review"),
        "reviewer": _pattern(_LOGIN),
        "comment_id": POSITIVE,
        "path": _pattern(_PATH),
        "line": _optional(POSITIVE),
    }
)
_CLASSIFICATION = _object(
    {
        "finding_id": SHA64,
        "outcome": _enum({"matched", "candidate-new-family", "false-positive-claimed", "ambiguous"}),
        "family_id": _optional(_pattern(_FAMILY)),
        "candidate_id": _optional(SHA64),
        "basis": _enum({"deterministic", "proven"}),
        "regression_tests": _list(_pattern(_TEST_ID), maximum=32),
        "authorization_id": _optional(state.AUTHORIZATION_ID),
    }
)
EVIDENCE_SCHEMAS: Final = {
    "finding_ingested": _object(
        {
            "finding_id": SHA64,
            "fingerprint": SHA64,
            "claim": _pattern(_CLAIM),
            "provenance": _PROVENANCE,
        }
    ),
    "finding_classified": _CLASSIFICATION,
    "family_candidate": _object(
        {
            "candidate_id": SHA64,
            "title": _pattern(_TITLE),
            "invariant": _pattern(_TEXT),
            "changed_paths": _list(_pattern(_PATH), maximum=32),
            "source_finding_ids": _list(SHA64, maximum=64),
            "regression_tests": _list(_pattern(_TEST_ID), maximum=32),
        }
    ),
    "remediation_requested": _object(
        {
            "finding_id": SHA64,
            "attempt": _int(1),
            "pull_request_number": POSITIVE,
            "bound_head_sha": SHA40,
            "authorization_id": state.AUTHORIZATION_ID,
        }
    ),
    "finding_proven": _object(
        {
            "finding_id": SHA64,
            "authorization_id": state.AUTHORIZATION_ID,
            "remediated_head_sha": SHA40,
            "receipt_sha256": SHA64,
            "preflight_run_id": POSITIVE,
            "regression_tests": _list(_pattern(_TEST_ID), maximum=32),
        }
    ),
    "thread_resolved": _object(
        {
            "finding_id": SHA64,
            "remediated_head_sha": SHA40,
            "reply_comment_id": POSITIVE,
        }
    ),
    "recurrence": _object({"family_id": _pattern(_FAMILY), "finding_id": SHA64}),
}
_RECORD = _object(
    {
        "schema_version": _exact(RECORD_SCHEMA_VERSION),
        "kind": _enum(EVIDENCE_SCHEMAS),
        "key": SHA64,
        "record_seq": _int(0),
        "prev_record_sha256": _optional(SHA64),
        "recorded_at": TIMESTAMP,
        "recorded_by": RECORDED_BY_VALIDATOR,
        "repository_id": POSITIVE,
        "evidence": lambda _value, _where: None,
        "signature": lambda _value, _where: None,
    }
)


class KnowledgeLedgerError(LedgerCorruptError):
    """The knowledge ledger failed verification or a write would break an L2 invariant."""


class FindingRefused(ValueError):
    """A reviewer observation is not an admissible finding (malformed, unauthenticated, wrong head)."""


# --- identity, normalization, fingerprint (L1) ----------------------------------------------------------


def finding_id(repository_id: int, pull_request_number: int, comment_id: int) -> str:
    return sha256_hex(
        canonical_json(
            {"repository_id": repository_id, "pull_request_number": pull_request_number, "comment_id": comment_id}
        )
    )


_MARKUP = (
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"<[^>]+>"),
    re.compile(r"\[([^\]]*)\]\([^)]*\)"),
    re.compile(r"```.*?```", re.S),
)


def normalized_claim(message: str) -> str:
    """The first sentence of a finding, without markup, badges, links or numbers; printable ASCII, bounded."""

    text = message
    for pattern in _MARKUP[:3]:
        text = pattern.sub(" ", text)
    text = _MARKUP[3].sub(r"\1", text)
    text = _MARKUP[4].sub(" ", text)
    text = _FAMILY_TAG.sub(" ", text)
    text = re.sub(r"[*_`#>|~]", " ", text)
    text = " ".join(text.split())
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""
    sentence = re.sub(r"\d+", " ", sentence.lower())
    sentence = "".join(ch if 0x20 <= ord(ch) < 0x7F else " " for ch in sentence)
    sentence = " ".join(sentence.split())[:MAX_CLAIM_CHARS].strip()
    if not sentence:
        raise FindingRefused("the finding has no textual claim")
    return sentence


def fingerprint(path: str, claim: str) -> str:
    return sha256_hex(canonical_json({"path": path, "claim": claim}))


def finding_from_observation(
    observation: Mapping[str, Any], *, repository_id: int, trusted_reviewers: frozenset[str]
) -> dict[str, Any]:
    """L1 evidence for one trusted-reviewer thread observation (``hunter_collect_learning_observations``)."""

    event = str(observation.get("event_id", ""))
    match = re.fullmatch(r"review-comment-([1-9][0-9]{0,18})", event)
    if match is None:
        raise FindingRefused("only inline review threads are findings")
    reviewer = str(observation.get("reviewer", ""))
    if reviewer.lower().removesuffix("[bot]") not in trusted_reviewers:
        raise FindingRefused("the reviewer is not an authenticated pool reviewer")
    path = observation.get("path")
    if not isinstance(path, str) or not path:
        raise FindingRefused("an inline finding must name its path")
    claim = normalized_claim(str(observation.get("message", "")))
    comment = int(match.group(1))
    provenance = {
        "pull_request_number": observation.get("source_pr"),
        "reviewed_head_sha": observation.get("reviewed_head_sha"),
        "reviewed_base_sha": observation.get("reviewed_base_sha"),
        "source_event_head_sha": observation.get("source_event_head_sha"),
        "provider": observation.get("provider"),
        "reviewer": reviewer,
        "comment_id": comment,
        "path": path,
        "line": observation.get("line"),
    }
    evidence = {
        "finding_id": finding_id(repository_id, int(provenance["pull_request_number"] or 0), comment),
        "fingerprint": fingerprint(path, claim),
        "claim": claim,
        "provenance": provenance,
    }
    try:
        EVIDENCE_SCHEMAS["finding_ingested"](evidence, "finding")
    except LedgerSchemaError as error:
        raise FindingRefused(f"malformed finding: {error}") from None
    return evidence


def family_tag(message: str) -> str | None:
    tags = {match.upper() for match in _FAMILY_TAG.findall(message)}
    if len(tags) > 1:
        raise FindingRefused("a finding names more than one family")
    return next(iter(tags), None)


def candidate_id(invariant: str, changed_paths: Sequence[str]) -> str:
    return sha256_hex(canonical_json({"invariant": invariant, "changed_paths": sorted(changed_paths)}))


# --- the verified view -----------------------------------------------------------------------------------


@dataclass(slots=True)
class Finding:
    ingested: dict[str, Any]
    classifications: dict[str, dict[str, Any]] = field(default_factory=dict)  # basis -> evidence
    remediations: list[dict[str, Any]] = field(default_factory=list)
    proven: dict[str, Any] | None = None
    resolved: dict[str, Any] | None = None

    @property
    def path(self) -> str:
        return str(self.ingested["provenance"]["path"])

    @property
    def pull_request_number(self) -> int:
        return int(self.ingested["provenance"]["pull_request_number"])

    @property
    def classification(self) -> dict[str, Any] | None:
        return self.classifications.get("proven") or self.classifications.get("deterministic")

    @property
    def open(self) -> bool:
        return self.resolved is None


@dataclass(slots=True)
class KnowledgeView:
    repository_id: int
    findings: dict[str, Finding] = field(default_factory=dict)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    recurrences: set[tuple[str, str]] = field(default_factory=set)
    keys: dict[tuple[str, str], str] = field(default_factory=dict)  # (kind, key) -> evidence digest
    head_record_digest: str | None = None
    next_seq: int = 0

    def matched_family_for(self, print_: str) -> str | None:
        families = {
            classification["family_id"]
            for item in self.findings.values()
            if item.ingested["fingerprint"] == print_
            and (classification := item.classification) is not None
            and classification["outcome"] == "matched"
        }
        return next(iter(families)) if len(families) == 1 else None

    def remediations_for_pr(self, pull_request_number: int) -> int:
        return sum(
            len(item.remediations) for item in self.findings.values() if item.pull_request_number == pull_request_number
        )


def record_key(kind: str, evidence: Mapping[str, Any]) -> str:
    """The L2 uniqueness key of a record, derived only from its evidence."""

    if kind in {"finding_ingested", "finding_proven", "thread_resolved"}:
        return str(evidence["finding_id"])
    if kind == "finding_classified":
        return sha256_hex(canonical_json({"finding_id": evidence["finding_id"], "basis": evidence["basis"]}))
    if kind == "family_candidate":
        return str(evidence["candidate_id"])
    if kind == "remediation_requested":
        return sha256_hex(canonical_json({"finding_id": evidence["finding_id"], "attempt": evidence["attempt"]}))
    if kind == "recurrence":
        return sha256_hex(canonical_json({"family_id": evidence["family_id"], "finding_id": evidence["finding_id"]}))
    raise KnowledgeLedgerError(f"unknown kind {kind}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise KnowledgeLedgerError(message)


def apply(view: KnowledgeView, record: Mapping[str, Any], *, trust: TrustRoots, provenance: ProvenanceCheck) -> None:
    """Verify one record against the verified prefix and fold it in. Any failure is ``STATE_CORRUPT``."""

    try:
        record = json.loads(canonical_json(record))
        _RECORD(record, "knowledge record")
        kind = record["kind"]
        EVIDENCE_SCHEMAS[kind](record["evidence"], f"knowledge evidence[{kind}]")
    except LedgerSchemaError as error:
        raise KnowledgeLedgerError(f"schema: {error}") from None
    if len(canonical_json(record)) > state.MAX_RECORD_BYTES:
        raise KnowledgeLedgerError("knowledge record exceeds its size bound")
    verify_record_signature(record, trust.state_keys, domain=KNOWLEDGE_LEDGER_DOMAIN)
    _require(record["record_seq"] == view.next_seq, "knowledge record sequence is not contiguous")
    _require(record["prev_record_sha256"] == view.head_record_digest, "knowledge record does not chain")
    _require(record["repository_id"] == view.repository_id == trust.repository_id, "foreign repository")
    _require(record["recorded_by"]["role"] in WRITER_ROLES[kind], f"role may not write {kind}")
    _require(bool(provenance(record["recorded_by"], record)), "knowledge record was not written by a trusted run")
    evidence = record["evidence"]
    _require(record["key"] == record_key(kind, evidence), "knowledge record key is not derived from its evidence")
    _require((kind, record["key"]) not in view.keys, f"{kind} is insert-only and already recorded")
    _fold(view, kind, evidence)
    view.keys[(kind, record["key"])] = sha256_hex(canonical_json(evidence))
    view.head_record_digest = record_digest(record)
    view.next_seq += 1


def _fold(view: KnowledgeView, kind: str, evidence: dict[str, Any]) -> None:
    if kind == "finding_ingested":
        provenance = evidence["provenance"]
        expected = finding_id(view.repository_id, provenance["pull_request_number"], provenance["comment_id"])
        _require(evidence["finding_id"] == expected, "finding id is not derived from its provenance")
        _require(evidence["fingerprint"] == fingerprint(provenance["path"], evidence["claim"]), "fingerprint")
        view.findings[expected] = Finding(dict(evidence))
        return
    if kind == "family_candidate":
        _require(
            evidence["candidate_id"] == candidate_id(evidence["invariant"], evidence["changed_paths"]),
            "candidate id is not derived from its invariant and paths",
        )
        for source in evidence["source_finding_ids"]:
            _require(source in view.findings, "family candidate names an unknown finding")
        view.candidates[evidence["candidate_id"]] = dict(evidence)
        return
    item = view.findings.get(evidence["finding_id"])
    _require(item is not None, f"{kind} for an unknown finding")
    assert item is not None
    if kind == "finding_classified":
        outcome, basis = evidence["outcome"], evidence["basis"]
        # One classification per basis is the (finding_id, basis) key plus the insert-only rule above.
        _require((outcome == "matched") == (evidence["family_id"] is not None), "family id iff matched")
        _require(
            (outcome == "candidate-new-family") == (evidence["candidate_id"] is not None),
            "candidate id iff candidate-new-family",
        )
        if basis == "proven":
            _require(outcome in {"matched", "candidate-new-family"}, "only a mapping can be proven")
            _require(bool(evidence["regression_tests"]) and evidence["authorization_id"] is not None, "no proof")
            deterministic = item.classifications.get("deterministic")
            if deterministic is not None and deterministic["outcome"] == "matched":
                _require(evidence["family_id"] == deterministic["family_id"], "proof contradicts the mapping")
            if outcome == "candidate-new-family":
                _require(evidence["candidate_id"] in view.candidates, "proven candidate is not recorded")
        else:
            _require(not evidence["regression_tests"] and evidence["authorization_id"] is None, "not deterministic")
            _require(outcome in {"matched", "ambiguous"}, "a deterministic outcome is a match or ambiguous")
        item.classifications[basis] = dict(evidence)
        return
    if kind == "remediation_requested":
        _require(item.open and item.proven is None, "remediation of a closed finding")
        _require(evidence["attempt"] == len(item.remediations) + 1, "remediation attempt is not the next attempt")
        _require(evidence["attempt"] <= MAX_REMEDIATIONS_PER_FINDING, "per-finding remediation budget exhausted")
        _require(evidence["pull_request_number"] == item.pull_request_number, "remediation targets another PR")
        _require(view.remediations_for_pr(item.pull_request_number) < MAX_REMEDIATIONS_PER_PR, "PR budget exhausted")
        item.remediations.append(dict(evidence))
        return
    if kind == "finding_proven":
        proven = item.classifications.get("proven")
        _require(proven is not None, "a finding is proven only after a proven classification")
        assert proven is not None
        _require(proven["authorization_id"] == evidence["authorization_id"], "proof from another remediation")
        _require(set(evidence["regression_tests"]) == set(proven["regression_tests"]), "proof tests differ")
        item.proven = dict(evidence)
        return
    if kind == "thread_resolved":
        _require(item.proven is not None, "a thread is resolved only after exact-head proof")
        assert item.proven is not None
        _require(evidence["remediated_head_sha"] == item.proven["remediated_head_sha"], "resolution at another head")
        item.resolved = dict(evidence)
        return
    if kind == "recurrence":
        classification = item.classification
        _require(
            item.proven is not None
            and classification is not None
            and classification.get("family_id") == evidence["family_id"],
            "recurrence of an unproven or differently mapped finding",
        )
        view.recurrences.add((evidence["family_id"], evidence["finding_id"]))
        return
    raise KnowledgeLedgerError(f"unknown kind {kind}")


def read(store: GitLedgerStore, *, trust: TrustRoots, provenance: ProvenanceCheck) -> tuple[str | None, KnowledgeView]:
    head, entries = store.read_files(KNOWLEDGE_LEDGER_REF, _FILES)
    view = KnowledgeView(trust.repository_id)
    for _commit, files in entries:
        try:
            record = json.loads(files["record.json"])
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise KnowledgeLedgerError("knowledge record is not JSON") from None
        apply(view, record, trust=trust, provenance=provenance)
    return head, view


@dataclass(frozen=True, slots=True)
class Write:
    kind: str
    evidence: Mapping[str, Any]


def append(
    store: GitLedgerStore,
    writes: Iterable[Write],
    *,
    trust: TrustRoots,
    provenance: ProvenanceCheck,
    signing_key: Ed25519PrivateKey,
    recorded_by: Mapping[str, Any],
    recorded_at: str,
) -> tuple[str | None, KnowledgeView, int]:
    """Append each write once (insert-only, idempotent). Returns ``(head, view, records_written)``.

    A write whose ``(kind, key)`` already holds byte-identical evidence is the idempotent replay of an earlier
    (possibly unacknowledged) write and is skipped; different evidence under the same key is refused.
    """

    head, view = read(store, trust=trust, provenance=provenance)
    written = 0
    for write in writes:
        key = record_key(write.kind, write.evidence)
        digest = sha256_hex(canonical_json(dict(write.evidence)))
        existing = view.keys.get((write.kind, key))
        if existing is not None:
            if existing != digest:
                raise KnowledgeLedgerError(f"{write.kind} {key[:12]} is already recorded with different evidence")
            continue
        record = sign_record(
            {
                "schema_version": RECORD_SCHEMA_VERSION,
                "kind": write.kind,
                "key": key,
                "record_seq": view.next_seq,
                "prev_record_sha256": view.head_record_digest,
                "recorded_at": recorded_at,
                "recorded_by": dict(recorded_by),
                "repository_id": trust.repository_id,
                "evidence": dict(write.evidence),
            },
            signing_key,
            domain=KNOWLEDGE_LEDGER_DOMAIN,
        )
        apply(view, record, trust=trust, provenance=provenance)
        head = store.append_files(
            KNOWLEDGE_LEDGER_REF,
            head,
            {"record.json": canonical_json(record)},
            message=f"knowledge {record['record_seq']} {write.kind}",
            timestamp=recorded_at,
        )
        written += 1
    return head, view, written


# --- ingestion with deterministic classification (L1, L3.1) ------------------------------------------------


FamilyApplicability = Callable[[str, str], bool | None]
"""``(family_id, path) -> True`` (applicable), ``False`` (exists, not applicable), ``None`` (no such family)."""


def ingestion_writes(
    view: KnowledgeView,
    observations: Sequence[Mapping[str, Any]],
    *,
    repository_id: int,
    trusted_reviewers: frozenset[str],
    applicability: FamilyApplicability,
) -> tuple[list[Write], list[str]]:
    """The fast-path records for a batch of observations, plus the refusal reasons (never silently dropped)."""

    writes: list[Write] = []
    refusals: list[str] = []
    pending: dict[str, str] = {}  # fingerprint -> family, within this batch
    queued: set[str] = set()
    for observation in observations:
        if not str(observation.get("event_id", "")).startswith("review-comment-"):
            continue  # top-level reviews stay learning observations, not findings
        try:
            evidence = finding_from_observation(
                observation, repository_id=repository_id, trusted_reviewers=trusted_reviewers
            )
            tag = family_tag(str(observation.get("message", "")))
        except FindingRefused as error:
            refusals.append(f"{observation.get('event_id')}: {error}")
            continue
        identity = evidence["finding_id"]
        if identity in view.findings or identity in queued:
            continue  # first observation wins; later heads re-observe the same thread (idempotent fast path)
        queued.add(identity)
        writes.append(Write("finding_ingested", evidence))
        family: str | None = None
        outcome: str | None = None
        if tag is not None:
            applies = applicability(tag, evidence["provenance"]["path"])
            outcome, family = ("matched", tag) if applies else ("ambiguous", None)
        else:
            family = view.matched_family_for(evidence["fingerprint"]) or pending.get(evidence["fingerprint"])
            outcome = "matched" if family is not None else None
        if outcome is None:
            continue
        if family is not None:
            pending[evidence["fingerprint"]] = family
        writes.append(
            Write(
                "finding_classified",
                {
                    "finding_id": identity,
                    "outcome": outcome,
                    "family_id": family,
                    "candidate_id": None,
                    "basis": "deterministic",
                    "regression_tests": [],
                    "authorization_id": None,
                },
            )
        )
    return writes, refusals


# --- DPM overlay (L2) -------------------------------------------------------------------------------------


def overlay_families(view: KnowledgeView) -> list[dict[str, Any]]:
    """Registry-shaped prevention entries for DPM: proven family candidates and every open finding."""

    entries: list[dict[str, Any]] = []
    for identity, candidate in sorted(view.candidates.items()):
        entries.append(
            {
                "id": f"KC-{identity[:12]}",
                "title": candidate["title"],
                "invariant": candidate["invariant"],
                "lifecycle": "recorded",
                "applicability": {"changed_paths": list(candidate["changed_paths"])},
                "prevention": {"boundary": "review"},
            }
        )
    for identity, item in sorted(view.findings.items()):
        if not item.open:
            continue
        classification = item.classification
        mapped = classification.get("family_id") if classification else None
        entries.append(
            {
                "id": f"KF-{identity[:12]}",
                "title": f"open-reviewer-finding-pr-{item.pull_request_number}",
                "invariant": f"Unresolved reviewer finding on {item.path}"
                + (f" (family {mapped})" if mapped else "")
                + f": {item.ingested['claim']}",
                "lifecycle": "recorded",
                "applicability": {"changed_paths": [item.path]},
                "prevention": {"boundary": "review"},
            }
        )
    return entries


__all__ = [
    "KNOWLEDGE_LEDGER_DOMAIN",
    "KNOWLEDGE_LEDGER_REF",
    "MAX_REMEDIATIONS_PER_FINDING",
    "MAX_REMEDIATIONS_PER_PR",
    "Finding",
    "FindingRefused",
    "KnowledgeLedgerError",
    "KnowledgeView",
    "Write",
    "append",
    "candidate_id",
    "family_tag",
    "finding_from_observation",
    "finding_id",
    "fingerprint",
    "ingestion_writes",
    "normalized_claim",
    "overlay_families",
    "read",
    "record_key",
]
