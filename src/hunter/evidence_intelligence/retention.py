"""Governed source-handling classification and retention derivation (ADR 0031).

ADR 0031 "Data handling and retention" requires that, before inclusion or
durable persistence, "every source reference must carry, or deterministically
derive from governed source policy, a handling classification sufficient to
decide whether its exact bytes may be processed, retained, and reconstructed".

This module owns that derivation. It exists because retention was previously
asserted by callers through a boolean, which is not a governed policy: a caller
flag records no policy identity, cannot be replayed, and cannot be audited. The
rule enforced here is that callers supply *facts* (a per-span handling
classification and the governing policy) while the retention *outcome* is
derived deterministically and recorded with its policy identity.

The module deliberately does not import the pre-model build module: the
dependency direction is build -> retention, never the reverse.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

SourceHandlingClassification = Literal[
    "UNCLASSIFIED",
    "RETAINABLE",
    "RESTRICTED",
    "EPHEMERAL",
    "SENSITIVE",
    "SECRET",
]

#: Absence of a classification is never treated as permission. ADR 0031 requires
#: fail-closed handling when the governing classification is unknown.
DEFAULT_CLASSIFICATION: SourceHandlingClassification = "UNCLASSIFIED"

UNCLASSIFIED_REASON_CODE = "SOURCE_HANDLING_UNCLASSIFIED"
NON_RETAINABLE_REASON_CODE = "SOURCE_BYTES_RETENTION_PROHIBITED"
PROMPT_RETENTION_PROHIBITED_REASON_CODE = "EXACT_PROMPT_RETENTION_PROHIBITED"

#: Sentinel recorded for bundles persisted before this conformance correction
#: existed. It marks the absence of a governed policy rather than inventing one.
LEGACY_RETENTION_POLICY_IDENTITY = "evidence-retention-policy:legacy-pre-f9"

# Conservative, high-signal credential markers. A false positive fails the build
# closed, which is the safe direction; a false negative would persist a secret,
# which ADR 0031 prohibits outright.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS_ACCESS_KEY_ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("BEARER_TOKEN", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "CREDENTIAL_ASSIGNMENT",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|password)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{16,}"
        ),
    ),
)


class SourceHandlingViolation(RuntimeError):
    """Raised when governed source handling forbids continuing the build.

    This is a fail-closed outcome, never a downgrade: a build that trips it
    produces no prompt artifact and no persisted bytes.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _identity(kind: str, value: object) -> str:
    digest = hashlib.sha256(_canonical_json(value)).hexdigest()
    return f"{kind}:{digest}"


@dataclass(frozen=True)
class EvidenceSourceHandlingClassification:
    """One governed handling classification for one exact source span.

    ``classification_source`` records where the classification came from so the
    decision remains auditable. It is required: an unattributed classification
    would be the same caller assertion this module exists to remove.
    """

    span_id: str
    classification: SourceHandlingClassification
    classification_source: str
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if not self.span_id:
            raise ValueError("span_id must be non-empty")
        if not self.classification_source:
            raise ValueError("classification_source must be non-empty")


@dataclass(frozen=True)
class EvidenceRetentionPolicy:
    """Versioned, content-addressed mapping from handling class to eligibility.

    ``retainable_classifications`` lists the classes whose exact bytes may be
    retained. ``prohibited_classifications`` lists the classes that must fail the
    build outright rather than merely suppress retention.
    """

    policy_id: str
    version: str
    retainable_classifications: tuple[SourceHandlingClassification, ...]
    prohibited_classifications: tuple[SourceHandlingClassification, ...] = ("SECRET",)
    schema_version: str = "1"

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("policy_id must be non-empty")
        if not self.version:
            raise ValueError("version must be non-empty")
        retainable = tuple(sorted(set(self.retainable_classifications)))
        prohibited = tuple(sorted(set(self.prohibited_classifications)))
        overlap = set(retainable).intersection(prohibited)
        if overlap:
            raise ValueError("a classification cannot be both retainable and prohibited")
        if DEFAULT_CLASSIFICATION in retainable:
            # Permitting UNCLASSIFIED retention would reintroduce the permissive
            # default this correction removes.
            raise ValueError("UNCLASSIFIED must never be retainable")
        object.__setattr__(self, "retainable_classifications", retainable)
        object.__setattr__(self, "prohibited_classifications", prohibited)

    @property
    def policy_identity(self) -> str:
        return _identity("evidence-retention-policy", asdict(self))


@dataclass(frozen=True)
class EvidenceRetentionDecision:
    """Derived, replayable retention outcome for one pre-model build.

    Prompt-byte retention and source-byte retention are separate outcomes.
    Prompt bytes depend only on the spans actually rendered into the prompt;
    source bytes are decided per span across the whole canonical inventory, so a
    non-retainable span that never entered the prompt still suppresses its own
    excerpt without suppressing the prompt.
    """

    policy_identity: str
    retain_prompt_bytes: bool
    non_retainable_span_ids: tuple[str, ...]
    span_classifications: tuple[tuple[str, str], ...]
    reason_codes: tuple[str, ...]
    schema_version: str = "1"

    @property
    def decision_identity(self) -> str:
        return _identity("evidence-retention-decision", asdict(self))

    @property
    def retain_source_bytes(self) -> bool:
        return not self.non_retainable_span_ids


def detect_secret_material(text: str) -> tuple[str, ...]:
    """Return the names of credential markers found in ``text``."""

    return tuple(name for name, pattern in _SECRET_PATTERNS if pattern.search(text))


def _classification_map(
    *,
    inventory_span_ids: tuple[str, ...],
    classifications: tuple[EvidenceSourceHandlingClassification, ...],
) -> dict[str, SourceHandlingClassification]:
    supplied: dict[str, SourceHandlingClassification] = {}
    for entry in classifications:
        existing = supplied.get(entry.span_id)
        if existing is not None and existing != entry.classification:
            # Two conflicting governed classifications for one span cannot be
            # silently reconciled; picking either would be an invented decision.
            raise SourceHandlingViolation(f"SOURCE_HANDLING_CONFLICT:{entry.span_id}")
        supplied[entry.span_id] = entry.classification
    return {span_id: supplied.get(span_id, DEFAULT_CLASSIFICATION) for span_id in inventory_span_ids}


def screen_source_handling(
    *,
    policy: EvidenceRetentionPolicy,
    classifications: tuple[EvidenceSourceHandlingClassification, ...],
    inventory_span_ids: tuple[str, ...],
    span_texts: tuple[tuple[str, str], ...],
) -> dict[str, SourceHandlingClassification]:
    """Fail closed before any prompt is rendered, and return the resolved map.

    Screening runs ahead of rendering so prohibited material never reaches an
    artifact, a hash, or a persisted payload. ADR 0031 requires rejection rather
    than "silent redaction followed by a claim of exact reconstruction".
    """

    resolved = _classification_map(
        inventory_span_ids=inventory_span_ids,
        classifications=classifications,
    )

    for span_id in sorted(resolved):
        if resolved[span_id] in policy.prohibited_classifications:
            raise SourceHandlingViolation(f"SOURCE_HANDLING_PROHIBITED:{span_id}")

    for span_id, text in sorted(span_texts):
        detected = detect_secret_material(text)
        if detected:
            raise SourceHandlingViolation(f"SECRET_MATERIAL_DETECTED:{span_id}:{detected[0]}")

    return resolved


def derive_retention_decision(
    *,
    policy: EvidenceRetentionPolicy,
    resolved_classifications: dict[str, SourceHandlingClassification],
    included_span_ids: tuple[str, ...],
) -> EvidenceRetentionDecision:
    """Derive retention from governed policy. No caller may assert the outcome."""

    non_retainable = tuple(
        sorted(
            span_id
            for span_id, classification in resolved_classifications.items()
            if classification not in policy.retainable_classifications
        )
    )
    retain_prompt = not any(span_id in set(non_retainable) for span_id in included_span_ids)

    reasons: list[str] = []
    if any(resolved_classifications[span_id] == DEFAULT_CLASSIFICATION for span_id in non_retainable):
        reasons.append(UNCLASSIFIED_REASON_CODE)
    if non_retainable:
        reasons.append(NON_RETAINABLE_REASON_CODE)
    if not retain_prompt:
        reasons.append(PROMPT_RETENTION_PROHIBITED_REASON_CODE)

    return EvidenceRetentionDecision(
        policy_identity=policy.policy_identity,
        retain_prompt_bytes=retain_prompt,
        non_retainable_span_ids=non_retainable,
        span_classifications=tuple(sorted(resolved_classifications.items())),
        reason_codes=tuple(sorted(set(reasons))),
    )


def legacy_retention_decision(*, exact_source_bytes_retained: bool) -> EvidenceRetentionDecision:
    """Represent a pre-correction persisted bundle without inventing a policy.

    Bundles written before this correction recorded only a boolean. That boolean
    is preserved as the historical fact it was, under an explicit legacy policy
    identity so the absence of a governed policy stays visible and is never
    mistaken for a derived decision.
    """

    return EvidenceRetentionDecision(
        policy_identity=LEGACY_RETENTION_POLICY_IDENTITY,
        retain_prompt_bytes=exact_source_bytes_retained,
        non_retainable_span_ids=() if exact_source_bytes_retained else ("*",),
        span_classifications=(),
        reason_codes=() if exact_source_bytes_retained else (NON_RETAINABLE_REASON_CODE,),
    )
